"""Builds the real overview tab from the repository's lab.json and feeds it status snapshots.

Hyper-V, port probes and Proxmox are not called: the tab is constructed without starting its refresh loop.
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import hyperv, memory, proxmox, status, table
from labmanager.config import Lab, load, parse
from labmanager.gui import (
    ConnectTab,
    OverviewTab,
    build_tabs,
    overview_rows,
    uptime_text,
)

GB = 2**30
LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")


def minimal_lab() -> Lab:
    """A lab with one host and nothing else: no Proxmox, Docker, backups or remote desktop."""
    return parse({"ssh_key": "C:\\keys\\lab", "vms": [], "services": [],
                  "hosts": [{"id": "pi", "label": "Raspberry", "address": "10.0.0.5", "ssh_user": "pi"}]})


def unused(*_: object) -> object:
    raise AssertionError("external call during GUI test")


BASE = status.Snapshot(
    vms={"Proxmox-VM": hyperv.VmInfo("Proxmox-VM", "Running", 7, 16 * GB, 4, 7200, 1, False),
         "Linux-Desktop": hyperv.VmInfo("Linux-Desktop", "Off", 0, 0, 4, 0, 2, True)},
    vm_error=None, vm_no_admin=False,
    host_up={"proxmox": True, "desktop": False, "docker": True},
    service_up={s.label: s.label == "Homepage" for s in LAB.services},
    cluster=proxmox.Cluster(nodes=(proxmox.Node("pve", "online", 0.1, 4, 16 * GB, 7000),),
                            vms=(proxmox.Vm(100, "docker", "pve", "running", 0.5, 2, 4 * GB, 70),)),
    cluster_error=None,
    host_memory={"proxmox": memory.Memory(total=16 * GB, available=12 * GB),
                 "docker": memory.Memory(total=4 * GB, available=int(0.2 * GB))},
    memory_errors={})


def by_iid(rows: list[table.Row]) -> dict[str, table.Row]:
    return {r.iid: r for r in rows}


class RowsTest(unittest.TestCase):
    def test_every_vm_host_proxmox_vm_and_service_in_order(self) -> None:
        kinds = [r.values[1] for r in overview_rows(LAB, BASE)]
        self.assertEqual(kinds, ["Hyper-V-VM"] * 2 + ["Host"] * 3 + ["Proxmox-VM"] + ["Dienst"] * 6)

    def test_rows_are_grouped_into_vms_hosts_and_services(self) -> None:
        groups = [(r.values[1], r.group) for r in overview_rows(LAB, BASE)]
        self.assertEqual(sorted(set(groups)), [("Dienst", "Dienste"), ("Host", "Hosts"),
                                               ("Hyper-V-VM", "Virtuelle Maschinen"),
                                               ("Proxmox-VM", "Virtuelle Maschinen")])

    def test_tab_shows_group_headings_in_order(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            tab = OverviewTab(ttk.Notebook(root), LAB, unused, unused, unused, unused, unused, unused, unused)
            tab.apply(BASE)
            tree = tab.table.tree
            self.assertEqual([tree.item(g, "text") for g in tree.get_children()],
                             ["Virtuelle Maschinen (3)", "Hosts (3)", "Dienste (6)"])
            self.assertEqual(tree.get_children("group:Virtuelle Maschinen"),
                             ("vm:Proxmox-VM", "vm:Linux-Desktop", "pve:100"))
        finally:
            root.destroy()

    def test_resources_state_and_colour(self) -> None:
        rows = by_iid(overview_rows(LAB, BASE))
        pt = rows["vm:Proxmox-VM"]
        self.assertEqual(pt.values[2:9], ("läuft", "7 %", "", "16,0 GB", "", "4", "2 Std. 0 Min."))
        self.assertEqual(pt.tag, "ok")
        off = rows["vm:Linux-Desktop"]
        self.assertEqual((off.values[2], off.values[5], off.values[7], off.tag), ("aus", "", "4", "down"))
        self.assertEqual(rows["host:proxmox"].values[3:7], ("10 %", "4,0 GB", "16,0 GB", "25%"))
        self.assertEqual(rows["host:desktop"].tag, "down")
        self.assertEqual(rows["svc:Dockge"].values[2], "antwortet nicht")

    def test_host_short_of_memory_is_yellow_and_vms_show_only_their_assignment(self) -> None:
        rows = by_iid(overview_rows(LAB, BASE))
        host = rows["host:docker"]
        self.assertEqual((host.values[4:7], host.tag), (("3,8 GB", "4,0 GB", "95%"), "other"))
        vm = rows["pve:100"]
        self.assertEqual((vm.values[0], vm.values[4:7], vm.tag), ("docker (VM 100)", ("", "4,0 GB", ""), "ok"))

    def test_missing_admin_rights_are_named_not_an_error(self) -> None:
        rows = by_iid(overview_rows(LAB, dataclasses.replace(BASE, vms={}, vm_error=hyperv.NO_ADMIN, vm_no_admin=True)))
        self.assertEqual((rows["vm:Proxmox-VM"].values[2], rows["vm:Proxmox-VM"].tag),
                         ("keine Adminrechte", "other"))

    def test_hyperv_error_and_missing_vm(self) -> None:
        rows = by_iid(overview_rows(LAB, dataclasses.replace(BASE, vms={}, vm_error="Zugriff verweigert")))
        self.assertEqual(rows["vm:Proxmox-VM"].values[2], "Fehler")
        rows = by_iid(overview_rows(LAB, dataclasses.replace(BASE, vms={})))
        self.assertEqual(rows["vm:Linux-Desktop"].values[2], "nicht gefunden")

    def test_before_the_first_snapshot_everything_is_pending(self) -> None:
        rows = overview_rows(LAB, None)
        self.assertTrue(all(r.values[2] == "…" for r in rows))
        self.assertNotIn("Proxmox-VM", [r.values[1] for r in rows])

    def test_uptime_text(self) -> None:
        self.assertEqual([uptime_text(s) for s in (0, 600, 7260, 2 * 86400 + 3 * 3600)],
                         ["", "10 Min.", "2 Std. 1 Min.", "2 T 3 Std."])


class OverviewTabTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.tab = OverviewTab(ttk.Notebook(self.root), LAB, unused, unused, unused, unused, unused, unused, unused)
        self.tab.apply(BASE)

    def tearDown(self) -> None:
        self.root.destroy()

    def labels(self, iid: str) -> list[str]:
        return [e.label for e in self.tab.menu_for(iid) if e is not None]

    def test_status_line_summarises_and_warns(self) -> None:
        text = str(self.tab.table.status.cget("text"))
        self.assertIn("1 von 2 Hyper-V-VMs laufen", text)
        self.assertIn("⚠ docker (VM 100): nur noch 0,2 GB von 4,0 GB RAM verfügbar.", text)

    def test_status_line_does_not_count_vms_it_cannot_see(self) -> None:
        self.tab.apply(dataclasses.replace(BASE, vms={}, vm_error=hyperv.NO_ADMIN, vm_no_admin=True))
        text = str(self.tab.table.status.cget("text"))
        self.assertIn("Hyper-V-VMs: unbekannt", text)
        self.assertNotIn("0 von 2", text)

    def test_context_menus_offer_the_right_actions(self) -> None:
        self.assertEqual(self.labels("vm:Proxmox-VM"),
                         ["Herunterfahren", "Konsole öffnen", "Einstellungen öffnen (CPU, RAM …)"])
        self.assertEqual(self.labels("vm:Linux-Desktop")[0], "Starten")
        self.assertEqual(self.labels("pve:100"),
                         ["Herunterfahren", "Neu starten", "Hardware in Proxmox öffnen (CPU, RAM …)"])
        arch = self.labels("host:desktop")
        self.assertEqual(arch[0], "SSH-Terminal öffnen")
        self.assertIn("Remotedesktop: Desktop - Fenster", arch)
        self.assertIn("Öffnen: Uptime Kuma", self.labels("host:docker"))
        self.assertEqual(self.labels("svc:Homepage"), ["Öffnen: Homepage"])

    def test_toolbar_buttons(self) -> None:
        self.assertEqual([b.cget("text") for b in self.tab.action_buttons],
                         ["Lab starten", "Lab herunterfahren", "Starten", "Herunterfahren", "Aktualisieren"])


class OptionalSectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.destroy()

    def tab_names(self, lab: Lab) -> list[str]:
        tabs = build_tabs(self.root, lab)
        return [tabs.notebook.tab(t, "text") for t in tabs.notebook.tabs()]

    def test_tabs_appear_only_for_configured_sections(self) -> None:
        self.assertEqual(self.tab_names(LAB), ["Übersicht", "Verbinden", "Container", "Backups"])
        self.assertEqual(self.tab_names(minimal_lab()), ["Übersicht", "Verbinden"])

    def test_lab_without_proxmox_and_rdp_shows_hosts_without_their_menus(self) -> None:
        lab = minimal_lab()
        snap = status.Snapshot(vms={}, vm_error=None, vm_no_admin=False, host_up={"pi": True}, service_up={},
                               cluster=None, cluster_error=None, host_memory={}, memory_errors={})
        rows = overview_rows(lab, snap)
        self.assertEqual([(r.iid, r.values[2]) for r in rows], [("host:pi", "erreichbar")])
        tab = OverviewTab(ttk.Notebook(self.root), lab, unused, unused, unused, unused, unused, unused, unused)
        tab.apply(snap)
        self.assertEqual([e.label for e in tab.menu_for("host:pi") if e is not None], ["SSH-Terminal öffnen"])

    def test_connect_tab_explains_when_there_is_nothing_to_connect(self) -> None:
        empty = parse({"vms": [], "hosts": [], "services": []})
        tab = ConnectTab(ttk.Notebook(self.root), empty)
        self.assertEqual(tab.buttons, [])
        texts = [w.cget("text") for w in tab.winfo_children() if isinstance(w, ttk.Label)]
        self.assertTrue(texts and "noch nichts zum Verbinden" in texts[0])


if __name__ == "__main__":
    unittest.main()
