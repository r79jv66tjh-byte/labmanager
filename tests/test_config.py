"""Tests for loading and validating lab.json.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager.config import ConfigError, load, parse, user_config_path

REPO_CONFIG = Path(__file__).resolve().parent / "fixtures" / "lab.json"


def valid() -> dict[str, object]:
    return {
        "ssh_key": "C:\\keys\\lab",
        "vms": [{"name": "Proxmox-VM", "label": "Proxmox"}],
        "hosts": [{"id": "pve", "label": "Proxmox VE", "address": "10.0.0.10", "ssh_user": "root"}],
        "services": [{"label": "UI", "host": "pve", "port": 8006, "url": "https://10.0.0.10:8006"}],
        "rdp": {"host": "pve", "username": "rdp", "base": ["audiomode:i:0"],
                "profiles": {"Fenster": ["screen mode id:i:1"]}},
        "backup": {"host": "pve", "remote_dir": "/dump", "pattern": "*.zst", "target_dir": "D:\\B",
                   "keep": 4, "max_age_days": 3, "copy_max_age_days": 8},
        "docker": {"host": "pve"},
        "proxmox": {"host": "pve", "web": "UI"},
    }


class ParseTest(unittest.TestCase):
    def test_valid_config_is_parsed(self) -> None:
        lab = parse(valid())
        self.assertEqual(lab.vms[0].name, "Proxmox-VM")
        self.assertEqual(lab.host("pve").address, "10.0.0.10")
        self.assertEqual(lab.services[0].port, 8006)
        self.assertEqual(lab.rdp.profiles[0].name, "Fenster")
        self.assertEqual(lab.backup.keep, 4)

    def test_missing_key_names_the_key(self) -> None:
        raw = valid()
        backup = raw["backup"]
        assert isinstance(backup, dict)
        del backup["keep"]
        with self.assertRaisesRegex(ConfigError, r"backup\.keep fehlt"):
            parse(raw)

    def test_missing_section_names_the_section(self) -> None:
        raw = valid()
        del raw["services"]
        with self.assertRaisesRegex(ConfigError, r"lab\.json\.services fehlt"):
            parse(raw)

    def test_unknown_host_reference_is_rejected(self) -> None:
        raw = valid()
        services = raw["services"]
        assert isinstance(services, list)
        services[0]["host"] = "nirgends"
        with self.assertRaisesRegex(ConfigError, "unbekannter Host 'nirgends'"):
            parse(raw)

    def test_docker_host_must_be_known(self) -> None:
        raw = valid()
        raw["docker"] = {"host": "nirgends"}
        with self.assertRaisesRegex(ConfigError, r"docker\.host: unbekannter Host 'nirgends'"):
            parse(raw)

    def test_optional_sections_may_be_left_out(self) -> None:
        raw = valid()
        for section in ("rdp", "backup", "docker", "proxmox"):
            del raw[section]
        lab = parse(raw)
        self.assertEqual((lab.rdp, lab.backup, lab.docker, lab.proxmox), (None, None, None, None))
        with self.assertRaisesRegex(ConfigError, "Docker ist in lab.json nicht eingerichtet"):
            lab.require_docker()
        with self.assertRaisesRegex(ConfigError, "Backups sind in lab.json nicht eingerichtet"):
            lab.require_backup()

    def test_present_sections_are_returned_by_require(self) -> None:
        lab = parse(valid())
        self.assertEqual(lab.require_docker().host, "pve")
        self.assertEqual(lab.require_proxmox().web.label, "UI")
        self.assertEqual(lab.require_rdp().username, "rdp")
        self.assertEqual(lab.require_backup().keep, 4)

    def test_empty_lab_is_valid(self) -> None:
        lab = parse({"vms": [], "hosts": [], "services": []})
        self.assertEqual((lab.vms, lab.hosts, lab.services, lab.ssh_key), ((), (), (), None))

    def test_hosts_need_an_ssh_key(self) -> None:
        raw = valid()
        del raw["ssh_key"]
        with self.assertRaisesRegex(ConfigError, "ssh_key fehlt"):
            parse(raw)

    def test_proxmox_web_must_name_a_service(self) -> None:
        raw = valid()
        raw["proxmox"] = {"host": "pve", "web": "Gibt es nicht"}
        with self.assertRaisesRegex(ConfigError, r"proxmox\.web: unbekannter Dienst 'Gibt es nicht'"):
            parse(raw)
        raw["proxmox"] = {"host": "pve", "web": "UI"}
        self.assertEqual(parse(raw).proxmox.web.url, "https://10.0.0.10:8006")

    def test_duplicate_host_id_is_rejected(self) -> None:
        raw = valid()
        hosts = raw["hosts"]
        assert isinstance(hosts, list)
        hosts.append(copy.deepcopy(hosts[0]))
        with self.assertRaisesRegex(ConfigError, "doppelt"):
            parse(raw)

    def test_port_must_be_positive_integer(self) -> None:
        for bad in (0, "8006", True):
            raw = valid()
            services = raw["services"]
            assert isinstance(services, list)
            services[0]["port"] = bad
            with self.subTest(port=bad), self.assertRaisesRegex(ConfigError, "port"):
                parse(raw)

    def test_lists_must_be_lists(self) -> None:
        raw = valid()
        raw["vms"] = "Proxmox-VM"
        with self.assertRaisesRegex(ConfigError, "vms: Liste erwartet"):
            parse(raw)

    def test_environment_variables_in_paths_are_expanded(self) -> None:
        raw = valid()
        raw["ssh_key"] = "%WINDIR%\\key"
        self.assertNotIn("%", str(parse(raw).ssh_key))


class LoadTest(unittest.TestCase):
    def test_missing_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "nicht gefunden"):
            load(Path(tempfile.gettempdir()) / "gibt-es-nicht" / "lab.json")

    def test_invalid_json_reports_line(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lab.json"
            p.write_text('{\n  "vms": [,]\n}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Zeile 2"):
                load(p)

    def test_repository_lab_json_is_valid(self) -> None:
        lab = load(REPO_CONFIG)
        self.assertEqual({h.id for h in lab.hosts}, {"proxmox", "desktop", "docker"})
        self.assertEqual(len(lab.rdp.profiles), 4)

    def test_user_config_lives_in_appdata(self) -> None:
        self.assertEqual(user_config_path({"APPDATA": "C:\\Users\\x\\AppData\\Roaming"}),
                         Path("C:/Users/x/AppData/Roaming/LabManager/lab.json"))
        with self.assertRaisesRegex(ConfigError, "APPDATA"):
            user_config_path({})

    def test_roundtrip_through_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "lab.json"
            p.write_text(json.dumps(valid()), encoding="utf-8")
            self.assertEqual(load(p).backup.max_age_days, 3)
            self.assertEqual(load(p).backup.copy_max_age_days, 8)


if __name__ == "__main__":
    unittest.main()
