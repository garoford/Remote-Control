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


def update(argv: list[str]) -> int:
    """update.sh now (what the timer does every 3 minutes), then the containers.

    The containers are updated by the freshly installed command, not by this
    process (it still runs the code from before the update).
    """
    from remote_control import multi
    from remote_control.updater import UPDATED_EXIT, update_script

    script = update_script()
    if script is None:
        print("error: no encuentro la instalación (update.sh)", file=sys.stderr)
        return 1
    src = script.parent / "src"
    print("Buscando la versión nueva…", flush=True)
    code = subprocess.run(["bash", str(script)], check=False).returncode
    if code == UPDATED_EXIT:
        print(f"✓ Remote Control actualizado ({_version(src)})")
    else:
        print(f"✓ Remote Control ya estaba al día ({_version(src)})")
    from remote_control import router

    if code == UPDATED_EXIT and router.service_active():
        # The domains' router runs the code it started with.
        router.ensure_service()
        print("✓ Router de dominios reiniciado")
    if "--no-containers" in argv or not multi.available():
        return 0
    try:
        boxes = multi.list_boxes()
    except multi.MultiError:
        boxes = []
    wrapper = Path.home() / ".local" / "bin" / "remote-control"
    failed = False
    for box in boxes:
        if not box.running:
            print(f"○ {box.name}: apagado. Al encenderlo:  remote-control multi update {box.name}")
            continue
        print(f"Actualizando el contenedor {box.name}…", flush=True)
        failed |= subprocess.run([str(wrapper), "multi", "update", box.name], check=False).returncode != 0
    if boxes:
        print("Los túneles encendidos usan el código nuevo cuando los apagas y enciendes.")
    elif code == UPDATED_EXIT:
        print("Si el túnel está encendido, apágalo y enciéndelo para usar el código nuevo.")
    return 1 if failed else 0


COMMANDS = {"status": status, "stop": stop, "password": password, "update": update}
