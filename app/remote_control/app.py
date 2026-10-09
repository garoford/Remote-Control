from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio

from remote_control import APP_ID
from remote_control.gate import GateUsage, consume_gate_args, write_password
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
            window = RemoteControlWindow(application=self)
        window.present()


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv if argv is None else argv)
    try:
        gtk_argv, password, clear = consume_gate_args(raw)
    except GateUsage as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if clear:
        write_password("")
    elif password is not None:
        write_password(password)
    app = RemoteControlApp()
    return app.run(gtk_argv)
