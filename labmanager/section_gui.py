"""Settings pages for the optional sections: remote desktop with its profiles, backups, Docker, Proxmox.

Each section has a form with "Übernehmen" (set it up or change it) and "Abschalten" (remove it). The
values are only suggestions until they are taken over; lab.json itself keeps every value explicit.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from labmanager import monitors, settings

# Shared .rdp settings for a newly set up remote desktop; GNOME's Remote Login also needs
# "use redirection server name:i:1", which the hint below mentions.
DEFAULT_RDP_BASE = ["dynamic resolution:i:1", "redirectclipboard:i:1", "authentication level:i:2",
                    "prompt for credentials:i:0"]
DEFAULT_PROFILE = "Fenster"
DEFAULT_PROFILE_SPEC = settings.ProfileSpec("window", 1920, 1080, (), ())
KIND_TEXT = {"window": "Fenster", "monitors": "Bildschirme", "custom": "Eigene Zeilen"}


@dataclass(frozen=True)
class SectionField:
    key: str
    label: str
    hint: str
    kind: str  # "text", "int", "host" (choice of host ids), "service" (choice of services), "folder"
    suggestion: str = ""


SECTION_TITLES = {"rdp": "Remotedesktop", "backup": "Backups", "docker": "Docker", "proxmox": "Proxmox"}
SECTION_FIELDS: dict[str, tuple[SectionField, ...]] = {
    "rdp": (
        SectionField("host", "Ziel-Host", "der Rechner, zu dem verbunden wird", "host"),
        SectionField("username", "Benutzer", "Anmeldename auf dem Ziel; das Kennwort fragt Windows", "text")),
    "backup": (
        SectionField("host", "Proxmox-Host", "Host, auf dem die Backups liegen", "host"),
        SectionField("remote_dir", "Ordner auf dem Host", "Proxmox-Standard: /var/lib/vz/dump", "text",
                     "/var/lib/vz/dump"),
        SectionField("pattern", "Dateimuster", "welche Backups, z. B. vzdump-qemu-100-*.vma.zst", "text",
                     "vzdump-qemu-*.vma.zst"),
        SectionField("target_dir", "Zielordner unter Windows", "dorthin wird kopiert", "folder"),
        SectionField("keep", "Kopien behalten", "ältere Kopien werden gelöscht", "int", "4"),
        SectionField("max_age_days", "Backup höchstens (Tage)", "älter = rot und Warnung", "int", "3"),
        SectionField("copy_max_age_days", "Kopie höchstens (Tage)", "älter = rot und Warnung", "int", "8")),
    "docker": (
        SectionField("host", "Docker-Host", "SSH-Benutzer dort braucht sudo ohne Passwort", "host"),),
    "proxmox": (
        SectionField("host", "Proxmox-Host", "für pvesh per SSH (meist root)", "host"),
        SectionField("web", "Weboberfläche", "Dienst mit der Proxmox-Weboberfläche, für Direkt-Links", "service")),
}


def section_values(key: str, texts: dict[str, str], existing: dict[str, object] | None,
                   base_lines: list[str]) -> dict[str, object]:
    """The section as lab.json stores it; remote desktop keeps its profiles or gets a first one."""
    values: dict[str, object] = {}
    for f in SECTION_FIELDS[key]:
        text = texts[f.key].strip()
        if f.kind == "int":
            try:
                values[f.key] = int(text)
            except ValueError as e:
                raise settings.SettingsError(f"{f.label}: ganze Zahl erwartet") from e
        else:
            values[f.key] = text
    if key == "rdp":
        values["base"] = [line.strip() for line in base_lines if line.strip()]
        profiles = existing.get("profiles") if existing is not None else None
        values["profiles"] = profiles if isinstance(profiles, dict) else {
            DEFAULT_PROFILE: settings.profile_lines(DEFAULT_PROFILE_SPEC)}
    return values


def screen_names(ids: tuple[int, ...], screens: list[monitors.Monitor]) -> str:
    labels = {m.rdp_id: m.label.split(" · ")[0] or f"Bildschirm {m.rdp_id}" for m in screens}
    return ", ".join(labels.get(i, f"Bildschirm {i} (nicht angeschlossen)") for i in ids)


def profile_summary(spec: settings.ProfileSpec, screens: list[monitors.Monitor]) -> str:
    if spec.kind == "window":
        return f"{spec.width}×{spec.height}"
    if spec.kind == "monitors":
        return screen_names(spec.monitors, screens)
    return "; ".join(spec.lines)


class SectionForm(ttk.LabelFrame):
    def __init__(self, parent: tk.Misc, key: str, apply: Callable[[str], None],
                 switch_off: Callable[[str], None]) -> None:
        super().__init__(parent, text=SECTION_TITLES[key], padding=8)
        self.key = key
        self.vars: dict[str, tk.StringVar] = {}
        self.choices: dict[str, ttk.Combobox] = {}
        self.filled_from: object = object()  # section content the fields were last filled from
        for row, f in enumerate(SECTION_FIELDS[key]):
            var = tk.StringVar()
            self.vars[f.key] = var
            ttk.Label(self, text=f.label + ":").grid(row=row, column=0, sticky="w", pady=2)
            if f.kind in ("host", "service"):
                box = ttk.Combobox(self, textvariable=var, state="readonly", width=38)
                box.grid(row=row, column=1, sticky="w", pady=2)
                self.choices[f.key] = box
            else:
                ttk.Entry(self, textvariable=var, width=40).grid(row=row, column=1, sticky="w", pady=2)
            if f.kind == "folder":
                ttk.Button(self, text="Durchsuchen …", command=lambda v=var: self.browse(v)).grid(
                    row=row, column=2, sticky="w", padx=(6, 0))
            ttk.Label(self, text=f.hint, foreground="#666666").grid(row=row, column=3, sticky="w", padx=(10, 0))
        row = len(SECTION_FIELDS[key])
        self.base: tk.Text | None = None
        if key == "rdp":
            ttk.Label(self, text="Gemeinsame Einstellungen:").grid(row=row, column=0, sticky="nw", pady=2)
            self.base = tk.Text(self, width=40, height=6)
            self.base.grid(row=row, column=1, sticky="w", pady=2)
            ttk.Label(self, foreground="#666666", justify="left", text=(
                "eine .rdp-Zeile pro Zeile, gilt für alle Profile;\nfür GNOME-Remoteanmeldung zusätzlich "
                "„use redirection server name:i:1“")).grid(row=row, column=3, sticky="w", padx=(10, 0))
            row += 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="Übernehmen", command=lambda: apply(key)).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Abschalten", command=lambda: switch_off(key)).pack(side="left", padx=(0, 12))
        self.state = ttk.Label(buttons, text="")
        self.state.pack(side="left")

    def browse(self, var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(title="Zielordner wählen")
        if chosen:
            var.set(str(Path(chosen)))

    def texts(self) -> dict[str, str]:
        return {k: v.get() for k, v in self.vars.items()}

    def base_lines(self) -> list[str]:
        return self.base.get("1.0", "end").splitlines() if self.base is not None else []

    def refresh(self, raw: settings.Raw) -> None:
        """Updates the choices and, when the section changed, the fields; keeps what is being typed."""
        hosts = [str(h.get("id")) for h in settings.entries(raw, "hosts")]
        services = [str(s.get("label")) for s in settings.entries(raw, "services")]
        for f in SECTION_FIELDS[self.key]:
            if f.key in self.choices:
                self.choices[f.key].configure(values=hosts if f.kind == "host" else services)
        section = raw.get(self.key)
        present = isinstance(section, dict)
        self.state.configure(text="eingerichtet" if present else "nicht eingerichtet",
                             foreground="#1a7f37" if present else "#666666")
        if section == self.filled_from:
            return
        self.filled_from = section
        for f in SECTION_FIELDS[self.key]:
            value = section.get(f.key) if isinstance(section, dict) else None
            self.vars[f.key].set(str(value) if value is not None else f.suggestion)
        if self.base is not None:
            lines = section.get("base") if isinstance(section, dict) else None
            self.base.delete("1.0", "end")
            self.base.insert("1.0", "\n".join(lines if isinstance(lines, list) else DEFAULT_RDP_BASE))


class ProfileDialog(tk.Toplevel):
    """Remote desktop profile: a window of a given size, chosen monitors, or own .rdp lines."""

    def __init__(self, parent: tk.Misc, title: str, name: str, spec: settings.ProfileSpec,
                 screens: list[monitors.Monitor],
                 accept: Callable[[str, settings.ProfileSpec], str | None]) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.screens = screens
        self.accept = accept
        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        self.name = tk.StringVar(value=name)
        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.name, width=36).grid(row=0, column=1, sticky="w", columnspan=2)

        self.kind = tk.StringVar(value=spec.kind)
        ttk.Radiobutton(form, text="Fenster", value="window", variable=self.kind).grid(
            row=1, column=0, sticky="w", pady=(10, 0))
        size = ttk.Frame(form)
        size.grid(row=1, column=1, sticky="w", pady=(10, 0))
        self.width = tk.StringVar(value=str(spec.width or 1920))
        self.height = tk.StringVar(value=str(spec.height or 1080))
        ttk.Entry(size, textvariable=self.width, width=7).pack(side="left")
        ttk.Label(size, text=" × ").pack(side="left")
        ttk.Entry(size, textvariable=self.height, width=7).pack(side="left")
        ttk.Label(size, text=" Pixel").pack(side="left")

        ttk.Radiobutton(form, text="Bildschirme", value="monitors", variable=self.kind).grid(
            row=2, column=0, sticky="nw", pady=(10, 0))
        boxes = ttk.Frame(form)
        boxes.grid(row=2, column=1, sticky="w", pady=(10, 0))
        known = {m.rdp_id for m in screens}
        self.checks: dict[int, tk.BooleanVar] = {}
        for m in screens:
            self.checks[m.rdp_id] = tk.BooleanVar(value=m.rdp_id in spec.monitors)
            ttk.Checkbutton(boxes, text=m.label, variable=self.checks[m.rdp_id]).pack(anchor="w")
        for i in spec.monitors:
            if i not in known:
                self.checks[i] = tk.BooleanVar(value=True)
                ttk.Checkbutton(boxes, text=f"Bildschirm {i} (nicht angeschlossen)", variable=self.checks[i]).pack(
                    anchor="w")

        ttk.Radiobutton(form, text="Eigene Zeilen", value="custom", variable=self.kind).grid(
            row=3, column=0, sticky="nw", pady=(10, 0))
        self.custom = tk.Text(form, width=40, height=4)
        self.custom.grid(row=3, column=1, sticky="w", pady=(10, 0))
        self.custom.insert("1.0", "\n".join(spec.lines))

        self.error = ttk.Label(form, text="", foreground="#c62828", wraplength=420, justify="left")
        self.error.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(form)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="OK", command=self.ok).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Abbrechen", command=self.destroy).pack(side="left")

    def spec(self) -> settings.ProfileSpec:
        kind = self.kind.get()
        if kind == "window":
            try:
                return settings.ProfileSpec("window", int(self.width.get()), int(self.height.get()), (), ())
            except ValueError as e:
                raise settings.SettingsError("Fenster: Breite und Höhe als ganze Zahlen eintragen.") from e
        if kind == "monitors":
            chosen = {i for i, var in self.checks.items() if var.get()}
            return settings.ProfileSpec("monitors", 0, 0, monitors.selection_order(chosen, self.screens), ())
        return settings.ProfileSpec("custom", 0, 0, (), tuple(self.custom.get("1.0", "end").splitlines()))

    def ok(self) -> None:
        try:
            error = self.accept(self.name.get(), self.spec())
        except settings.SettingsError as e:
            error = str(e)
        if error is None:
            self.destroy()
        else:
            self.error.configure(text=error)

    def show(self) -> None:
        self.grab_set()
        self.wait_window()
