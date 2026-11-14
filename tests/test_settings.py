"""Tests for editing lab.json: entries, references, SSH key and safe saving.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import containers, settings
from labmanager.config import load

REPO_CONFIG = Path(__file__).resolve().parent / "fixtures" / "lab.json"


def raw() -> settings.Raw:
    return settings.load_raw(REPO_CONFIG)


HOMEPAGE = {"label": "Homepage", "host": "docker", "port": 3000, "url": "http://192.0.2.30:3000"}


class EntryTest(unittest.TestCase):
    def test_add_edit_and_remove_a_vm_without_touching_other_sections(self) -> None:
        before = raw()
        added = settings.with_entry(before, "vms", None, {"name": "win11", "label": "Windows 11"})
        self.assertEqual(settings.entries(added, "vms")[-1], {"name": "win11", "label": "Windows 11"})
        self.assertEqual(added["rdp"], before["rdp"])
        self.assertEqual(len(settings.entries(before, "vms")), 2, "the original must stay unchanged")
        edited = settings.with_entry(added, "vms", 2, {"name": "win11", "label": "Windows 11 Pro"})
        self.assertEqual(settings.entries(edited, "vms")[2]["label"], "Windows 11 Pro")
        removed = settings.without_entry(edited, "vms", 2)
        self.assertEqual(len(settings.entries(removed, "vms")), 2)

    def test_invalid_entries_are_rejected_with_the_config_message(self) -> None:
        with self.assertRaisesRegex(settings.SettingsError, r"vms\[2\]\.label: nicht-leerer Text"):
            settings.with_entry(raw(), "vms", None, {"name": "x", "label": " "})
        with self.assertRaisesRegex(settings.SettingsError, "doppelt"):
            settings.with_entry(raw(), "vms", None, {"name": "Linux-Desktop", "label": "nochmal"})
        bad_port = dict(HOMEPAGE, label="Neu", port=0)
        with self.assertRaisesRegex(settings.SettingsError, "port"):
            settings.with_entry(raw(), "services", None, bad_port)

    def test_a_host_in_use_cannot_be_removed(self) -> None:
        hosts = settings.entries(raw(), "hosts")
        docker = next(i for i, h in enumerate(hosts) if h["id"] == "docker")
        with self.assertRaisesRegex(settings.SettingsError,
                                    "docker \\(VM 100\\) wird noch verwendet von: Dienst Homepage, .*, Docker$"):
            settings.without_entry(raw(), "hosts", docker)
        proxmox = next(i for i, h in enumerate(hosts) if h["id"] == "proxmox")
        with self.assertRaisesRegex(settings.SettingsError, "Proxmox VE wird noch verwendet von: .*Backups, Proxmox$"):
            settings.without_entry(raw(), "hosts", proxmox)

    def test_unused_host_can_be_removed(self) -> None:
        added = settings.with_entry(raw(), "hosts", None,
                                    {"id": "pi", "label": "Raspberry", "address": "192.0.2.40", "ssh_user": "pi"})
        removed = settings.without_entry(added, "hosts", len(settings.entries(added, "hosts")) - 1)
        self.assertNotIn("pi", [h["id"] for h in settings.entries(removed, "hosts")])

    def test_host_id_cannot_change(self) -> None:
        hosts = settings.entries(raw(), "hosts")
        changed = dict(hosts[1], id="arch2")
        with self.assertRaisesRegex(settings.SettingsError, "ID"):
            settings.with_entry(raw(), "hosts", 1, changed)
        renamed = settings.with_entry(raw(), "hosts", 1, dict(hosts[1], label="Arch-Steuerung"))
        self.assertEqual(settings.entries(renamed, "hosts")[1]["label"], "Arch-Steuerung")

    def test_service_used_by_proxmox_cannot_be_removed_but_renaming_follows(self) -> None:
        services = settings.entries(raw(), "services")
        web = next(i for i, s in enumerate(services) if s["label"] == "Proxmox Web-UI")
        with self.assertRaisesRegex(settings.SettingsError, "Proxmox"):
            settings.without_entry(raw(), "services", web)
        renamed = settings.with_entry(raw(), "services", web, dict(services[web], label="Proxmox"))
        proxmox_section = renamed["proxmox"]
        assert isinstance(proxmox_section, dict)
        self.assertEqual(proxmox_section["web"], "Proxmox")

    def test_first_host_needs_the_ssh_key(self) -> None:
        empty = dict(settings.EMPTY_LAB)
        pi = {"id": "pi", "label": "Raspberry", "address": "10.0.0.5", "ssh_user": "pi"}
        with self.assertRaisesRegex(settings.SettingsError, "ssh_key fehlt"):
            settings.with_entry(empty, "hosts", None, pi)
        keyed = settings.with_ssh_key(empty, "%USERPROFILE%\\.ssh\\id_ed25519")
        self.assertEqual(len(settings.entries(settings.with_entry(keyed, "hosts", None, pi), "hosts")), 1)

    def test_ssh_key_must_not_be_empty(self) -> None:
        with self.assertRaisesRegex(settings.SettingsError, "ssh_key"):
            settings.with_ssh_key(raw(), "  ")


class SaveTest(unittest.TestCase):
    def test_save_writes_valid_json_and_load_reads_it_back(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "LabManager" / "lab.json"
            changed = settings.with_entry(raw(), "vms", None, {"name": "win11", "label": "Windows 11"})
            settings.save_raw(path, changed)
            self.assertEqual(load(path).vms[-1].label, "Windows 11")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), changed)
            self.assertEqual(list(path.parent.iterdir()), [path], "no temporary file may stay behind")

    def test_invalid_config_is_never_written(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lab.json"
            path.write_text("{}", encoding="utf-8")
            broken = dict(raw(), docker={"host": "nirgends"})
            with self.assertRaises(settings.SettingsError):
                settings.save_raw(path, broken)
            self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_empty_lab_is_a_valid_start(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lab.json"
            settings.save_raw(path, settings.EMPTY_LAB)
            self.assertEqual(load(path).hosts, ())


if __name__ == "__main__":
    unittest.main()


DOCKER_CONTAINERS = [
    containers.Container("uptime-kuma", "basis", "running", "Up", "louislam/uptime-kuma:2", (3001,)),
    containers.Container("grafana", "monitoring", "running", "Up", "grafana/grafana", (3002,)),
    containers.Container("nginx-proxy-manager", "basis", "running", "Up", "jc21/npm", (80, 81, 443)),
    containers.Container("intern", "", "running", "Up", "redis", ()),
]


class DockerSuggestionTest(unittest.TestCase):

    def test_only_ports_not_yet_listed_are_suggested(self) -> None:
        found = settings.docker_suggestions(raw(), DOCKER_CONTAINERS)
        self.assertEqual([(e["label"], e["port"], e["url"]) for e in found], [
            ("grafana", 3002, "http://192.0.2.30:3002"),
            ("nginx-proxy-manager (80)", 80, "http://192.0.2.30:80"),
            ("nginx-proxy-manager (443)", 443, "https://192.0.2.30:443"),
        ])
        self.assertTrue(all(e["host"] == "docker" for e in found))

    def test_suggestions_are_valid_services(self) -> None:
        r = raw()
        for entry in settings.docker_suggestions(r, DOCKER_CONTAINERS):
            r = settings.with_entry(r, "services", None, entry)
        self.assertEqual(len(settings.entries(r, "services")), 9)

    def test_without_docker_there_is_nothing_to_suggest(self) -> None:
        with self.assertRaisesRegex(settings.SettingsError, "Docker ist nicht eingerichtet"):
            settings.docker_suggestions(settings.with_section(raw(), "docker", None), DOCKER_CONTAINERS)
