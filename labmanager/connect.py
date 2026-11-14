"""Opens remote desktop profiles, web interfaces and SSH sessions.

LabManager runs elevated because Hyper-V needs it. Browsers and mstsc are therefore started through
explorer.exe, which runs them in the user's normal, non-elevated context. Links and .rdp files are
written to a temporary folder first and explorer.exe opens the file.
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path

from labmanager.config import Host, Lab, RdpProfile

TEMP_DIR = Path(tempfile.gettempdir()) / "LabManager"


def rdp_text(lab: Lab, profile: RdpProfile) -> str:
    """Content of the .rdp file: target and user from lab.json, then the profile and shared settings."""
    rdp = lab.require_rdp()
    target = lab.host(rdp.host)
    lines = [f"full address:s:{target.address}", f"username:s:{rdp.username}",
             *profile.settings, *rdp.base]
    return "\n".join(lines) + "\n"


def write_rdp(lab: Lab, profile: RdpProfile, folder: Path) -> Path:
    """Writes the profile as a Windows .rdp file (CRLF line endings)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{profile.name}.rdp"
    path.write_text(rdp_text(lab, profile), encoding="ascii", newline="\r\n")
    return path


def ssh_command(lab: Lab, host: Host, *options: str) -> list[str]:
    """ssh call for the host; options such as ("-o", "BatchMode=yes") go before the destination."""
    return ["ssh", *options, "-i", str(lab.require_ssh_key()), f"{host.ssh_user}@{host.address}"]


def open_unelevated(target: str) -> None:
    """Opens a file with its default program, outside the elevated LabManager process."""
    subprocess.Popen(["explorer.exe", target])


def show_in_explorer(path: Path) -> None:
    """Opens the folder in Explorer with the file selected."""
    subprocess.Popen(["explorer.exe", f"/select,{path}"])


def write_url(url: str, folder: Path) -> Path:
    """Internet shortcut for the URL, one file per URL so quick clicks do not overwrite each other.

    explorer.exe misreads some URLs given directly (a '#' with '%2F' opens the Documents folder);
    a .url file reaches the default browser unchanged.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"link-{hashlib.sha1(url.encode()).hexdigest()[:12]}.url"
    path.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="ascii", newline="\r\n")
    return path


def open_url(url: str) -> None:
    open_unelevated(str(write_url(url, TEMP_DIR)))


def open_rdp(lab: Lab, profile: RdpProfile) -> None:
    open_unelevated(str(write_rdp(lab, profile, TEMP_DIR)))


def open_ssh(lab: Lab, host: Host) -> None:
    subprocess.Popen(["wt.exe", "new-tab", "--title", host.label, *ssh_command(lab, host)])
