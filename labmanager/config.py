"""Loads lab.json: the single description of the lab (VMs, hosts, services, RDP, backups, Docker, Proxmox).

The file lives in the user's profile (%APPDATA%/LabManager/lab.json), so updates of the program never
touch it. VMs, hosts and services may be empty lists; the sections rdp, backup, docker and proxmox may
be left out, and LabManager then hides what needs them. Inside a list entry or a section every value is
required: a missing or malformed value stops the program with a message that names the exact key,
because there is no value that would be right for every lab.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "lab.json"
APP_FOLDER = "LabManager"


class ConfigError(Exception):
    """lab.json is missing, unreadable or incomplete."""


@dataclass(frozen=True)
class Vm:
    name: str
    label: str


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    address: str
    ssh_user: str


@dataclass(frozen=True)
class Service:
    label: str
    host: str
    port: int
    url: str


@dataclass(frozen=True)
class RdpProfile:
    name: str
    settings: tuple[str, ...]


@dataclass(frozen=True)
class Rdp:
    host: str
    username: str
    base: tuple[str, ...]
    profiles: tuple[RdpProfile, ...]


@dataclass(frozen=True)
class Backup:
    host: str
    remote_dir: str
    pattern: str
    target_dir: Path
    keep: int
    max_age_days: int  # newest Proxmox backup older than this is shown red
    copy_max_age_days: int  # newest Windows copy older than this is shown red


@dataclass(frozen=True)
class Docker:
    host: str


@dataclass(frozen=True)
class Proxmox:
    host: str
    web: Service  # the Proxmox web interface, used for links to a VM's pages


@dataclass(frozen=True)
class Lab:
    ssh_key: Path | None  # required as soon as there are hosts
    vms: tuple[Vm, ...]
    hosts: tuple[Host, ...]
    services: tuple[Service, ...]
    rdp: Rdp | None
    backup: Backup | None
    docker: Docker | None
    proxmox: Proxmox | None

    def host(self, host_id: str) -> Host:
        for h in self.hosts:
            if h.id == host_id:
                return h
        raise KeyError(host_id)

    def require_ssh_key(self) -> Path:
        if self.ssh_key is None:
            raise ConfigError("ssh_key ist in lab.json nicht eingerichtet.")
        return self.ssh_key

    def require_rdp(self) -> Rdp:
        if self.rdp is None:
            raise ConfigError("Remotedesktop ist in lab.json nicht eingerichtet.")
        return self.rdp

    def require_backup(self) -> Backup:
        if self.backup is None:
            raise ConfigError("Backups sind in lab.json nicht eingerichtet.")
        return self.backup

    def require_docker(self) -> Docker:
        if self.docker is None:
            raise ConfigError("Docker ist in lab.json nicht eingerichtet.")
        return self.docker

    def require_proxmox(self) -> Proxmox:
        if self.proxmox is None:
            raise ConfigError("Proxmox ist in lab.json nicht eingerichtet.")
        return self.proxmox


def _obj(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: Objekt erwartet")
    return {str(k): v for k, v in value.items()}


def _list(data: dict[str, object], key: str, where: str) -> list[object]:
    value = _get(data, key, where)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}.{key}: nicht-leere Liste erwartet")
    return value


def _entries(data: dict[str, object], key: str, where: str) -> list[object]:
    """A list of the lab's VMs, hosts or services; it may be empty."""
    value = _get(data, key, where)
    if not isinstance(value, list):
        raise ConfigError(f"{where}.{key}: Liste erwartet")
    return value


def _section(data: dict[str, object], key: str) -> dict[str, object] | None:
    """An optional section: absent means the lab does not use it."""
    return _obj(data[key], key) if key in data else None


def _get(data: dict[str, object], key: str, where: str) -> object:
    if key not in data:
        raise ConfigError(f"{where}.{key} fehlt")
    return data[key]


def _str(data: dict[str, object], key: str, where: str) -> str:
    value = _get(data, key, where)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key}: nicht-leerer Text erwartet")
    return value


def _int(data: dict[str, object], key: str, where: str, minimum: int) -> int:
    value = _get(data, key, where)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{where}.{key}: ganze Zahl >= {minimum} erwartet")
    return value


def _strings(data: dict[str, object], key: str, where: str) -> tuple[str, ...]:
    items = _list(data, key, where)
    if not all(isinstance(i, str) and i.strip() for i in items):
        raise ConfigError(f"{where}.{key}: Liste aus nicht-leerem Text erwartet")
    return tuple(str(i) for i in items)


def _path(data: dict[str, object], key: str, where: str) -> Path:
    return Path(os.path.expandvars(_str(data, key, where)))


def _unique(names: list[str], where: str) -> None:
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise ConfigError(f"{where}: '{n}' kommt doppelt vor")
        seen.add(n)


def parse(raw: object) -> Lab:
    """Validates the decoded JSON and builds the typed lab description."""
    root = _obj(raw, "lab.json")

    vms = tuple(
        Vm(name=_str(o, "name", w), label=_str(o, "label", w))
        for i, item in enumerate(_entries(root, "vms", "lab.json"))
        for w in [f"vms[{i}]"]
        for o in [_obj(item, w)]
    )
    _unique([v.name for v in vms], "vms")

    hosts = tuple(
        Host(id=_str(o, "id", w), label=_str(o, "label", w), address=_str(o, "address", w),
             ssh_user=_str(o, "ssh_user", w))
        for i, item in enumerate(_entries(root, "hosts", "lab.json"))
        for w in [f"hosts[{i}]"]
        for o in [_obj(item, w)]
    )
    _unique([h.id for h in hosts], "hosts")
    host_ids = {h.id for h in hosts}

    def known_host(host_id: str, where: str) -> str:
        if host_id not in host_ids:
            raise ConfigError(f"{where}: unbekannter Host '{host_id}'")
        return host_id

    services = tuple(
        Service(label=_str(o, "label", w), host=known_host(_str(o, "host", w), f"{w}.host"),
                port=_int(o, "port", w, 1), url=_str(o, "url", w))
        for i, item in enumerate(_entries(root, "services", "lab.json"))
        for w in [f"services[{i}]"]
        for o in [_obj(item, w)]
    )

    rdp: Rdp | None = None
    rdp_raw = _section(root, "rdp")
    if rdp_raw is not None:
        profiles_raw = _obj(_get(rdp_raw, "profiles", "rdp"), "rdp.profiles")
        if not profiles_raw:
            raise ConfigError("rdp.profiles: mindestens ein Profil erwartet")
        rdp = Rdp(
            host=known_host(_str(rdp_raw, "host", "rdp"), "rdp.host"),
            username=_str(rdp_raw, "username", "rdp"),
            base=_strings(rdp_raw, "base", "rdp"),
            profiles=tuple(RdpProfile(name=name, settings=_strings(profiles_raw, name, "rdp.profiles"))
                           for name in profiles_raw),
        )

    backup: Backup | None = None
    b = _section(root, "backup")
    if b is not None:
        backup = Backup(
            host=known_host(_str(b, "host", "backup"), "backup.host"),
            remote_dir=_str(b, "remote_dir", "backup"),
            pattern=_str(b, "pattern", "backup"),
            target_dir=_path(b, "target_dir", "backup"),
            keep=_int(b, "keep", "backup", 1),
            max_age_days=_int(b, "max_age_days", "backup", 1),
            copy_max_age_days=_int(b, "copy_max_age_days", "backup", 1),
        )

    d = _section(root, "docker")
    docker = Docker(host=known_host(_str(d, "host", "docker"), "docker.host")) if d is not None else None

    proxmox: Proxmox | None = None
    px = _section(root, "proxmox")
    if px is not None:
        web_label = _str(px, "web", "proxmox")
        web = next((s for s in services if s.label == web_label), None)
        if web is None:
            raise ConfigError(f"proxmox.web: unbekannter Dienst '{web_label}'")
        proxmox = Proxmox(host=known_host(_str(px, "host", "proxmox"), "proxmox.host"), web=web)

    ssh_key = _path(root, "ssh_key", "lab.json") if hosts or "ssh_key" in root else None
    return Lab(ssh_key=ssh_key, vms=vms, hosts=hosts,
               services=services, rdp=rdp, backup=backup, docker=docker, proxmox=proxmox)


def user_config_path(environ: Mapping[str, str]) -> Path:
    """%APPDATA%/LabManager/lab.json of the current user."""
    if "APPDATA" not in environ:
        raise ConfigError("Umgebungsvariable APPDATA fehlt; ohne sie kennt LabManager keinen Ort für lab.json.")
    return Path(environ["APPDATA"]) / APP_FOLDER / CONFIG_NAME


def load(path: Path) -> Lab:
    """Reads and validates lab.json; any problem raises ConfigError with a readable message."""
    if not path.is_file():
        raise ConfigError(f"Konfiguration nicht gefunden: {path}")
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name} ist kein gültiges JSON (Zeile {e.lineno}): {e.msg}") from e
    return parse(raw)
