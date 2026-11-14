"""Docker containers on the lab's Docker host, reached over SSH.

The Docker socket belongs to root and the lab user has passwordless sudo, so every command runs as
`sudo -n docker ...`; `-n` makes sudo fail with a message instead of waiting for a password.

Live values come from Docker's own streams instead of repeated queries: `docker stats` pushes
resource usage about once a second, and `docker events` reports state changes, after which the
container list is read again.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from labmanager import ssh
from labmanager.config import Lab, Service

# Container names as Docker allows them; anything else would be interpreted by the remote shell.
CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
COMPOSE_PROJECT = re.compile(r"(?:^|,)com\.docker\.compose\.project=([^,]*)")
# `docker stats` redraws a terminal screen; its cursor and clear sequences are not part of the data.
TERMINAL_CODES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PUBLISHED_PORT = re.compile(r":(\d+)(?:-(\d+))?->")
SIZE = re.compile(r"([0-9.]+)\s*([A-Za-z]*)")
UNITS = {"B": 1, "kB": 10**3, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
         "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40}
ACTIONS = ("start", "stop", "restart", "pause", "unpause")
# Healthchecks produce exec_* events every few seconds; only these change what the list shows.
STATE_EVENTS = ("create", "start", "stop", "die", "pause", "unpause", "destroy", "rename", "health_status")


class DockerError(ssh.SshError):
    """Docker on the lab host returned unexpected output or was asked for something invalid."""


@dataclass(frozen=True)
class Container:
    name: str
    stack: str  # compose project, empty for containers started without compose
    state: str  # running, exited, restarting, paused, created, dead
    status: str  # Docker's own text, e.g. "Up 5 hours (healthy)"
    image: str
    ports: tuple[int, ...]  # published host ports


@dataclass(frozen=True)
class Stats:
    name: str
    cpu: float  # percent of one core
    memory: int  # bytes
    memory_text: str
    memory_percent: float
    net_io: str
    block_io: str
    net_bytes: int
    block_bytes: int
    pids: int


def remote(lab: Lab, *docker_args: str, terminal: bool = False) -> list[str]:
    return ssh.command(lab, lab.host(lab.require_docker().host), "sudo", "-n", "docker", *docker_args, terminal=terminal)


def _record(line: str) -> dict[str, object]:
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as e:
        raise DockerError(f"Unerwartete Ausgabe von Docker: {line[:120]}") from e
    if not isinstance(value, dict):
        raise DockerError(f"Unerwartete Ausgabe von Docker: {line[:120]}")
    return {str(k): v for k, v in value.items()}


def _field(record: dict[str, object], key: str) -> str:
    if key not in record:
        raise DockerError(f"Docker-Ausgabe ohne Feld '{key}'")
    return str(record[key])


def size(text: str) -> int:
    """Bytes from Docker's size text such as '137.3MiB' or '4.96MB'."""
    m = SIZE.fullmatch(text.strip())
    if not m or m.group(2) not in UNITS:
        raise DockerError(f"Unbekannte Größenangabe von Docker: {text!r}")
    return round(float(m.group(1)) * UNITS[m.group(2)])


def _io_total(text: str) -> int:
    return sum(size(part) for part in text.split(" / "))


def _percent(text: str) -> float:
    try:
        return float(text.rstrip("%"))
    except ValueError as e:
        raise DockerError(f"Unbekannte Prozentangabe von Docker: {text!r}") from e


def published_ports(text: str) -> tuple[int, ...]:
    """Host ports from Docker's port text, e.g. '0.0.0.0:80-81->80-81/tcp, [::]:443->443/tcp'."""
    ports: set[int] = set()
    for m in PUBLISHED_PORT.finditer(text):
        first = int(m.group(1))
        ports.update(range(first, int(m.group(2) or first) + 1))
    return tuple(sorted(ports))


def parse_ps(text: str) -> list[Container]:
    """`docker ps -a --format json` (one JSON object per line)."""
    result: list[Container] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        r = _record(line)
        project = COMPOSE_PROJECT.search(_field(r, "Labels"))
        result.append(Container(name=_field(r, "Names"), stack=project.group(1) if project else "",
                                state=_field(r, "State"), status=_field(r, "Status"),
                                image=_field(r, "Image"), ports=published_ports(_field(r, "Ports"))))
    return result


def parse_stats_line(line: str) -> Stats | None:
    """One line of the `docker stats --format json` stream; None for lines that only redraw the screen.

    A container that is just starting is reported with '--' values; it has none yet and the next
    line of the stream brings them, so it is skipped like a redraw line.
    """
    text = TERMINAL_CODES.sub("", line).strip()
    if not text:
        return None
    r = _record(text)
    if _field(r, "MemUsage").startswith("--") or _field(r, "CPUPerc").startswith("--"):
        return None
    memory_text = _field(r, "MemUsage").split(" / ")[0]
    return Stats(name=_field(r, "Name"), cpu=_percent(_field(r, "CPUPerc")), memory=size(memory_text),
                 memory_text=memory_text, memory_percent=_percent(_field(r, "MemPerc")),
                 net_io=_field(r, "NetIO"), block_io=_field(r, "BlockIO"),
                 net_bytes=_io_total(_field(r, "NetIO")), block_bytes=_io_total(_field(r, "BlockIO")),
                 pids=int(_field(r, "PIDs")))


def list_containers(lab: Lab, run: ssh.Runner) -> list[Container]:
    return parse_ps(run(remote(lab, "ps", "-a", "--format", "json")))


def read_stats(lab: Lab, run: ssh.Runner) -> dict[str, Stats]:
    """One reading of `docker stats` for commands that cannot keep a stream open."""
    readings = (parse_stats_line(line) for line in run(remote(lab, "stats", "--no-stream", "--format", "json")).splitlines())
    return {s.name: s for s in readings if s is not None}


def _checked(name: str) -> str:
    if not CONTAINER_NAME.fullmatch(name):
        raise DockerError(f"Ungültiger Containername: {name!r}")
    return name


def control(lab: Lab, action: str, name: str, run: ssh.Runner) -> None:
    if action not in ACTIONS:
        raise DockerError(f"Unbekannte Aktion: {action}")
    run(remote(lab, action, _checked(name)))


def logs_command(lab: Lab, name: str) -> list[str]:
    return remote(lab, "logs", "-f", "--tail", "200", _checked(name), terminal=True)


def shell_command(lab: Lab, name: str) -> list[str]:
    return remote(lab, "exec", "-it", _checked(name), "sh", terminal=True)


def web_services(lab: Lab, container: Container) -> list[Service]:
    """Services from lab.json served by this container (same host, one of its published ports)."""
    return [s for s in lab.services if s.host == lab.require_docker().host and s.port in container.ports]


def stats_stream_command(lab: Lab) -> list[str]:
    return remote(lab, "stats", "--format", "json")


def events_stream_command(lab: Lab) -> list[str]:
    filters = [arg for event in STATE_EVENTS for arg in ("--filter", f"event={event}")]
    return remote(lab, "events", "--filter", "type=container", *filters, "--format", "json")

