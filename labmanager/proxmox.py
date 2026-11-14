"""Proxmox VE node and VMs, read and controlled over SSH with Proxmox' own CLI (`pvesh`).

LabManager only shows resource assignments; changing cores or memory happens in Proxmox' web
interface, which LabManager opens directly on the VM's hardware page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from labmanager import ssh
from labmanager.config import Lab

ACTIONS = ("start", "shutdown", "reboot")
NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
# The web interface keeps its view in the URL hash (PVE.StateProvider in pvemanagerlib.js):
# view, resource id, log tab, then the tab of the node, storage, sdn, pool, qemu, lxc and datacenter panels.
# Known words are replaced by numbers: server=0, root=18, tasks=4, hardware=7, backup=21; other values
# are written as "=" plus the URL-encoded text.
VIEW_SERVER, RID_ROOT, LOG_TASKS, QEMU_HARDWARE, DC_BACKUP = "0", "18", "4", "7", "21"


class ProxmoxError(ssh.SshError):
    """Proxmox returned unexpected output or was asked for something invalid."""


@dataclass(frozen=True)
class Node:
    name: str
    status: str  # "online", "offline"
    cpu: float  # fraction of all cores, 0..1
    cores: int
    memory_max: int
    uptime_s: int


@dataclass(frozen=True)
class Vm:
    vmid: int
    name: str
    node: str
    status: str  # "running", "stopped", "paused"
    cpu: float  # fraction of the VM's cores, 0..1
    cores: int
    memory_max: int  # bytes assigned
    uptime_s: int


@dataclass(frozen=True)
class Cluster:
    nodes: tuple[Node, ...]
    vms: tuple[Vm, ...]

    @property
    def assigned_memory(self) -> int:
        return sum(v.memory_max for v in self.vms)

    @property
    def node_memory(self) -> int:
        return sum(n.memory_max for n in self.nodes)


def _number(record: dict[str, object], key: str) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProxmoxError(f"Proxmox-Antwort ohne Zahl '{key}': {record.get('id')}")
    return float(value)


def _text(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ProxmoxError(f"Proxmox-Antwort ohne Text '{key}': {record.get('id')}")
    return value


def parse_resources(output: str) -> Cluster:
    """`pvesh get /cluster/resources --output-format json`; storage and network entries are not shown."""
    try:
        data: object = json.loads(output)
    except json.JSONDecodeError as e:
        raise ProxmoxError(f"Unerwartete Antwort von Proxmox: {output[:120]}") from e
    if not isinstance(data, list):
        raise ProxmoxError(f"Unerwartete Antwort von Proxmox: {output[:120]}")
    nodes: list[Node] = []
    vms: list[Vm] = []
    for item in data:
        if not isinstance(item, dict):
            raise ProxmoxError(f"Unerwartete Antwort von Proxmox: {item!r}")
        r = {str(k): v for k, v in item.items()}
        kind = _text(r, "type")
        if kind == "node":
            nodes.append(Node(name=_text(r, "node"), status=_text(r, "status"), cpu=_number(r, "cpu"),
                              cores=int(_number(r, "maxcpu")),
                              memory_max=int(_number(r, "maxmem")), uptime_s=int(_number(r, "uptime"))))
        elif kind == "qemu" and not _number(r, "template"):
            vms.append(Vm(vmid=int(_number(r, "vmid")), name=_text(r, "name"), node=_text(r, "node"),
                          status=_text(r, "status"), cpu=_number(r, "cpu"), cores=int(_number(r, "maxcpu")),
                          memory_max=int(_number(r, "maxmem")),
                          uptime_s=int(_number(r, "uptime"))))
    return Cluster(nodes=tuple(nodes), vms=tuple(sorted(vms, key=lambda v: v.vmid)))


def pvesh(lab: Lab, *args: str) -> list[str]:
    return ssh.command(lab, lab.host(lab.require_proxmox().host), "pvesh", *args)


def read(lab: Lab, run: ssh.Runner) -> Cluster:
    return parse_resources(run(pvesh(lab, "get", "/cluster/resources", "--output-format", "json")))


def control(lab: Lab, vm: Vm, action: str, run: ssh.Runner) -> None:
    """Starts, cleanly shuts down or reboots a VM through the Proxmox API."""
    if action not in ACTIONS:
        raise ProxmoxError(f"Unbekannte Aktion: {action}")
    if not NODE_NAME.fullmatch(vm.node):
        raise ProxmoxError(f"Ungültiger Knotenname: {vm.node!r}")
    run(pvesh(lab, "create", f"/nodes/{vm.node}/qemu/{vm.vmid}/status/{action}"))


def web_url(lab: Lab, rid: str, qemu_tab: str, dc_tab: str) -> str:
    fields = [VIEW_SERVER, rid, LOG_TASKS, "", "", "", "", qemu_tab, "", dc_tab]
    return lab.require_proxmox().web.url.rstrip("/") + "/#v1:" + ":".join(fields)


def hardware_url(lab: Lab, vmid: int) -> str:
    return web_url(lab, f"=qemu%2F{vmid}", QEMU_HARDWARE, "")


def backup_jobs_url(lab: Lab) -> str:
    return web_url(lab, RID_ROOT, "", DC_BACKUP)
