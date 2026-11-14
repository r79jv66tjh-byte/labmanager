"""Collects one status snapshot of the whole lab, derives warnings and runs lab-wide start/stop."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from labmanager import hyperv, memory, proxmox, ssh
from labmanager.config import Host, Lab

SSH_PORT = 22
PROBE_TIMEOUT = 1.5


@dataclass(frozen=True)
class Snapshot:
    vms: dict[str, hyperv.VmInfo]
    vm_error: str | None
    vm_no_admin: bool
    host_up: dict[str, bool]
    service_up: dict[str, bool]
    cluster: proxmox.Cluster | None
    cluster_error: str | None
    host_memory: dict[str, memory.Memory]  # measured inside each reachable host
    memory_errors: dict[str, str]


VmReader = Callable[[list[str]], dict[str, hyperv.VmInfo]]
ClusterReader = Callable[[], proxmox.Cluster]
MemoryReader = Callable[[Host], memory.Memory]
PortProbe = Callable[[str, int, float], bool]


def collect(lab: Lab, read_vms: VmReader, probe: PortProbe, read_cluster: ClusterReader,
            read_memory: MemoryReader) -> Snapshot:
    """Queries Hyper-V, every host/service port, Proxmox and the memory of reachable hosts in parallel."""
    vm_error: str | None = None
    no_admin = False
    cluster: proxmox.Cluster | None = None
    cluster_error: str | None = None
    with ThreadPoolExecutor(max_workers=8) as pool:
        hosts = {h.id: pool.submit(probe, h.address, SSH_PORT, PROBE_TIMEOUT) for h in lab.hosts}
        services = {s.label: pool.submit(probe, lab.host(s.host).address, s.port, PROBE_TIMEOUT)
                    for s in lab.services}
        try:
            vms = read_vms([v.name for v in lab.vms])
        except hyperv.NotAdminError as e:
            vms, vm_error, no_admin = {}, str(e), True
        except hyperv.HyperVError as e:
            vms, vm_error = {}, str(e)
        host_up = {k: f.result() for k, f in hosts.items()}
        memories = {h.id: pool.submit(read_memory, h) for h in lab.hosts if host_up[h.id]}
        if lab.proxmox is None:
            pass  # no Proxmox in this lab: no cluster and nothing to report
        elif host_up[lab.proxmox.host]:
            try:
                cluster = read_cluster()
            except ssh.SshError as e:
                cluster_error = str(e)
        else:
            cluster_error = f"{lab.host(lab.proxmox.host).label} ist nicht erreichbar."
        host_memory: dict[str, memory.Memory] = {}
        memory_errors: dict[str, str] = {}
        for host_id, future in memories.items():
            try:
                host_memory[host_id] = future.result()
            except ssh.SshError as e:
                memory_errors[host_id] = str(e)
        return Snapshot(vms=vms, vm_error=vm_error, vm_no_admin=no_admin, host_up=host_up,
                        service_up={k: f.result() for k, f in services.items()},
                        cluster=cluster, cluster_error=cluster_error,
                        host_memory=host_memory, memory_errors=memory_errors)


def gigabytes(size: int) -> str:
    return f"{size / 2**30:.1f} GB".replace(".", ",")


def share(used: int, total: int) -> float:
    return used / total if total else 0.0


def warnings(lab: Lab, snap: Snapshot) -> list[str]:
    """Resource problems worth acting on, in plain words.

    Memory is judged only by what each host measures itself; see labmanager.memory for why the
    numbers Hyper-V and Proxmox report from outside are not used for warnings.
    """
    found: list[str] = []
    for h in lab.hosts:
        m = snap.host_memory.get(h.id)
        if m is not None and m.low:
            found.append(f"{h.label}: nur noch {gigabytes(m.available)} von {gigabytes(m.total)} RAM verfügbar.")
    if snap.cluster is not None and snap.cluster.assigned_memory > snap.cluster.node_memory:
        found.append(f"Proxmox weist seinen VMs {gigabytes(snap.cluster.assigned_memory)} RAM zu, "
                     f"hat aber nur {gigabytes(snap.cluster.node_memory)}.")
    return found


def start_lab(lab: Lab, read_vms: VmReader, start: Callable[[str], None]) -> list[str]:
    """Starts every configured VM that is not running, in lab.json order; returns the started names."""
    vms = read_vms([v.name for v in lab.vms])
    started: list[str] = []
    for vm in lab.vms:
        if vm.name not in vms or vms[vm.name].state != "Running":
            start(vm.name)
            started.append(vm.name)
    return started


def stop_lab(lab: Lab, read_vms: VmReader, stop: Callable[[str], None]) -> list[str]:
    """Shuts running VMs down cleanly in reverse lab.json order; returns the stopped names."""
    vms = read_vms([v.name for v in lab.vms])
    stopped: list[str] = []
    for vm in reversed(lab.vms):
        if vm.name in vms and vms[vm.name].state == "Running":
            stop(vm.name)
            stopped.append(vm.name)
    return stopped
