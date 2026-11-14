"""Editing lab.json from the settings tab: VMs, hosts, services and the SSH key.

Changes are made on the decoded JSON, so sections that are not edited here stay exactly as they are,
and every change is checked with config.parse, the same validation LabManager runs at start-up.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from labmanager import containers
from labmanager.config import ConfigError, Lab, parse

Raw = dict[str, object]
Entry = dict[str, object]
KINDS = ("vms", "hosts", "services")
EMPTY_LAB: Raw = {"vms": [], "hosts": [], "services": []}
# Sections that point at a host by its id, with the name shown when that host cannot be removed.
HOST_SECTIONS = {"rdp": "Remotedesktop", "backup": "Backups", "docker": "Docker", "proxmox": "Proxmox"}


class SettingsError(Exception):
    """A change would make lab.json invalid or break a reference; the message says why."""


def load_raw(path: Path) -> Raw:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SettingsError(f"{path} nicht lesbar: {e}") from e
    if not isinstance(value, dict):
        raise SettingsError(f"{path}: kein JSON-Objekt")
    return {str(k): v for k, v in value.items()}


def checked(raw: Raw) -> Lab:
    try:
        return parse(raw)
    except ConfigError as e:
        raise SettingsError(str(e)) from e


def entries(raw: Raw, kind: str) -> list[Entry]:
    items = raw.get(kind)
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise SettingsError(f"lab.json.{kind}: Liste von Einträgen erwartet")
    return [{str(k): v for k, v in i.items()} for i in items]


def _section(raw: Raw, key: str) -> dict[str, object] | None:
    value = raw.get(key)
    return value if isinstance(value, dict) else None


def host_references(raw: Raw, host_id: str) -> list[str]:
    found = [f"Dienst {s['label']}" for s in entries(raw, "services") if s.get("host") == host_id]
    found += [name for key, name in HOST_SECTIONS.items()
              if (section := _section(raw, key)) is not None and section.get("host") == host_id]
    return found


def service_references(raw: Raw, label: str) -> list[str]:
    proxmox = _section(raw, "proxmox")
    return ["Proxmox (Weboberfläche)"] if proxmox is not None and proxmox.get("web") == label else []


def with_entry(raw: Raw, kind: str, index: int | None, entry: Entry) -> Raw:
    """A copy of raw with the entry added (index None) or replacing entry `index`, checked as a whole."""
    new = copy.deepcopy(raw)
    items = entries(new, kind)
    if index is None:
        items.append(dict(entry))
    else:
        old = items[index]
        if kind == "hosts" and old.get("id") != entry.get("id"):
            raise SettingsError(f"Die ID eines Hosts lässt sich nicht ändern ('{old.get('id')}'); "
                                "für eine andere ID den Host neu anlegen.")
        proxmox = _section(new, "proxmox")
        if kind == "services" and proxmox is not None and proxmox.get("web") == old.get("label"):
            proxmox["web"] = entry.get("label")
        items[index] = dict(entry)
    new[kind] = items
    checked(new)
    return new


def without_entry(raw: Raw, kind: str, index: int) -> Raw:
    items = entries(raw, kind)
    entry = items[index]
    if kind == "hosts":
        used = host_references(raw, str(entry.get("id")))
    elif kind == "services":
        used = service_references(raw, str(entry.get("label")))
    else:
        used = []
    if used:
        raise SettingsError(f"{entry.get('label')} wird noch verwendet von: {', '.join(used)}")
    new = copy.deepcopy(raw)
    new[kind] = [e for i, e in enumerate(items) if i != index]
    checked(new)
    return new


def with_ssh_key(raw: Raw, key: str) -> Raw:
    if not key.strip():
        raise SettingsError("ssh_key: Pfad zum privaten SSH-Schlüssel eintragen.")
    new = copy.deepcopy(raw)
    new["ssh_key"] = key.strip()
    checked(new)
    return new


def save_raw(path: Path, raw: Raw) -> None:
    """Writes a valid lab.json; a temporary file replaces the old one, so a failure never leaves half a file."""
    checked(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes((json.dumps(raw, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.replace(temporary, path)


SECTIONS = ("rdp", "backup", "docker", "proxmox")


def with_section(raw: Raw, key: str, section: dict[str, object] | None) -> Raw:
    """A copy of raw with an optional section set, or switched off with None; checked as a whole."""
    if key not in SECTIONS:
        raise KeyError(key)
    new = copy.deepcopy(raw)
    if section is None:
        new.pop(key, None)
    else:
        new[key] = copy.deepcopy(section)
    checked(new)
    return new


@dataclass(frozen=True)
class ProfileSpec:
    """A remote desktop profile as the settings dialog shows it."""

    kind: str  # "window", "monitors" or "custom"
    width: int  # window only
    height: int  # window only
    monitors: tuple[int, ...]  # monitors only: Remote Desktop ids, the session's main screen first
    lines: tuple[str, ...]  # custom only: the .rdp lines as written


WINDOW = re.compile(r"screen mode id:i:1\nuse multimon:i:0\ndesktopwidth:i:(\d+)\ndesktopheight:i:(\d+)")
MONITORS = re.compile(r"screen mode id:i:2\nuse multimon:i:1\nselectedmonitors:s:(\d+(?:,\d+)*)")


def read_profile(lines: object) -> ProfileSpec:
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise SettingsError(f"Remotedesktop-Profil: Liste von Zeilen erwartet, nicht {lines!r}")
    text = "\n".join(lines)
    if m := WINDOW.fullmatch(text):
        return ProfileSpec("window", int(m.group(1)), int(m.group(2)), (), ())
    if m := MONITORS.fullmatch(text):
        return ProfileSpec("monitors", 0, 0, tuple(int(i) for i in m.group(1).split(",")), ())
    return ProfileSpec("custom", 0, 0, (), tuple(lines))


def profile_lines(spec: ProfileSpec) -> list[str]:
    if spec.kind == "window":
        if spec.width < 1 or spec.height < 1:
            raise SettingsError("Fenster: Breite und Höhe in Pixeln eintragen, z. B. 1920 und 1080.")
        return ["screen mode id:i:1", "use multimon:i:0", f"desktopwidth:i:{spec.width}",
                f"desktopheight:i:{spec.height}"]
    if spec.kind == "monitors":
        if not spec.monitors:
            raise SettingsError("Bildschirme: mindestens einen Bildschirm ankreuzen.")
        return ["screen mode id:i:2", "use multimon:i:1", "selectedmonitors:s:" + ",".join(map(str, spec.monitors))]
    lines = [line.strip() for line in spec.lines if line.strip()]
    if not lines:
        raise SettingsError("Eigene Zeilen: mindestens eine .rdp-Zeile eintragen, z. B. screen mode id:i:1")
    return lines


def _profiles(raw: Raw) -> dict[str, object]:
    rdp = _section(raw, "rdp")
    profiles = rdp.get("profiles") if rdp is not None else None
    if not isinstance(profiles, dict):
        raise SettingsError("Remotedesktop ist nicht eingerichtet; zuerst Host und Benutzer übernehmen.")
    return profiles


def with_profile(raw: Raw, old_name: str | None, name: str, lines: list[str]) -> Raw:
    """Adds a profile (old_name None) or replaces/renames one in place, keeping the order of the others."""
    name = name.strip()
    if not name:
        raise SettingsError("Profil: einen Namen eintragen, z. B. „Fenster“ oder „Alle Bildschirme“.")
    new = copy.deepcopy(raw)
    profiles = _profiles(new)
    if name != old_name and name in profiles:
        raise SettingsError(f"Ein Profil „{name}“ gibt es schon.")
    items = [(name if key == old_name else key, lines if key == old_name else value) for key, value in profiles.items()]
    if old_name is None:
        items.append((name, lines))
    profiles.clear()
    profiles.update(items)
    checked(new)
    return new


def without_profile(raw: Raw, name: str) -> Raw:
    new = copy.deepcopy(raw)
    profiles = _profiles(new)
    if len(profiles) == 1:
        raise SettingsError("Remotedesktop braucht mindestens ein Profil; zum Abschalten „Abschalten“ verwenden.")
    del profiles[name]
    checked(new)
    return new


# Ports that usually serve HTTPS; every other published port is suggested as http.
HTTPS_PORTS = {443, 8006, 8443}


def docker_suggestions(raw: Raw, items: list[containers.Container]) -> list[Entry]:
    """Services for published container ports that are not listed yet on the Docker host."""
    docker = _section(raw, "docker")
    if docker is None:
        raise SettingsError("Docker ist nicht eingerichtet; zuerst unter „Docker & Proxmox“ den Host übernehmen.")
    host_id = docker.get("host")
    host = next((h for h in entries(raw, "hosts") if h.get("id") == host_id), None)
    if host is None:
        raise SettingsError(f"docker.host: unbekannter Host '{host_id}'")
    listed = {s.get("port") for s in entries(raw, "services") if s.get("host") == host_id}
    found: list[Entry] = []
    for c in items:
        ports = [p for p in c.ports if p not in listed]
        for port in ports:
            scheme = "https" if port in HTTPS_PORTS else "http"
            found.append({"label": c.name if len(c.ports) == 1 else f"{c.name} ({port})", "host": host_id,
                          "port": port, "url": f"{scheme}://{host['address']}:{port}"})
    return found
