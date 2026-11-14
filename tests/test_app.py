"""Tests for the command line of LabManager.exe (used by scheduled tasks).

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import app, backups, containers, hyperv, memory, ssh, status
from labmanager.config import load

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")


def unused(cmd: list[str]) -> str:
    raise AssertionError("must not run")


class CommandLineTest(unittest.TestCase):
    def test_unknown_command_is_rejected(self) -> None:
        # A temporary APPDATA: run() would otherwise copy lab.json into the real user profile.
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"APPDATA": d}):
            self.assertEqual(app.run(["mach-was"]), app.EXIT_USAGE)
            self.assertEqual(app.run(["status", "--zuviel"]), app.EXIT_USAGE)
            self.assertEqual(app.run(["backup-copy", "--dry-run"]), app.EXIT_CONFIG, "valid command, no lab.json")

    def test_backup_copy_reports_success_and_failure_by_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lab = dataclasses.replace(LAB, backup=dataclasses.replace(LAB.backup, target_dir=Path(d)))

            def offline(cmd: list[str]) -> str:
                raise ssh.SshError("Connection timed out")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(app.backup_copy(lab, offline, offline, dry_run=False), app.EXIT_FAILED)
            self.assertIn("Connection timed out", err.getvalue())
            self.assertIn("FEHLER: Connection timed out", backups.last_log_line(Path(d)) or "")

            name = "vzdump-qemu-100-2026_11_13-17_28_03.vma.zst"
            (Path(d) / name).write_bytes(b"x" * 5)

            def listing(cmd: list[str]) -> str:
                return f"{name}|5|1794587306\n"

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(app.backup_copy(lab, listing, offline, dry_run=False), app.EXIT_OK)
            self.assertIn("bereits vorhanden", backups.last_log_line(Path(d)) or "")

    def test_backup_copy_dry_run_prints_the_plan_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lab = dataclasses.replace(LAB, backup=dataclasses.replace(LAB.backup, target_dir=Path(d)))
            name = "vzdump-qemu-100-2026_11_13-17_28_03.vma.zst"

            def listing(cmd: list[str]) -> str:
                return f"{name}|5|1794587306\n"

            def no_copy(cmd: list[str]) -> str:
                raise AssertionError("dry run must not copy")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = app.backup_copy(lab, listing, no_copy, dry_run=True)
            self.assertEqual(code, app.EXIT_OK)
            self.assertEqual(json.loads(out.getvalue()),
                             {"dry_run": True, "newest": name, "size": 5, "copy": True, "remove": []})
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_backup_copy_prints_its_messages(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lab = dataclasses.replace(LAB, backup=dataclasses.replace(LAB.backup, target_dir=Path(d)))
            name = "vzdump-qemu-100-2026_11_13-17_28_03.vma.zst"
            (Path(d) / name).write_bytes(b"x" * 5)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                app.backup_copy(lab, lambda cmd: f"{name}|5|1794587306\n", lambda cmd: "", dry_run=False)
            self.assertIn("bereits vorhanden", json.loads(out.getvalue())["messages"][0])

    def test_status_data_is_json_with_warnings(self) -> None:
        snap = status.Snapshot(
            vms={"Linux-Desktop": hyperv.VmInfo("Linux-Desktop", "Running", 3, 8 * 2**30, 4, 60, 2, True)},
            vm_error=None, vm_no_admin=False, host_up={h.id: True for h in LAB.hosts},
            service_up={s.label: True for s in LAB.services}, cluster=None, cluster_error="weg",
            host_memory={"docker": memory.Memory(total=4 * 2**30, available=2**20)}, memory_errors={})
        data = json.loads(json.dumps(app.status_data(LAB, snap)))
        self.assertEqual(data["vms"]["Linux-Desktop"]["memory_assigned"], 8 * 2**30)
        self.assertEqual(data["host_memory"]["docker"]["available"], 2**20)
        self.assertEqual(data["warnings"], ["docker (VM 100): nur noch 0,0 GB von 4,0 GB RAM verfügbar."])

    def test_containers_data_joins_stats(self) -> None:
        kuma = containers.Container("uptime-kuma", "basis", "running", "Up", "louislam/uptime-kuma:2", (3001,))
        old = containers.Container("test", "", "exited", "Exited (0)", "test:latest", ())
        stats = {"uptime-kuma": containers.Stats("uptime-kuma", 0.3, 100, "100B", 1.0, "1B / 2B", "0B / 0B", 3, 0, 5)}
        data = json.loads(json.dumps(app.containers_data([kuma, old], stats)))
        self.assertEqual([(c["name"], c["stats"] is None) for c in data], [("uptime-kuma", False), ("test", True)])
        self.assertEqual(data[0]["ports"], [3001])

    def test_commands_name_a_section_the_lab_does_not_set_up(self) -> None:
        lab = dataclasses.replace(LAB, docker=None, backup=None)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(app.command(lab, ["containers"]), app.EXIT_CONFIG)
            self.assertEqual(app.backup_copy(lab, unused, unused, dry_run=True), app.EXIT_CONFIG)
        self.assertIn("Docker ist in lab.json nicht eingerichtet", err.getvalue())
        self.assertIn("Backups sind in lab.json nicht eingerichtet", err.getvalue())


if __name__ == "__main__":
    unittest.main()
