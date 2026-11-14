"""Tests for reading and controlling Proxmox VMs.

The sample is shortened real output of `pvesh get /cluster/resources --output-format json`.
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import proxmox
from labmanager.config import load

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")

RESOURCES = json.dumps([
    {"cpu": 0.0203, "id": "qemu/100", "maxcpu": 2, "maxmem": 4294967296, "mem": 3947687936, "name": "docker",
     "node": "pve", "status": "running", "template": 0, "type": "qemu", "uptime": 21254, "vmid": 100},
    {"cpu": 0.0149, "id": "node/pve", "maxcpu": 4, "maxmem": 16768610304, "mem": 6051315712, "node": "pve",
     "status": "online", "type": "node", "uptime": 21566},
    {"id": "storage/pve/local", "node": "pve", "status": "available", "type": "storage"},
    {"cpu": 0, "id": "qemu/9000", "maxcpu": 2, "maxmem": 2147483648, "mem": 0, "name": "vorlage", "node": "pve",
     "status": "stopped", "template": 1, "type": "qemu", "uptime": 0, "vmid": 9000},
])


def unused(cmd: list[str]) -> str:
    raise AssertionError("must not run")


class ParseTest(unittest.TestCase):
    def test_node_and_vms_without_storage_and_templates(self) -> None:
        cluster = proxmox.parse_resources(RESOURCES)
        self.assertEqual([n.name for n in cluster.nodes], ["pve"])
        self.assertEqual([(v.vmid, v.name, v.cores, v.memory_max) for v in cluster.vms],
                         [(100, "docker", 2, 4294967296)])
        self.assertEqual((cluster.assigned_memory, cluster.node_memory), (4294967296, 16768610304))

    def test_unexpected_output_is_an_error(self) -> None:
        missing = json.dumps([{"id": "qemu/1", "type": "qemu", "template": 0, "vmid": 1}])
        for bad in ("kaputt", "{}", missing):
            with self.subTest(output=bad), self.assertRaises(proxmox.ProxmoxError):
                proxmox.parse_resources(bad)


class CommandTest(unittest.TestCase):
    def test_read_uses_pvesh_as_the_proxmox_ssh_user(self) -> None:
        calls: list[list[str]] = []

        def run(cmd: list[str]) -> str:
            calls.append(cmd)
            return RESOURCES

        self.assertEqual(len(proxmox.read(LAB, run).vms), 1)
        self.assertIn("root@192.0.2.10", calls[0])
        self.assertEqual(calls[0][-1], "pvesh get /cluster/resources --output-format json")

    def test_control_calls_the_status_endpoint(self) -> None:
        calls: list[list[str]] = []
        vm = proxmox.parse_resources(RESOURCES).vms[0]
        proxmox.control(LAB, vm, "shutdown", lambda cmd: calls.append(cmd) or "")
        self.assertEqual(calls[0][-1], "pvesh create /nodes/pve/qemu/100/status/shutdown")

    def test_unknown_action_and_unsafe_node_are_rejected(self) -> None:
        vm = proxmox.parse_resources(RESOURCES).vms[0]
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.control(LAB, vm, "destroy", unused)
        bad = proxmox.Vm(1, "x", "pve;reboot", "running", 0, 1, 1, 0)
        with self.assertRaises(proxmox.ProxmoxError):
            proxmox.control(LAB, bad, "start", unused)

    def test_hardware_link_points_at_the_vm(self) -> None:
        self.assertEqual(proxmox.hardware_url(LAB, 100), "https://192.0.2.10:8006/#v1:0:=qemu%2F100:4:::::7::")

    def test_backup_jobs_link_points_at_the_datacenter_backup_panel(self) -> None:
        self.assertEqual(proxmox.backup_jobs_url(LAB), "https://192.0.2.10:8006/#v1:0:18:4:::::::21")


if __name__ == "__main__":
    unittest.main()
