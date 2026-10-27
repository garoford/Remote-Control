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
from textual.widgets import Button, Footer, Input, Select, Static, Switch

from . import autostart, control, domains
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


class DomainScreen(ModalScreen[bool]):
    """Your own domain (domains.py): key, then one of the account's domains.

    ``account_only`` (multi_tui.py): just the provider account and its domain;
    each container picks its own name, so no name or reach check here.
    """

    BINDINGS = [Binding("escape", "dismiss(False)", "Cancelar")]

    def __init__(self, account_only: bool = False) -> None:
        super().__init__()
        self.account_only = account_only
        self.saved = domains.load_config()
        self.cfg = self.saved or domains.DomainConfig(provider="namecom")
        self.zones: list[str] = []
        self.reach_ok = account_only

    def compose(self) -> ComposeResult:
        cfg = self.cfg
        with VerticalScroll(id="domain"):
            yield Static("Cuenta de dominios" if self.account_only else "Dominio propio", id="confirm-title")
            yield Static("Proveedor", classes="label")
            yield Select([(label, key) for key, label in domains.PROVIDERS.items()], value=cfg.provider, allow_blank=False, id="provider", compact=True)
            # Every provider's fields; only the chosen one's are shown.
            for provider, fields in domains.FIELDS.items():
                for key, label, secret, _required in fields:
                    value = cfg.creds.get(key, "") if provider == cfg.provider else ""
                    yield Static(label, classes=f"label cred cred-{provider}")
                    yield Input(value, password=secret, id=f"cred-{provider}-{key}", classes=f"cred cred-{provider}", compact=True)
            with Horizontal(id="load-row"):
                yield Button("Cargar mis dominios", id="domain-load", compact=True)
            with Vertical(id="pick"):
                yield Static("Dominio", classes="label")
                yield Select([], prompt="Elige un dominio", id="zone", compact=True)
                yield Static("Subdominio (vacío = el dominio mismo)", classes="label sub-only")
                yield Input("rc", id="sub", classes="sub-only", compact=True)
                yield Static("", id="preview", classes="sub-only")
            # Served here, no redirect: it can be saved only once reachable.
            yield Static("Comprobando si tu PC es alcanzable desde internet…", id="reach", classes="sub-only")
            yield Static("", id="domain-result")
            with Horizontal(id="domain-buttons"):
                yield Button("Quitar" if self.saved else "Cancelar", id="domain-remove", compact=True)
                yield Button("Guardar", id="domain-save", classes="accent", compact=True)

    def probe(self) -> None:
        def work() -> None:
            try:
                reach = domains.direct_reach(domains.public_ipv4())
            except Exception as exc:
                reach = domains.Reach(False, str(exc), domains.FALLBACK_PORT)
            self.app.call_from_thread(self.reached, reach)

        self.run_worker(work, thread=True, group="reach")

    def reached(self, reach: domains.Reach) -> None:
        self.reach_ok = reach.ok
        label = self.query_one("#reach", Static)
        label.set_classes("" if reach.ok else "error")
        label.update(f"✓ {reach.why}." if reach.ok else f"No disponible: {reach.why}.")
        self.paint_save()

    def paint_save(self) -> None:
        self.query_one("#domain-save", Button).disabled = not (self.zones and self.reach_ok)

    def on_mount(self) -> None:
        for widget in self.query(".sub-only"):
            widget.display = not self.account_only
        if not self.account_only:
            self.probe()
        self.show_provider(self.cfg.provider)
        saved = self.saved
        if saved and saved.zone:
            # Editing a saved one: its domain is there without asking the API again.
            name = saved.host[: -len(saved.zone)].rstrip(".") if saved.host.endswith(saved.zone) else ""
            self.query_one("#sub", Input).value = name
            self.set_zones([saved.zone], saved.zone)
        else:
            self.set_zones([])

    def show_provider(self, provider: str) -> None:
        for widget in self.query(".cred"):
            widget.display = widget.has_class(f"cred-{provider}")

    def set_zones(self, zones: list[str], selected: str = "") -> None:
        self.zones = zones
        select = self.query_one("#zone", Select)
        select.set_options([(z, z) for z in zones])
        if selected in zones:
            select.value = selected
        elif zones:
            select.value = zones[0]
        self.query_one("#pick").display = bool(zones)
        self.paint_save()
        self.paint_preview()

    def chosen_host(self) -> str:
        value = self.query_one("#zone", Select).value
        if not self.zones or not isinstance(value, str):
            return ""
        return domains.compose_host(self.query_one("#sub", Input).value, value)

    def paint_preview(self) -> None:
        host = self.chosen_host()
        self.query_one("#preview", Static).update(f"Quedará en {host}" if host else "")

    def say(self, message: str, error: bool = False) -> None:
        result = self.query_one("#domain-result", Static)
        result.set_classes("error" if error else "")
        result.update(message)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "provider":
            self.show_provider(str(event.value))
            # Another account: its domains have to be loaded again.
            self.set_zones([])
            self.say("")
        elif event.select.id == "zone":
            self.paint_preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sub":
            self.paint_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "domain-remove":
            if self.saved:
                domains.clear_config()
                self.app.notify("Dominio quitado: vuelve trycloudflare.com", timeout=4)
            self.dismiss(bool(self.saved))
        elif event.button.id == "domain-load":
            self.load()
        elif event.button.id == "domain-save":
            self.save()

    def creds(self, provider: str) -> dict[str, str]:
        return {key: self.query_one(f"#cred-{provider}-{key}", Input).value.strip() for key, *_rest in domains.FIELDS[provider]}

    def load(self) -> None:
        provider = str(self.query_one("#provider", Select).value)
        creds = self.creds(provider)
        self.say("Consultando tu cuenta…")
        self.query_one("#domain-load", Button).disabled = True

        def work() -> None:
            try:
                zones = domains.account_domains(provider, creds)
                self.app.call_from_thread(self.loaded, zones, "")
            except Exception as exc:
                self.app.call_from_thread(self.loaded, [], str(exc))

        self.run_worker(work, thread=True, exclusive=True)

    def loaded(self, zones: list[str], error: str) -> None:
        self.query_one("#domain-load", Button).disabled = False
        if error:
            self.say(error, error=True)
            return
        current = self.query_one("#zone", Select).value
        self.set_zones(zones, current if isinstance(current, str) else self.cfg.zone)
        self.say(f"{len(zones)} dominio(s) en la cuenta.")

    def save(self) -> None:
        provider = str(self.query_one("#provider", Select).value)
        zone = self.query_one("#zone", Select).value
        new = domains.DomainConfig(
            provider=provider,
            # Account only: never the bare domain as this machine's name.
            host=(self.saved.host if self.saved and self.saved.zone == zone else "") if self.account_only else self.chosen_host(),
            zone=zone if isinstance(zone, str) else "",
            creds=self.creds(provider),
            mode="direct",
            email=self.cfg.email,
        )
        self.say("Comprobando…")
        self.query_one("#domain-save", Button).disabled = True

        def work() -> None:
            try:
                message = domains.check(new)
                domains.save_config(new)
                self.app.call_from_thread(self.done, message, True)
            except Exception as exc:
                self.app.call_from_thread(self.done, str(exc), False)

        self.run_worker(work, thread=True, exclusive=True)

    def done(self, message: str, ok: bool) -> None:
        self.paint_save()
        if ok:
            self.app.notify(message, title="Dominio guardado", timeout=8)
            self.dismiss(True)
            return
        self.say(message, error=True)


class RemoteControlTUI(App):
    TITLE = "Remote Control"
    CSS = """
    /* A compact panel, centred both ways; it scrolls only when the terminal
       is shorter than the panel. */
    /* ansi_default: the terminal's own background shows through (the
       picture or color chosen in Remote Control, any terminal theme). */
    Screen { background: ansi_default; color: #d6deeb; align: center middle; }
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
        background: ansi_default; border: round #1d3b53;
        border-title-color: #d6deeb; border-title-style: bold;
        border-subtitle-color: #8a9bb3;
    }
    #hero.on { border: round #21c7a8; }
    #hero.busy { border: round #82aaff; }
    #hero-top { height: auto; }
    #badge { width: 6; height: 3; content-align: center middle; background: #21c7a8; color: #011627; text-style: bold; }
    #hero.off #badge { background: #1d3b53; color: #7fdbca; }
    #hero-text { width: 1fr; height: auto; padding: 0 2; }
    #hero-title { height: 3; content-align: left middle; text-style: bold; }
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
    #auto-row { height: auto; }
    #auto-title { width: 1fr; height: 3; content-align: left middle; text-style: bold; }
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
    #domain-row { height: auto; }
    #domain-text { width: 1fr; height: auto; }
    #domain-title { width: auto; text-style: bold; margin-right: 1; }
    #domain-head { height: 1; }
    #domain-card.dim #domain-title, #domain-card.dim #domain-info { color: #5f7187; }
    #domain-info { color: #8a9bb3; }
    #domain-row Button { margin-left: 1; }
    #domain { width: 64; height: auto; max-height: 90%; padding: 1 2; background: #0b2942; border: round #82aaff; }
    #domain Input { background: #011627; }
    #domain Input:focus { background: #1d3b53; }
    #domain-buttons { height: auto; align-horizontal: right; margin-top: 1; }
    #domain-buttons Button { margin-left: 1; }
    #domain-result { margin-top: 1; color: #8a9bb3; }
    #load-row { height: auto; margin: 1 0; }
    #pick { height: auto; }
    #preview { color: #7fdbca; margin-top: 1; }
    #reach { color: #7fdbca; }
    #reach.error { color: #ffeb95; }
    #domain-result.error { color: #ffeb95; }
    ModalScreen { align: center middle; background: #011627 70%; }
    Footer { background: ansi_default; }
    """

    BINDINGS = [
        Binding("space", "toggle_tunnel", "Encender/apagar"),
        Binding("p", "toggle_password", "Ver clave"),
        Binding("n", "new_password", "Nueva clave"),
        Binding("c", "copy_url", "Copiar URL"),
        Binding("o", "open_url", "Abrir"),
        Binding("a", "toggle_autostart", "Inicio automático"),
        Binding("d", "domain", "Dominio"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        # ansi_color keeps "ansi_default" as the terminal's own background
        # instead of turning it into a fixed color.
        super().__init__(ansi_color=True)
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
                    with Vertical(id="auto-card", classes="card"):
                        with Horizontal(id="auto-row"):
                            yield Static("Iniciar con el sistema", id="auto-title")
                            yield Switch(value=autostart.is_enabled(), id="autostart", animate=False)
                    with Vertical(id="domain-card", classes="card"):
                        with Horizontal(id="domain-row"):
                            with Vertical(id="domain-text"):
                                with Horizontal(id="domain-head"):
                                    yield Static("Dominio propio", id="domain-title")
                                    yield Static("No disponible", id="domain-state", classes="chip off")
                                yield Static("", id="domain-info")
                            yield Button("Configurar", id="domain-open", compact=True)
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
        self.refresh_domain()
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
        switch = self.query_one("#switch", Switch)
        with switch.prevent(Switch.Changed):
            switch.value = kind != "off"
        # Inside the tunnel it may be switched on, never off.
        switch.disabled = self.busy or (self.inside and self.running)
        self.query_one("#password", Input).disabled = self.running or self.busy
        self.query_one("#regen", Button).disabled = self.running or self.busy
        # The domain is chosen at start: not while the tunnel runs.
        self.query_one("#domain-open", Button).disabled = self.running or self.busy
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
        lock = self.query_one("#lock-chip", Static)
        lock.update("🔒 Con contraseña" if text else "🔓 Sin contraseña")
        lock.set_classes("chip" if text else "chip warn")

    def set_domain_state(self, kind: str, text: str, detail: str) -> None:
        """kind: on (in use), busy (checking) or off (looks disabled)."""
        state = self.query_one("#domain-state", Static)
        state.set_classes(f"chip {kind}")
        state.update(text)
        self.query_one("#domain-info", Static).update(detail)
        self.query_one("#domain-card").set_class(kind != "on", "dim")

    def refresh_domain(self) -> None:
        """Active only once the settings are complete and this machine is
        reachable from the internet (domains.direct_reach); off until then."""
        cfg = domains.load_config()
        if cfg is None or not cfg.host:
            self.set_domain_state("off", "No disponible", "Sin configurar")
            return
        problems = cfg.problems()
        if problems:
            self.set_domain_state("off", "No disponible", problems[0])
            return
        detail = f"{cfg.host} · {domains.PROVIDERS.get(cfg.provider, cfg.provider)}"
        self.set_domain_state("busy", "Comprobando…", detail)

        def work() -> None:
            try:
                reach = domains.direct_reach(domains.public_ipv4())
            except Exception as exc:
                reach = domains.Reach(False, str(exc), domains.FALLBACK_PORT)
            if reach.ok:
                self.call_from_thread(self.set_domain_state, "on", "Disponible", detail)
            else:
                self.call_from_thread(self.set_domain_state, "off", "No disponible", reach.why)

        self.run_worker(work, thread=True, group="domain-check", exclusive=True)

    def paint_qr(self) -> None:
        if not self.url:
            return
        art = qr_text(self.url)
        self.query_one("#qr", Static).update(art if art is not None else "Instala 'segno' para ver el QR")
        self._qr_for = self.url

    # --- events --------------------------------------------------------------

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "autostart":
            self.set_autostart(event.value)
            return
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
            "domain-open": self.action_domain,
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
            Confirm("¿Apagar el túnel?", "La URL pública dejará de funcionar y se cierran las terminales. Vuelven como estaban al encenderlo.", "Apagar"),
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
                self.tunnel.stop(close_sessions=True)
                self.call_from_thread(self.on_tunnel_done, None, None)
            except Exception as exc:
                self.call_from_thread(self.on_tunnel_done, None, str(exc))

        self.run_worker(work, thread=True, exclusive=True)

    def on_tunnel_done(self, url: str | None, error: str | None) -> None:
        self.busy = False
        self.refresh_domain()
        if error:
            self.notify(error.strip().splitlines()[0], title="No se pudo", severity="error", timeout=8)
        elif self.tunnel.domain_warning:
            self.notify(self.tunnel.domain_warning, title="Dominio propio", severity="warning", timeout=10)
        self.refresh_state()
        if url:
            self.notify("Túnel en línea", timeout=3)

    def action_domain(self) -> None:
        if self.running or self.busy:
            self.notify("Apaga el túnel para cambiar el dominio.", severity="warning")
            return
        def done(changed: bool | None) -> None:
            self.refresh_domain()
            if changed and self.running:
                self.notify("Apaga y enciende el túnel para usar el dominio.", timeout=6)

        self.push_screen(DomainScreen(), done)

    def action_toggle_autostart(self) -> None:
        self.query_one("#autostart", Switch).value = not autostart.is_enabled()

    def set_autostart(self, on: bool) -> None:
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            self.notify(f"No se pudo: {exc.strerror or exc}", severity="error")
            switch = self.query_one("#autostart", Switch)
            with switch.prevent(Switch.Changed):
                switch.value = autostart.is_enabled()
            return
        self.notify("Se encenderá al iniciar sesión" if on else "Ya no se enciende al iniciar sesión", timeout=3)

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
    from . import multi

    if multi.enabled():
        from .multi_tui import MultiTUI

        MultiTUI().run()
    else:
        RemoteControlTUI().run()
    return 0
