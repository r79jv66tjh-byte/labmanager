"""Tests for reading, streaming and controlling Docker containers on the lab's Docker host.

The sample lines are shortened real output of `docker ps -a --format json` and of the
`docker stats --format json` stream from the docker VM (including its terminal redraw codes).
Ausführen:  python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from labmanager import containers, ssh
from labmanager.config import load
from labmanager.gui import ContainerTab, container_row

LAB = load(Path(__file__).resolve().parent / "fixtures" / "lab.json")


def ps_line(name: str, state: str, status: str, labels: str, ports: str = "") -> str:
    return json.dumps({"Names": name, "State": state, "Status": status, "Labels": labels,
                       "Image": f"{name}:latest", "Ports": ports})


KUMA = ps_line("uptime-kuma", "running", "Up 5 hours (healthy)",
               "com.docker.compose.project=basis,com.docker.compose.service=uptime-kuma",
               "0.0.0.0:3001->3001/tcp, [::]:3001->3001/tcp")
NPM = ps_line("nginx-proxy-manager", "running", "Up 5 hours",
              "org.label-schema.description=Docker container for managing Nginx proxy hosts, simple ,"
              "com.docker.compose.project=basis,maintainer=Jamie",
              "0.0.0.0:80-81->80-81/tcp, [::]:80-81->80-81/tcp, 0.0.0.0:443->443/tcp")
OLD = ps_line("test", "exited", "Exited (0) 2 days ago", "")
KUMA_STATS = ('\x1b[H{"BlockIO":"107MB / 43.8MB","CPUPerc":"0.35%","MemPerc":"5.12%",'
              '"MemUsage":"200.9MiB / 3.83GiB","Name":"uptime-kuma","NetIO":"1.68MB / 2.91MB","PIDs":"19"}\x1b[K\n')


def unused(cmd: list[str]) -> str:
    raise AssertionError("external call during test")


class ParseTest(unittest.TestCase):
    def test_ps_lines_with_stack_image_and_published_ports(self) -> None:
        result = containers.parse_ps(f"{KUMA}\n{OLD}\n{NPM}\n")
        self.assertEqual([(c.name, c.stack, c.state) for c in result],
                         [("uptime-kuma", "basis", "running"), ("test", "", "exited"),
                          ("nginx-proxy-manager", "basis", "running")])
        self.assertEqual(result[2].ports, (80, 81, 443))
        self.assertEqual(result[0].image, "uptime-kuma:latest")

    def test_stats_line_from_the_stream(self) -> None:
        s = containers.parse_stats_line(KUMA_STATS)
        assert s is not None
        self.assertEqual((s.name, s.cpu, s.memory_text, s.memory_percent, s.pids),
                         ("uptime-kuma", 0.35, "200.9MiB", 5.12, 19))
        self.assertEqual(s.memory, round(200.9 * 2**20))
        self.assertEqual(s.net_bytes, 1_680_000 + 2_910_000)

    def test_redraw_lines_and_starting_containers_carry_no_values(self) -> None:
        self.assertIsNone(containers.parse_stats_line("\x1b[J\n"))
        starting = json.dumps({"Name": "x", "CPUPerc": "--", "MemUsage": "-- / --"})
        self.assertIsNone(containers.parse_stats_line(starting))

    def test_unexpected_output_is_an_error(self) -> None:
        for bad in ("kein json", json.dumps({"Names": "x"}), "[]"):
            with self.subTest(ps=bad), self.assertRaises(containers.DockerError):
                containers.parse_ps(bad)
        with self.assertRaisesRegex(containers.DockerError, "Größenangabe"):
            containers.size("12 Liter")

    def test_sizes_in_decimal_and_binary_units(self) -> None:
        self.assertEqual(containers.size("4.96MB"), 4_960_000)
        self.assertEqual(containers.size("1.5KiB"), 1536)
        self.assertEqual(containers.size("0B"), 0)


class StatsOnceTest(unittest.TestCase):
    def test_one_shot_stats_by_container(self) -> None:
        calls: list[list[str]] = []

        def run(cmd: list[str]) -> str:
            calls.append(cmd)
            return KUMA_STATS + "\x1b[J\n"

        stats = containers.read_stats(LAB, run)
        self.assertEqual(list(stats), ["uptime-kuma"])
        self.assertEqual(calls[0][-1], "sudo -n docker stats --no-stream --format json")


class CommandTest(unittest.TestCase):
    def test_remote_command_runs_docker_through_sudo_without_prompt(self) -> None:
        cmd = containers.remote(LAB, "ps", "-a")
        self.assertIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-2:], ["admin@192.0.2.30", "sudo -n docker ps -a"])

    def test_terminal_commands_allocate_a_tty(self) -> None:
        cmd = containers.shell_command(LAB, "homepage")
        self.assertEqual(cmd[:2], ["ssh", "-t"])
        self.assertEqual(cmd[-1], "sudo -n docker exec -it homepage sh")
        self.assertEqual(containers.logs_command(LAB, "homepage")[-1], "sudo -n docker logs -f --tail 200 homepage")

    def test_events_stream_listens_only_for_state_changes(self) -> None:
        cmd = containers.events_stream_command(LAB)
        self.assertIn("--filter event=die", cmd[-1])
        self.assertNotIn("exec_create", cmd[-1])

    def test_control_runs_the_action(self) -> None:
        calls: list[list[str]] = []

        def run(cmd: list[str]) -> str:
            calls.append(cmd)
            return ""

        containers.control(LAB, "restart", "uptime-kuma", run)
        self.assertEqual(calls[-1][-1], "sudo -n docker restart uptime-kuma")

    def test_unknown_actions_and_unsafe_names_are_rejected(self) -> None:
        with self.assertRaises(containers.DockerError):
            containers.control(LAB, "rm", "uptime-kuma", unused)
        for bad in ("", "a;reboot", "-x", "a b"):
            with self.subTest(name=bad), self.assertRaises(containers.DockerError):
                containers.control(LAB, "restart", bad, unused)
            with self.subTest(name=bad), self.assertRaises(containers.DockerError):
                containers.shell_command(LAB, bad)

    def test_failed_command_raises_with_its_error_output(self) -> None:
        with self.assertRaisesRegex(ssh.SshError, "kaputt"):
            ssh.run([sys.executable, "-c", "import sys; sys.stderr.write('kaputt'); sys.exit(3)"])

    def test_web_services_match_published_ports(self) -> None:
        kuma = containers.parse_ps(KUMA)[0]
        self.assertEqual([s.label for s in containers.web_services(LAB, kuma)], ["Uptime Kuma"])


class StreamTest(unittest.TestCase):
    def test_lines_are_delivered_and_an_unexpected_end_is_reported(self) -> None:
        lines: list[str] = []
        ended: list[str] = []
        stream = ssh.Stream(
            [sys.executable, "-c", "import sys; print('a'); print('b'); sys.stderr.write('weg'); sys.exit(1)"],
            lines.append, ended.append)
        stream.reader.join(timeout=10)
        self.assertEqual([line.strip() for line in lines], ["a", "b"])
        self.assertEqual(ended, ["weg"])

    def test_stopping_ends_the_process_without_reporting_an_error(self) -> None:
        ended: list[str] = []
        stream = ssh.Stream([sys.executable, "-c", "import time; time.sleep(60)"], print, ended.append)
        stream.stop()
        stream.reader.join(timeout=10)
        self.assertIsNotNone(stream.process.poll())
        self.assertEqual(ended, [])


class ContainerViewTest(unittest.TestCase):
    def test_rows_show_german_state_colour_and_live_values(self) -> None:
        kuma, old = containers.parse_ps(f"{KUMA}\n{OLD}")
        row = container_row(kuma, containers.parse_stats_line(KUMA_STATS))
        self.assertEqual(row.values[:6], ("uptime-kuma", "basis", "läuft (healthy)", "0.3 %", "200.9MiB", "5.1 %"))
        self.assertEqual(row.tag, "ok")
        stopped = container_row(old, None)
        self.assertEqual((stopped.values[2], stopped.values[3], stopped.tag), ("beendet", "", "down"))

    def test_tab_menu_offers_actions_for_the_state(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            tab = ContainerTab(ttk.Notebook(root), LAB, unused)
            tab.items = {c.name: c for c in containers.parse_ps(f"{KUMA}\n{OLD}")}
            tab.show()
            self.assertEqual(set(tab.table.tree.get_children()), {"uptime-kuma", "test"})
            self.assertEqual([b.cget("text") for b in tab.action_buttons],
                             ["Starten", "Stoppen", "Neu starten", "Aktualisieren"])
            running = [e.label for e in tab.menu_for("uptime-kuma") if e is not None]
            self.assertEqual(running, ["Stoppen", "Neu starten", "Pausieren", "Logs anzeigen", "Shell öffnen",
                                       "Öffnen: Uptime Kuma"])
            stopped = [e.label for e in tab.menu_for("test") if e is not None]
            self.assertEqual(stopped, ["Starten", "Neu starten", "Logs anzeigen"])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
