"""`remote-control cli` in multi mode: the containers' manager (multi.py) in the terminal.

Same layout as the window (multi_window.py): the containers on the left,
the chosen one on the right with its power, tunnel, URLs, password, QR and
actions. Podman runs in worker threads; the list refreshes every few seconds.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, ListItem, ListView, Select, Static, Switch

from . import control, domains, multi, router
from .tui import Confirm, RemoteControlTUI, qr_text

REFRESH_S = 4


class CreateScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def compose(self) -> ComposeResult:
        with Vertical(id="domain"):
            yield Static("Nuevo contenedor", id="confirm-title")
            yield Static("Nombre", classes="label")
            yield Input(placeholder="backend", id="new-name", compact=True)
            yield Static("Sistema", classes="label")
            yield Select([(label, key) for key, label in multi.BASES.items()], value="fedora", allow_blank=False, id="new-base", compact=True)
            yield Static("", id="domain-result")
            with Horizontal(id="domain-buttons"):
                yield Button("Cancelar", id="new-cancel", compact=True)
                yield Button("Crear", id="new-ok", classes="accent", compact=True)

    def on_input_submitted(self, _event) -> None:
        self.create()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-cancel":
            self.dismiss(None)
        elif event.button.id == "new-ok":
            self.create()

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.step = ""
        self.started = 0.0
        self.tick = 0

    def action_dismiss(self, result=None) -> None:
        # Esc does nothing while it is being created.
        if not self.busy:
            self.dismiss(result)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        ok = self.query_one("#new-ok", Button)
        ok.label = "Creando…" if busy else "Crear"
        for wid in ("#new-ok", "#new-cancel", "#new-name", "#new-base"):
            self.query_one(wid).disabled = busy

    def paint_progress(self) -> None:
        if not self.busy:
            return
        import time

        self.tick += 1
        secs = int(time.monotonic() - self.started)
        spin = self.SPIN[self.tick % len(self.SPIN)]
        self.query_one("#domain-result", Static).update(f"{spin} {self.step}  ·  {secs}s")

    def create(self) -> None:
        import time

        if self.busy:
            return
        name = self.query_one("#new-name", Input).value.strip().lower()
        result = self.query_one("#domain-result", Static)
        if not multi.valid_name(name):
            result.set_classes("error")
            result.update("Nombre inválido: minúsculas, números y guiones.")
            return
        base = str(self.query_one("#new-base", Select).value)
        result.set_classes("busy")
        self.step = f"Creando {name}… (la primera vez tarda unos minutos)"
        self.started = time.monotonic()
        self.set_busy(True)
        self.paint_progress()
        timer = self.set_interval(0.25, self.paint_progress)

        def log(line: str) -> None:
            line = line.strip()
            if line.startswith(("Creando", "Copiando", "Preparando")) or line.endswith("…") or line.startswith("STEP"):
                self.step = line[:70]

        def work() -> None:
            try:
                multi.create(name, base, log=log)
            except Exception as exc:
                def fail() -> None:
                    timer.stop()
                    self.set_busy(False)
                    result.set_classes("error")
                    result.update(str(exc).splitlines()[0])
                self.app.call_from_thread(fail)
                return

            def done() -> None:
                timer.stop()
                self.busy = False
                self.dismiss(name)
            self.app.call_from_thread(done)

        self.run_worker(work, thread=True)


class RenameScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancelar")]

    def __init__(self, old: str) -> None:
        super().__init__()
        self.old = old

    def compose(self) -> ComposeResult:
        with Vertical(id="domain"):
            yield Static(f"Renombrar {self.old}", id="confirm-title")
            yield Input(self.old, id="rename-name", compact=True)
            yield Static("", id="domain-result")
            with Horizontal(id="domain-buttons"):
                yield Button("Cancelar", id="rename-cancel", compact=True)
                yield Button("Renombrar", id="rename-ok", classes="accent", compact=True)

    def on_input_submitted(self, _event) -> None:
        self.rename()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-cancel":
            self.dismiss(None)
        elif event.button.id == "rename-ok":
            self.rename()

    def rename(self) -> None:
        new = self.query_one("#rename-name", Input).value.strip().lower()
        try:
            multi.rename(self.old, new)
        except multi.MultiError as exc:
            result = self.query_one("#domain-result", Static)
            result.set_classes("error")
            result.update(str(exc))
            return
        self.dismiss(new)


class MultiTUI(App):
    # Not a subclass of RemoteControlTUI: Textual would also run its handlers.
    TITLE = "Remote Control · Multi"
    CSS = RemoteControlTUI.CSS + """
    #body { width: 100%; }
    #mcols { width: 100%; height: auto; }
    #side { width: 30; height: auto; min-height: 20; margin-right: 1; }
    #boxes { height: auto; max-height: 24; background: ansi_default; }
    #boxes > ListItem { background: ansi_default; padding: 0 1; }
    #boxes > ListItem.-highlight { background: #1d3b53; }
    .box-sub { color: #8a9bb3; }
    #new { width: 100%; margin-top: 1; }
    #main { width: 1fr; height: auto; }
    #mhero-top { height: auto; }
    #mhero.on { border: round #21c7a8; }
    #mhero.busy { border: round #82aaff; }
    #mname { width: 1fr; height: 3; content-align: left middle; text-style: bold; }
    #power { margin-right: 2; }
    #mmid { height: auto; }
    #mleft { width: 1fr; height: auto; }
    #mqr-card { width: 46; height: auto; margin-left: 1; }
    #actions { height: auto; }
    #actions Button { margin-right: 1; }
    #rm { background: #3b1d22; color: #ef5350; }
    #empty { color: #8a9bb3; padding: 2 2; }
    #domain-buttons Button:disabled, #dom-card Button:disabled { background: #1d3b53; color: #5f7187; text-style: none; }
    #domain-result.busy { color: #82aaff; }
    #dom-sub { width: 1fr; }
    #dom-zone { width: auto; color: #8a9bb3; padding: 0 1; }
    #dom-hint { color: #8a9bb3; margin-top: 1; }
    #dom-hint.warn { color: #ffeb95; }
    """

    BINDINGS = [
        Binding("n", "new", "Nuevo"),
        Binding("space", "toggle_tunnel", "Túnel"),
        Binding("p", "power", "Encender/apagar"),
        Binding("c", "copy_url", "Copiar URL"),
        Binding("o", "open_url", "Abrir"),
        Binding("q", "quit", "Salir"),
    ]

    # Copy and open work as in the single CLI (OSC 52, wl-copy, xdg-open).
    copy = RemoteControlTUI.copy
    open_url = RemoteControlTUI.open_url
    action_copy_url = RemoteControlTUI.action_copy_url
    action_open_url = RemoteControlTUI.action_open_url

    def __init__(self) -> None:
        super().__init__(ansi_color=True)
        self.inside = control.inside_tunnel()
        self.url: str | None = None
        self.boxes: list[multi.Box] = []
        self.selected: str | None = None
        self.tunnel: dict = {}
        self.busy_name: str | None = None
        self.busy_text = ""
        self._qr_for = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="body"):
            with Horizontal(id="mcols"):
                with Vertical(id="side", classes="card"):
                    yield ListView(id="boxes")
                    yield Button("＋ Nuevo contenedor", id="new", classes="accent", compact=True)
                with Vertical(id="main"):
                    yield Static("Crea un contenedor: cada uno es un espacio de trabajo aislado con su propio túnel.", id="empty")
                    with Vertical(id="mhero", classes="card off"):
                        with Horizontal(id="mhero-top"):
                            yield Static(">_", id="badge")
                            yield Static("", id="mname")
                            yield Button("Encender", id="power", compact=True)
                            yield Switch(id="switch", animate=False)
                        with Horizontal(id="chips"):
                            yield Static("● Apagado", id="status-chip", classes="chip")
                            yield Static("", id="minfo", classes="chip")
                    with Horizontal(id="mmid"):
                        with Vertical(id="mleft"):
                            with Vertical(id="share", classes="card"):
                                yield Static("URL pública", classes="label")
                                with Horizontal(classes="row"):
                                    yield Static("", id="public-url", classes="url")
                                    yield Button("Copiar", id="copy-url", classes="accent", compact=True)
                                yield Static("URL local", classes="label")
                                with Horizontal(classes="row"):
                                    yield Static("", id="local-url", classes="url")
                                    yield Button("Copiar", id="copy-local", compact=True)
                            with Vertical(id="pass-card", classes="card"):
                                with Horizontal(classes="row"):
                                    yield Input(id="password", password=True, compact=True)
                                    yield Button("Ver", id="show", compact=True)
                                    yield Button("↻ Nueva", id="regen", compact=True)
                                    yield Button("Copiar", id="copy-pass", classes="accent", compact=True)
                            with Vertical(id="dom-card", classes="card"):
                                with Horizontal(classes="row"):
                                    yield Static("No disponible", id="dom-state", classes="chip")
                                    yield Input(id="dom-sub", compact=True)
                                    yield Static("", id="dom-zone")
                                    yield Button("Activar", id="dom-btn", classes="accent", compact=True)
                                    yield Button("Cuenta", id="dom-account", compact=True)
                                yield Static("", id="dom-hint")
                            with Vertical(id="auto-card", classes="card"):
                                with Horizontal(id="auto-row"):
                                    yield Static("Encender con el sistema", id="auto-title")
                                    yield Switch(id="autostart", animate=False)
                            with Horizontal(id="actions"):
                                yield Button("Actualizar", id="update", compact=True)
                                yield Button("Renombrar", id="rename", compact=True)
                                yield Button("Borrar", id="rm", compact=True)
                        with Vertical(id="mqr-card", classes="card"):
                            with Center(id="qr-wrap"):
                                yield Static("", id="qr")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#side").border_title = "Contenedores"
        self.query_one("#share").border_title = "Compartir"
        self.query_one("#pass-card").border_title = "Contraseña"
        self.query_one("#mqr-card").border_title = "Abrir en el celular"
        self.query_one("#dom-card").border_title = "Dominio propio"
        self.reach: domains.Reach | None = None
        self.dom_for: str | None = None
        self.probe_reach()
        self.query_one("#password", Input).disabled = True
        self.refresh_boxes()
        self.set_interval(REFRESH_S, self.refresh_boxes)

    # --- state ---------------------------------------------------------------

    def refresh_boxes(self) -> None:
        def work() -> None:
            try:
                boxes = multi.list_boxes()
            except multi.MultiError as exc:
                self.call_from_thread(self.notify, str(exc), severity="error")
                return
            box = next((b for b in boxes if b.name == self.selected), None) or (boxes[0] if boxes and not self.selected else None)
            tunnel = multi.tunnel_status(box.name) if box and box.running and box.name != self.busy_name else None
            auto = multi.autostart(box.name) if box and box.running else None
            self.call_from_thread(self.paint, boxes, tunnel, auto)

        self.run_worker(work, thread=True, group="refresh", exclusive=True)

    def paint(self, boxes: list[multi.Box], tunnel: dict | None, auto: bool | None) -> None:
        view = self.query_one("#boxes", ListView)
        if [b.name for b in boxes] != [b.name for b in self.boxes]:
            view.clear()
            for b in boxes:
                # Held directly: a just appended item has not mounted its children yet.
                title, sub = Static(""), Static("", classes="box-sub")
                item = ListItem(title, sub)
                item.box_name, item.title, item.sub = b.name, title, sub
                view.append(item)
        self.boxes = boxes
        for item, b in zip(view.children, boxes):
            dot = Text("● " if b.running else "○ ", style="#22da6e" if b.running else "#8a9bb3")
            item.title.update(dot + Text(b.name, style="bold"))
            item.sub.update(f"  {multi.BASES.get(b.base, b.base)} · {multi.human_size(b.size)}")
        names = [b.name for b in boxes]
        if self.selected not in names:
            self.selected = names[0] if names else None
            self.tunnel = {}
            self.load_password()
        if self.selected:
            view.index = names.index(self.selected)
        if tunnel is not None:
            self.tunnel = tunnel
        if auto is not None:
            sw = self.query_one("#autostart", Switch)
            with sw.prevent(Switch.Changed):
                sw.value = auto
        self.paint_panel()

    def box(self) -> multi.Box | None:
        return next((b for b in self.boxes if b.name == self.selected), None)

    def paint_panel(self) -> None:
        b = self.box()
        self.query_one("#empty").display = b is None
        for wid in ("#mhero", "#mmid"):
            self.query_one(wid).display = b is not None
        if b is None:
            return
        busy = b.name == self.busy_name
        on = bool(self.tunnel.get("running")) and b.running
        kind = "busy" if busy else "on" if on else "off"
        hero = self.query_one("#mhero")
        hero.set_classes(f"card {kind}")
        self.query_one("#mname", Static).update(b.name)
        text = f"● {self.busy_text or 'Un momento…'}" if busy else "● En línea" if on else "● Túnel apagado" if b.running else "● Contenedor apagado"
        chip = self.query_one("#status-chip", Static)
        chip.set_classes(f"chip {kind}")
        chip.update(text)
        self.query_one("#minfo", Static).update(f"{multi.BASES.get(b.base, b.base)} · {multi.human_size(b.size)}")
        self.query_one("#power", Button).label = "Apagar" if b.running else "Encender"
        self.query_one("#power", Button).disabled = busy
        sw = self.query_one("#switch", Switch)
        with sw.prevent(Switch.Changed):
            sw.value = on
        sw.disabled = busy
        self.query_one("#autostart", Switch).disabled = busy or not b.running
        # Its password changes with the tunnel off, container on or not (multi.password).
        self.query_one("#regen", Button).disabled = busy or on
        url = (self.tunnel.get("url") or "") if on else ""
        self.url = url or None
        self.query_one("#public-url", Static).update(Text(url, style=f"link {url}") if url else Text("Aparece al encender el túnel", style="#5f7187"))
        # Nothing answers there with the tunnel off.
        local = f"http://localhost:{b.port}/" if on else ""
        self.query_one("#local-url", Static).update(Text(local) if on else Text("Aparece al encender el túnel", style="#5f7187"))
        if url != self._qr_for:
            art = qr_text(url) if url else None
            self.query_one("#qr", Static).update(art if art is not None else Text("Enciende el túnel para ver el QR", style="#8a9bb3"))
            self._qr_for = url
        self.paint_domain()

    def probe_reach(self) -> None:
        def work() -> None:
            try:
                reach = domains.direct_reach(domains.public_ipv4())
            except Exception as exc:
                reach = domains.Reach(False, str(exc), domains.FALLBACK_PORT)
            self.reach = reach
            self.call_from_thread(self.paint_domain)

        self.run_worker(work, thread=True, group="reach")

    def paint_domain(self) -> None:
        b = self.box()
        if b is None:
            return
        cfg = domains.load_config()
        zone = cfg.zone if cfg else ""
        host = router.load_routes().get(b.name)
        state, sub, btn, hint = (self.query_one(w) for w in ("#dom-state", "#dom-sub", "#dom-btn", "#dom-hint"))
        self.query_one("#dom-zone", Static).update(f".{zone}" if zone else "")
        busy = b.name == self.busy_name
        # The name is set with the tunnel off; on, it is the one in use.
        on = bool(self.tunnel.get("running")) and b.running
        # With the tunnel on nothing here changes, the account included.
        self.query_one("#dom-account", Button).disabled = busy or on
        hint.set_classes("")
        if host:
            sub.value = host[: -len(zone) - 1] if zone and host.endswith("." + zone) else host
            sub.disabled = True
            btn.label, btn.disabled = "Quitar", busy or on
            state.set_classes("chip on")
            state.update("Activo")
            url = router.public_url(host, self.reach.public_port if self.reach else 443)
            hint.update(url + ("  ·  apaga el túnel para cambiarlo" if on else ""))
            return
        btn.label = "Activar"
        if self.dom_for != b.name:
            sub.value = b.name
            self.dom_for = b.name
        if not zone:
            state.set_classes("chip")
            state.update("Sin cuenta")
            hint.update("Configura tu proveedor de dominios con «Cuenta».")
            ok = False
        elif self.reach is None:
            state.set_classes("chip busy")
            state.update("Comprobando…")
            hint.update("Comprobando si tu PC es alcanzable desde internet…")
            ok = False
        elif not self.reach.ok:
            state.set_classes("chip")
            state.update("No disponible")
            hint.set_classes("warn")
            hint.update(self.reach.why + ".")
            ok = False
        else:
            state.set_classes("chip on")
            state.update("Disponible")
            hint.update(self.reach.why + ".")
            ok = True
        if ok and on:
            hint.update("Apaga el túnel para configurar el dominio.")
        sub.disabled = not ok or busy or on
        btn.disabled = not ok or busy or on

    def account_dialog(self) -> None:
        """The provider account (the same one the window's ⚙ opens)."""
        from .tui import DomainScreen

        def done(_changed: bool | None) -> None:
            self.paint_domain()

        self.push_screen(DomainScreen(account_only=True), done)

    def toggle_domain(self) -> None:
        b = self.box()
        if b is None:
            return
        name, had, tunnel_on = b.name, router.load_routes().get(b.name), bool(self.tunnel.get("running"))
        sub = self.query_one("#dom-sub", Input).value.strip().lower()

        def work():
            if had:
                router.unassign(name)
                url = None
            else:
                url = router.assign(name, sub)
            # Its URL changes (domain ↔ trycloudflare): the tunnel picks it up on start.
            if tunnel_on:
                multi.tunnel_stop(name)
                url = multi.tunnel_start(name)
            return url

        if not had:
            self.query_one("#dom-hint", Static).update("Apuntando el nombre y pidiendo el certificado HTTPS (puede tardar unos minutos)…")
        self.act(work, f"{had} quitado" if had else "Dominio activo", lambda url: tunnel_on and url and self.tunnel.update(running=True, url=url))

    def load_password(self) -> None:
        b = self.box()
        field = self.query_one("#password", Input)
        field.value = ""
        if b is None:
            return
        name = b.name

        def work() -> None:
            try:
                pw = multi.password(name)
            except multi.MultiError:
                return
            if name == self.selected:
                self.call_from_thread(setattr, field, "value", pw)

        self.run_worker(work, thread=True, group="password")

    # --- events --------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = getattr(event.item, "box_name", None)
        if name and name != self.selected:
            self.selected = name
            self.tunnel = {}
            self.paint_panel()
            self.load_password()
            self.refresh_boxes()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            self.on_list_view_selected(event)  # type: ignore[arg-type]

    def on_switch_changed(self, event: Switch.Changed) -> None:
        b = self.box()
        if b is None:
            return
        if event.switch.id == "autostart":
            on = event.value
            self.act(lambda: multi.set_autostart(b.name, on), "Se encenderá con su túnel al iniciar sesión" if on else "Ya no se enciende solo")
            return
        with event.switch.prevent(Switch.Changed):
            event.switch.value = not event.value
        self.action_toggle_tunnel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        handlers = {
            "new": self.action_new,
            "power": self.action_power,
            "copy-url": self.action_copy_url,
            "copy-local": lambda: self.url and self.box() and self.copy(f"http://localhost:{self.box().port}/", "URL local copiada"),
            "copy-pass": lambda: self.copy(self.query_one("#password", Input).value, "Contraseña copiada"),
            "show": self.action_toggle_password,
            "regen": self.new_password,
            "update": self.update_rc,
            "rename": self.rename_box,
            "rm": self.remove_box,
            "dom-btn": self.toggle_domain,
            "dom-account": self.account_dialog,
        }
        handler = handlers.get(event.button.id or "")
        if handler:
            handler()

    # --- actions -------------------------------------------------------------

    def act(self, work, ok: str, after=None) -> None:
        b = self.box()
        if b is None:
            return
        self.busy_name = b.name
        self.paint_panel()

        def run() -> None:
            try:
                result = work()
            except Exception as exc:
                self.call_from_thread(self.notify, str(exc).splitlines()[0], title="No se pudo", severity="error", timeout=8)
            else:
                self.call_from_thread(self.notify, ok, timeout=4)
                if after:
                    self.call_from_thread(after, result)
            finally:
                self.busy_name = None
                self.busy_text = ""
                self.call_from_thread(self.refresh_boxes)

        self.run_worker(run, thread=True, group="act")

    def action_new(self) -> None:
        def done(name: str | None) -> None:
            if name:
                self.selected = name
                self.notify(f"{name} listo", timeout=4)
                self.refresh_boxes()

        self.push_screen(CreateScreen(), done)

    def action_power(self) -> None:
        b = self.box()
        if b is None:
            return
        if b.running:
            self.tunnel = {}
            self.act(lambda: multi.stop(b.name), f"{b.name} apagado")
        else:
            self.act(lambda: multi.start(b.name), f"{b.name} encendido", lambda _r: self.load_password())

    def action_toggle_tunnel(self) -> None:
        b = self.box()
        if b is None or self.busy_name:
            return
        if not self.tunnel.get("running"):
            self.act(lambda: multi.tunnel_start(b.name), "Túnel en línea", lambda url: self.tunnel.update(running=True, url=url))
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.act(lambda: multi.tunnel_stop(b.name), "Túnel apagado", lambda _r: self.tunnel.clear())

        self.push_screen(Confirm("¿Apagar el túnel?", f"La URL pública de {b.name} dejará de funcionar y se apaga el contenedor. Sus terminales vuelven al encenderlo.", "Apagar"), done)

    def action_toggle_password(self) -> None:
        field = self.query_one("#password", Input)
        field.password = not field.password
        self.query_one("#show", Button).label = "Ocultar" if not field.password else "Ver"

    def new_password(self) -> None:
        b = self.box()
        if b is not None:
            self.act(lambda: multi.password(b.name, "new"), "Nueva contraseña", lambda pw: setattr(self.query_one("#password", Input), "value", pw))

    def update_rc(self) -> None:
        b = self.box()
        if b is not None and b.running:
            def step(line: str) -> None:
                # What it is doing, in the status chip (the environment can take minutes).
                if line.strip():
                    self.busy_text = line.strip()[:60]
                    self.call_from_thread(self.paint_panel)

            self.act(lambda: multi.install_rc(b.name, log=step), "Remote Control actualizado adentro")
        elif b is not None:
            self.notify("Enciende el contenedor primero", severity="warning")

    def rename_box(self) -> None:
        b = self.box()
        if b is None:
            return

        def done(new: str | None) -> None:
            if new:
                self.selected = new
                self.notify(f"Ahora se llama {new}", timeout=4)
                self.refresh_boxes()

        self.push_screen(RenameScreen(b.name), done)

    def remove_box(self) -> None:
        b = self.box()
        if b is None:
            return

        def done(yes: bool | None) -> None:
            if yes:
                self.act(lambda: multi.remove(b.name), f"{b.name} borrado")

        self.push_screen(
            Confirm(
                f"¿Borrar {b.name}?",
                f"Se borra el contenedor con todo lo que tiene ({multi.human_size(b.size)}): archivos, programas instalados y sesiones. No se puede deshacer.",
                "Borrar",
            ),
            done,
        )
