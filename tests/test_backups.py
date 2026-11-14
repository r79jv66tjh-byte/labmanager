"""Tests for listing, copying and pruning Proxmox backups.

The copy itself runs for real into a temporary folder; only scp is replaced by a local file copy.
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import backups, ssh
from labmanager.config import Lab, load
from labmanager.gui import BackupTab, backup_rows, backup_summary

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")
NAME = "vzdump-qemu-100-2026_11_13-17_28_03.vma.zst"
OLDER = "vzdump-qemu-100-2026_11_12-03_00_01.vma.zst"
# Real `pvesh get /cluster/backup --output-format json` from the lab.
JOBS = json.dumps([{"comment": "Docker-VM inkl. Vaultwarden", "compress": "zstd", "enabled": 1, "id": "docker-daily",
                    "mode": "snapshot", "next-run": 1794621600, "prune-backups": {"keep-last": "7"},
                    "repeat-missed": 1, "schedule": "03:00", "storage": "local", "type": "vzdump", "vmid": "100"}])


def lab_with_target(folder: Path, keep: int = 4) -> Lab:
    return dataclasses.replace(LAB, backup=dataclasses.replace(LAB.backup, target_dir=folder, keep=keep))


class ParseTest(unittest.TestCase):
    def test_remote_listing(self) -> None:
        files = backups.parse_remote(f"{NAME}|3672914211|1794587306.5\n{OLDER}|100|1794500000\n")
        self.assertEqual(files[0], backups.BackupFile(NAME, 3672914211, 1794587306.5))
        self.assertEqual(backups.newest(files), files[0])

    def test_unexpected_listing_is_an_error(self) -> None:
        with self.assertRaises(backups.BackupError):
            backups.parse_remote("kaputt")

    def test_jobs(self) -> None:
        job = backups.parse_jobs(JOBS)[0]
        self.assertEqual((job.id, job.schedule, job.enabled, job.keep), ("docker-daily", "03:00", True, "keep-last 7"))
        self.assertEqual(job.next_run, 1794621600.0)
        with self.assertRaises(backups.BackupError):
            backups.parse_jobs('[{"id": 1}]')

    def test_listing_command_quotes_the_pattern(self) -> None:
        calls: list[list[str]] = []
        backups.list_remote(LAB, lambda cmd: calls.append(cmd) or "")
        self.assertEqual(calls[0][-1], "find /var/lib/vz/dump -maxdepth 1 -name 'vzdump-qemu-100-*.vma.zst' "
                                       "-printf '%f|%s|%T@\\n'")

    def test_scp_reads_from_the_backup_host(self) -> None:
        cmd = backups.scp_command(LAB, NAME, Path("D:/x.partial"))
        self.assertEqual(cmd[0], "scp")
        self.assertIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-2], f"root@192.0.2.10:/var/lib/vz/dump/{NAME}")


class CopyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.remote = self.root / "proxmox"
        self.remote.mkdir()
        (self.remote / NAME).write_bytes(b"x" * 1000)
        self.target = self.root / "Lab-Backups"
        self.lab = lab_with_target(self.target, keep=2)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_listing(self, cmd: list[str]) -> str:
        return "".join(f"{p.name}|{p.stat().st_size}|{p.stat().st_mtime}\n" for p in self.remote.iterdir())

    def scp(self, cmd: list[str]) -> str:
        shutil.copyfile(self.remote / cmd[-2].rsplit("/", 1)[1], cmd[-1])
        return ""

    def test_copies_newest_prunes_old_and_logs(self) -> None:
        self.target.mkdir()
        for name in ("vzdump-qemu-100-2026_11_01-03_00_00.vma.zst", "vzdump-qemu-100-2026_11_06-03_00_00.vma.zst"):
            (self.target / name).write_bytes(b"alt")
        messages = backups.copy_newest(self.lab, self.run_listing, self.scp)
        self.assertTrue(messages[0].startswith(f"Kopiert: {NAME}"))
        self.assertEqual(messages[1], "Alte Kopie entfernt: vzdump-qemu-100-2026_11_01-03_00_00.vma.zst")
        self.assertEqual(sorted(p.name for p in self.target.glob("*.zst")),
                         ["vzdump-qemu-100-2026_11_06-03_00_00.vma.zst", NAME])
        self.assertFalse(list(self.target.glob("*.partial")))
        log = (self.target / backups.LOG_NAME).read_text(encoding="utf-8")
        self.assertIn(f"Kopiert: {NAME}", log)
        self.assertIn("Alte Kopie entfernt", backups.last_log_line(self.target) or "")

    def test_dry_run_plans_copy_and_pruning_without_touching_anything(self) -> None:
        self.target.mkdir()
        for name in ("vzdump-qemu-100-2026_11_01-03_00_00.vma.zst", "vzdump-qemu-100-2026_11_06-03_00_00.vma.zst"):
            (self.target / name).write_bytes(b"alt")
        plan = backups.plan_copy(self.lab, self.run_listing)
        self.assertEqual(plan, backups.CopyPlan(newest=NAME, size=1000, copy=True,
                                                remove=["vzdump-qemu-100-2026_11_01-03_00_00.vma.zst"]))
        self.assertEqual(len(list(self.target.iterdir())), 2)
        self.assertIsNone(backups.last_log_line(self.target))

    def test_dry_run_with_missing_target_folder_plans_a_copy(self) -> None:
        plan = backups.plan_copy(self.lab, self.run_listing)
        self.assertEqual((plan.copy, plan.remove), (True, []))
        self.assertFalse(self.target.exists())

    def test_existing_copy_is_not_copied_again(self) -> None:
        self.target.mkdir()
        (self.target / NAME).write_bytes(b"x" * 1000)

        def no_scp(cmd: list[str]) -> str:
            raise AssertionError("must not copy")

        self.assertIn("bereits vorhanden", backups.copy_newest(self.lab, self.run_listing, no_scp)[0])

    def test_wrong_size_is_rejected_logged_and_cleaned_up(self) -> None:
        def short_scp(cmd: list[str]) -> str:
            Path(cmd[-1]).write_bytes(b"x" * 10)
            return ""

        with self.assertRaisesRegex(backups.BackupError, "Größe stimmt nicht"):
            backups.copy_newest(self.lab, self.run_listing, short_scp)
        self.assertEqual([p.name for p in self.target.iterdir()], [backups.LOG_NAME])
        self.assertIn("FEHLER: Größe stimmt nicht", backups.last_log_line(self.target) or "")

    def test_failed_ssh_is_logged_and_raised(self) -> None:
        def offline(cmd: list[str]) -> str:
            raise ssh.SshError("Connection timed out")

        with self.assertRaisesRegex(ssh.SshError, "timed out"):
            backups.copy_newest(self.lab, offline, self.scp)
        self.assertIn("FEHLER: Connection timed out", backups.last_log_line(self.target) or "")

    def test_no_backup_on_proxmox_is_an_error(self) -> None:
        (self.remote / NAME).unlink()
        with self.assertRaisesRegex(backups.BackupError, "kein Backup"):
            backups.copy_newest(self.lab, self.run_listing, self.scp)

    def test_missing_target_folder_is_reported_when_listing(self) -> None:
        with self.assertRaisesRegex(backups.BackupError, "fehlt"):
            backups.list_local(self.root / "gibt-es-nicht", "*.zst")


NOW = 1794600000.0
DAY = 86400.0
JOB = backups.parse_jobs(JOBS)[0]


def state(remote: list[backups.BackupFile], local: list[backups.BackupFile],
          jobs: tuple[backups.Job, ...] = (JOB,)) -> backups.BackupState:
    return backups.BackupState(remote=tuple(remote), remote_error=None, local=tuple(local), local_error=None,
                               jobs=jobs, jobs_error=None, free_bytes=500 * 2**30, last_log="2026-11-13  Kopiert: x")


FRESH = backups.BackupFile(NAME, 3 * 2**30, NOW - 0.25 * DAY)
OLD = backups.BackupFile(OLDER, 3 * 2**30, NOW - 1.5 * DAY)


class ViewTest(unittest.TestCase):
    def test_rows_grouped_with_age_colour_and_copy_info(self) -> None:
        rows = {r.iid: r for r in backup_rows(LAB, state([FRESH, OLD], [OLD]), NOW)}
        newest = rows[f"Auf Proxmox:{NAME}"]
        self.assertEqual((newest.group, newest.tag, newest.values[3]), ("Auf Proxmox", "ok", "6 Std."))
        self.assertEqual(newest.values[4], "neuestes · nicht auf Windows")
        self.assertEqual(rows[f"Auf Proxmox:{OLDER}"].values[4], "auf Windows kopiert")
        copy = rows[f"Kopien auf Windows:{OLDER}"]
        self.assertEqual(copy.tag, "ok")
        self.assertIn("neueres Backup auf Proxmox noch nicht kopiert", copy.values[4])
        job = rows["Backup-Jobs:docker-daily"]
        self.assertTrue(job.values[4].startswith("aktiv · Zeitplan 03:00 · keep-last 7 · nächster Lauf"))

    def test_too_old_backups_are_red_and_warned(self) -> None:
        ancient = backups.BackupFile(NAME, 10, NOW - 10 * DAY)
        s = state([ancient], [ancient])
        rows = backup_rows(LAB, s, NOW)
        self.assertEqual([r.tag for r in rows[:2]], ["down", "down"])
        found = backups.warnings(LAB, s, NOW)
        self.assertEqual(len(found), 2)
        self.assertIn("Proxmox-Backup ist 10 Tage alt (Grenze 3)", found[0])
        self.assertIn("Kopie auf Windows ist 10 Tage alt (Grenze 8)", found[1])

    def test_missing_backups_and_disabled_job_are_warned(self) -> None:
        off = dataclasses.replace(JOB, enabled=False)
        found = backups.warnings(LAB, state([], [], (off,)), NOW)
        self.assertEqual(found, ["Auf Proxmox liegt kein Backup.", "Auf Windows liegt noch keine Kopie.",
                                 "Ein Backup-Job in Proxmox ist ausgeschaltet."])

    def test_summary(self) -> None:
        text = backup_summary(LAB, state([FRESH], [FRESH]), NOW)
        self.assertIn(f"Neuestes Backup vor 6 Std. · neueste Kopie vor 6 Std. · Ziel {LAB.backup.target_dir}, "
                      "frei 500,0 GB", text)
        self.assertIn("Letzter Kopierlauf: 2026-11-13  Kopiert: x", text)
        self.assertNotIn("⚠", text)

    def test_tab_menus(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            def unused(cmd: list[str]) -> str:
                raise AssertionError("external call during GUI test")

            tab = BackupTab(ttk.Notebook(root), LAB, unused, unused)
            tab.apply(state([FRESH, OLD], [OLD]))

            def labels(iid: str) -> list[str]:
                return [e.label for e in tab.menu_for(iid) if e is not None]

            self.assertEqual(labels(f"Auf Proxmox:{NAME}"), ["Jetzt auf Windows kopieren", "Backup-Jobs in Proxmox öffnen"])
            self.assertEqual(labels(f"Auf Proxmox:{OLDER}"), ["Backup-Jobs in Proxmox öffnen"])
            self.assertEqual(labels(f"Kopien auf Windows:{OLDER}"), ["Im Explorer zeigen"])
            self.assertEqual([tab.table.tree.item(g, "text") for g in tab.table.tree.get_children()],
                             ["Auf Proxmox (2)", "Kopien auf Windows (1)", "Backup-Jobs (1)"])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
