"""Tests for the settings tab, its entry dialog, the welcome screen and the restart command.

The real widgets are built (withdrawn); dialogs are driven through their methods instead of clicks.
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import containers, gui, hyperv, monitors, settings, settings_gui
from labmanager.config import load

REPO_CONFIG = Path(__file__).resolve().parent / "fixtures" / "lab.json"
VMS, HOSTS, SERVICES = settings_gui.KINDS
SCREENS = monitors.numbered([
    monitors.Device("\\\\.\\DISPLAY1", attached=True, primary=False, width=1920, height=1200, left=2560, top=136),
    monitors.Device("\\\\.\\DISPLAY2", attached=True, primary=True, width=2560, height=1440, left=0, top=0),
    monitors.Device("\\\\.\\DISPLAY3", attached=True, primary=False, width=1920, height=1080, left=-1920, top=145)])


class TabCase(unittest.TestCase):
    """Builds the settings tab on the repository's lab.json with a temporary config path."""

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "LabManager" / "lab.json"
        self.saved: list[bool] = []
        self.tab = settings_gui.SettingsTab(ttk.Notebook(self.root), settings.load_raw(REPO_CONFIG), self.path,
                                            on_saved=lambda: self.saved.append(True), screens=SCREENS)

    def tearDown(self) -> None:
        self.root.destroy()
        self.tmp.cleanup()

    def rows(self, kind: settings_gui.Kind) -> list[tuple[str, ...]]:
        tree = self.tab.trees[kind.key]
        return [tuple(tree.item(i, "values")) for i in tree.get_children()]


class SettingsTabTest(TabCase):
    def test_lists_show_the_lab(self) -> None:
        self.assertEqual([r[0] for r in self.rows(VMS)], ["Proxmox-VM", "Linux-Desktop"])
        self.assertEqual([r[0] for r in self.rows(HOSTS)], ["proxmox", "desktop", "docker"])
        self.assertEqual(len(self.rows(SERVICES)), 6)
        self.assertEqual(str(self.tab.state_label.cget("text")), "Alles gespeichert")
        self.assertIn("disabled", self.tab.save_button.state())

    def test_valid_change_is_shown_and_marked_unsaved(self) -> None:
        error = self.tab.try_change(VMS, None, {"name": "win11", "label": "Windows 11"})
        self.assertIsNone(error)
        self.assertEqual(self.rows(VMS)[-1], ("win11", "Windows 11"))
        self.assertEqual(str(self.tab.state_label.cget("text")), "Ungespeicherte Änderungen")
        self.assertNotIn("disabled", self.tab.save_button.state())

    def test_invalid_change_returns_the_reason_and_changes_nothing(self) -> None:
        error = self.tab.try_change(SERVICES, None, {"label": "Neu", "host": "docker", "port": "drei", "url": ""})
        self.assertEqual(error, "Port: ganze Zahl erwartet, z. B. 3000")
        self.assertEqual(len(self.rows(SERVICES)), 6)

    def test_service_url_is_filled_from_host_and_port(self) -> None:
        self.assertIsNone(self.tab.try_change(SERVICES, None, {"label": "Grafana", "host": "docker",
                                                               "port": "3002", "url": " "}))
        self.assertEqual(self.rows(SERVICES)[-1], ("Grafana", "docker", "3002", "http://192.0.2.30:3002"))

    def test_removing_a_host_in_use_explains_why_not(self) -> None:
        self.tab.trees["hosts"].selection_set("2")
        self.tab.remove(HOSTS)
        self.assertIn("wird noch verwendet von: Dienst Homepage", str(self.tab.message.cget("text")))
        self.assertEqual(len(self.rows(HOSTS)), 3)

    def test_save_writes_the_file_and_restarts(self) -> None:
        self.tab.try_change(VMS, None, {"name": "win11", "label": "Windows 11"})
        self.tab.save()
        self.assertEqual(load(self.path).vms[-1].name, "win11")
        self.assertEqual(self.saved, [True])
        self.assertFalse(self.tab.has_unsaved_changes())

    def test_discard_returns_to_the_saved_state(self) -> None:
        self.tab.try_change(VMS, None, {"name": "win11", "label": "Windows 11"})
        self.tab.discard()
        self.assertEqual(len(self.rows(VMS)), 2)
        self.assertFalse(self.tab.has_unsaved_changes())
        self.assertFalse(self.path.exists())


class SectionPagesTest(TabCase):
    def section(self, key: str) -> object:
        return self.tab.raw.get(key)

    def test_forms_show_the_configured_sections(self) -> None:
        self.assertEqual(self.tab.forms["docker"].vars["host"].get(), "docker")
        self.assertEqual(self.tab.forms["proxmox"].vars["web"].get(), "Proxmox Web-UI")
        self.assertEqual(self.tab.forms["backup"].vars["keep"].get(), "4")
        self.assertEqual(self.tab.forms["rdp"].vars["username"].get(), "rdp-user")
        self.assertIn("use redirection server name:i:1", self.tab.forms["rdp"].base_lines())
        self.assertEqual(str(self.tab.forms["docker"].state.cget("text")), "eingerichtet")

    def test_switch_off_and_set_up_again(self) -> None:
        self.tab.switch_off("docker")
        self.assertIsNone(self.section("docker"))
        self.assertEqual(str(self.tab.forms["docker"].state.cget("text")), "nicht eingerichtet")
        self.tab.forms["docker"].vars["host"].set("desktop")
        self.tab.apply_section("docker")
        self.assertEqual(self.section("docker"), {"host": "desktop"})
        self.assertTrue(self.tab.has_unsaved_changes())

    def test_invalid_number_is_reported(self) -> None:
        self.tab.forms["backup"].vars["keep"].set("viele")
        self.tab.apply_section("backup")
        self.assertEqual(str(self.tab.message.cget("text")), "Backups: Kopien behalten: ganze Zahl erwartet")

    def test_remote_desktop_set_up_from_scratch_gets_a_first_profile(self) -> None:
        self.tab.switch_off("rdp")
        self.assertEqual(self.tab.profile_tree.get_children(), ())
        form = self.tab.forms["rdp"]
        form.vars["host"].set("desktop")
        form.vars["username"].set("admin")
        self.tab.apply_section("rdp")
        rdp = self.section("rdp")
        assert isinstance(rdp, dict)
        self.assertEqual(rdp["profiles"], {"Fenster": ["screen mode id:i:1", "use multimon:i:0",
                                                       "desktopwidth:i:1920", "desktopheight:i:1080"]})
        self.assertNotIn("use redirection server name:i:1", rdp["base"])

    def test_profiles_are_listed_with_kind_and_screens(self) -> None:
        rows = {i: tuple(self.tab.profile_tree.item(i, "values")) for i in self.tab.profile_tree.get_children()}
        self.assertEqual(rows["Desktop - alle 3"], ("Desktop - alle 3", "Bildschirme", "Mitte, links, rechts"))
        self.assertEqual(rows["Desktop - Fenster"], ("Desktop - Fenster", "Fenster", "1920×1080"))

    def test_add_profile_from_the_dialog_with_chosen_screens(self) -> None:
        d = self.tab.profile_dialog(None)
        d.name.set("Rechts + Mitte")
        d.kind.set("monitors")
        d.checks[0].set(True)
        d.checks[1].set(True)
        d.ok()
        self.assertFalse(d.winfo_exists())
        rdp = self.section("rdp")
        assert isinstance(rdp, dict)
        self.assertEqual(rdp["profiles"]["Rechts + Mitte"][-1], "selectedmonitors:s:1,0")

    def test_docker_suggestions_are_offered_and_the_ticked_ones_added(self) -> None:
        items = [containers.Container("grafana", "monitoring", "running", "Up", "grafana/grafana", (3002,)),
                 containers.Container("loki", "monitoring", "running", "Up", "grafana/loki", (3100,))]
        d = self.tab.offer_suggestions(items)
        assert d is not None
        d.choices[1][0].set(False)
        d.ok()
        self.assertFalse(d.winfo_exists())
        self.assertEqual(self.rows(SERVICES)[-1], ("grafana", "docker", "3002", "http://192.0.2.30:3002"))
        self.assertEqual(len(self.rows(SERVICES)), 7)
        self.assertTrue(self.tab.has_unsaved_changes())

    def test_nothing_new_from_docker_is_said_so(self) -> None:
        kuma = containers.Container("uptime-kuma", "basis", "running", "Up", "louislam/uptime-kuma:2", (3001,))
        self.assertIsNone(self.tab.offer_suggestions([kuma]))
        self.assertIn("schon als Dienst eingetragen", str(self.tab.message.cget("text")))

    def test_suggestions_need_docker(self) -> None:
        self.tab.switch_off("docker")
        self.tab.suggest_docker()
        self.assertIn("Docker ist in lab.json nicht eingerichtet", str(self.tab.message.cget("text")))

    def test_profile_dialog_explains_missing_screens(self) -> None:
        d = self.tab.profile_dialog(None)
        d.name.set("Leer")
        d.kind.set("monitors")
        d.ok()
        self.assertTrue(d.winfo_exists())
        self.assertIn("mindestens einen Bildschirm", str(d.error.cget("text")))
        d.destroy()

class EntryDialogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.destroy()

    def test_error_keeps_the_dialog_open_and_success_closes_it(self) -> None:
        answers = ["Name fehlt", None]
        d = settings_gui.EntryDialog(self.root, "VM hinzufügen", VMS, {}, [], lambda _t: answers.pop(0),
                                     editing=False)
        d.ok()
        self.assertEqual(str(d.error.cget("text")), "Name fehlt")
        self.assertTrue(d.winfo_exists())
        d.vars["name"].set("win11")
        d.ok()
        self.assertFalse(d.winfo_exists())
        self.assertEqual(d.result, {"name": "win11", "label": ""})

    def test_host_id_is_locked_when_editing(self) -> None:
        d = settings_gui.EntryDialog(self.root, "Host bearbeiten", HOSTS, {"id": "docker"}, [], lambda _t: None,
                                     editing=True)
        self.assertIn("disabled", d.widgets["id"].state())
        d.destroy()


class WelcomeTest(unittest.TestCase):
    def test_create_writes_an_empty_lab_and_continues(self) -> None:
        root = tk.Tk()
        root.withdraw()
        created: list[bool] = []
        try:
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "LabManager" / "lab.json"
                gui.show_welcome(root, path, lambda: created.append(True))
                frame = root.winfo_children()[0]
                buttons = [w for c in frame.winfo_children() for w in c.winfo_children() if isinstance(w, ttk.Button)]
                next(b for b in buttons if b.cget("text") == "Neue Konfiguration anlegen").invoke()
                self.assertEqual(load(path).hosts, ())
                self.assertEqual(created, [True])
        finally:
            root.destroy()


class HelperTest(unittest.TestCase):
    def test_restart_command_starts_the_exe_or_the_script_again(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\Lab\LabManager.exe"):
            self.assertEqual(gui.restart_command(), [r"C:\Lab\LabManager.exe"])
        with mock.patch.object(sys, "argv", ["LabManager.pyw"]):
            command = gui.restart_command()
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(Path(command[1]).is_absolute() and command[1].endswith("LabManager.pyw"))

    def test_vm_names_are_parsed_and_sorted(self) -> None:
        self.assertEqual(hyperv.parse_names('["Linux-Desktop","Proxmox-VM"]'), ["Linux-Desktop", "Proxmox-VM"])
        self.assertEqual(hyperv.parse_names(""), [])
        with self.assertRaises(hyperv.HyperVError):
            hyperv.parse_names('[1]')


if __name__ == "__main__":
    unittest.main()
