"""Sounds from herdr and the Remote Control terminals, played on their device.

Everything that runs in herdr or in a Remote Control tab plays its sounds on
this machine: herdr's ding comes from each device's herdr client (one per
device, all of them here), and a CLI in a pane plays where it runs. Those
programs start with ``PULSE_SINK`` set to a silent sink (tmux's global
environment, profiles.zsh), so they no longer play on this machine's
speakers. Each stream that lands there is checked against the process tree
and recorded on its own (``pacat --monitor-stream``) for the pages it
belongs to:

- a herdr client's sound (its ding): the device that client draws for;
- a sound from inside a herdr pane or a tab: every device of its owner;
- anything else of ours: every page listening.

Pages listen on ``/rc-sound`` (a WebSocket, proxy.py). Streams are never
moved between sinks: WirePlumber would remember the move for that whole
application (every Chrome, every paplay) from then on. Audio anywhere else
on this machine (a browser, music), or a program started before this, plays
as always.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

SINK = "rc_terminal_audio"
RATE = 22050
# 100 ms of 16-bit mono per event.
CHUNK = RATE * 2 // 10
QUEUE_MAX = 400
EVENT_RE = re.compile(r"Event '(new|remove)' on sink-input #(\d+)")
DEVICE_ID_RE = re.compile(r"^d[a-z0-9]{8,32}$")
PROF_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,31})?$")
VIEW_RE = re.compile(r"^hv-(d[a-z0-9]{8,32})-")


# --- who listens ----------------------------------------------------------------------


class Listener:
    def __init__(self, dev: str, prof: str) -> None:
        self.dev = dev
        self.prof = prof
        self.events: queue.Queue = queue.Queue(QUEUE_MAX)
        # What the page last said about its audio (proxy.py), for diagnosis.
        self.status: dict = {}


_listeners: list[Listener] = []
_lock = threading.Lock()


def listen(dev: str, prof: str) -> Listener | None:
    """A page's subscription; None when this machine cannot capture sound."""
    if not DEVICE_ID_RE.match(dev) or not PROF_RE.match(prof):
        return None
    if not available():
        return None
    item = Listener(dev, prof)
    with _lock:
        _listeners.append(item)
    _watcher.start()
    return item


def unlisten(item: Listener) -> None:
    with _lock:
        if item in _listeners:
            _listeners.remove(item)


def wants(item: Listener, target: dict) -> bool:
    if "dev" in target:
        return item.dev == target["dev"]
    if "prof" in target:
        return item.prof == target["prof"]
    return True


def publish(target: dict, event: dict) -> None:
    with _lock:
        items = [item for item in _listeners if wants(item, target)]
    for item in items:
        try:
            item.events.put_nowait(event)
        except queue.Full:
            pass


def available() -> bool:
    return bool(shutil.which("pactl") and shutil.which("pacat"))


# --- whose a sound is --------------------------------------------------------------------


def _parent(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat[stat.rindex(")") + 2 :].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def _argv(pid: int) -> list[str]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").split("\0")
    except OSError:
        return []


def ancestors(pid: int) -> list[int]:
    """pid and its parents, nearest first, up to (not including) init."""
    chain = []
    while pid > 1 and len(chain) < 64:
        chain.append(pid)
        pid = _parent(pid)
    return chain


def _is_herdr(argv: list[str]) -> bool:
    return bool(argv) and os.path.basename(argv[0]) == "herdr"


def _is_herdr_server(argv: list[str]) -> bool:
    return _is_herdr(argv) and len(argv) > 1 and argv[1] == "server"


def route(pid: int, tmux_panes: dict[int, str], pane_of_shell, tab_option) -> dict | None:
    """Who should hear a sound played by ``pid``; None when it is not ours.

    ``tmux_panes`` maps the Remote Control tmux panes' pids to their session,
    ``pane_of_shell(pid)`` gives the herdr pane whose shell that is, and
    ``tab_option(session, name)`` reads a tab's tmux option.
    """
    chain = ancestors(pid)
    argvs = [_argv(p) for p in chain]
    # Inside a herdr pane: the process right under the server is its shell.
    for i, argv in enumerate(argvs):
        if _is_herdr_server(argv):
            pane = pane_of_shell(chain[i - 1]) if i > 0 else None
            return pane if pane is not None else {"all": True}
    for i, p in enumerate(chain):
        session = tmux_panes.get(p)
        if session is None:
            continue
        view = VIEW_RE.match(session)
        if view:
            return {"dev": view.group(1)}
        if any(_is_herdr(argv) for argv in argvs[:i]):
            # The herdr client of the tab itself: the device that opened it.
            dev = tab_option(session, "@rc_dev")
            return {"dev": dev} if DEVICE_ID_RE.match(dev) else {"all": True}
        prof = tab_option(session, "@rc_prof")
        return {"prof": prof[1:]} if prof.startswith("p") else {"all": True}
    return None


# --- capture ----------------------------------------------------------------------------


def _run(*args: str, timeout: float = 3) -> str:
    try:
        res = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout if res.returncode == 0 else ""


class Watcher:
    """Follows PipeWire's playback streams while any page listens."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._recorders: dict[int, subprocess.Popen] = {}
        self._shells: dict[int, str] = {}
        self._shells_at = 0.0
        self.tmux_socket = "cf-remote"

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="rc-sound", daemon=True)
            self._thread.start()

    def _tmux(self, *args: str) -> str:
        return _run("tmux", "-L", self.tmux_socket, *args)

    def _sink_id(self) -> int | None:
        for line in _run("pactl", "list", "short", "sinks").splitlines():
            parts = line.split("\t")
            if len(parts) > 1 and parts[1] == SINK and parts[0].isdigit():
                return int(parts[0])
        return None

    def _ensure_sink(self) -> None:
        if SINK in _run("pactl", "list", "short", "sinks"):
            return
        default = _run("pactl", "get-default-sink").strip()
        _run("pactl", "load-module", "module-null-sink", f"sink_name={SINK}", "sink_properties=device.description=Remote-Control")
        # A new sink must never become this machine's speakers.
        if default and _run("pactl", "get-default-sink").strip() != default:
            _run("pactl", "set-default-sink", default)
        # Terminals and herdr views opened from now on play straight into it.
        self._tmux("set-environment", "-g", "PULSE_SINK", SINK)

    def _loop(self) -> None:
        while True:
            self._ensure_sink()
            for index in self._stream_ids():
                self._take(index)
            try:
                proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True)
            except OSError:
                return
            assert proc.stdout is not None
            for line in proc.stdout:
                found = EVENT_RE.search(line)
                if not found:
                    continue
                kind, index = found.group(1), int(found.group(2))
                if kind == "new":
                    threading.Thread(target=self._take, args=(index,), daemon=True).start()
                else:
                    self._drop(index)
            proc.wait()
            time.sleep(2)

    def _stream_ids(self) -> list[int]:
        return [item["index"] for item in self._streams()]

    def _streams(self) -> list[dict]:
        try:
            data = json.loads(_run("pactl", "-f", "json", "list", "sink-inputs") or "[]")
        except ValueError:
            return []
        return [item for item in data if isinstance(item, dict) and isinstance(item.get("index"), int)]

    def _tmux_panes(self) -> dict[int, str]:
        panes = {}
        for line in self._tmux("list-panes", "-a", "-F", "#{pane_pid} #{session_name}").splitlines():
            pid, _, session = line.partition(" ")
            if pid.isdigit():
                panes[int(pid)] = session
        return panes

    def _tab_option(self, session: str, name: str) -> str:
        return self._tmux("show-options", "-t", f"={session}:", "-qv", name).strip()

    def _pane_of_shell(self, shell: int) -> dict | None:
        from . import herdr

        if shell not in self._shells and time.time() - self._shells_at > 5:
            self._shells_at = time.time()
            snap = (herdr._api("api", "snapshot") or {}).get("snapshot") or {}
            for pane in snap.get("panes") or []:
                pid = pane.get("pane_id") if isinstance(pane, dict) else None
                if not isinstance(pid, str) or pid in self._shells.values():
                    continue
                info = (herdr._api("pane", "process-info", pid) or {}).get("process_info") or {}
                if isinstance(info.get("shell_pid"), int):
                    self._shells[info["shell_pid"]] = pid
        pane = self._shells.get(shell)
        if pane is None:
            return None
        return {"prof": herdr._read_pin(pane, herdr._load_owners().get(pane, ""))}

    def _take(self, index: int) -> None:
        stream = next((item for item in self._streams() if item["index"] == index), None)
        if stream is None:
            return
        props = stream.get("properties") or {}
        try:
            pid = int(props.get("application.process.id") or 0)
        except ValueError:
            return
        if pid <= 1:
            return
        if stream.get("sink") != self._sink_id():
            return
        target = route(pid, self._tmux_panes(), self._pane_of_shell, self._tab_option)
        if target is None:
            # On the silent sink but not ours: everyone hears it rather than no one.
            target = {"all": True}
        with self._lock:
            if index in self._recorders:
                return
            try:
                rec = subprocess.Popen(
                    ["pacat", "--record", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1",
                     "--latency-msec=40", f"--monitor-stream={index}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return
            self._recorders[index] = rec
        sid = f"{index}-{int(time.time() * 1000) % 100000}"
        assert rec.stdout is not None
        try:
            while True:
                data = rec.stdout.read(CHUNK)
                if not data:
                    break
                publish(target, {"s": sid, "rate": RATE, "d": base64.b64encode(data).decode("ascii")})
        finally:
            publish(target, {"s": sid, "end": True})
            self._drop(index)

    def _drop(self, index: int) -> None:
        with self._lock:
            rec = self._recorders.pop(index, None)
        if rec is not None and rec.poll() is None:
            rec.terminate()


_watcher = Watcher()
