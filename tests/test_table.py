"""Tests for the shared task-manager style list: search, sorting and in-place updates.

Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import table

COLUMNS = (table.Column("name", "Name", 100), table.Column("cpu", "CPU", 60, numeric=True))


def row(name: str, cpu: float, group: str = "") -> table.Row:
    return table.Row(iid=name, values=(name, f"{cpu} %"), keys=(name, cpu), tag="ok", group=group)


ROWS = [row("homepage", 0.4), row("dockge", 2.5), row("vaultwarden", 0.1)]


class HelperTest(unittest.TestCase):
    def test_search_ignores_case_and_looks_at_all_columns(self) -> None:
        self.assertTrue(table.matches(ROWS[0], "HOME"))
        self.assertTrue(table.matches(ROWS[1], "2.5"))
        self.assertFalse(table.matches(ROWS[2], "kuma"))
        self.assertTrue(table.matches(ROWS[2], "  "))

    def test_sorting_by_column(self) -> None:
        self.assertEqual([r.iid for r in table.ordered(ROWS, 1, True)], ["dockge", "homepage", "vaultwarden"])
        self.assertEqual([r.iid for r in table.ordered(ROWS, 0, False)], ["dockge", "homepage", "vaultwarden"])
        self.assertEqual([r.iid for r in table.ordered(ROWS, None, False)], ["homepage", "dockge", "vaultwarden"])


class TaskTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.table = table.TaskTable(self.root, COLUMNS, lambda _iid: [])
        self.table.set_rows(ROWS)

    def tearDown(self) -> None:
        self.root.destroy()

    def test_numeric_column_sorts_largest_first_then_toggles(self) -> None:
        self.table.sort_by(1)
        self.assertEqual(self.table.tree.get_children(), ("dockge", "homepage", "vaultwarden"))
        self.table.sort_by(1)
        self.assertEqual(self.table.tree.get_children(), ("vaultwarden", "homepage", "dockge"))

    def test_search_filters_rows(self) -> None:
        self.table.search.set("warden")
        self.assertEqual(self.table.tree.get_children(), ("vaultwarden",))
        self.table.search.set("")
        self.assertEqual(len(self.table.tree.get_children()), 3)

    def test_update_keeps_selection_and_moves_row(self) -> None:
        self.table.sort_by(1)
        self.table.tree.selection_set("vaultwarden")
        self.table.set_rows([row("homepage", 0.4), row("dockge", 2.5), row("vaultwarden", 9.0)])
        self.assertEqual(self.table.tree.get_children()[0], "vaultwarden")
        self.assertEqual(self.table.selected(), "vaultwarden")
        self.assertEqual(self.table.tree.item("vaultwarden", "values"), ("9.0 %",))


GROUPED = [row("arch", 3.0, "VMs"), row("pve", 9.0, "VMs"), row("homepage", 0.4, "Dienste"),
           row("dockge", 2.5, "Dienste"), row("proxmox", 1.0, "Hosts")]


class GroupedTableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.table = table.TaskTable(self.root, COLUMNS, lambda _iid: [])
        self.table.set_rows(GROUPED)
        self.tree = self.table.tree

    def tearDown(self) -> None:
        self.root.destroy()

    def test_groups_in_order_of_first_row_with_count(self) -> None:
        self.assertEqual(self.tree.get_children(), ("group:VMs", "group:Dienste", "group:Hosts"))
        self.assertEqual(self.tree.item("group:Dienste", "text"), "Dienste (2)")
        self.assertEqual(self.tree.get_children("group:VMs"), ("arch", "pve"))

    def test_sorting_stays_inside_each_group(self) -> None:
        self.table.sort_by(1)
        self.assertEqual(self.tree.get_children(), ("group:VMs", "group:Dienste", "group:Hosts"))
        self.assertEqual(self.tree.get_children("group:VMs"), ("pve", "arch"))
        self.assertEqual(self.tree.get_children("group:Dienste"), ("dockge", "homepage"))

    def test_search_hides_empty_groups_and_updates_counts(self) -> None:
        self.table.search.set("o")
        self.assertEqual(self.tree.get_children(), ("group:Dienste", "group:Hosts"))
        self.assertEqual(self.tree.item("group:Dienste", "text"), "Dienste (2)")
        self.table.search.set("")
        self.assertEqual(len(self.tree.get_children()), 3)

    def test_group_heading_is_not_a_selection(self) -> None:
        self.tree.selection_set("group:VMs")
        self.assertIsNone(self.table.selected())
        self.tree.selection_set("pve")
        self.assertEqual(self.table.selected(), "pve")


if __name__ == "__main__":
    unittest.main()
