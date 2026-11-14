"""Proxmox backups of the lab and their copies on Windows.

Proxmox creates the backups with its own scheduled job. LabManager lists them, copies the newest one
to the Windows target folder (via a .partial file whose size is checked before it is renamed), keeps
the configured number of copies and appends every run to copy.log in the target folder.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from labmanager import ssh
from labmanager.config import Lab

LOG_NAME = "copy.log"
COPY_TIMEOUT_S = 3600
PARTIAL_SUFFIX = ".partial"
DAY_S = 86400


class BackupError(ssh.SshError):
    """A backup could not be listed or copied."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    size: int  # bytes
    mtime: float  # seconds since 1970


@dataclass(frozen=True)
class Job:
    id: str
    schedule: str
    enabled: bool
    next_run: float | None  # seconds since 1970; Proxmox omits it for disabled jobs
    keep: str  # Proxmox' retention, e.g. "keep-last 7"
    comment: str


Copier = Callable[[list[str]], str]


def parse_remote(output: str) -> list[BackupFile]:
    """Lines 'name|size|mtime' from find -printf."""
    files: list[BackupFile] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split("|")
        try:
            name, size, mtime = parts
            files.append(BackupFile(name=name, size=int(size), mtime=float(mtime)))
        except ValueError as e:
            raise BackupError(f"Unerwartete Antwort von Proxmox: {line[:120]}") from e
    return files


def list_remote(lab: Lab, run: ssh.Runner) -> list[BackupFile]:
    b = lab.require_backup()
    remote = ["find", b.remote_dir, "-maxdepth", "1", "-name", b.pattern, "-printf", "%f|%s|%T@\\n"]
    return parse_remote(run(ssh.command(lab, lab.host(b.host), *remote)))


def list_local(folder: Path, pattern: str) -> list[BackupFile]:
    if not folder.is_dir():
        raise BackupError(f"Zielordner {folder} fehlt (wird beim ersten Kopieren angelegt).")
    return [BackupFile(name=p.name, size=p.stat().st_size, mtime=p.stat().st_mtime)
            for p in folder.glob(pattern) if p.is_file()]


def parse_jobs(output: str) -> list[Job]:
    """`pvesh get /cluster/backup --output-format json`."""
    try:
        data: object = json.loads(output)
    except json.JSONDecodeError as e:
        raise BackupError(f"Unerwartete Antwort von Proxmox: {output[:120]}") from e
    if not isinstance(data, list):
        raise BackupError(f"Unerwartete Antwort von Proxmox: {output[:120]}")
    jobs: list[Job] = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) \
                or not isinstance(item.get("schedule"), str):
            raise BackupError(f"Unerwartete Antwort von Proxmox: {item!r}")
        prune = item.get("prune-backups")
        keep = ", ".join(f"{k} {v}" for k, v in prune.items()) if isinstance(prune, dict) else ""
        next_run = item.get("next-run")
        # Proxmox leaves out "enabled" and "comment" when they have their default values (on, none).
        jobs.append(Job(id=item["id"], schedule=item["schedule"], enabled=bool(item.get("enabled", 1)),
                        next_run=float(next_run) if isinstance(next_run, int | float) else None,
                        keep=keep, comment=str(item.get("comment", ""))))
    return jobs


def list_jobs(lab: Lab, run: ssh.Runner) -> list[Job]:
    cmd = ssh.command(lab, lab.host(lab.require_backup().host), "pvesh", "get", "/cluster/backup", "--output-format", "json")
    return parse_jobs(run(cmd))


def newest(files: list[BackupFile]) -> BackupFile | None:
    return max(files, key=lambda f: f.mtime) if files else None


def age_days(f: BackupFile, now: float) -> float:
    return (now - f.mtime) / DAY_S


def partial_path(lab: Lab, name: str) -> Path:
    return lab.require_backup().target_dir / (name + PARTIAL_SUFFIX)


def scp_command(lab: Lab, remote_name: str, local: Path) -> list[str]:
    host = lab.host(lab.require_backup().host)
    source = f"{host.ssh_user}@{host.address}:{lab.require_backup().remote_dir.rstrip('/')}/{remote_name}"
    return ["scp", "-q", "-i", str(lab.require_ssh_key()), *ssh.OPTIONS, source, str(local)]


def surplus(names: set[str], keep: int) -> list[str]:
    """All but the newest `keep` copies; names carry the backup time, so they sort by age."""
    return sorted(names, reverse=True)[keep:]


def local_names(folder: Path, pattern: str) -> set[str]:
    return {p.name for p in folder.glob(pattern) if p.is_file()} if folder.is_dir() else set()


def prune(folder: Path, pattern: str, keep: int) -> list[str]:
    """Deletes the surplus copies; returns the removed names."""
    old = surplus(local_names(folder, pattern), keep)
    for name in old:
        (folder / name).unlink()
    return old


@dataclass(frozen=True)
class CopyPlan:
    newest: str
    size: int
    copy: bool  # False when the newest backup is already there with the same size
    remove: list[str]  # copies that pruning deletes afterwards


def plan_copy(lab: Lab, run: ssh.Runner) -> CopyPlan:
    """What copy_newest would do, without changing anything."""
    b = lab.require_backup()
    remote = newest(list_remote(lab, run))
    if remote is None:
        raise BackupError(f"Auf Proxmox liegt kein Backup ({b.remote_dir}/{b.pattern}).")
    target = b.target_dir / remote.name
    present = target.is_file() and target.stat().st_size == remote.size
    return CopyPlan(newest=remote.name, size=remote.size, copy=not present,
                    remove=surplus(local_names(b.target_dir, b.pattern) | {remote.name}, b.keep))


def log(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / LOG_NAME).open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")


def last_log_line(folder: Path) -> str | None:
    path = folder / LOG_NAME
    if not path.is_file():
        return None
    lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return lines[-1] if lines else None


def gigabytes(size: int) -> str:
    return f"{size / 2**30:.1f} GB".replace(".", ",")


def copy_newest(lab: Lab, run: ssh.Runner, copy: Copier) -> list[str]:
    """Copies the newest Proxmox backup unless it is already there, then prunes old copies.

    Every result and every failure is written to copy.log; failures are raised afterwards.
    """
    b = lab.require_backup()
    messages: list[str] = []
    try:
        plan = plan_copy(lab, run)
        b.target_dir.mkdir(parents=True, exist_ok=True)
        if not plan.copy:
            messages.append(f"Aktuellstes Backup {plan.newest} ist bereits vorhanden - nichts zu tun.")
        else:
            partial = partial_path(lab, plan.newest)
            started = time.monotonic()
            copy(scp_command(lab, plan.newest, partial))
            size = partial.stat().st_size
            if size != plan.size:
                partial.unlink()
                raise BackupError(f"Größe stimmt nicht: lokal {size}, Proxmox {plan.size} Bytes.")
            partial.replace(b.target_dir / plan.newest)
            messages.append(f"Kopiert: {plan.newest} ({gigabytes(size)} in {time.monotonic() - started:.0f} s)")
        messages += [f"Alte Kopie entfernt: {name}" for name in prune(b.target_dir, b.pattern, b.keep)]
    except (ssh.SshError, OSError) as e:
        log(b.target_dir, f"FEHLER: {e}")
        raise
    for m in messages:
        log(b.target_dir, m)
    return messages


@dataclass(frozen=True)
class BackupState:
    remote: tuple[BackupFile, ...]
    remote_error: str | None
    local: tuple[BackupFile, ...]
    local_error: str | None
    jobs: tuple[Job, ...]
    jobs_error: str | None
    free_bytes: int | None  # free space on the target drive; None when it cannot be read
    last_log: str | None


def read_state(lab: Lab, run: ssh.Runner) -> BackupState:
    """Everything the backup tab shows; each source fails on its own and keeps its error text."""
    b = lab.require_backup()
    remote: list[BackupFile] = []
    local: list[BackupFile] = []
    jobs: list[Job] = []
    remote_error = local_error = jobs_error = None
    free: int | None = None
    try:
        remote = list_remote(lab, run)
        jobs = list_jobs(lab, run)
    except ssh.SshError as e:
        remote_error = jobs_error = str(e)
    try:
        local = list_local(b.target_dir, b.pattern)
        free = shutil.disk_usage(b.target_dir).free
    except (BackupError, OSError) as e:
        local_error = str(e)
    return BackupState(remote=tuple(sorted(remote, key=lambda f: f.mtime, reverse=True)), remote_error=remote_error,
                 local=tuple(sorted(local, key=lambda f: f.mtime, reverse=True)), local_error=local_error,
                 jobs=tuple(jobs), jobs_error=jobs_error, free_bytes=free, last_log=last_log_line(b.target_dir))


def warnings(lab: Lab, state: BackupState, now: float) -> list[str]:
    b = lab.require_backup()
    found: list[str] = []
    newest_remote = newest(list(state.remote))
    newest_local = newest(list(state.local))
    if state.remote_error is None:
        if newest_remote is None:
            found.append("Auf Proxmox liegt kein Backup.")
        elif age_days(newest_remote, now) > b.max_age_days:
            found.append(f"Neuestes Proxmox-Backup ist {age_days(newest_remote, now):.0f} Tage alt "
                         f"(Grenze {b.max_age_days}). Läuft der Backup-Job?")
    if state.local_error is None:
        if newest_local is None:
            found.append("Auf Windows liegt noch keine Kopie.")
        elif age_days(newest_local, now) > b.copy_max_age_days:
            found.append(f"Neueste Kopie auf Windows ist {age_days(newest_local, now):.0f} Tage alt "
                         f"(Grenze {b.copy_max_age_days}). „Jetzt kopieren“ holt das neueste Backup.")
    if any(not j.enabled for j in state.jobs):
        found.append("Ein Backup-Job in Proxmox ist ausgeschaltet.")
    return found
