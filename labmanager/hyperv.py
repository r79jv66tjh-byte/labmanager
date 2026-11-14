"""Hyper-V access through PowerShell (Get-VM, Start-VM, Stop-VM) and vmconnect.

Start and stop need administrator rights; PowerShell's own error text is passed on unchanged so
the user sees why an action failed.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
from dataclasses import dataclass

CREATE_NO_WINDOW = 0x08000000
NO_ADMIN = "LabManager läuft ohne Adminrechte. Hyper-V meldet dann keine VMs; bitte LabManager.exe verwenden oder als Administrator starten."


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


class HyperVError(Exception):
    """A Hyper-V command failed; the message is PowerShell's error output."""


class NotAdminError(HyperVError):
    """LabManager runs without administrator rights, so Hyper-V cannot be queried at all."""


@dataclass(frozen=True)
class VmInfo:
    name: str
    state: str  # "Running", "Off", "Saved", ...
    cpu_percent: int  # of the VM's own virtual processors
    memory_assigned: int  # bytes
    processors: int
    uptime_s: int
    generation: int
    dynamic_memory: bool


FIELDS = {"Name": str, "State": str, "CPUUsage": int, "MemoryAssigned": int,
          "ProcessorCount": int, "UptimeSeconds": int, "Generation": int, "DynamicMemoryEnabled": bool}


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_powershell(script: str, timeout: float) -> str:
    """Runs a PowerShell snippet and returns stdout; any error output raises HyperVError."""
    full = "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=CREATE_NO_WINDOW, check=False,
    )
    if result.returncode != 0:
        raise HyperVError(result.stderr.strip() or f"PowerShell-Exitcode {result.returncode}")
    return result.stdout


def parse_vms(output: str) -> dict[str, VmInfo]:
    """Turns the JSON list from vm_info's PowerShell into {vm name: VmInfo}."""
    text = output.strip()
    if not text:
        return {}
    data: object = json.loads(text)
    items = data if isinstance(data, list) else [data]
    vms: dict[str, VmInfo] = {}
    for item in items:
        if not isinstance(item, dict) or any(
                type(item.get(key)) is not kind for key, kind in FIELDS.items()):
            raise HyperVError(f"Unerwartete Antwort von Get-VM: {item!r}")
        vms[item["Name"]] = VmInfo(
            name=item["Name"], state=item["State"], cpu_percent=item["CPUUsage"],
            memory_assigned=item["MemoryAssigned"],
            processors=item["ProcessorCount"], uptime_s=item["UptimeSeconds"],
            generation=item["Generation"], dynamic_memory=item["DynamicMemoryEnabled"])
    return vms


def vm_info(names: list[str]) -> dict[str, VmInfo]:
    """State and resources of the named VMs; unknown VMs are missing."""
    # Without elevation Get-VM silently returns nothing, which would look like "VM not found".
    if not is_admin():
        raise NotAdminError(NO_ADMIN)
    quoted = ",".join(_quote(n) for n in names)
    script = (
        f"ConvertTo-Json -Compress -InputObject @(Get-VM -Name {quoted} -ErrorAction SilentlyContinue | "
        "Select-Object Name, @{n='State';e={$_.State.ToString()}}, CPUUsage, MemoryAssigned, "
        "ProcessorCount, @{n='UptimeSeconds';e={[int]$_.Uptime.TotalSeconds}}, Generation, DynamicMemoryEnabled)"
    )
    return parse_vms(run_powershell(script, timeout=20))


def parse_names(output: str) -> list[str]:
    text = output.strip()
    data: object = json.loads(text) if text else []
    if not isinstance(data, list) or not all(isinstance(n, str) for n in data):
        raise HyperVError(f"Unerwartete Antwort von Get-VM: {text[:120]}")
    return sorted(data, key=str.casefold)


def vm_names() -> list[str]:
    """Names of all Hyper-V VMs on this PC, for choosing them in the settings."""
    if not is_admin():
        raise NotAdminError(NO_ADMIN)
    return parse_names(run_powershell(
        "ConvertTo-Json -Compress -InputObject @(Get-VM | ForEach-Object { $_.Name })", timeout=20))


def start_vm(name: str) -> None:
    run_powershell(f"Start-VM -Name {_quote(name)}", timeout=120)


def stop_vm(name: str) -> None:
    """Asks the guest to shut down cleanly (no hard power-off)."""
    run_powershell(f"Stop-VM -Name {_quote(name)} -Force", timeout=300)


def _require_admin() -> None:
    # vmconnect without elevation reports the VM as "not found" plus a permission hint, which misleads.
    if not is_admin():
        raise NotAdminError(NO_ADMIN)


def open_console(name: str) -> None:
    """Opens the VM's console window (Hyper-V's own viewer)."""
    _require_admin()
    subprocess.Popen(["vmconnect.exe", "localhost", name])


def open_settings(name: str) -> None:
    """Opens the VM's settings dialog of the Hyper-V manager, where processors and memory are changed."""
    _require_admin()
    subprocess.Popen(["vmconnect.exe", "localhost", name, "/edit"])
