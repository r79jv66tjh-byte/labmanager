"""Reachability checks for hosts and services."""

from __future__ import annotations

import socket


def port_open(address: str, port: int, timeout: float) -> bool:
    """True when a TCP connection to address:port succeeds within the timeout.

    connect_ex reports refused, unreachable and timed-out connections as an error code, which is the
    answer "not reachable". Problems that are not about reachability, such as an address that cannot
    be resolved, still raise OSError so a broken lab.json entry does not look like a stopped service.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((address, port)) == 0
