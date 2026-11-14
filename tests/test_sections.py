"""Tests for editing the optional sections (remote desktop, backups, Docker, Proxmox) and RDP profiles,
and for numbering monitors the way the Remote Desktop client does.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import monitors, settings

REPO_CONFIG = Path(__file__).resolve().parent / "fixtures" / "lab.json"


def raw() -> settings.Raw:
    return settings.load_raw(REPO_CONFIG)


def profiles(r: settings.Raw) -> dict[str, object]:
    rdp = r["rdp"]
    assert isinstance(rdp, dict)
    value = rdp["profiles"]
    assert isinstance(value, dict)
    return value


class SectionTest(unittest.TestCase):
    def test_set_and_switch_off_a_section(self) -> None:
        moved = settings.with_section(raw(), "docker", {"host": "desktop"})
        self.assertEqual(moved["docker"], {"host": "desktop"})
        off = settings.with_section(moved, "docker", None)
        self.assertNotIn("docker", off)
        self.assertIsNone(settings.checked(off).docker)

    def test_invalid_section_is_rejected(self) -> None:
        with self.assertRaisesRegex(settings.SettingsError, "docker.host: unbekannter Host 'nirgends'"):
            settings.with_section(raw(), "docker", {"host": "nirgends"})
        with self.assertRaisesRegex(settings.SettingsError, "backup.keep fehlt"):
            settings.with_section(raw(), "backup", {"host": "proxmox", "remote_dir": "/d", "pattern": "*",
                                                    "target_dir": "D:\\B", "max_age_days": 3, "copy_max_age_days": 8})

    def test_unknown_section_is_a_programming_error(self) -> None:
        with self.assertRaises(KeyError):
            settings.with_section(raw(), "irgendwas", None)

    def test_a_service_used_by_proxmox_survives_switching_proxmox_off(self) -> None:
        off = settings.with_section(raw(), "proxmox", None)
        web = next(i for i, s in enumerate(settings.entries(off, "services")) if s["label"] == "Proxmox Web-UI")
        self.assertEqual(len(settings.entries(settings.without_entry(off, "services", web), "services")), 5)


class ProfileTest(unittest.TestCase):
    def test_existing_profiles_are_recognised(self) -> None:
        p = profiles(raw())
        self.assertEqual(settings.read_profile(p["Desktop - Fenster"]),
                         settings.ProfileSpec("window", 1920, 1080, (), ()))
        self.assertEqual(settings.read_profile(p["Desktop - alle 3"]), settings.ProfileSpec("monitors", 0, 0, (1, 2, 0), ()))
        custom = ["screen mode id:i:2", "smart sizing:i:1"]
        self.assertEqual(settings.read_profile(custom), settings.ProfileSpec("custom", 0, 0, (), tuple(custom)))

    def test_lines_for_each_kind(self) -> None:
        self.assertEqual(settings.profile_lines(settings.ProfileSpec("window", 1600, 900, (), ())),
                         ["screen mode id:i:1", "use multimon:i:0", "desktopwidth:i:1600", "desktopheight:i:900"])
        self.assertEqual(settings.profile_lines(settings.ProfileSpec("monitors", 0, 0, (1, 0), ())),
                         ["screen mode id:i:2", "use multimon:i:1", "selectedmonitors:s:1,0"])
        with self.assertRaisesRegex(settings.SettingsError, "mindestens einen Bildschirm"):
            settings.profile_lines(settings.ProfileSpec("monitors", 0, 0, (), ()))
        with self.assertRaisesRegex(settings.SettingsError, "Breite"):
            settings.profile_lines(settings.ProfileSpec("window", 0, 900, (), ()))

    def test_add_rename_and_remove_profiles_keeping_their_order(self) -> None:
        window = settings.profile_lines(settings.ProfileSpec("window", 1280, 720, (), ()))
        added = settings.with_profile(raw(), None, "Klein", window)
        self.assertEqual(list(profiles(added))[-1], "Klein")
        renamed = settings.with_profile(added, "Desktop - nur Mitte", "Nur Mitte", ["screen mode id:i:2"])
        self.assertEqual(list(profiles(renamed))[2], "Nur Mitte")
        self.assertEqual(profiles(renamed)["Nur Mitte"], ["screen mode id:i:2"])
        removed = settings.without_profile(renamed, "Klein")
        self.assertNotIn("Klein", profiles(removed))

    def test_profile_names_are_unique_and_not_empty(self) -> None:
        with self.assertRaisesRegex(settings.SettingsError, "gibt es schon"):
            settings.with_profile(raw(), None, "Desktop - Fenster", ["screen mode id:i:1"])
        with self.assertRaisesRegex(settings.SettingsError, "Namen"):
            settings.with_profile(raw(), None, "  ", ["screen mode id:i:1"])

    def test_last_profile_cannot_be_removed(self) -> None:
        r = raw()
        for name in list(profiles(r))[:-1]:
            r = settings.without_profile(r, name)
        with self.assertRaisesRegex(settings.SettingsError, "mindestens ein Profil"):
            settings.without_profile(r, next(iter(profiles(r))))


# The lab PC: EnumDisplayDevices lists DISPLAY1..3 attached (DISPLAY2 primary) and unused outputs;
# `mstsc /l` numbered them 0 = right 1920x1200, 1 = middle 2560x1440, 2 = left 1920x1080.
DEVICES = [
    monitors.Device("\\\\.\\DISPLAY1", attached=True, primary=False, width=1920, height=1200, left=2560, top=136),
    monitors.Device("\\\\.\\DISPLAY2", attached=True, primary=True, width=2560, height=1440, left=0, top=0),
    monitors.Device("\\\\.\\DISPLAY3", attached=True, primary=False, width=1920, height=1080, left=-1920, top=145),
    monitors.Device("\\\\.\\DISPLAY4", attached=False, primary=False, width=0, height=0, left=0, top=0),
]


class MonitorTest(unittest.TestCase):
    def test_ids_follow_the_remote_desktop_numbering(self) -> None:
        found = monitors.numbered(DEVICES)
        self.assertEqual([(m.rdp_id, m.width, m.height) for m in found], [(0, 1920, 1200), (1, 2560, 1440), (2, 1920, 1080)])

    def test_labels_name_position_size_and_primary(self) -> None:
        labels = [m.label for m in monitors.numbered(DEVICES)]
        self.assertEqual(labels, ["rechts · 1920×1200", "Mitte · 2560×1440 · Hauptbildschirm", "links · 1920×1080"])

    def test_primary_comes_first_in_a_selection(self) -> None:
        self.assertEqual(monitors.selection_order({0, 1, 2}, monitors.numbered(DEVICES)), (1, 0, 2))
        self.assertEqual(monitors.selection_order({0, 2}, monitors.numbered(DEVICES)), (0, 2))


if __name__ == "__main__":
    unittest.main()
