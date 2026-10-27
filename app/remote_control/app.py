from __future__ import annotations

import os
import sys

from remote_control.gate import GateUsage, consume_gate_args, write_password


def _run_gui(gtk_argv: list[str]) -> int:
    # GTK is imported only here: `remote-control cli` must start without a display.
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")

    from gi.repository import Adw, Gio

    from remote_control import APP_ID
    from remote_control.window import RemoteControlWindow

    class RemoteControlApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id=APP_ID,
                flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            )
            self.connect("activate", self._on_activate)

        def _on_activate(self, *_args) -> None:
            style = self.get_style_manager()
            style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            window = self.props.active_window
            if window is None:
                from remote_control import multi

                if multi.enabled():
                    from remote_control.multi_window import MultiWindow

                    window = MultiWindow(application=self)
                else:
                    window = RemoteControlWindow(application=self)
            window.present()

    return RemoteControlApp().run(gtk_argv)


def _uninstall(args: list[str]) -> int:
    """install.sh --uninstall, run from a copy: it deletes the folder it lives in."""
    import shutil
    import tempfile
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for script in (here.parent / "src" / "install.sh", here.parent.parent / "install.sh"):
        if script.is_file():
            break
    else:
        print("error: no encuentro install.sh; corre  curl -fsSL …/install.sh | bash -s -- --uninstall", file=sys.stderr)
        return 1
    fd, copy = tempfile.mkstemp(prefix="remote-control-uninstall-", suffix=".sh")
    os.close(fd)
    shutil.copyfile(script, copy)
    os.execvp("bash", ["bash", copy, "--uninstall", *args])
    return 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv if argv is None else argv)
    if len(raw) > 1 and raw[1] in ("cli", "tui"):
        from remote_control.tui import run

        return run()
    if len(raw) > 1 and raw[1] == "start":
        from remote_control.autostart import headless_start

        return headless_start()
    if len(raw) > 1 and raw[1] == "uninstall":
        return _uninstall(raw[2:])
    if len(raw) > 1 and raw[1] == "router":
        from remote_control.router import main as router_main

        return router_main(raw[2:])
    if len(raw) > 1 and raw[1] == "multi":
        from remote_control.multi import cli as multi_cli

        return multi_cli(raw[2:])
    if len(raw) > 1:
        from remote_control.commands import COMMANDS

        if raw[1] in COMMANDS:
            return COMMANDS[raw[1]](raw[2:])
    if len(raw) > 1 and raw[1] == "domain":
        from remote_control.domains import cli

        return cli(raw[2:])
    if len(raw) > 1 and raw[1] == "autostart":
        from remote_control import autostart

        arg = raw[2] if len(raw) > 2 else "status"
        if arg not in ("on", "off", "status"):
            print("uso: remote-control autostart [on|off|status]", file=sys.stderr)
            return 2
        if arg != "status":
            autostart.set_enabled(arg == "on")
        print("activado" if autostart.is_enabled() else "desactivado")
        return 0
    try:
        gtk_argv, password, clear = consume_gate_args(raw)
    except GateUsage as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if clear:
        write_password("")
    elif password is not None:
        write_password(password)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        # A server over SSH: no window to open, the CLI does the same job.
        from remote_control.tui import run

        return run()
    return _run_gui(gtk_argv)
