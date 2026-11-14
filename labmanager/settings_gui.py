"""Settings tab: VMs, hosts, services and the SSH key, plus remote desktop, backups, Docker and Proxmox.

Changes collect in memory and are written with "Speichern"; LabManager then restarts itself with the
new lab.json. Every change is validated by labmanager.settings before it is accepted.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from labmanager import containers, hyperv, monitors, probes, settings, ssh
from labmanager.config import ConfigError
from labmanager.section_gui import (
    DEFAULT_PROFILE_SPEC,
    KIND_TEXT,
    SECTION_TITLES,
    ProfileDialog,
    SectionForm,
    profile_summary,
    section_values,
)

QUEUE_CHECK_MS = 150
SSH_PORT = 22
PROBE_TIMEOUT = 1.5


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    hint: str
    kind: str = "text"  # "text", "port" or "host" (choice of host ids)


@dataclass(frozen=True)
class Kind:
    key: str  # list in lab.json: "vms", "hosts" or "services"
    title: str
    singular: str
    fields: tuple[Field, ...]


KINDS = (
    Kind("vms", "VMs", "VM", (
        Field("name", "Name in Hyper-V", "genau wie im Hyper-V-Manager"),
        Field("label", "Anzeigename", "so erscheint die VM in LabManager"))),
    Kind("hosts", "Hosts", "Host", (
        Field("id", "ID", "kurzes Kürzel, z. B. docker; später nicht mehr änderbar"),
        Field("label", "Anzeigename", "so erscheint der Host in LabManager"),
        Field("address", "Adresse", "IP-Adresse oder Rechnername"),
        Field("ssh_user", "SSH-Benutzer", "Benutzer für die Anmeldung per SSH-Schlüssel"))),
    Kind("services", "Dienste", "Dienst", (
        Field("label", "Anzeigename", "z. B. Homepage"),
        Field("host", "Host", "auf welchem Host der Dienst läuft", kind="host"),
        Field("port", "Port", "z. B. 3000", kind="port"),
        Field("url", "Adresse (URL)", "leer lassen: http://<Host-Adresse>:<Port>"))),
)


def entry_values(kind: Kind, texts: dict[str, str], raw: settings.Raw) -> settings.Entry:
    """Turns the dialog texts into a lab.json entry; raises SettingsError for a port that is no number."""
    entry: settings.Entry = {f.key: texts[f.key].strip() for f in kind.fields}
    for f in kind.fields:
        if f.kind == "port":
            try:
                entry[f.key] = int(texts[f.key].strip())
            except ValueError as e:
                raise settings.SettingsError(f"{f.label}: ganze Zahl erwartet, z. B. 3000") from e
    if kind.key == "services" and not entry["url"]:
        host = next((h for h in settings.entries(raw, "hosts") if h.get("id") == entry["host"]), None)
        if host is not None:
            entry["url"] = f"http://{host['address']}:{entry['port']}"
    return entry


class EntryDialog(tk.Toplevel):
    """Form for one entry. `accept` returns an error text (dialog stays open) or None (dialog closes)."""

    def __init__(self, parent: tk.Misc, title: str, kind: Kind, initial: settings.Entry, host_ids: list[str],
                 accept: Callable[[dict[str, str]], str | None], editing: bool) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.accept = accept
        self.result: dict[str, str] | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self.widgets: dict[str, ttk.Widget] = {}
        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        for row, f in enumerate(kind.fields):
            var = tk.StringVar(value=str(initial.get(f.key, "")))
            self.vars[f.key] = var
            ttk.Label(form, text=f.label + ":").grid(row=row * 2, column=0, sticky="w", pady=(6, 0))
            widget: ttk.Widget
            if f.kind == "host":
                widget = ttk.Combobox(form, textvariable=var, values=host_ids, state="readonly", width=40)
            else:
                widget = ttk.Entry(form, textvariable=var, width=42)
            if editing and kind.key == "hosts" and f.key == "id":
                widget.state(["disabled"])
            widget.grid(row=row * 2, column=1, sticky="w", pady=(6, 0))
            ttk.Label(form, text=f.hint, foreground="#666666").grid(row=row * 2 + 1, column=1, sticky="w")
            self.widgets[f.key] = widget
        self.extra = ttk.Frame(form)
        self.extra.grid(row=len(kind.fields) * 2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.error = ttk.Label(form, text="", foreground="#c62828", wraplength=420, justify="left")
        self.error.grid(row=len(kind.fields) * 2 + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(form)
        buttons.grid(row=len(kind.fields) * 2 + 2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self.ok).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda _e: self.ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def texts(self) -> dict[str, str]:
        return {k: v.get() for k, v in self.vars.items()}

    def ok(self) -> None:
        error = self.accept(self.texts())
        if error is None:
            self.result = self.texts()
            self.destroy()
        else:
            self.error.configure(text=error, foreground="#c62828")

    def show(self) -> None:
        self.grab_set()
        self.wait_window()


class SettingsTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, raw: settings.Raw, config_path: Path,
                 on_saved: Callable[[], None], screens: list[monitors.Monitor]) -> None:
        super().__init__(parent, padding=8)
        self.saved_raw = raw
        self.raw = raw
        self.screens = screens
        self.forms: dict[str, SectionForm] = {}
        self.config_path = config_path
        self.on_saved = on_saved
        self.results: queue.Queue[tuple[str, object]] = queue.Queue()
        self.trees: dict[str, ttk.Treeview] = {}
        self.test_dialog: EntryDialog | None = None

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 6))
        self.save_button = ttk.Button(top, text="Speichern und neu laden", command=self.save)
        self.save_button.pack(side="left", padx=(0, 6))
        self.discard_button = ttk.Button(top, text="Änderungen verwerfen", command=self.discard)
        self.discard_button.pack(side="left", padx=(0, 6))
        self.state_label = ttk.Label(top, text="")
        self.state_label.pack(side="left", padx=(12, 0))

        self.pages = ttk.Notebook(self)
        self.pages.pack(fill="both", expand=True)
        for kind in KINDS:
            self.pages.add(self._page(kind), text=kind.title)
        self.pages.add(self._rdp_page(), text="Remotedesktop")
        self.pages.add(self._sections_page(("backup",)), text="Backups")
        self.pages.add(self._sections_page(("docker", "proxmox")), text="Docker & Proxmox")

        self.message = ttk.Label(self, text="", wraplength=1100, justify="left")
        self.message.pack(fill="x", pady=(6, 0))
        self.show()

    def start(self) -> None:
        self.after(QUEUE_CHECK_MS, self.drain)

    # --- layout -----------------------------------------------------------------------------
    def _page(self, kind: Kind) -> ttk.Frame:
        page = ttk.Frame(self.pages, padding=8)
        if kind.key == "hosts":
            key_row = ttk.Frame(page)
            key_row.pack(fill="x", pady=(0, 8))
            ttk.Label(key_row, text="SSH-Schlüssel (für alle Hosts):").pack(side="left")
            self.ssh_key = tk.StringVar(value=str(self.raw.get("ssh_key", "")))
            ttk.Entry(key_row, textvariable=self.ssh_key, width=60).pack(side="left", padx=6)
            ttk.Button(key_row, text="Durchsuchen …", command=self.browse_key).pack(side="left", padx=(0, 6))
            ttk.Button(key_row, text="Übernehmen", command=self.apply_key).pack(side="left")
        buttons = ttk.Frame(page)
        buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="Hinzufügen …", command=lambda: self.add(kind)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Bearbeiten …", command=lambda: self.edit(kind)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Entfernen", command=lambda: self.remove(kind)).pack(side="left", padx=(0, 6))
        if kind.key == "services":
            ttk.Button(buttons, text="Aus Docker vorschlagen …", command=self.suggest_docker).pack(
                side="left", padx=(12, 0))
        tree = ttk.Treeview(page, columns=[f.key for f in kind.fields], show="headings", selectmode="browse")
        for f in kind.fields:
            tree.heading(f.key, text=f.label)
            tree.column(f.key, width=220 if f.key in ("label", "url") else 140)
        tree.pack(fill="both", expand=True)
        tree.bind("<Double-1>", lambda _e: self.edit(kind))
        self.trees[kind.key] = tree
        return page

    def _sections_page(self, keys: tuple[str, ...]) -> ttk.Frame:
        page = ttk.Frame(self.pages, padding=8)
        for key in keys:
            form = SectionForm(page, key, self.apply_section, self.switch_off)
            form.pack(fill="x", pady=(0, 10), anchor="n")
            self.forms[key] = form
        return page

    def _rdp_page(self) -> ttk.Frame:
        page = self._sections_page(("rdp",))
        profiles = ttk.LabelFrame(page, text="Profile (je ein Knopf unter „Verbinden“)", padding=8)
        profiles.pack(fill="both", expand=True)
        buttons = ttk.Frame(profiles)
        buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(buttons, text="Hinzufügen …", command=self.add_profile).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Bearbeiten …", command=self.edit_profile).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Entfernen", command=self.remove_profile).pack(side="left")
        self.profile_tree = ttk.Treeview(profiles, columns=("name", "kind", "details"), show="headings",
                                         selectmode="browse", height=5)
        for col, title, width in (("name", "Name", 200), ("kind", "Art", 120), ("details", "Einstellung", 520)):
            self.profile_tree.heading(col, text=title)
            self.profile_tree.column(col, width=width)
        self.profile_tree.pack(fill="both", expand=True)
        self.profile_tree.bind("<Double-1>", lambda _e: self.edit_profile())
        return page

    def profile_items(self) -> list[tuple[str, settings.ProfileSpec]]:
        rdp = self.raw.get("rdp")
        profiles = rdp.get("profiles") if isinstance(rdp, dict) else None
        if not isinstance(profiles, dict):
            return []
        return [(str(name), settings.read_profile(lines)) for name, lines in profiles.items()]

    def show(self) -> None:
        for form in self.forms.values():
            form.refresh(self.raw)
        self.profile_tree.delete(*self.profile_tree.get_children())
        for name, spec in self.profile_items():
            self.profile_tree.insert("", "end", iid=name,
                                     values=(name, KIND_TEXT[spec.kind], profile_summary(spec, self.screens)))
        for kind in KINDS:
            tree = self.trees[kind.key]
            tree.delete(*tree.get_children())
            for i, e in enumerate(settings.entries(self.raw, kind.key)):
                tree.insert("", "end", iid=str(i), values=[str(e.get(f.key, "")) for f in kind.fields])
        dirty = self.raw != self.saved_raw
        self.state_label.configure(text="Ungespeicherte Änderungen" if dirty else "Alles gespeichert",
                                   foreground="#9a6700" if dirty else "#1a7f37")
        for b in (self.save_button, self.discard_button):
            b.state(["!disabled"] if dirty else ["disabled"])

    def selected(self, kind: Kind) -> int | None:
        selection = self.trees[kind.key].selection()
        if not selection:
            self.message.configure(text=f"Bitte zuerst einen Eintrag unter „{kind.title}“ auswählen.")
            return None
        return int(selection[0])

    # --- changes ----------------------------------------------------------------------------
    def change(self, new_raw: settings.Raw) -> None:
        self.raw = new_raw
        self.message.configure(text="")
        self.show()

    def try_change(self, kind: Kind, index: int | None, texts: dict[str, str]) -> str | None:
        """Applies the dialog's values; returns the error to show in the dialog, or None."""
        try:
            self.change(settings.with_entry(self.raw, kind.key, index, entry_values(kind, texts, self.raw)))
        except settings.SettingsError as e:
            return str(e)
        return None

    def dialog(self, kind: Kind, index: int | None) -> EntryDialog:
        initial = settings.entries(self.raw, kind.key)[index] if index is not None else {}
        host_ids = [str(h.get("id")) for h in settings.entries(self.raw, "hosts")]
        title = f"{kind.singular} {'bearbeiten' if index is not None else 'hinzufügen'}"
        d = EntryDialog(self, title, kind, initial, host_ids, lambda t: self.try_change(kind, index, t),
                        editing=index is not None)
        if kind.key == "vms":
            ttk.Button(d.extra, text="Aus Hyper-V auswählen …", command=lambda: self.pick_vm(d)).pack(side="left")
        if kind.key == "hosts":
            ttk.Button(d.extra, text="Verbindung testen", command=lambda: self.test_host(d)).pack(side="left")
        return d

    def add(self, kind: Kind) -> None:
        self.dialog(kind, None).show()

    def edit(self, kind: Kind) -> None:
        index = self.selected(kind)
        if index is not None:
            self.dialog(kind, index).show()

    def remove(self, kind: Kind) -> None:
        index = self.selected(kind)
        if index is None:
            return
        try:
            self.change(settings.without_entry(self.raw, kind.key, index))
        except settings.SettingsError as e:
            self.message.configure(text=str(e))

    def browse_key(self) -> None:
        chosen = filedialog.askopenfilename(title="Privaten SSH-Schlüssel wählen",
                                            initialdir=str(Path.home() / ".ssh"))
        if chosen:
            self.ssh_key.set(str(Path(chosen)))
            self.apply_key()

    def apply_key(self) -> None:
        try:
            self.change(settings.with_ssh_key(self.raw, self.ssh_key.get()))
        except settings.SettingsError as e:
            self.message.configure(text=str(e))

    # --- optional sections and remote desktop profiles ----------------------------------------
    def apply_section(self, key: str) -> None:
        form = self.forms[key]
        existing = self.raw.get(key)
        try:
            values = section_values(key, form.texts(), existing if isinstance(existing, dict) else None,
                                    form.base_lines())
            self.change(settings.with_section(self.raw, key, values))
        except settings.SettingsError as e:
            self.message.configure(text=f"{SECTION_TITLES[key]}: {e}")
            return
        self.message.configure(text=f"{SECTION_TITLES[key]} übernommen (noch nicht gespeichert).")

    def switch_off(self, key: str) -> None:
        try:
            self.change(settings.with_section(self.raw, key, None))
        except settings.SettingsError as e:
            self.message.configure(text=f"{SECTION_TITLES[key]}: {e}")

    def try_profile(self, old_name: str | None, name: str, spec: settings.ProfileSpec) -> str | None:
        try:
            self.change(settings.with_profile(self.raw, old_name, name, settings.profile_lines(spec)))
        except settings.SettingsError as e:
            return str(e)
        return None

    def profile_dialog(self, old_name: str | None) -> ProfileDialog:
        specs = dict(self.profile_items())
        spec = specs[old_name] if old_name is not None else DEFAULT_PROFILE_SPEC
        title = "Profil bearbeiten" if old_name is not None else "Profil hinzufügen"
        return ProfileDialog(self, title, old_name or "", spec, self.screens,
                             lambda name, s: self.try_profile(old_name, name, s))

    def add_profile(self) -> None:
        if not self.profile_items():
            self.message.configure(text="Erst Remotedesktop mit Ziel-Host und Benutzer übernehmen.")
            return
        self.profile_dialog(None).show()

    def edit_profile(self) -> None:
        selection = self.profile_tree.selection()
        if not selection:
            self.message.configure(text="Bitte zuerst ein Profil auswählen.")
            return
        self.profile_dialog(selection[0]).show()

    def remove_profile(self) -> None:
        selection = self.profile_tree.selection()
        if not selection:
            self.message.configure(text="Bitte zuerst ein Profil auswählen.")
            return
        try:
            self.change(settings.without_profile(self.raw, selection[0]))
        except settings.SettingsError as e:
            self.message.configure(text=str(e))

    def discard(self) -> None:
        self.raw = self.saved_raw
        self.ssh_key.set(str(self.raw.get("ssh_key", "")))
        self.message.configure(text="Änderungen verworfen.")
        self.show()

    def save(self) -> None:
        try:
            settings.save_raw(self.config_path, self.raw)
        except (settings.SettingsError, OSError) as e:
            self.message.configure(text=f"Speichern fehlgeschlagen: {e}")
            return
        self.saved_raw = self.raw
        self.show()
        self.on_saved()

    def has_unsaved_changes(self) -> bool:
        return self.raw != self.saved_raw

    # --- helpers in dialogs -----------------------------------------------------------------
    def pick_vm(self, d: EntryDialog) -> None:
        try:
            names = hyperv.vm_names()
        except hyperv.HyperVError as e:
            d.error.configure(text=f"Hyper-V nicht abfragbar: {e}")
            return
        if not names:
            d.error.configure(text="Hyper-V meldet keine VMs.")
            return
        menu = tk.Menu(d, tearoff=False)
        for name in names:
            def choose(n: str = name) -> None:
                d.vars["name"].set(n)
                if not d.vars["label"].get().strip():
                    d.vars["label"].set(n)
            menu.add_command(label=name, command=choose)
        menu.tk_popup(d.winfo_pointerx(), d.winfo_pointery())

    def test_host(self, d: EntryDialog) -> None:
        texts = d.texts()
        kind = KINDS[1]
        try:
            entry = entry_values(kind, texts, self.raw)
            hosts = settings.entries(self.raw, "hosts")
            index = next((i for i, h in enumerate(hosts) if h.get("id") == entry["id"]), None)
            lab = settings.checked(settings.with_entry(self.raw, "hosts", index, entry))
        except settings.SettingsError as e:
            d.error.configure(text=str(e))
            return
        host = lab.host(str(entry["id"]))
        d.error.configure(text=f"Teste {host.ssh_user}@{host.address} …", foreground="#9a6700")

        def work() -> None:
            try:
                if not probes.port_open(host.address, SSH_PORT, PROBE_TIMEOUT):
                    self.results.put(("test", f"{host.address}: SSH-Port {SSH_PORT} antwortet nicht."))
                    return
                ssh.run(ssh.command(lab, host, "echo", "ok"))
                self.results.put(("test", f"✓ Anmeldung als {host.ssh_user}@{host.address} klappt."))
            except (ssh.SshError, OSError) as e:
                self.results.put(("test", f"Anmeldung fehlgeschlagen: {e}"))
        self.test_dialog = d
        threading.Thread(target=work, daemon=True).start()

    def suggest_docker(self) -> None:
        try:
            lab = settings.checked(self.raw)
            lab.require_docker()
        except (settings.SettingsError, ConfigError) as e:
            self.message.configure(text=str(e))
            return
        self.message.configure(text="Frage die Container der Docker-VM ab …")

        def work() -> None:
            try:
                self.results.put(("docker", containers.list_containers(lab, ssh.run)))
            except (ssh.SshError, OSError) as e:
                self.results.put(("error", f"Container nicht abfragbar: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def offer_suggestions(self, items: list[containers.Container]) -> SuggestDialog | None:
        try:
            found = settings.docker_suggestions(self.raw, items)
        except settings.SettingsError as e:
            self.message.configure(text=str(e))
            return None
        if not found:
            self.message.configure(text="Alle veröffentlichten Container-Ports sind schon als Dienst eingetragen.")
            return None
        self.message.configure(text="")
        return SuggestDialog(self, found, self.add_services)

    def add_services(self, chosen: list[settings.Entry]) -> str | None:
        new_raw = self.raw
        try:
            for entry in chosen:
                new_raw = settings.with_entry(new_raw, "services", None, entry)
        except settings.SettingsError as e:
            return str(e)
        self.change(new_raw)
        self.message.configure(text=f"{len(chosen)} Dienst(e) übernommen (noch nicht gespeichert).")
        return None

    def drain(self) -> None:
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            if kind == "docker" and isinstance(payload, list):
                dialog = self.offer_suggestions([c for c in payload if isinstance(c, containers.Container)])
                if dialog is not None:
                    dialog.show()
            elif kind == "error":
                self.message.configure(text=str(payload))
            elif kind == "test":
                text = str(payload)
                d = self.test_dialog
                if d is not None and d.winfo_exists():
                    d.error.configure(text=text, foreground="#1a7f37" if text.startswith("✓") else "#c62828")
        self.after(QUEUE_CHECK_MS, self.drain)


class SuggestDialog(tk.Toplevel):
    """Check list of suggested services; OK hands the ticked ones to `accept`."""

    def __init__(self, parent: tk.Misc, suggestions: list[settings.Entry],
                 accept: Callable[[list[settings.Entry]], str | None]) -> None:
        super().__init__(parent)
        self.title("Dienste aus Docker")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.accept = accept
        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="Veröffentlichte Ports, die noch kein Dienst sind:").pack(anchor="w", pady=(0, 6))
        self.choices: list[tuple[tk.BooleanVar, settings.Entry]] = []
        for entry in suggestions:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(form, text=f"{entry['label']}  –  {entry['url']}", variable=var).pack(anchor="w")
            self.choices.append((var, entry))
        self.error = ttk.Label(form, text="", foreground="#c62828", wraplength=460, justify="left")
        self.error.pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(form)
        buttons.pack(anchor="e", pady=(10, 0))
        ttk.Button(buttons, text="Übernehmen", command=self.ok).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="left")

    def ok(self) -> None:
        error = self.accept([entry for var, entry in self.choices if var.get()])
        if error is None:
            self.destroy()
        else:
            self.error.configure(text=error)

    def show(self) -> None:
        self.grab_set()
        self.wait_window()
