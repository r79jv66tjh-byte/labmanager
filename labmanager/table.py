"""Task-manager style list shared by the tabs: search field, sortable columns, context menu, status line.

Rows may belong to a group; groups are shown as expanded headings with a count, in the order of
their first row, and sorting and searching work inside each group. Rows are updated in place (same iid keeps its selection and position in the sort order), so live
values can stream in without the list flickering or losing the user's selection.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from tkinter import ttk

SortKey = float | str


@dataclass(frozen=True)
class Column:
    id: str
    title: str
    width: int
    numeric: bool = False  # numeric columns sort largest first on the first click, like Task Manager


@dataclass(frozen=True)
class Row:
    iid: str
    values: tuple[str, ...]  # one text per column, first is the name
    keys: tuple[SortKey, ...]  # one sort key per column
    tag: str  # "ok", "down" or "other"
    group: str  # heading the row is shown under; "" for a flat list


@dataclass(frozen=True)
class MenuItem:
    label: str
    command: Callable[[], None]


MenuEntry = MenuItem | None  # None draws a separator
MenuBuilder = Callable[[str], Sequence[MenuEntry]]


def matches(row: Row, query: str) -> bool:
    """Case-insensitive search across all visible texts of the row."""
    q = query.strip().casefold()
    return not q or any(q in v.casefold() for v in row.values)


def ordered(rows: Sequence[Row], column: int | None, descending: bool) -> list[Row]:
    if column is None:
        return list(rows)
    return sorted(rows, key=lambda r: r.keys[column], reverse=descending)


GROUP_PREFIX = "group:"


class TaskTable(ttk.Frame):
    def __init__(self, parent: tk.Misc, columns: Sequence[Column], menu: MenuBuilder) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.menu_builder = menu
        self.rows: list[Row] = []
        self.sort_column: int | None = None
        self.descending = False

        self.toolbar = ttk.Frame(self)
        self.toolbar.pack(fill="x", pady=(0, 6))
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.render())
        ttk.Entry(self.toolbar, textvariable=self.search, width=28).pack(side="right")
        ttk.Label(self.toolbar, text="Suchen:").pack(side="right", padx=(12, 4))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        ids = [c.id for c in self.columns[1:]]
        self.tree = ttk.Treeview(body, columns=ids, show="tree headings", selectmode="browse")
        for index, col in enumerate(self.columns):
            tree_id = "#0" if index == 0 else col.id
            self.tree.heading(tree_id, text=col.title, command=lambda i=index: self.sort_by(i))
            self.tree.column(tree_id, width=col.width, minwidth=40, stretch=index == 0,
                             anchor="e" if col.numeric else "w")
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("down", foreground="#c62828")
        self.tree.tag_configure("other", foreground="#9a6700")
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree.bind("<Button-3>", self._open_menu)

        self.status = ttk.Label(self, text="", anchor="w", wraplength=1100, justify="left")
        self.status.pack(fill="x", pady=(6, 0))

    def set_rows(self, rows: Sequence[Row]) -> None:
        self.rows = list(rows)
        self.render()

    def render(self) -> None:
        visible = [r for r in ordered(self.rows, self.sort_column, self.descending)
                   if matches(r, self.search.get())]
        wanted = {r.iid for r in visible}
        groups = list(dict.fromkeys(r.group for r in self.rows if r.group and r.iid in wanted))
        wanted_groups = {GROUP_PREFIX + g for g in groups}
        for top in self.tree.get_children():
            for child in self.tree.get_children(top):
                if child not in wanted:
                    self.tree.delete(child)
            if top not in wanted and top not in wanted_groups:
                self.tree.delete(top)
        for index, g in enumerate(groups):
            gid = GROUP_PREFIX + g
            text = f"{g} ({sum(1 for r in visible if r.group == g)})"
            if self.tree.exists(gid):
                self.tree.item(gid, text=text)
                self.tree.move(gid, "", index)
            else:
                self.tree.insert("", index, iid=gid, text=text, open=True, tags=("group",))
        positions: dict[str, int] = {}
        for r in visible:
            parent = GROUP_PREFIX + r.group if r.group else ""
            index = positions.get(parent, len(groups) if not parent else 0)
            positions[parent] = index + 1
            if self.tree.exists(r.iid):
                self.tree.item(r.iid, text=r.values[0], values=r.values[1:], tags=(r.tag,))
                self.tree.move(r.iid, parent, index)
            else:
                self.tree.insert(parent, index, iid=r.iid, text=r.values[0], values=r.values[1:], tags=(r.tag,))

    def sort_by(self, column: int) -> None:
        if self.sort_column == column:
            self.descending = not self.descending
        else:
            self.sort_column = column
            self.descending = self.columns[column].numeric
        arrow = " ▼" if self.descending else " ▲"
        for index, col in enumerate(self.columns):
            tree_id = "#0" if index == 0 else col.id
            self.tree.heading(tree_id, text=col.title + (arrow if index == column else ""))
        self.render()

    def selected(self) -> str | None:
        selection = self.tree.selection()
        if not selection or selection[0].startswith(GROUP_PREFIX):
            return None
        return selection[0]

    def _open_menu(self, event: tk.Event[ttk.Treeview]) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid or iid.startswith(GROUP_PREFIX):
            return
        self.tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=False)
        for entry in self.menu_builder(iid):
            if entry is None:
                menu.add_separator()
            else:
                menu.add_command(label=entry.label, command=entry.command)
        menu.tk_popup(event.x_root, event.y_root)
