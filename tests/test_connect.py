"""Tests for .rdp generation, SSH commands and the connect tab.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import connect
from labmanager.config import RdpProfile, load
from labmanager.gui import ConnectTab

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")


def profile(name: str) -> RdpProfile:
    return next(p for p in LAB.rdp.profiles if p.name == name)


class RdpTest(unittest.TestCase):
    def test_target_user_profile_then_shared_settings(self) -> None:
        lines = connect.rdp_text(LAB, profile("Desktop - Mitte + rechts")).splitlines()
        self.assertEqual(lines[:2], ["full address:s:192.0.2.20", "username:s:rdp-user"])
        self.assertIn("selectedmonitors:s:1,0", lines)
        self.assertEqual(lines[-len(LAB.rdp.base):], list(LAB.rdp.base))

    def test_file_uses_crlf_and_profile_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = connect.write_rdp(LAB, profile("Desktop - Fenster"), Path(d) / "neu")
            self.assertEqual(path.name, "Desktop - Fenster.rdp")
            self.assertIn(b"\r\nscreen mode id:i:1\r\n", path.read_bytes())


class UrlShortcutTest(unittest.TestCase):
    def test_shortcut_keeps_the_full_link_including_hash(self) -> None:
        url = "https://192.0.2.10:8006/#v1:0:=qemu%2F100:4:::::7::"
        with tempfile.TemporaryDirectory() as d:
            path = connect.write_url(url, Path(d))
            self.assertEqual(path.suffix, ".url")
            self.assertEqual(path.read_bytes(), f"[InternetShortcut]\r\nURL={url}\r\n".encode("ascii"))

    def test_each_link_gets_its_own_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            a = connect.write_url("http://a/", Path(d))
            b = connect.write_url("http://b/", Path(d))
            self.assertNotEqual(a, b)


class SshTest(unittest.TestCase):
    def test_command_uses_key_user_and_address(self) -> None:
        cmd = connect.ssh_command(LAB, LAB.host("docker"))
        self.assertEqual(cmd, ["ssh", "-i", str(LAB.ssh_key), "admin@192.0.2.30"])

    def test_options_come_before_the_destination(self) -> None:
        cmd = connect.ssh_command(LAB, LAB.host("docker"), "-o", "BatchMode=yes")
        self.assertEqual(cmd, ["ssh", "-o", "BatchMode=yes", "-i", str(LAB.ssh_key), "admin@192.0.2.30"])


class ConnectTabTest(unittest.TestCase):
    def test_one_button_per_profile_service_and_host(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            tab = ConnectTab(ttk.Notebook(root), LAB)
            labels = [b.cget("text") for b in tab.buttons]
            expected = ([p.name for p in LAB.rdp.profiles] + [s.label for s in LAB.services]
                        + [h.label for h in LAB.hosts])
            self.assertEqual(labels, expected)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
