"""Entry point of LabManager: the window, or a command for ToolHub, Claude and scheduled tasks.

    LabManager.exe                       opens the window
    LabManager.pyw status                VMs, hosts (real memory), Proxmox VMs, services, warnings
    LabManager.pyw containers            Docker containers with one reading of their resources
    LabManager.pyw backups               Proxmox backups, Windows copies, backup jobs, warnings
    LabManager.pyw backup-copy [--dry-run]
                                         copy the newest Proxmox backup to Windows (scheduled task);
                                         --dry-run only shows what would be copied and removed

The configuration is %APPDATA%/LabManager/lab.json. Commands print JSON on stdout. The windowed .exe has no stdout, so there they report only through
their exit code (Task Scheduler's "Letztes Ergebnis") and, for backup-copy, through copy.log.
Without administrator rights `status` reports the Hyper-V VMs as not readable; everything else works.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict

from labmanager import (
    backups,
    containers,
    gui,
    hyperv,
    memory,
    probes,
    proxmox,
    ssh,
    status,
)
from labmanager.config import (
    ConfigError,
    Lab,
    load,
    user_config_path,
)

EXIT_OK = 0
EXIT_FAILED = 1  # the command ran and failed; details on stderr or in its log
EXIT_USAGE = 2  # unknown command or option
EXIT_CONFIG = 3  # lab.json missing or invalid, or the command needs a section it does not set up

# Each command with the option lists it accepts.
COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "status": ((),), "containers": ((),), "backups": ((),), "backup-copy": ((), ("--dry-run",))}


def usage_ok(args: list[str]) -> bool:
    return bool(args) and args[0] in COMMANDS and tuple(args[1:]) in COMMANDS[args[0]]


def emit(data: object) -> None:
    if sys.stdout is not None:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")


def fail(message: str) -> int:
    if sys.stderr is not None:
        sys.stderr.write(message + "\n")
    return EXIT_FAILED


def status_data(lab: Lab, snap: status.Snapshot) -> dict[str, object]:
    return {**asdict(snap), "warnings": status.warnings(lab, snap)}


def containers_data(items: list[containers.Container], stats: dict[str, containers.Stats]) -> list[dict[str, object]]:
    return [{**asdict(c), "stats": asdict(stats[c.name]) if c.name in stats else None} for c in items]


def backups_data(lab: Lab, state: backups.BackupState, now: float) -> dict[str, object]:
    return {**asdict(state), "warnings": backups.warnings(lab, state, now)}


def backup_copy(lab: Lab, run: ssh.Runner, copy: backups.Copier, dry_run: bool) -> int:
    try:
        lab.require_backup()
    except ConfigError as e:
        fail(str(e))
        return EXIT_CONFIG
    if dry_run:
        try:
            emit({"dry_run": True, **asdict(backups.plan_copy(lab, run))})
        except (ssh.SshError, OSError) as e:
            return fail(str(e))
        return EXIT_OK
    try:
        emit({"messages": backups.copy_newest(lab, run, copy)})
    except (ssh.SshError, OSError) as e:
        # copy_newest has already written the error to copy.log; the exit code tells Task Scheduler.
        return fail(str(e))
    return EXIT_OK


def command(lab: Lab, args: list[str]) -> int:
    if not usage_ok(args):
        return EXIT_USAGE
    name, options = args[0], args[1:]
    if name == "backup-copy":
        return backup_copy(lab, ssh.run, lambda cmd: ssh.run_for(cmd, backups.COPY_TIMEOUT_S), dry_run=bool(options))
    try:
        if name == "status":
            snap = status.collect(lab, hyperv.vm_info, probes.port_open, lambda: proxmox.read(lab, ssh.run),
                                  lambda host: memory.read(lab, host, ssh.run))
            emit(status_data(lab, snap))
        elif name == "containers":
            emit(containers_data(containers.list_containers(lab, ssh.run), containers.read_stats(lab, ssh.run)))
        else:
            emit(backups_data(lab, backups.read_state(lab, ssh.run), time.time()))
    except ConfigError as e:
        fail(str(e))
        return EXIT_CONFIG
    except (ssh.SshError, OSError) as e:
        return fail(str(e))
    return EXIT_OK


def run(args: list[str]) -> int:
    try:
        config_path = user_config_path(os.environ)
    except ConfigError as e:
        fail(str(e))
        return EXIT_CONFIG
    if not args:
        gui.main(config_path)
        return EXIT_OK
    if not usage_ok(args):
        return EXIT_USAGE
    try:
        lab = load(config_path)
    except ConfigError as e:
        fail(str(e))
        return EXIT_CONFIG
    return command(lab, args)
