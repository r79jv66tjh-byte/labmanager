"""Tests for Hyper-V output parsing, port probes, the lab snapshot, warnings and lab-wide start/stop.

Hyper-V and Proxmox are replaced by fakes; port probes run against a real local socket.
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import dataclasses
import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import hyperv, memory, probes, proxmox, ssh, status
from labmanager.config import Host, Lab, load

LAB: Lab = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")
GB = 2**30


def vm(name: str, state: str) -> hyperv.VmInfo:
    return hyperv.VmInfo(name=name, state=state, cpu_percent=3, memory_assigned=8 * GB,
                         processors=4, uptime_s=3600, generation=2, dynamic_memory=False)


# Shortened real Get-VM output (elevated) from the lab PC.
GET_VM = json.dumps([
    {"Name": "Linux-Desktop", "State": "Running", "CPUUsage": 0, "MemoryAssigned": 8589934592,
     "MemoryDemand": 7644119040, "ProcessorCount": 4, "UptimeSeconds": 21573, "Generation": 2,
     "DynamicMemoryEnabled": True},
    {"Name": "Proxmox-VM", "State": "Off", "CPUUsage": 0, "MemoryAssigned": 0, "MemoryDemand": 0,
     "ProcessorCount": 4, "UptimeSeconds": 0, "Generation": 1, "DynamicMemoryEnabled": False},
])


class ParseVmsTest(unittest.TestCase):
    def test_list_of_vms(self) -> None:
        vms = hyperv.parse_vms(GET_VM)
        self.assertEqual(vms["Linux-Desktop"].memory_assigned, 8589934592)
        self.assertTrue(vms["Linux-Desktop"].dynamic_memory)
        self.assertEqual((vms["Proxmox-VM"].state, vms["Proxmox-VM"].generation), ("Off", 1))

    def test_single_vm_object(self) -> None:
        single = json.dumps(json.loads(GET_VM)[0])
        self.assertEqual(list(hyperv.parse_vms(single)), ["Linux-Desktop"])

    def test_empty_output_means_no_vms(self) -> None:
        self.assertEqual(hyperv.parse_vms("  \r\n"), {})

    def test_unexpected_shape_is_an_error(self) -> None:
        broken = json.loads(GET_VM)[0] | {"CPUUsage": "viel"}
        with self.assertRaises(hyperv.HyperVError):
            hyperv.parse_vms(json.dumps(broken))


class PortOpenTest(unittest.TestCase):
    def test_listening_port_is_open(self) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.assertTrue(probes.port_open("127.0.0.1", server.getsockname()[1], 1.0))

    def test_closed_port_is_reported_closed(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertFalse(probes.port_open("127.0.0.1", port, 0.5))

    def test_invalid_address_is_an_error_not_a_closed_port(self) -> None:
        with self.assertRaises(OSError):
            probes.port_open("kein.gueltiger.host.invalid", 22, 0.5)


class AdminCheckTest(unittest.TestCase):
    def test_vm_info_without_admin_rights_raises_its_own_error(self) -> None:
        original = hyperv.is_admin
        hyperv.is_admin = lambda: False
        try:
            with self.assertRaisesRegex(hyperv.NotAdminError, "Adminrechte"):
                hyperv.vm_info(["Proxmox-VM"])
        finally:
            hyperv.is_admin = original

    def test_console_and_settings_without_admin_rights_raise_instead_of_opening(self) -> None:
        original = hyperv.is_admin
        hyperv.is_admin = lambda: False
        try:
            for open_it in (hyperv.open_console, hyperv.open_settings):
                with self.subTest(open_it.__name__), self.assertRaisesRegex(hyperv.NotAdminError, "Adminrechte"):
                    open_it("Linux-Desktop")
        finally:
            hyperv.is_admin = original


def fake_vms(vms: dict[str, hyperv.VmInfo]) -> status.VmReader:
    def read(names: list[str]) -> dict[str, hyperv.VmInfo]:
        return {n: vms[n] for n in names if n in vms}
    return read


CLUSTER = proxmox.Cluster(
    nodes=(proxmox.Node("pve", "online", 0.02, 4, 16 * GB, 100),),
    vms=(proxmox.Vm(100, "docker", "pve", "running", 0.02, 2, 4 * GB, 100),),
)


def no_cluster() -> proxmox.Cluster:
    raise AssertionError("Proxmox must not be queried")


MEM = memory.Memory(total=4 * GB, available=3 * GB)


def mem_ok(host: Host) -> memory.Memory:
    return MEM


def no_memory(host: Host) -> memory.Memory:
    raise AssertionError("memory must not be read from an unreachable host")


class CollectTest(unittest.TestCase):
    def test_snapshot_combines_vms_hosts_services_and_proxmox(self) -> None:
        up_ports = {("192.0.2.10", 22), ("192.0.2.10", 8006)}
        read: list[str] = []

        def read_memory(host: Host) -> memory.Memory:
            read.append(host.id)
            return MEM

        snap = status.collect(LAB, fake_vms({"Proxmox-VM": vm("Proxmox-VM", "Running")}),
                              lambda a, p, _t: (a, p) in up_ports, lambda: CLUSTER, read_memory)
        self.assertEqual(list(snap.vms), ["Proxmox-VM"])
        self.assertIsNone(snap.vm_error)
        self.assertEqual(snap.host_up, {"proxmox": True, "desktop": False, "docker": False})
        self.assertTrue(snap.service_up["Proxmox Web-UI"])
        self.assertIs(snap.cluster, CLUSTER)
        self.assertEqual((read, snap.host_memory, snap.memory_errors), (["proxmox"], {"proxmox": MEM}, {}))

    def test_unreachable_proxmox_is_not_queried(self) -> None:
        snap = status.collect(LAB, fake_vms({}), lambda a, p, _t: False, no_cluster, no_memory)
        self.assertIsNone(snap.cluster)
        self.assertEqual(snap.cluster_error, "Proxmox VE ist nicht erreichbar.")

    def test_proxmox_error_is_kept(self) -> None:
        def failing() -> proxmox.Cluster:
            raise ssh.SshError("Permission denied")
        snap = status.collect(LAB, fake_vms({}), lambda a, p, _t: True, failing, mem_ok)
        self.assertEqual(snap.cluster_error, "Permission denied")

    def test_missing_admin_rights_are_marked(self) -> None:
        def no_admin(names: list[str]) -> dict[str, hyperv.VmInfo]:
            raise hyperv.NotAdminError(hyperv.NO_ADMIN)
        snap = status.collect(LAB, no_admin, lambda a, p, _t: True, lambda: CLUSTER, mem_ok)
        self.assertTrue(snap.vm_no_admin)
        self.assertTrue(all(snap.host_up.values()))

    def test_other_hyperv_error_is_kept(self) -> None:
        def failing(names: list[str]) -> dict[str, hyperv.VmInfo]:
            raise hyperv.HyperVError("Zugriff verweigert")
        snap = status.collect(LAB, failing, lambda a, p, _t: True, lambda: CLUSTER, mem_ok)
        self.assertEqual((snap.vm_error, snap.vm_no_admin), ("Zugriff verweigert", False))

    def test_lab_without_proxmox_is_not_asked(self) -> None:
        lab = dataclasses.replace(LAB, proxmox=None)
        snap = status.collect(lab, fake_vms({}), lambda a, p, _t: True, no_cluster, mem_ok)
        self.assertEqual((snap.cluster, snap.cluster_error), (None, None))

    def test_memory_error_is_kept_per_host(self) -> None:
        def flaky(host: Host) -> memory.Memory:
            if host.id == "docker":
                raise ssh.SshError("Connection reset")
            return MEM

        snap = status.collect(LAB, fake_vms({}), lambda a, p, _t: True, lambda: CLUSTER, flaky)
        self.assertEqual(snap.memory_errors, {"docker": "Connection reset"})
        self.assertEqual(set(snap.host_memory), {"proxmox", "desktop"})


def snapshot(vms: dict[str, hyperv.VmInfo], cluster: proxmox.Cluster | None,
             host_memory: dict[str, memory.Memory]) -> status.Snapshot:
    return status.Snapshot(vms=vms, vm_error=None, vm_no_admin=False,
                           host_up={h.id: True for h in LAB.hosts},
                           service_up={s.label: True for s in LAB.services},
                           cluster=cluster, cluster_error=None, host_memory=host_memory, memory_errors={})


class WarningsTest(unittest.TestCase):
    def test_host_with_little_available_memory_is_reported(self) -> None:
        short = {"docker": memory.Memory(total=4 * GB, available=int(0.3 * GB))}
        self.assertEqual(status.warnings(LAB, snapshot({}, CLUSTER, short)),
                         ["docker (VM 100): nur noch 0,3 GB von 4,0 GB RAM verfügbar."])

    def test_numbers_from_outside_the_guest_do_not_warn(self) -> None:
        # Real case: Hyper-V reported a demand of 10,1 of 8 GB (committed memory) and Proxmox 3,7 of 4 GB
        # (page cache), while inside the guests 9,6 and 2,5 GB were available. Only the inside counts.
        busy_outside = {"Linux-Desktop": vm("Linux-Desktop", "Running")}
        inside = {"desktop": memory.Memory(total=12 * GB, available=int(9.6 * GB)),
                  "docker": memory.Memory(total=4 * GB, available=int(2.5 * GB))}
        self.assertEqual(status.warnings(LAB, snapshot(busy_outside, CLUSTER, inside)), [])

    def test_overcommitted_proxmox_is_reported(self) -> None:
        big = proxmox.Cluster(nodes=CLUSTER.nodes,
                              vms=(proxmox.Vm(100, "a", "pve", "stopped", 0, 2, 12 * GB, 0),
                                   proxmox.Vm(101, "b", "pve", "stopped", 0, 2, 8 * GB, 0)))
        self.assertEqual(status.warnings(LAB, snapshot({}, big, {})),
                         ["Proxmox weist seinen VMs 20,0 GB RAM zu, hat aber nur 16,0 GB."])


class StartStopTest(unittest.TestCase):
    def test_start_lab_starts_only_stopped_vms_in_config_order(self) -> None:
        calls: list[str] = []
        started = status.start_lab(LAB, fake_vms({"Proxmox-VM": vm("Proxmox-VM", "Off"),
                                                  "Linux-Desktop": vm("Linux-Desktop", "Saved")}), calls.append)
        self.assertEqual(started, ["Proxmox-VM", "Linux-Desktop"])
        self.assertEqual(calls, ["Proxmox-VM", "Linux-Desktop"])

    def test_start_lab_skips_running_vms(self) -> None:
        calls: list[str] = []
        status.start_lab(LAB, fake_vms({"Proxmox-VM": vm("Proxmox-VM", "Running"),
                                        "Linux-Desktop": vm("Linux-Desktop", "Off")}), calls.append)
        self.assertEqual(calls, ["Linux-Desktop"])

    def test_stop_lab_stops_running_vms_in_reverse_order(self) -> None:
        calls: list[str] = []
        stopped = status.stop_lab(LAB, fake_vms({"Proxmox-VM": vm("Proxmox-VM", "Running"),
                                                 "Linux-Desktop": vm("Linux-Desktop", "Running")}), calls.append)
        self.assertEqual(stopped, ["Linux-Desktop", "Proxmox-VM"])
        self.assertEqual(calls, ["Linux-Desktop", "Proxmox-VM"])


if __name__ == "__main__":
    unittest.main()
