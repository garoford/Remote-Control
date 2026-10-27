"""Headless commands: what the multi manager (multi.py) asks each container.

remote-control status [--json]   is the tunnel on, its URL and local port
remote-control stop              switch the tunnel off (tabs keep running)
remote-control password [show | new | set PASSWORD]
"""

from __future__ import annotations

import json
import sys

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


COMMANDS = {"status": status, "stop": stop, "password": password}
