"""Main window of LabManager."""

from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from labmanager import (
    backups,
    connect,
    containers,
    hyperv,
    memory,
    monitors,
    probes,
    proxmox,
    settings,
    settings_gui,
    ssh,
    status,
    table,
)
from labmanager.config import ConfigError, Lab, load

# Hyper-V, SSH ports, web services, Proxmox' resource list and the hosts' /proc/meminfo offer no
# change notifications, so the overview is re-checked on a timer.
REFRESH_MS = 5000
# Results from worker threads are handed to Tk through a queue; Tk widgets must only be touched here.
QUEUE_CHECK_MS = 150

VM_STATE_TEXT = {"Running": "läuft", "Off": "aus", "Saved": "gespeichert", "Paused": "pausiert",
                 "Starting": "startet", "Stopping": "fährt herunter"}
CONTAINER_STATE_TEXT = {"running": "läuft", "exited": "beendet", "restarting": "startet neu",
                        "paused": "pausiert", "created": "erstellt", "dead": "abgestürzt"}
CONTAINER_ACTION_TEXT = {"start": "starten", "stop": "stoppen", "restart": "neu starten",
                         "pause": "pausieren", "unpause": "fortsetzen"}


OVERVIEW_COLUMNS = (
    table.Column("name", "Name", 230), table.Column("kind", "Typ", 100), table.Column("state", "Zustand", 130),
    table.Column("cpu", "CPU", 70, numeric=True), table.Column("memory_used", "RAM belegt", 90, numeric=True),
    table.Column("memory_total", "RAM zugewiesen", 110, numeric=True),
    table.Column("memory_percent", "RAM %", 70, numeric=True), table.Column("cores", "Kerne", 60, numeric=True),
    table.Column("uptime", "Laufzeit", 110, numeric=True), table.Column("address", "Adresse", 210),
    table.Column("info", "Info", 260),
)
PVE_STATE_TEXT = {"running": "läuft", "stopped": "aus", "paused": "pausiert"}
GROUP_VMS, GROUP_HOSTS, GROUP_SERVICES = "Virtuelle Maschinen", "Hosts", "Dienste"
NONE = -1.0  # sort key for empty numeric cells: below every real value


def uptime_text(seconds: int) -> str:
    if seconds <= 0:
        return ""
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days} T {hours} Std."
    return f"{hours} Std. {rest // 60} Min." if hours else f"{rest // 60} Min."


def resource_row(group: str, iid: str, name: str, kind: str, state: str, tag: str, address: str, info: str,
                 cpu: float | None, measured: memory.Memory | None, assigned: int | None, cores: int | None,
                 uptime: int | None) -> table.Row:
    """Row with the shared resource columns; None leaves a cell empty (VM off, value unknown).

    Used memory and its share come only from inside a host (`measured`); VM rows show just how much
    memory is assigned, because Hyper-V and Proxmox measure it from outside in misleading ways.
    """
    total = measured.total if measured is not None else assigned
    percent = status.share(measured.used, measured.total) if measured is not None else None
    if measured is not None and measured.low and tag == "ok":
        tag = "other"
    values = (name, kind, state,
              f"{cpu:.0f} %" if cpu is not None else "",
              status.gigabytes(measured.used) if measured is not None else "",
              status.gigabytes(total) if total else "",
              f"{percent:.0%}" if percent is not None else "",
              str(cores) if cores is not None else "",
              uptime_text(uptime) if uptime is not None else "", address, info)
    keys: tuple[table.SortKey, ...] = (
        name, kind, state, cpu if cpu is not None else NONE,
        float(measured.used) if measured is not None else NONE, float(total) if total else NONE,
        percent if percent is not None else NONE, float(cores) if cores is not None else NONE,
        float(uptime) if uptime is not None else NONE, address, info)
    return table.Row(iid=iid, values=values, keys=keys, tag=tag, group=group)


def hyperv_row(lab: Lab, vm_name: str, label: str, snap: status.Snapshot | None) -> table.Row:
    iid, kind = f"vm:{vm_name}", "Hyper-V-VM"
    info_text = f"Hyper-V-Name: {vm_name}"
    if snap is None:
        return resource_row(GROUP_VMS, iid, label, kind, "…", "other", "", info_text, None, None, None, None, None)
    if snap.vm_no_admin:
        return resource_row(GROUP_VMS, iid, label, kind, "keine Adminrechte", "other", "", info_text,
                            None, None, None, None, None)
    if snap.vm_error is not None:
        return resource_row(GROUP_VMS, iid, label, kind, "Fehler", "down", "", info_text, None, None, None, None, None)
    vm = snap.vms.get(vm_name)
    if vm is None:
        return resource_row(GROUP_VMS, iid, label, kind, "nicht gefunden", "down", "", info_text, None, None, None, None, None)
    running = vm.state == "Running"
    tag = "ok" if running else "down" if vm.state == "Off" else "other"
    details = f"{info_text} · Gen {vm.generation}" + (" · dynamischer RAM" if vm.dynamic_memory else "")
    return resource_row(GROUP_VMS, iid, label, kind, VM_STATE_TEXT.get(vm.state, vm.state), tag, "", details,
                        float(vm.cpu_percent) if running else None, None,
                        vm.memory_assigned if running else None, vm.processors, vm.uptime_s if running else None)


def overview_rows(lab: Lab, snap: status.Snapshot | None) -> list[table.Row]:
    """All rows in display order: Hyper-V VMs, hosts, Proxmox VMs, services."""
    rows = [hyperv_row(lab, vm.name, vm.label, snap) for vm in lab.vms]
    node = snap.cluster.nodes[0] if snap and snap.cluster and len(snap.cluster.nodes) == 1 else None
    for h in lab.hosts:
        up = snap.host_up[h.id] if snap else None
        state = "…" if up is None else "erreichbar" if up else "nicht erreichbar"
        tag = "other" if up is None else "ok" if up else "down"
        info = f"SSH: {h.ssh_user}@{h.address}"
        measured = snap.host_memory.get(h.id) if snap else None
        if lab.proxmox is not None and h.id == lab.proxmox.host and node is not None:
            rows.append(resource_row(GROUP_HOSTS, f"host:{h.id}", h.label, "Host", state, tag, h.address,
                                     f"{info} · Proxmox-Knoten {node.name}", node.cpu * 100, measured,
                                     None, node.cores, node.uptime_s))
        else:
            rows.append(resource_row(GROUP_HOSTS, f"host:{h.id}", h.label, "Host", state, tag, h.address, info,
                                     None, measured, None, None, None))
    if snap and snap.cluster:
        for pv in snap.cluster.vms:
            running = pv.status == "running"
            tag = "ok" if running else "down" if pv.status == "stopped" else "other"
            rows.append(resource_row(
                GROUP_VMS, f"pve:{pv.vmid}", f"{pv.name} (VM {pv.vmid})", "Proxmox-VM", PVE_STATE_TEXT.get(pv.status, pv.status),
                tag, "", f"Proxmox-Knoten {pv.node}", pv.cpu * 100 if running else None,
                None, pv.memory_max, pv.cores,
                pv.uptime_s if running else None))
    for s in lab.services:
        up = snap.service_up[s.label] if snap else None
        state = "…" if up is None else "antwortet" if up else "antwortet nicht"
        tag = "other" if up is None else "ok" if up else "down"
        rows.append(resource_row(GROUP_SERVICES, f"svc:{s.label}", s.label, "Dienst", state, tag, s.url,
                                 f"Host: {lab.host(s.host).label}", None, None, None, None, None))
    return rows


def overview_summary(lab: Lab, snap: status.Snapshot) -> str:
    running = sum(1 for v in lab.vms if v.name in snap.vms and snap.vms[v.name].state == "Running")
    vm_text = "Hyper-V-VMs: unbekannt" if snap.vm_error is not None else f"{running} von {len(lab.vms)} Hyper-V-VMs laufen"
    lines = [(f"{vm_text} · "
              f"{sum(snap.host_up.values())} von {len(lab.hosts)} Hosts erreichbar · "
              f"{sum(snap.service_up.values())} von {len(lab.services)} Diensten antworten")]
    if snap.vm_error is not None:
        lines.append(f"Hyper-V nicht abfragbar: {snap.vm_error}")
    if snap.cluster_error is not None:
        lines.append(f"Proxmox nicht abfragbar: {snap.cluster_error}")
    lines += [f"RAM von {lab.host(h).label} nicht lesbar: {e}" for h, e in snap.memory_errors.items()]
    lines += [f"⚠ {w}" for w in status.warnings(lab, snap)]
    return "\n".join(lines)


class OverviewTab(ttk.Frame):
    """Whole lab like Task Manager's process list: Hyper-V VMs, hosts, Proxmox VMs and services."""

    def __init__(self, parent: ttk.Notebook, lab: Lab, read_vms: status.VmReader, probe: status.PortProbe,
                 read_cluster: status.ClusterReader, read_memory: status.MemoryReader,
                 start: Callable[[str], None], stop: Callable[[str], None],
                 run: ssh.Runner) -> None:
        super().__init__(parent, padding=8)
        self.lab = lab
        self.read_vms = read_vms
        self.probe = probe
        self.read_cluster = read_cluster
        self.read_memory = read_memory
        self.start_vm = start
        self.stop_vm = stop
        self.run = run
        self.snap: status.Snapshot | None = None
        self.results: queue.Queue[tuple[str, object]] = queue.Queue()
        self.checking = False
        self.action_text = ""

        self.table = table.TaskTable(self, OVERVIEW_COLUMNS, self.menu_for)
        self.table.pack(fill="both", expand=True)
        self.action_buttons = [
            ttk.Button(self.table.toolbar, text="Lab starten", command=self.on_start_lab),
            ttk.Button(self.table.toolbar, text="Lab herunterfahren", command=self.on_stop_lab),
            ttk.Button(self.table.toolbar, text="Starten", command=lambda: self.on_selected("start")),
            ttk.Button(self.table.toolbar, text="Herunterfahren", command=lambda: self.on_selected("shutdown")),
            ttk.Button(self.table.toolbar, text="Aktualisieren", command=self.refresh_now),
        ]
        for b in self.action_buttons:
            b.pack(side="left", padx=(0, 6))
        self.table.set_rows(overview_rows(lab, None))

    # --- status -----------------------------------------------------------------------------
    def start_refresh(self) -> None:
        self.refresh()
        self.after(QUEUE_CHECK_MS, self.drain)

    def refresh(self) -> None:
        self.refresh_now()
        self.after(REFRESH_MS, self.refresh)

    def refresh_now(self) -> None:
        if not self.checking:
            self.checking = True
            threading.Thread(target=self._collect, daemon=True).start()

    def _collect(self) -> None:
        self.results.put(("snapshot", status.collect(self.lab, self.read_vms, self.probe, self.read_cluster,
                                                         self.read_memory)))

    def drain(self) -> None:
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            if kind == "snapshot" and isinstance(payload, status.Snapshot):
                self.checking = False
                self.apply(payload)
            elif kind == "done" and isinstance(payload, str):
                self.set_busy(False)
                self.action_text = payload
                self.refresh_now()
            elif kind == "error" and isinstance(payload, str):
                self.set_busy(False)
                self.action_text = ""
                messagebox.showerror("LabManager", payload)
        self.after(QUEUE_CHECK_MS, self.drain)

    def apply(self, snap: status.Snapshot) -> None:
        self.snap = snap
        self.table.set_rows(overview_rows(self.lab, snap))
        summary = overview_summary(self.lab, snap)
        self.table.status.configure(text=f"{self.action_text}\n{summary}" if self.action_text else summary)

    # --- actions ----------------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        for b in self.action_buttons[:4]:
            b.state(["disabled"] if busy else ["!disabled"])

    def run_action(self, title: str, action: Callable[[], str]) -> None:
        self.set_busy(True)
        self.action_text = f"{title} …"
        self.table.status.configure(text=self.action_text)

        def work() -> None:
            try:
                self.results.put(("done", action()))
            except (hyperv.HyperVError, ssh.SshError, OSError) as e:
                self.results.put(("error", f"{title} fehlgeschlagen:\n\n{e}"))

        threading.Thread(target=work, daemon=True).start()

    def on_start_lab(self) -> None:
        def action() -> str:
            started = status.start_lab(self.lab, self.read_vms, self.start_vm)
            return f"Gestartet: {', '.join(started)}" if started else "Alle VMs liefen bereits."
        self.run_action("Lab starten", action)

    def on_stop_lab(self) -> None:
        if not messagebox.askyesno("LabManager", "Alle laufenden Hyper-V-VMs sauber herunterfahren?\n"
                                   "Proxmox fährt dabei auch seine eigenen VMs herunter."):
            return

        def action() -> str:
            stopped = status.stop_lab(self.lab, self.read_vms, self.stop_vm)
            return f"Heruntergefahren: {', '.join(stopped)}" if stopped else "Es lief keine VM."
        self.run_action("Lab herunterfahren", action)

    def on_selected(self, action: str) -> None:
        iid = self.table.selected()
        if iid is None or not iid.startswith(("vm:", "pve:")):
            messagebox.showinfo("LabManager", "Bitte zuerst eine Hyper-V- oder Proxmox-VM in der Liste auswählen.")
            return
        if iid.startswith("vm:"):
            self.hyperv_action(iid[3:], action)
        else:
            self.proxmox_action(self.proxmox_vm(iid), action)

    def hyperv_action(self, name: str, action: str) -> None:
        if action == "shutdown":
            if not messagebox.askyesno("LabManager", f"{name} sauber herunterfahren?"):
                return

            def stop() -> str:
                self.stop_vm(name)
                return f"{name} heruntergefahren."
            self.run_action(f"{name} herunterfahren", stop)
        else:
            def start() -> str:
                self.start_vm(name)
                return f"{name} gestartet."
            self.run_action(f"{name} starten", start)

    def proxmox_vm(self, iid: str) -> proxmox.Vm:
        assert self.snap is not None and self.snap.cluster is not None
        return next(v for v in self.snap.cluster.vms if f"pve:{v.vmid}" == iid)

    def proxmox_action(self, vm: proxmox.Vm, action: str) -> None:
        word = {"start": "starten", "shutdown": "herunterfahren", "reboot": "neu starten"}[action]
        title = f"{vm.name} (VM {vm.vmid}) {word}"
        if action != "start" and not messagebox.askyesno("LabManager", f"{title}?"):
            return

        def work() -> str:
            proxmox.control(self.lab, vm, action, self.run)
            return f"{title}: an Proxmox übergeben."
        self.run_action(title, work)

    def _open(self, title: str, open_it: Callable[[], None]) -> None:
        try:
            open_it()
        except (hyperv.HyperVError, OSError) as e:
            messagebox.showerror("LabManager", f"{title} fehlgeschlagen:\n\n{e}")

    def menu_for(self, iid: str) -> list[table.MenuEntry]:
        kind, _, key = iid.partition(":")
        if kind == "vm":
            vm = self.snap.vms.get(key) if self.snap else None
            running = vm is not None and vm.state == "Running"
            return [
                table.MenuItem("Herunterfahren" if running else "Starten",
                               lambda: self.hyperv_action(key, "shutdown" if running else "start")),
                None,
                table.MenuItem("Konsole öffnen", lambda: self._open("Konsole", lambda: hyperv.open_console(key))),
                table.MenuItem("Einstellungen öffnen (CPU, RAM …)",
                               lambda: self._open("Einstellungen", lambda: hyperv.open_settings(key))),
            ]
        if kind == "pve":
            pv = self.proxmox_vm(iid)
            entries: list[table.MenuEntry] = (
                [table.MenuItem("Herunterfahren", lambda: self.proxmox_action(pv, "shutdown")),
                 table.MenuItem("Neu starten", lambda: self.proxmox_action(pv, "reboot"))]
                if pv.status == "running" else [table.MenuItem("Starten", lambda: self.proxmox_action(pv, "start"))])
            url = proxmox.hardware_url(self.lab, pv.vmid)
            return [*entries, None, table.MenuItem("Hardware in Proxmox öffnen (CPU, RAM …)",
                                                   lambda: self._open("Proxmox", lambda: connect.open_url(url)))]
        if kind == "host":
            host = self.lab.host(key)
            entries = [table.MenuItem("SSH-Terminal öffnen",
                                      lambda: self._open("SSH", lambda: connect.open_ssh(self.lab, host)))]
            rdp = self.lab.rdp
            if rdp is not None and key == rdp.host:
                entries.append(None)
                entries += [table.MenuItem(f"Remotedesktop: {p.name}",
                                           lambda p=p: self._open("Remotedesktop", lambda: connect.open_rdp(self.lab, p)))
                            for p in rdp.profiles]
            services = [s for s in self.lab.services if s.host == key]
            if services:
                entries.append(None)
            entries += [table.MenuItem(f"Öffnen: {s.label}",
                                       lambda s=s: self._open(s.label, lambda: connect.open_url(s.url)))
                        for s in services]
            return entries
        service = next(s for s in self.lab.services if s.label == key)
        return [table.MenuItem(f"Öffnen: {service.label}",
                               lambda: self._open(service.label, lambda: connect.open_url(service.url)))]


class ConnectTab(ttk.Frame):
    """Buttons for remote desktop profiles, web interfaces and SSH terminals."""

    def __init__(self, parent: ttk.Notebook, lab: Lab) -> None:
        super().__init__(parent, padding=8)
        self.lab = lab
        self.buttons: list[ttk.Button] = []

        if lab.rdp is not None:
            rdp = self._group(f"Remotedesktop zu {lab.host(lab.rdp.host).label}")
            for p in lab.rdp.profiles:
                self._button(rdp, p.name, lambda p=p: connect.open_rdp(self.lab, p))
        if lab.services:
            web = self._group("Web-Oberflächen")
            for s in lab.services:
                self._button(web, s.label, lambda s=s: connect.open_url(s.url))
        if lab.hosts:
            ssh = self._group("SSH-Terminal")
            for h in lab.hosts:
                self._button(ssh, h.label, lambda h=h: connect.open_ssh(self.lab, h))
        if not self.buttons:
            ttk.Label(self, text="In lab.json ist noch nichts zum Verbinden eingerichtet "
                                 "(Remotedesktop, Dienste oder Hosts).").pack(anchor="w")

    def _group(self, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(self, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _button(self, parent: ttk.LabelFrame, text: str, action: Callable[[], None]) -> None:
        def run() -> None:
            try:
                action()
            except OSError as e:
                messagebox.showerror("LabManager", f"„{text}“ konnte nicht geöffnet werden:\n\n{e}")
        button = ttk.Button(parent, text=text, command=run)
        button.pack(side="left", padx=(0, 6), pady=2)
        self.buttons.append(button)


CONTAINER_COLUMNS = (
    table.Column("name", "Container", 190), table.Column("stack", "Stack", 90),
    table.Column("state", "Zustand", 130), table.Column("cpu", "CPU", 70, numeric=True),
    table.Column("memory", "RAM", 90, numeric=True), table.Column("memory_percent", "RAM %", 70, numeric=True),
    table.Column("net", "Netzwerk E/A", 140, numeric=True),
    table.Column("block", "Datenträger E/A", 140, numeric=True),
    table.Column("pids", "Prozesse", 75, numeric=True), table.Column("image", "Image", 220),
    table.Column("ports", "Ports", 110), table.Column("status", "Docker-Status", 180),
)
HEALTH = re.compile(r"\((healthy|unhealthy|health: starting)\)")


def container_row(c: containers.Container, s: containers.Stats | None) -> table.Row:
    """One list row; resource columns stay empty for stopped containers and until the first stats line."""
    health = HEALTH.search(c.status)
    state = CONTAINER_STATE_TEXT.get(c.state, c.state) + (f" ({health.group(1)})" if health else "")
    if c.state == "running" and not (health and health.group(1) == "unhealthy"):
        tag = "ok"
    elif c.state in ("exited", "dead") or health:
        tag = "down"
    else:
        tag = "other"
    live = s if c.state == "running" else None
    ports = ", ".join(str(p) for p in c.ports)
    values = (c.name, c.stack or "–", state,
              f"{live.cpu:.1f} %" if live else "", live.memory_text if live else "",
              f"{live.memory_percent:.1f} %" if live else "", live.net_io if live else "",
              live.block_io if live else "", str(live.pids) if live else "", c.image, ports, c.status)
    keys: tuple[table.SortKey, ...] = (
        c.name, c.stack, state, live.cpu if live else -1.0, float(live.memory) if live else -1.0,
        live.memory_percent if live else -1.0, float(live.net_bytes) if live else -1.0,
        float(live.block_bytes) if live else -1.0, float(live.pids) if live else -1.0,
        c.image, float(c.ports[0]) if c.ports else -1.0, c.status)
    return table.Row(iid=c.name, values=values, keys=keys, tag=tag, group="")


class ContainerTab(ttk.Frame):
    """Docker containers on the lab's Docker host, live like Task Manager's details view.

    While the tab is shown, two SSH streams run: `docker stats` (resource usage) and `docker events`
    (state changes, after which the list is read again). Leaving the tab stops both.
    """

    def __init__(self, parent: ttk.Notebook, lab: Lab, run: ssh.Runner) -> None:
        super().__init__(parent, padding=8)
        self.notebook = parent
        self.lab = lab
        self.run = run
        self.results: queue.Queue[tuple[str, object]] = queue.Queue()
        self.items: dict[str, containers.Container] = {}
        self.stats: dict[str, containers.Stats] = {}
        self.streams: list[ssh.Stream] = []

        self.table = table.TaskTable(self, CONTAINER_COLUMNS, self.menu_for)
        self.table.pack(fill="both", expand=True)
        self.action_buttons = [
            ttk.Button(self.table.toolbar, text="Starten", command=lambda: self.on_action("start")),
            ttk.Button(self.table.toolbar, text="Stoppen", command=lambda: self.on_action("stop")),
            ttk.Button(self.table.toolbar, text="Neu starten", command=lambda: self.on_action("restart")),
            ttk.Button(self.table.toolbar, text="Aktualisieren", command=self.connect),
        ]
        for b in self.action_buttons:
            b.pack(side="left", padx=(0, 6))
        host = lab.host(lab.require_docker().host)
        self.table.status.configure(text=f"Container auf {host.label} ({host.address})")
        parent.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    # --- lifecycle --------------------------------------------------------------------------
    def start(self) -> None:
        self.after(QUEUE_CHECK_MS, self.drain)

    def _on_tab_changed(self, _event: object) -> None:
        if self.notebook.select() == str(self):
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        self.disconnect()
        self.reload()
        try:
            self.streams = [
                ssh.Stream(containers.stats_stream_command(self.lab),
                                  lambda line: self.results.put(("stats", line)), self._stream_ended),
                ssh.Stream(containers.events_stream_command(self.lab),
                                  lambda _line: self.results.put(("event", None)), self._stream_ended),
            ]
        except OSError as e:
            self._stream_ended(str(e))

    def disconnect(self) -> None:
        for s in self.streams:
            s.stop()
        self.streams = []

    def _stream_ended(self, reason: str) -> None:
        self.results.put(("error", (f"Live-Verbindung zur Docker-VM getrennt:\n{reason}\n"
                                    "„Aktualisieren“ startet sie wieder.")))

    def reload(self) -> None:
        def work() -> None:
            try:
                self.results.put(("list", containers.list_containers(self.lab, self.run)))
            except (ssh.SshError, OSError) as e:
                self.results.put(("error", f"Container laden fehlgeschlagen:\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def drain(self) -> None:
        changed = False
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            if kind == "list" and isinstance(payload, list):
                self.items = {c.name: c for c in payload if isinstance(c, containers.Container)}
                changed = True
            elif kind == "stats" and isinstance(payload, str):
                try:
                    s = containers.parse_stats_line(payload)
                except containers.DockerError as e:
                    self.table.status.configure(text=str(e))
                    continue
                if s is not None:
                    self.stats[s.name] = s
                    changed = True
            elif kind == "event":
                self.reload()
            elif kind == "done" and isinstance(payload, str):
                self.set_busy(False)
                self.table.status.configure(text=payload)
                self.reload()
            elif kind == "error" and isinstance(payload, str):
                self.set_busy(False)
                self.table.status.configure(text=payload)
        if changed:
            self.show()
        self.after(QUEUE_CHECK_MS, self.drain)

    def show(self) -> None:
        self.table.set_rows([container_row(c, self.stats.get(c.name)) for c in self.items.values()])
        running = sum(c.state == "running" for c in self.items.values())
        host = self.lab.host(self.lab.require_docker().host)
        self.table.status.configure(
            text=f"{running} von {len(self.items)} Containern laufen · live von {host.label} ({host.address})")

    # --- actions ----------------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        for b in self.action_buttons:
            b.state(["disabled"] if busy else ["!disabled"])

    def on_action(self, action: str, name: str | None = None) -> None:
        target = name or self.table.selected()
        if target is None:
            messagebox.showinfo("LabManager", "Bitte zuerst einen Container in der Liste auswählen.")
            return
        title = f"{target} {CONTAINER_ACTION_TEXT[action]}"
        if action != "start" and not messagebox.askyesno("LabManager", f"{title}?"):
            return
        self.set_busy(True)
        self.table.status.configure(text=f"{title} …")

        def work() -> None:
            try:
                containers.control(self.lab, action, target, self.run)
                self.results.put(("done", f"{title}: erledigt."))
            except (ssh.SshError, OSError) as e:
                self.results.put(("error", f"{title} fehlgeschlagen:\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def _open(self, title: str, open_it: Callable[[], None]) -> None:
        try:
            open_it()
        except (ssh.SshError, OSError) as e:
            messagebox.showerror("LabManager", f"{title} fehlgeschlagen:\n\n{e}")

    def _logs(self, name: str) -> None:
        self._open("Logs öffnen",
                   lambda: ssh.open_in_terminal(f"Logs {name}", containers.logs_command(self.lab, name)))

    def _shell(self, name: str) -> None:
        self._open("Shell öffnen", lambda: ssh.open_in_terminal(name, containers.shell_command(self.lab, name)))

    def menu_for(self, name: str) -> list[table.MenuEntry]:
        c = self.items[name]
        running = c.state == "running"
        entries: list[table.MenuEntry] = []
        if c.state in ("exited", "created", "dead"):
            entries.append(table.MenuItem("Starten", lambda: self.on_action("start", name)))
        if running or c.state == "paused":
            entries.append(table.MenuItem("Stoppen", lambda: self.on_action("stop", name)))
        entries.append(table.MenuItem("Neu starten", lambda: self.on_action("restart", name)))
        if running:
            entries.append(table.MenuItem("Pausieren", lambda: self.on_action("pause", name)))
        if c.state == "paused":
            entries.append(table.MenuItem("Fortsetzen", lambda: self.on_action("unpause", name)))
        entries.append(None)
        entries.append(table.MenuItem("Logs anzeigen", lambda: self._logs(name)))
        if running:
            entries.append(table.MenuItem("Shell öffnen", lambda: self._shell(name)))
        services = containers.web_services(self.lab, c)
        if services:
            entries.append(None)
        for s in services:
            entries.append(table.MenuItem(f"Öffnen: {s.label}",
                                          lambda s=s: self._open(s.label, lambda: connect.open_url(s.url))))
        return entries


BACKUP_COLUMNS = (
    table.Column("name", "Name", 380), table.Column("size", "Größe", 90, numeric=True),
    table.Column("created", "Erstellt", 130, numeric=True), table.Column("age", "Alter", 90, numeric=True),
    table.Column("info", "Info", 420),
)
GROUP_REMOTE, GROUP_LOCAL, GROUP_JOBS = "Auf Proxmox", "Kopien auf Windows", "Backup-Jobs"
# Copy progress: scp reports nothing while running, so the growing .partial file is measured.
PROGRESS_MS = 1000


def date_text(seconds: float) -> str:
    return f"{datetime.fromtimestamp(seconds):%d.%m.%Y %H:%M}"


def age_text(days: float) -> str:
    return f"{days * 24:.0f} Std." if days < 1 else f"{days:.0f} T"


def file_row(group: str, f: backups.BackupFile, now: float, tag: str, info: str) -> table.Row:
    age = backups.age_days(f, now)
    return table.Row(iid=f"{group}:{f.name}", group=group, tag=tag,
                     values=(f.name, backups.gigabytes(f.size), date_text(f.mtime), age_text(age), info),
                     keys=(f.name, float(f.size), f.mtime, age, info))


def backup_rows(lab: Lab, state: backups.BackupState, now: float) -> list[table.Row]:
    b = lab.require_backup()
    local = {f.name: f for f in state.local}
    rows: list[table.Row] = []
    for i, f in enumerate(state.remote):
        copied = f.name in local and local[f.name].size == f.size
        tag = ("ok" if backups.age_days(f, now) <= b.max_age_days else "down") if i == 0 else ""
        info = ("neuestes · " if i == 0 else "") + ("auf Windows kopiert" if copied else "nicht auf Windows")
        rows.append(file_row(GROUP_REMOTE, f, now, tag, info))
    newest_remote = state.remote[0] if state.remote else None
    for i, f in enumerate(state.local):
        tag = ("ok" if backups.age_days(f, now) <= b.copy_max_age_days else "down") if i == 0 else ""
        info = "neueste Kopie" if i == 0 else ""
        if i == 0 and newest_remote is not None and newest_remote.name != f.name:
            info += " · neueres Backup auf Proxmox noch nicht kopiert"
        rows.append(file_row(GROUP_LOCAL, f, now, tag, info))
    for j in state.jobs:
        when = f"nächster Lauf {date_text(j.next_run)}" if j.next_run is not None else "kein nächster Lauf"
        info = " · ".join(x for x in (f"Zeitplan {j.schedule}", j.keep, when, j.comment) if x)
        rows.append(table.Row(iid=f"{GROUP_JOBS}:{j.id}", group=GROUP_JOBS, tag="ok" if j.enabled else "down",
                              values=(j.id, "", "", "", ("aktiv · " if j.enabled else "ausgeschaltet · ") + info),
                              keys=(j.id, NONE, j.next_run or NONE, NONE, info)))
    return rows


def backup_summary(lab: Lab, state: backups.BackupState, now: float) -> str:
    parts = []
    if state.remote:
        parts.append(f"Neuestes Backup vor {age_text(backups.age_days(state.remote[0], now))}")
    if state.local:
        parts.append(f"neueste Kopie vor {age_text(backups.age_days(state.local[0], now))}")
    free = f", frei {backups.gigabytes(state.free_bytes)}" if state.free_bytes is not None else ""
    parts.append(f"Ziel {lab.require_backup().target_dir}{free}")
    lines = [" · ".join(parts)]
    if state.last_log:
        lines.append(f"Letzter Kopierlauf: {state.last_log}")
    if state.remote_error:
        lines.append(f"Proxmox nicht abfragbar: {state.remote_error}")
    if state.local_error:
        lines.append(state.local_error)
    lines += [f"⚠ {w}" for w in backups.warnings(lab, state, now)]
    return "\n".join(lines)


class BackupTab(ttk.Frame):
    """Proxmox backups and their Windows copies; loaded when the tab is opened and after each action."""

    def __init__(self, parent: ttk.Notebook, lab: Lab, run: ssh.Runner, copy: backups.Copier) -> None:
        super().__init__(parent, padding=8)
        self.notebook = parent
        self.lab = lab
        self.run = run
        self.copy = copy
        self.state: backups.BackupState | None = None
        self.results: queue.Queue[tuple[str, object]] = queue.Queue()
        self.copying: str | None = None  # name of the backup being copied
        self.copy_size = 0

        self.table = table.TaskTable(self, BACKUP_COLUMNS, self.menu_for)
        self.table.pack(fill="both", expand=True)
        self.copy_button = ttk.Button(self.table.toolbar, text="Jetzt kopieren", command=self.on_copy)
        self.copy_button.pack(side="left", padx=(0, 6))
        for text, command in (("Zielordner öffnen", self.open_folder), ("Protokoll öffnen", self.open_log),
                              ("Aktualisieren", self.reload)):
            ttk.Button(self.table.toolbar, text=text, command=command).pack(side="left", padx=(0, 6))
        self.table.status.configure(text=f"Backups von {lab.host(lab.require_backup().host).label} → {lab.require_backup().target_dir}")
        parent.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    def start(self) -> None:
        self.after(QUEUE_CHECK_MS, self.drain)

    def _on_tab_changed(self, _event: object) -> None:
        if self.notebook.select() == str(self):
            self.reload()

    def reload(self) -> None:
        threading.Thread(target=lambda: self.results.put(("state", backups.read_state(self.lab, self.run))),
                         daemon=True).start()

    def drain(self) -> None:
        while not self.results.empty():
            kind, payload = self.results.get_nowait()
            if kind == "state" and isinstance(payload, backups.BackupState):
                self.apply(payload)
            elif kind == "done" and isinstance(payload, list):
                self.copying = None
                self.copy_button.state(["!disabled"])
                messagebox.showinfo("LabManager", "\n".join(str(m) for m in payload))
                self.reload()
            elif kind == "error" and isinstance(payload, str):
                self.copying = None
                self.copy_button.state(["!disabled"])
                messagebox.showerror("LabManager", payload)
                self.reload()
        self.after(QUEUE_CHECK_MS, self.drain)

    def apply(self, state: backups.BackupState) -> None:
        self.state = state
        now = time.time()
        self.table.set_rows(backup_rows(self.lab, state, now))
        if self.copying is None:
            self.table.status.configure(text=backup_summary(self.lab, state, now))

    def on_copy(self) -> None:
        if self.state is None or not self.state.remote:
            messagebox.showinfo("LabManager", "Es ist noch kein Backup auf Proxmox bekannt. Bitte „Aktualisieren“.")
            return
        newest = self.state.remote[0]
        self.copying, self.copy_size = newest.name, newest.size
        self.copy_button.state(["disabled"])
        self.show_progress()

        def work() -> None:
            try:
                self.results.put(("done", backups.copy_newest(self.lab, self.run, self.copy)))
            except (ssh.SshError, OSError) as e:
                self.results.put(("error", f"Kopieren fehlgeschlagen:\n\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def show_progress(self) -> None:
        if self.copying is None:
            return
        partial = backups.partial_path(self.lab, self.copying)
        done = partial.stat().st_size if partial.is_file() else 0
        share = done / self.copy_size if self.copy_size else 0.0
        self.table.status.configure(text=f"Kopiere {self.copying} … {share:.0%} "
                                         f"({backups.gigabytes(done)} von {backups.gigabytes(self.copy_size)})")
        self.after(PROGRESS_MS, self.show_progress)

    def _open(self, title: str, open_it: Callable[[], None]) -> None:
        try:
            open_it()
        except OSError as e:
            messagebox.showerror("LabManager", f"{title} fehlgeschlagen:\n\n{e}")

    def open_folder(self) -> None:
        self._open("Zielordner öffnen", lambda: connect.open_unelevated(str(self.lab.require_backup().target_dir)))

    def open_log(self) -> None:
        log = self.lab.require_backup().target_dir / backups.LOG_NAME
        if not log.is_file():
            messagebox.showinfo("LabManager", f"Noch kein Protokoll vorhanden:\n{log}")
            return
        self._open("Protokoll öffnen", lambda: connect.open_unelevated(str(log)))

    def menu_for(self, iid: str) -> list[table.MenuEntry]:
        group, _, name = iid.partition(":")
        if group == GROUP_LOCAL:
            path = self.lab.require_backup().target_dir / name
            return [table.MenuItem("Im Explorer zeigen", lambda: self._open("Explorer", lambda: connect.show_in_explorer(path)))]
        url = proxmox.backup_jobs_url(self.lab)
        entries: list[table.MenuEntry] = []
        if group == GROUP_REMOTE and self.state is not None and self.state.remote and self.state.remote[0].name == name:
            entries += [table.MenuItem("Jetzt auf Windows kopieren", self.on_copy), None]
        entries.append(table.MenuItem("Backup-Jobs in Proxmox öffnen",
                                      lambda: self._open("Proxmox", lambda: connect.open_url(url))))
        return entries


@dataclass
class Tabs:
    notebook: ttk.Notebook
    overview: OverviewTab
    containers: ContainerTab | None  # only when lab.json sets up Docker
    backups: BackupTab | None  # only when lab.json sets up backups


def build_tabs(root: tk.Misc, lab: Lab) -> Tabs:
    notebook = ttk.Notebook(root)
    overview = OverviewTab(notebook, lab, hyperv.vm_info, probes.port_open, lambda: proxmox.read(lab, ssh.run),
                           lambda host: memory.read(lab, host, ssh.run), hyperv.start_vm, hyperv.stop_vm, ssh.run)
    notebook.add(overview, text="Übersicht")
    notebook.add(ConnectTab(notebook, lab), text="Verbinden")
    container_tab = ContainerTab(notebook, lab, ssh.run) if lab.docker is not None else None
    if container_tab is not None:
        notebook.add(container_tab, text="Container")
    backup_tab = (BackupTab(notebook, lab, ssh.run, lambda cmd: ssh.run_for(cmd, backups.COPY_TIMEOUT_S))
                  if lab.backup is not None else None)
    if backup_tab is not None:
        notebook.add(backup_tab, text="Backups")
    return Tabs(notebook=notebook, overview=overview, containers=container_tab, backups=backup_tab)


def show_welcome(root: tk.Tk, config_path: Path, on_created: Callable[[], None]) -> None:
    """Shown instead of the tabs while the user has no lab.json yet."""
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Willkommen bei LabManager", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(frame, justify="left", wraplength=900, text=(
        "Für dieses Lab gibt es noch keine Konfiguration.\n\n"
        "Leg eine leere Konfiguration an und trage dann im Reiter „Einstellungen“ deine VMs, Hosts "
        "und Dienste ein.\n\n"
        f"Gespeichert wird sie hier:\n{config_path}")).pack(anchor="w", pady=(12, 12))
    error = ttk.Label(frame, text="", foreground="#c62828", wraplength=900, justify="left")

    def create() -> None:
        try:
            settings.save_raw(config_path, settings.EMPTY_LAB)
        except (settings.SettingsError, OSError) as e:
            error.configure(text=f"Anlegen fehlgeschlagen: {e}")
            return
        on_created()

    def open_folder() -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        connect.open_unelevated(str(config_path.parent))
    buttons = ttk.Frame(frame)
    buttons.pack(anchor="w")
    ttk.Button(buttons, text="Neue Konfiguration anlegen", command=create).pack(side="left", padx=(0, 6))
    ttk.Button(buttons, text="Ordner öffnen", command=open_folder).pack(side="left", padx=(0, 6))
    ttk.Button(buttons, text="Beenden", command=root.destroy).pack(side="left")
    error.pack(anchor="w", pady=(10, 0))


def restart_command() -> list[str]:
    """Command that starts this LabManager again: the .exe itself, or the interpreter with the script."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(sys.argv[0]).resolve())]


def main(config_path: Path) -> None:
    root = tk.Tk()
    root.title("LabManager")
    root.geometry("1280x640")
    tabs: Tabs | None = None

    def restart() -> None:
        # A fresh process instead of rebuilding the tabs: refresh timers and ssh streams start clean.
        # It inherits LabManager's administrator rights, so there is no second UAC prompt.
        if tabs is not None and tabs.containers is not None:
            tabs.containers.disconnect()
        subprocess.Popen(restart_command())
        root.destroy()

    if not config_path.is_file():
        show_welcome(root, config_path, restart)
        root.mainloop()
        return
    try:
        lab = load(config_path)
    except ConfigError as e:
        root.withdraw()
        messagebox.showerror("LabManager", f"Konfiguration fehlerhaft:\n\n{e}\n\nDatei: {config_path}")
        root.destroy()
        sys.exit(1)

    tabs = build_tabs(root, lab)
    settings_tab = settings_gui.SettingsTab(tabs.notebook, settings.load_raw(config_path), config_path,
                                            on_saved=restart, screens=monitors.current())
    tabs.notebook.add(settings_tab, text="Einstellungen")
    if not (lab.vms or lab.hosts or lab.services):
        tabs.notebook.select(settings_tab)
    tabs.notebook.pack(fill="both", expand=True)
    tabs.overview.start_refresh()
    settings_tab.start()
    if tabs.containers is not None:
        tabs.containers.start()
    if tabs.backups is not None:
        tabs.backups.start()

    def close() -> None:
        if settings_tab.has_unsaved_changes() and not messagebox.askyesno(
                "LabManager", "Es gibt ungespeicherte Änderungen in den Einstellungen. Trotzdem schließen?"):
            return
        # Child processes outlive their parent on Windows; the ssh streams must be ended explicitly.
        if tabs is not None and tabs.containers is not None:
            tabs.containers.disconnect()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()
