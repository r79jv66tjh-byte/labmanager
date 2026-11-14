"""Runs commands on lab hosts over SSH: one-shot with captured output, or as a long-running stream.

All calls run without a console window and never prompt: a missing key or unknown host fails with
ssh's own message instead of waiting for input nobody can give.
"""

from __future__ import annotations

import shlex
import subprocess
import threading
from collections.abc import Callable

from labmanager import connect
from labmanager.config import Host, Lab
from labmanager.hyperv import CREATE_NO_WINDOW

TIMEOUT_S = 30
# BatchMode: never ask for a password or host key; ServerAliveInterval: notice dropped streams.
OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=10")

Runner = Callable[[list[str]], str]


class SshError(Exception):
    """A command on a lab host failed, timed out or returned unexpected output."""


def command(lab: Lab, host: Host, *remote: str, terminal: bool = False) -> list[str]:
    """ssh call running the words `remote` on the host; `terminal` allocates a TTY for interactive use.

    The words are shell-quoted and joined into one argument. Windows' ssh.exe drops single quotes
    from arguments without spaces, so quoting only survives inside one argument that Windows wraps
    in double quotes; a command of at least two words always contains a space.
    """
    if len(remote) < 2:
        raise ValueError(f"Befehl für {host.label} braucht mindestens zwei Wörter: {remote!r}")
    options = ("-t",) if terminal else OPTIONS
    return [*connect.ssh_command(lab, host, *options), " ".join(shlex.quote(word) for word in remote)]


def run(cmd: list[str]) -> str:
    """Runs a short command (queries, actions) with the usual time limit."""
    return run_for(cmd, TIMEOUT_S)


def run_for(cmd: list[str], timeout_s: int) -> str:
    """Runs the command and returns stdout; a non-zero exit raises SshError with its stderr."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout_s, creationflags=CREATE_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired as e:
        raise SshError(f"Keine Antwort nach {timeout_s} Sekunden.") from e
    if result.returncode != 0:
        raise SshError(result.stderr.strip() or f"Exit-Code {result.returncode}")
    return result.stdout


def open_in_terminal(title: str, cmd: list[str]) -> None:
    subprocess.Popen(["wt.exe", "new-tab", "--title", title, *cmd])


class Stream:
    """Long-running command whose output lines are handed to a callback on a reader thread."""

    def __init__(self, cmd: list[str], on_line: Callable[[str], None], on_end: Callable[[str], None]) -> None:
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        self.stopped = False
        self.on_line = on_line
        self.on_end = on_end
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None and self.process.stderr is not None
        with self.process.stdout, self.process.stderr:
            for line in self.process.stdout:
                self.on_line(line)
            error = self.process.stderr.read().strip()
        code = self.process.wait()
        if not self.stopped:
            self.on_end(error or f"Exit-Code {code}")

    def stop(self) -> None:
        self.stopped = True
        self.process.kill()
        self.process.wait(timeout=5)
