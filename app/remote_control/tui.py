"""`remote-control cli`: the desktop window, in the terminal (Textual).

Same pieces as the GTK window: tunnel switch and state, password (show,
generate, copy), public and local URL (copy, open) and the QR of the public URL.
Inside the tunnel itself the switch cannot turn it off: that would cut the
connection this screen is shown through.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static, Switch

from . import control
from .gate import STRENGTH_LABEL, generate_password, password_strength, read_password, without_spaces, write_password
from .tunnel import TunnelService

STRENGTH_BARS = {"none": 0, "weak": 1, "ok": 2, "strong": 4}
# Below this many columns the QR goes under the controls instead of beside them.
WIDE_MIN = 118  # the two columns + margins


def qr_text(url: str) -> Text | None:
    """QR in half blocks: one text row holds two module rows."""
    rows = control.qr_matrix(url)
    if rows is None:
        return None
    if len(rows) % 2:
        rows.append([False] * len(rows[0]))
    out = Text()
    for top, bottom in zip(rows[0::2], rows[1::2]):
        line = ""
        for a, b in zip(top, bottom):
            line += "█" if a and b else "▀" if a else "▄" if b else " "
        out.append(line, style="#011627 on #ffffff")
        out.append("\n")
    return out


class Confirm(ModalScreen[bool]):
    """Yes / no box (switch the tunnel off)."""

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancelar")]

    def __init__(self, title: str, body: str, yes: str) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._yes = yes

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self._title, id="confirm-title")
            yield Static(self._body, id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancelar", id="no", compact=True)
                yield Button(self._yes, id="yes", variant="error", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class RemoteControlTUI(App):
    TITLE = "Remote Control"
    CSS = """
    /* A compact panel, centred both ways; it scrolls only when the terminal
       is shorter than the panel. */
    Screen { background: #011627; color: #d6deeb; align: center middle; }
    #body { width: auto; height: auto; max-height: 100%; padding: 0 1; scrollbar-size-vertical: 1; }
    /* Wide terminal: controls on the left, QR on the right (like btop's panes).
       Narrow (phone, split pane): one column. */
    #cols { width: auto; height: auto; }
    #left { width: 66; height: auto; }
    #right { width: 45; height: auto; margin-left: 2; }
    #cols.narrow { layout: vertical; }
    #body.narrow { width: 100%; }
    #cols.narrow { width: 100%; align-horizontal: center; }
    #cols.narrow #left, #cols.narrow #right { width: 100%; max-width: 66; margin-left: 0; }
    #cols.off #right { display: none; }
    .card {
        height: auto; margin: 0 0 1 0; padding: 0 2;
        background: #011627; border: round #1d3b53;
        border-title-color: #d6deeb; border-title-style: bold;
        border-subtitle-color: #8a9bb3;
    }
    #hero.on { border: round #21c7a8; }
    #hero.busy { border: round #82aaff; }
    #hero-top { height: auto; }
    #badge { width: 6; height: 3; content-align: center middle; background: #21c7a8; color: #011627; text-style: bold; }
    #hero.off #badge { background: #1d3b53; color: #7fdbca; }
    #hero-text { width: 1fr; height: auto; padding: 0 2; }
    #hero-title { text-style: bold; }
    #hero-sub { color: #8a9bb3; }
    #switch { width: auto; }
    Switch { background: #0b2942; border: tall #1d3b53; }
    Switch > .switch--slider { color: #1d3b53; background: #011627; }
    Switch.-on > .switch--slider { color: #21c7a8; }
    #chips { height: auto; margin-top: 1; }
    #cols.narrow #chips { layout: vertical; }
    #cols.narrow .chip { margin-bottom: 1; }
    .chip { width: auto; height: 1; padding: 0 1; margin-right: 1; background: #1d3b53; color: #8a9bb3; }
    .chip.on { background: #0f3b2e; color: #22da6e; }
    .chip.busy { background: #1b2f55; color: #82aaff; }
    .chip.warn { background: #3b3417; color: #ffeb95; }
    .row { height: auto; }
    .row Input { width: 1fr; }
    .row Button { min-width: 7; margin-left: 1; }
    #show { min-width: 9; }
    Input { height: 1; border: none; padding: 0 1; background: #0b2942; color: #d6deeb; }
    Input:disabled { color: #8a9bb3; opacity: 1; }
    Input:focus { background: #1d3b53; }
    Button { background: #1d3b53; color: #d6deeb; border: none; }
    Button:hover { background: #2a4d6b; }
    Button.accent { background: #21c7a8; color: #011627; text-style: bold; }
    Button.accent:hover { background: #7fdbca; }
    #meter { height: 1; margin-top: 1; }
    .bar { width: 1fr; height: 1; margin-right: 1; background: #1d3b53; }
    .bar.weak { background: #ef5350; }
    .bar.ok { background: #ffeb95; }
    .bar.strong { background: #22da6e; }
    #pass-hint, .hint { color: #8a9bb3; margin-top: 1; }
    #pass-hint.warn { color: #ffeb95; }
    .label { color: #8a9bb3; text-style: bold; margin-top: 1; }
    .label:first-child { margin-top: 0; }
    .url { width: 1fr; height: 1; padding: 0 1; background: #0b2942; color: #7fdbca; }
    #qr-wrap { height: auto; }
    #qr { width: auto; height: auto; }
    #confirm { width: 56; height: auto; padding: 1 2; background: #0b2942; border: round #ef5350; }
    #confirm-title { text-style: bold; }
    #confirm-body { color: #8a9bb3; margin: 1 0; }
    #confirm-buttons { height: auto; align-horizontal: right; }
    #confirm-buttons Button { margin-left: 1; }
    #yes { background: #ef5350; color: #011627; text-style: bold; }
    ModalScreen { align: center middle; background: #011627 70%; }
    Footer { background: #0b2942; }
    """

    BINDINGS = [
        Binding("space", "toggle_tunnel", "Encender/apagar"),
        Binding("p", "toggle_password", "Ver clave"),
        Binding("n", "new_password", "Nueva clave"),
        Binding("c", "copy_url", "Copiar URL"),
        Binding("o", "open_url", "Abrir"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.tunnel = TunnelService()
        self.inside = control.inside_tunnel()
        self.busy = False
        self.url: str | None = None
        self.running = False
        self._qr_for = ""

    # --- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="body"):
            with Horizontal(id="cols"):
                with Vertical(id="left"):
                    with Vertical(id="hero", classes="card off"):
                        with Horizontal(id="hero-top"):
                            yield Static(">_", id="badge")
                            with Vertical(id="hero-text"):
                                yield Static("Túnel web", id="hero-title")
                                yield Static("Publica tu terminal en una URL temporal", id="hero-sub")
                            yield Switch(id="switch", animate=False)
                        with Horizontal(id="chips"):
                            yield Static("● Apagado", id="status-chip", classes="chip")
                            yield Static("🔒 Con contraseña", id="lock-chip", classes="chip")
                            if self.inside:
                                yield Static("⚠ Apagar solo en PC", classes="chip warn")
                    with Vertical(id="pass-card", classes="card"):
                        with Horizontal(classes="row"):
                            yield Input(id="password", password=True, placeholder="Sin contraseña", compact=True)
                            yield Button("Ver", id="show", compact=True)
                            yield Button("↻ Nueva", id="regen", compact=True)
                            yield Button("Copiar", id="copy-pass", classes="accent", compact=True)
                        with Horizontal(id="meter"):
                            for _ in range(4):
                                yield Static("", classes="bar")
                        yield Static("", id="pass-hint")
                    with Vertical(id="share", classes="card"):
                        yield Static("URL pública", classes="label")
                        with Horizontal(classes="row"):
                            yield Static("", id="public-url", classes="url")
                            yield Button("Copiar", id="copy-url", classes="accent", compact=True)
                            yield Button("Abrir", id="open-url", compact=True)
                        yield Static("URL local", classes="label")
                        with Horizontal(classes="row"):
                            yield Static(control.local_url(self.tunnel.port), id="local-url", classes="url")
                            yield Button("Copiar", id="copy-local", compact=True)
                            yield Button("Abrir", id="open-local", compact=True)
                with Vertical(id="right"):
                    with Vertical(id="qr-card", classes="card"):
                        with Center(id="qr-wrap"):
                            yield Static("", id="qr")
        yield Footer()

    def on_resize(self, event) -> None:
        narrow = self.size.width < WIDE_MIN
        self.query_one("#cols").set_class(narrow, "narrow")
        self.query_one("#body").set_class(narrow, "narrow")

    def on_mount(self) -> None:
        narrow = self.size.width < WIDE_MIN
        self.query_one("#cols").set_class(narrow, "narrow")
        self.query_one("#body").set_class(narrow, "narrow")
        self.query_one("#hero").border_title = "Remote Control"
        self.query_one("#pass-card").border_title = "Contraseña"
        self.query_one("#share").border_title = "Compartir"
        self.query_one("#qr-card").border_title = "Abrir en el celular"
        self.query_one("#qr-card").border_subtitle = "Escanea la URL pública"

        saved = without_spaces(read_password())
        if not saved:
            saved = generate_password()
        try:
            write_password(saved)
        except OSError:
            pass
        field = self.query_one("#password", Input)
        with field.prevent(Input.Changed):
            field.value = saved
        self.refresh_password()
        self.refresh_state()
        self.set_interval(2, self.refresh_state)

    # --- state ---------------------------------------------------------------

    def refresh_state(self) -> None:
        if not self.busy:
            status = self.tunnel.status()
            self.running = bool(status.running and status.url)
            self.url = status.url if self.running else None
            self.set_status("on" if self.running else "off")

    def set_status(self, kind: str, text: str | None = None) -> None:
        hero = self.query_one("#hero")
        for cls in ("on", "off", "busy"):
            hero.remove_class(cls)
        hero.add_class(kind)
        chip = self.query_one("#status-chip", Static)
        chip.set_classes(f"chip {kind}")
        chip.update(text or {"on": "● En línea", "off": "● Apagado", "busy": "● Un momento…"}[kind])
        self.query_one("#hero-sub", Static).update(
            {"on": "Tu terminal está publicada", "off": "Publica tu terminal en una URL temporal", "busy": "Un momento…"}[kind]
        )
        switch = self.query_one("#switch", Switch)
        with switch.prevent(Switch.Changed):
            switch.value = kind != "off"
        # Inside the tunnel it may be switched on, never off.
        switch.disabled = self.busy or (self.inside and self.running)
        self.query_one("#password", Input).disabled = self.running or self.busy
        self.query_one("#regen", Button).disabled = self.running or self.busy
        self.query_one("#share").display = self.running
        self.query_one("#cols").set_class(not self.running, "off")
        if self.running and self.url:
            self.query_one("#public-url", Static).update(Text(self.url, style=f"link {self.url}"))
            if self._qr_for != self.url:
                self.paint_qr()

    def refresh_password(self) -> None:
        text = self.query_one("#password", Input).value
        kind = password_strength(text)
        self.query_one("#pass-card").border_subtitle = STRENGTH_LABEL[kind]
        for i, bar in enumerate(self.query(".bar")):
            bar.set_classes("bar " + (kind if i < STRENGTH_BARS[kind] else ""))
        hint = self.query_one("#pass-hint", Static)
        if text:
            hint.update("Se pide al abrir la URL; se recuerda 30 días por dispositivo.")
            hint.remove_class("warn")
        else:
            hint.update("Sin contraseña: cualquiera con la URL podrá usar tu terminal.")
            hint.add_class("warn")
        lock = self.query_one("#lock-chip", Static)
        lock.update("🔒 Con contraseña" if text else "🔓 Sin contraseña")
        lock.set_classes("chip" if text else "chip warn")

    def paint_qr(self) -> None:
        if not self.url:
            return
        art = qr_text(self.url)
        self.query_one("#qr", Static).update(art if art is not None else "Instala 'segno' para ver el QR")
        self._qr_for = self.url

    # --- events --------------------------------------------------------------

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.value and not self.running:
            self.start_tunnel()
        elif not event.value and self.running:
            with event.switch.prevent(Switch.Changed):
                event.switch.value = True
            self.action_toggle_tunnel()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "password":
            return
        clean = without_spaces(event.value)
        if clean != event.value:
            with event.input.prevent(Input.Changed):
                event.input.value = clean
        try:
            write_password(clean)
        except OSError:
            pass
        self.refresh_password()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "show": self.action_toggle_password,
            "regen": self.action_new_password,
            "copy-pass": lambda: self.copy(self.query_one("#password", Input).value, "Contraseña copiada"),
            "copy-url": self.action_copy_url,
            "open-url": self.action_open_url,
            "copy-local": lambda: self.copy(control.local_url(self.tunnel.port), "URL local copiada"),
            "open-local": lambda: self.open_url(control.local_url(self.tunnel.port)),
        }
        handler = actions.get(event.button.id or "")
        if handler:
            handler()

    # --- actions -------------------------------------------------------------

    def action_toggle_tunnel(self) -> None:
        if self.busy:
            return
        if not self.running:
            self.start_tunnel()
            return
        if self.inside:
            self.notify("Estás viendo esto a través del túnel: apágalo desde la PC.", severity="warning")
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.stop_tunnel()

        self.push_screen(
            Confirm("¿Apagar el túnel?", "La URL pública dejará de funcionar y quien esté conectado perderá el acceso.", "Apagar"),
            done,
        )

    def start_tunnel(self) -> None:
        self.busy = True
        self.set_status("busy", "● Encendiendo…")
        password = self.query_one("#password", Input).value

        def work() -> None:
            try:
                url = self.tunnel.start(password or None)
                self.call_from_thread(self.on_tunnel_done, url, None)
            except Exception as exc:  # shown to the user, first line
                self.call_from_thread(self.on_tunnel_done, None, str(exc))

        self.run_worker(work, thread=True, exclusive=True)

    def stop_tunnel(self) -> None:
        self.busy = True
        self.set_status("busy", "● Apagando…")

        def work() -> None:
            try:
                self.tunnel.stop()
                self.call_from_thread(self.on_tunnel_done, None, None)
            except Exception as exc:
                self.call_from_thread(self.on_tunnel_done, None, str(exc))

        self.run_worker(work, thread=True, exclusive=True)

    def on_tunnel_done(self, url: str | None, error: str | None) -> None:
        self.busy = False
        if error:
            self.notify(error.strip().splitlines()[0], title="No se pudo", severity="error", timeout=8)
        self.refresh_state()
        if url:
            self.notify("Túnel en línea", timeout=3)

    def action_toggle_password(self) -> None:
        field = self.query_one("#password", Input)
        field.password = not field.password
        self.query_one("#show", Button).label = "Ocultar" if not field.password else "Ver"

    def action_new_password(self) -> None:
        if self.running:
            self.notify("Apaga el túnel para cambiar la contraseña.", severity="warning")
            return
        self.query_one("#password", Input).value = generate_password()
        self.notify("Nueva contraseña generada", timeout=2)

    def action_copy_url(self) -> None:
        if self.url:
            self.copy(self.url, "URL pública copiada")

    def action_open_url(self) -> None:
        if self.url:
            self.open_url(self.url)

    # --- clipboard / browser ---------------------------------------------------

    def copy(self, text: str, message: str) -> None:
        if not text:
            return
        # OSC 52 reaches the clipboard of the device showing this terminal
        # (the phone through the tunnel, the laptop over SSH)...
        self.copy_to_clipboard(text)
        # ...and on the PC's own screen wl-copy is the reliable way.
        if not self.inside and os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            try:
                subprocess.run(["wl-copy"], input=text.encode(), timeout=3, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        self.notify(message, timeout=2)

    def open_url(self, url: str) -> None:
        if self.inside or not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            self.notify(f"Ábrelo en tu navegador: {url}", timeout=6)
            return
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.notify(url, timeout=6)


def run() -> int:
    RemoteControlTUI().run()
    return 0
