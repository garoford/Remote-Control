"""Headless commands: what the multi manager (multi.py) asks each container.

remote-control status [--json]   is the tunnel on, its URL and local port
remote-control stop              switch the tunnel off (tabs keep running)
remote-control password [show | new | set PASSWORD]
remote-control update [--no-containers]   this install from GitHub, then each running container
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from remote_control.gate import GateUsage, generate_password, read_password, without_spaces, write_password


def status(argv: list[str]) -> int:
    from remote_control.tunnel import TunnelService

    st = TunnelService().status()
    data = {"running": st.running, "url": st.url, "port": st.port}
    if "--json" in argv:
        print(json.dumps(data))
    else:
        print(f"encendido: {st.url}" if st.running else "apagado")
    return 0


def stop(_argv: list[str]) -> int:
    from remote_control.tunnel import TunnelService

    # Its terminals close too; the next start brings them back.
    TunnelService().stop(close_sessions=True)
    print("apagado")
    return 0


def password(argv: list[str]) -> int:
    cmd = argv[0] if argv else "show"
    try:
        if cmd == "new":
            write_password(generate_password())
        elif cmd == "set":
            if len(argv) < 2:
                print("uso: remote-control password set CONTRASEÑA", file=sys.stderr)
                return 2
            write_password(without_spaces(argv[1]))
        elif cmd != "show":
            print("uso: remote-control password [show | new | set CONTRASEÑA]", file=sys.stderr)
            return 2
    except (GateUsage, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(read_password())
    return 0


def _version(src: Path) -> str:
    res = subprocess.run(["git", "-C", str(src), "log", "-1", "--format=%h · %cr"], capture_output=True, text=True, check=False)
    return res.stdout.strip() or "?"


class _Steps:
    """The installer's look (install.sh): a spinner while a step runs, then
    ✓/!/✗, its note and seconds. Plain lines when it is not a terminal."""

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        import os

        self.tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        on = self.tty
        self.B, self.R = ("\033[1m", "\033[0m") if on else ("", "")
        rgb = lambda r, g, b: f"\033[38;2;{r};{g};{b}m" if on else ""  # noqa: E731
        self.TEAL, self.BLUE, self.GREEN = rgb(33, 199, 168), rgb(130, 170, 255), rgb(34, 218, 110)
        self.YELLOW, self.RED, self.MUTED = rgb(255, 235, 149), rgb(239, 83, 80), rgb(138, 155, 179)

    def width(self) -> int:
        import shutil

        return shutil.get_terminal_size((90, 24)).columns

    def banner(self, sub: str) -> None:
        if self.tty:
            print(f"\n  {self.B}{self.TEAL} ▶_ {self.R}  {self.B}Remote Control{self.R}")
            print(f"        {self.MUTED}{sub}{self.R}\n")
        else:
            print(f"Remote Control: {sub}")

    def section(self, title: str) -> None:
        print(f"\n  {self.MUTED}{title.upper()}{self.R}" if self.tty else f"{title}:")

    def line(self, mark: str, color: str, title: str, note: str = "") -> None:
        if self.tty:
            print(f"\r  {color}{mark}{self.R} {title:<26} {self.MUTED}{note}{self.R}\033[K", flush=True)
        else:
            print(f"{mark} {title}" + (f": {note}" if note else ""), flush=True)

    def run(self, title: str, work) -> tuple[bool, str]:
        """work(detail) -> note; detail(text) shows what it is doing now."""
        import threading
        import time

        state = {"detail": "", "done": False}
        start = time.monotonic()

        def spin() -> None:
            i = 0
            while not state["done"]:
                secs = int(time.monotonic() - start)
                detail = state["detail"]
                room = max(0, self.width() - 40)
                detail = detail[: room - 1] + "…" if len(detail) > room else detail
                sys.stdout.write(
                    f"\r  {self.BLUE}{self.SPIN[i % len(self.SPIN)]}{self.R} {title:<26} {self.MUTED}{detail}  {secs}s{self.R}\033[K"
                )
                sys.stdout.flush()
                i += 1
                time.sleep(0.1)

        spinner = None
        if self.tty:
            sys.stdout.write("\033[?25l")
            spinner = threading.Thread(target=spin, daemon=True)
            spinner.start()
        ok, note = True, ""
        try:
            note = work(lambda text: state.update(detail=text.strip())) or ""
        except Exception as exc:  # shown as the step's failure
            ok, note = False, str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        finally:
            state["done"] = True
            if spinner:
                spinner.join()
                sys.stdout.write("\033[?25h")
        secs = int(time.monotonic() - start)
        if ok and secs:
            note = f"{note} · {secs}s" if note else f"{secs}s"
        self.line("✓" if ok else "✗", self.GREEN if ok else self.RED, title, note)
        return ok, note


def _rev(src: Path) -> str:
    res = subprocess.run(["git", "-C", str(src), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
    return res.stdout.strip()


def update(argv: list[str]) -> int:
    """update.sh now (what the timer does every 3 minutes), then the containers.

    The containers are updated by the freshly installed command, not by this
    process (it still runs the code from before the update).
    """
    import socket
    import time

    from remote_control import multi
    from remote_control.updater import UPDATED_EXIT, update_script

    ui = _Steps()
    script = update_script()
    if script is None:
        print("error: no encuentro la instalación (update.sh)", file=sys.stderr)
        return 1
    src = script.parent / "src"
    began = time.monotonic()
    ui.banner(f"actualizar · {socket.gethostname()}")
    before = _rev(src)
    result = {"code": 0}

    def fetch(detail) -> str:
        detail("Buscando la versión nueva en GitHub…")
        result["code"] = subprocess.run(["bash", str(script)], check=False).returncode
        after = _rev(src)
        if result["code"] == UPDATED_EXIT:
            return f"{before} → {after} · versión nueva"
        if result["code"] not in (0, UPDATED_EXIT):
            raise RuntimeError(f"update.sh salió con {result['code']} (mira {script.parent / 'update.log'})")
        return f"al día · {_version(src)}"

    ok, _ = ui.run("Remote Control", fetch)
    if not ok:
        return 1
    updated = result["code"] == UPDATED_EXIT
    from remote_control import router

    if updated and router.service_active():
        # The domains' router runs the code it started with.
        ui.run("Router de dominios", lambda detail: router.ensure_service() or "reiniciado")
    boxes = []
    if "--no-containers" not in argv and multi.available():
        try:
            boxes = multi.list_boxes()
        except multi.MultiError:
            boxes = []
    wrapper = Path.home() / ".local" / "bin" / "remote-control"
    failed = False
    if boxes:
        ui.section("Contenedores")
    for box in boxes:
        if not box.running:
            ui.line("○", ui.MUTED, box.name, f"apagado · al encenderlo:  remote-control multi update {box.name}")
            continue
        seen: list[str] = []

        def one(detail, name=box.name, seen=seen) -> str:
            proc = subprocess.Popen(
                [str(wrapper), "multi", "update", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            assert proc.stdout is not None
            for raw in proc.stdout:
                text = raw.strip()
                if text:
                    seen.append(text)
                    detail(text)
            if proc.wait() != 0:
                raise RuntimeError(seen[-1] if seen else "falló")
            missing = next((t for t in seen if t.startswith("Sin instalar")), "")
            if missing:
                return missing
            return "entorno al día" if "Entorno al día" in seen else "entorno actualizado" if "Entorno listo" in seen else "listo"

        box_ok, _ = ui.run(box.name, one)
        failed |= not box_ok
        if not box_ok and seen:
            for text in seen[-4:]:
                print(f"      {ui.MUTED}{text}{ui.R}")
    total = int(time.monotonic() - began)
    hint = ""
    if boxes:
        hint = "Los túneles encendidos usan el código nuevo cuando los apagas y enciendes."
    elif updated:
        hint = "Si el túnel está encendido, apágalo y enciéndelo para usar el código nuevo."
    print()
    if ui.tty:
        state = f"{ui.RED}con errores{ui.R}" if failed else f"{ui.GREEN}listo{ui.R}"
        print(f"  {ui.B}{state}{ui.R} {ui.MUTED}en {total}s{ui.R}")
        if hint:
            print(f"  {ui.MUTED}{hint}{ui.R}")
        print()
    elif hint:
        print(hint)
    return 1 if failed else 0


COMMANDS = {"status": status, "stop": stop, "password": password, "update": update}
