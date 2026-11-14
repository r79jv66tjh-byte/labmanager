"""Real memory use of a lab host, read inside the host from /proc/meminfo.

Numbers from outside mislead for Linux guests: Proxmox counts the page cache as used, and Hyper-V's
memory demand follows the guest's committed (reserved, mostly unused) memory. MemAvailable is what
the kernel can hand out right now, cache included, so used = MemTotal - MemAvailable matches `free`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from labmanager import ssh
from labmanager.config import Host, Lab

# Below this share of available memory a host is short of RAM.
LOW_AVAILABLE = 0.1
FIELD = re.compile(r"^(MemTotal|MemAvailable):\s+(\d+) kB$", re.MULTILINE)


class HostMemoryError(ssh.SshError):
    """/proc/meminfo could not be read or lacked a value."""


@dataclass(frozen=True)
class Memory:
    total: int  # bytes
    available: int  # bytes

    @property
    def used(self) -> int:
        return self.total - self.available

    @property
    def low(self) -> bool:
        return self.available < self.total * LOW_AVAILABLE


def parse_meminfo(text: str) -> Memory:
    values = {name: int(kb) * 1024 for name, kb in FIELD.findall(text)}
    for name in ("MemTotal", "MemAvailable"):
        if name not in values:
            raise HostMemoryError(f"/proc/meminfo ohne {name}: {text[:120]!r}")
    return Memory(total=values["MemTotal"], available=values["MemAvailable"])


def read(lab: Lab, host: Host, run: ssh.Runner) -> Memory:
    return parse_meminfo(run(ssh.command(lab, host, "grep", "-E", "^(MemTotal|MemAvailable):", "/proc/meminfo")))
