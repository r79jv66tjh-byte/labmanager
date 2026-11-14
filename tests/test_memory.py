"""Tests for reading a host's real memory use from /proc/meminfo.

The sample is real output from a Linux VM (Hyper-V reported 10,1 GB demand at that time).
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import memory
from labmanager.config import load

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")
MEMINFO = "MemTotal:       12315648 kB\nMemAvailable:   10113024 kB\n"


class ParseTest(unittest.TestCase):
    def test_total_available_and_used(self) -> None:
        m = memory.parse_meminfo(MEMINFO)
        self.assertEqual((m.total, m.available), (12315648 * 1024, 10113024 * 1024))
        self.assertEqual(m.used, (12315648 - 10113024) * 1024)

    def test_missing_field_is_an_error(self) -> None:
        with self.assertRaisesRegex(memory.HostMemoryError, "MemAvailable"):
            memory.parse_meminfo("MemTotal:       12315648 kB\n")

    def test_low_means_less_than_ten_percent_available(self) -> None:
        self.assertFalse(memory.Memory(total=100, available=10).low)
        self.assertTrue(memory.Memory(total=100, available=9).low)


class CommandTest(unittest.TestCase):
    def test_reads_meminfo_on_the_host(self) -> None:
        calls: list[list[str]] = []

        def run(cmd: list[str]) -> str:
            calls.append(cmd)
            return MEMINFO

        self.assertEqual(memory.read(LAB, LAB.host("desktop"), run).total, 12315648 * 1024)
        self.assertEqual(calls[0][-2:], ["admin@192.0.2.20", "grep -E '^(MemTotal|MemAvailable):' /proc/meminfo"])


if __name__ == "__main__":
    unittest.main()
