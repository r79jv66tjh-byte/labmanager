"""Tests for building ssh commands.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import ssh
from labmanager.config import load

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")


class CommandTest(unittest.TestCase):
    def test_remote_command_is_one_shell_quoted_argument(self) -> None:
        # Windows' ssh.exe drops single quotes from arguments without spaces, so the remote command
        # must arrive as one argument that Windows wraps in double quotes.
        cmd = ssh.command(LAB, LAB.host("proxmox"), "find", "/var/lib/vz/dump", "-name", "vzdump-*.zst",
                          "-printf", "%f|%s\\n")
        self.assertEqual(cmd[-2], "root@192.0.2.10")
        self.assertEqual(cmd[-1], "find /var/lib/vz/dump -name 'vzdump-*.zst' -printf '%f|%s\\n'")
        self.assertTrue(subprocess.list2cmdline(cmd).endswith('"find /var/lib/vz/dump -name \'vzdump-*.zst\' '
                                                              '-printf \'%f|%s\\n\'"'))

    def test_options_and_terminal(self) -> None:
        batch = ssh.command(LAB, LAB.host("docker"), "uptime", "-p")
        self.assertIn("BatchMode=yes", batch)
        terminal = ssh.command(LAB, LAB.host("docker"), "top", "-b", terminal=True)
        self.assertEqual(terminal[:2], ["ssh", "-t"])
        self.assertNotIn("BatchMode=yes", terminal)

    def test_single_word_commands_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ssh.command(LAB, LAB.host("docker"), "uptime")


if __name__ == "__main__":
    unittest.main()
