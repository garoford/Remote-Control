"""The desktop window in multi mode: a manager of workspace containers (multi.py).

Sidebar: the containers, their state and size, and "Nuevo". The right side
is the chosen one: power for the container, its tunnel (switch, URLs, QR,
password), autostart, and actions (update, rename, delete). Every Podman
call runs off the GTK thread; the sidebar refreshes every few seconds.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from remote_control import control, domains, multi, router

REFRESH_S = 4
# Width of the right column (QR, and the actions under it): edges line up.
RIGHT = 330


def _bg(work, done) -> None:
    """work() in a thread, done(result, error) back on the GTK thread."""

    def run() -> None:
        try:
            result, error = work(), None
        except Exception as exc:
            result, error = None, str(exc)
        GLib.idle_add(lambda: done(result, error) and False)

    threading.Thread(target=run, daemon=True).start()


class MultiWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title("Remote Control")
        self.set_default_size(1100, 760)
        self.add_css_class("rc-window")
        self.boxes: list[multi.Box] = []
        self.selected: str | None = None
        self.tunnel: dict = {}
        self._busy: set[str] = set()
        self._qr_for = ""
        # The container whose password is in the field.
        self._shown: str | None = None
        # Can the internet reach this machine (domains.direct_reach)? Asked once.
        self._reach: domains.Reach | None = None
        self._shown_domain_for: str | None = None
        self._load_css()
        self._build()
        self.refresh()
        GLib.timeout_add_seconds(REFRESH_S, self._tick)

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name("style.css")))
        # Above the user's own gtk.css, as the single window does.
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    # --- layout --------------------------------------------------------------

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("rc-header")
        header.set_title_widget(Adw.WindowTitle(title="Remote Control", subtitle="Multi"))
        toolbar.add_top_bar(header)
        self.toasts = Adw.ToastOverlay()
        toolbar.set_content(self.toasts)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        root.add_css_class("rc-canvas")
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(14 if side != "top" else 4)
        root.append(self._build_sidebar())
        self.detail = Gtk.Stack()
        self.detail.set_hexpand(True)
        self.detail.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.detail.add_named(self._build_empty(), "empty")
        self.detail.add_named(self._build_panel(), "panel")
        root.append(self.detail)
        self.toasts.set_child(root)
        self.set_content(toolbar)

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        side.add_css_class("rc-card")
        side.add_css_class("rc-sidebar")
        side.set_size_request(230, -1)
        title = Gtk.Label(label="Contenedores", xalign=0)
        title.add_css_class("rc-card-title")
        side.append(title)
        self.list = Gtk.ListBox()
        self.list.add_css_class("rc-box-list")
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.connect("row-selected", self._on_row)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.list)
        side.append(scroll)
        new = Gtk.Button(label="＋ Nuevo contenedor")
        new.add_css_class("rc-btn-save")
        new.connect("clicked", lambda *_: self._dialog_create())
        side.append(new)
        if not multi.available():
            warn = Gtk.Label(label="Falta Podman: instálalo para crear contenedores.", wrap=True, xalign=0)
            warn.add_css_class("rc-hint-text")
            warn.add_css_class("warn")
            side.append(warn)
            new.set_sensitive(False)
        return side

    def _build_empty(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("rc-card")
        box.set_valign(Gtk.Align.FILL)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("rc-qr-empty")
        inner.append(icon)
        self.empty_label = Gtk.Label(label="Crea un contenedor: cada uno es un espacio de trabajo aislado con su propio túnel.",
                                     wrap=True, justify=Gtk.Justification.CENTER)
        self.empty_label.set_max_width_chars(40)
        self.empty_label.add_css_class("rc-hint-text")
        inner.append(self.empty_label)
        box.append(inner)
        return box

    def _build_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Hero: the container, its state and the tunnel switch.
        self.hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.hero.add_css_class("rc-card")
        self.hero.add_css_class("rc-hero")
        badge = Gtk.Box()
        badge.add_css_class("rc-badge")
        badge.set_size_request(46, 46)
        badge.set_valign(Gtk.Align.CENTER)
        badge.set_homogeneous(True)
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        icon.set_pixel_size(22)
        badge.append(icon)
        self.hero.append(badge)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        self.name_label = Gtk.Label(xalign=0)
        self.name_label.add_css_class("rc-hero-title")
        texts.append(self.name_label)
        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.state_chip = Gtk.Label()
        self.state_chip.add_css_class("rc-domain-state")
        chips.append(self.state_chip)
        self.info_label = Gtk.Label(xalign=0)
        self.info_label.add_css_class("rc-hint-text")
        chips.append(self.info_label)
        texts.append(chips)
        self.hero.append(texts)
        self.power_btn = Gtk.Button()
        self.power_btn.add_css_class("rc-btn-cancel")
        self.power_btn.set_valign(Gtk.Align.CENTER)
        self.power_btn.connect("clicked", lambda *_: self._toggle_power())
        self.hero.append(self.power_btn)
        tunnel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        tunnel_box.set_valign(Gtk.Align.CENTER)
        tlabel = Gtk.Label(label="Túnel")
        tlabel.add_css_class("rc-field-label")
        tunnel_box.append(tlabel)
        self.switch = Gtk.Switch()
        self.switch.add_css_class("rc-switch")
        self.switch.connect("state-set", self._on_switch)
        tunnel_box.append(self.switch)
        self.hero.append(tunnel_box)
        panel.append(self.hero)

        # Middle: links and password beside the QR (as the single window).
        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        left.set_hexpand(True)
        share = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        share.add_css_class("rc-card")
        share.set_vexpand(True)
        head = Gtk.Label(label="Compartir", xalign=0)
        head.add_css_class("rc-card-title")
        share.append(head)
        self.url_entry = Gtk.Entry(placeholder_text="Aparece al encender el túnel")
        share.append(self._row("URL pública", self.url_entry, [
            ("edit-copy-symbolic", "Copiar", lambda *_: self._copy(self.url_entry.get_text(), "URL pública copiada")),
            ("web-browser-symbolic", "Abrir", lambda *_: self._open(self.url_entry.get_text())),
        ]))
        self.local_entry = Gtk.Entry()
        share.append(self._row("URL local", self.local_entry, [
            ("edit-copy-symbolic", "Copiar", lambda *_: self._copy(self.local_entry.get_text(), "URL local copiada")),
            ("web-browser-symbolic", "Abrir", lambda *_: self._open(self.local_entry.get_text())),
        ]))
        left.append(share)

        pw = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        pw.add_css_class("rc-card")
        pt = Gtk.Label(label="Contraseña", xalign=0)
        pt.add_css_class("rc-card-title")
        pw.append(pt)
        self.pass_entry = Gtk.Entry()
        self.pass_entry.set_visibility(False)
        self.pass_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
        self.pass_entry.connect("icon-press", lambda e, _p: e.set_visibility(not e.get_visibility()))
        self.regen_btn = None
        pw.append(self._row(None, self.pass_entry, [
            ("view-refresh-symbolic", "Nueva contraseña", lambda *_: self._new_password()),
            ("edit-copy-symbolic", "Copiar", lambda *_: self._copy(self.pass_entry.get_text(), "Contraseña copiada")),
        ]))
        left.append(pw)
        left.append(self._build_domain())
        middle.append(left)

        qr = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        qr.add_css_class("rc-card")
        qr.set_size_request(RIGHT, -1)
        qr.set_hexpand(False)
        qt = Gtk.Label(label="Abrir en el celular", xalign=0)
        qt.add_css_class("rc-card-title")
        qr.append(qt)
        self.qr_stack = Gtk.Stack()
        self.qr_stack.set_vexpand(True)
        frame = Gtk.Box()
        frame.add_css_class("rc-qr")
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_valign(Gtk.Align.CENTER)
        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(196, 196)
        frame.append(self.qr_picture)
        self.qr_stack.add_named(frame, "qr")
        empty = Gtk.Label(label="Enciende el túnel para ver el QR", wrap=True, justify=Gtk.Justification.CENTER)
        empty.add_css_class("rc-hint-text")
        self.qr_stack.add_named(empty, "empty")
        qr.append(self.qr_stack)
        middle.append(qr)
        panel.append(middle)

        # Bottom: autostart and the container's actions.
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        auto.add_css_class("rc-card")
        auto.set_hexpand(True)
        at = Gtk.Label(label="Encender con el sistema", xalign=0)
        at.add_css_class("rc-card-title")
        at.set_hexpand(True)
        auto.append(at)
        self.auto_switch = Gtk.Switch()
        self.auto_switch.add_css_class("rc-switch")
        self.auto_switch.set_valign(Gtk.Align.CENTER)
        self.auto_switch.connect("state-set", self._on_autostart)
        auto.append(self.auto_switch)
        bottom.append(auto)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.add_css_class("rc-card")
        actions.set_size_request(RIGHT, -1)
        actions.set_hexpand(False)
        actions.set_homogeneous(True)
        for label, tip, css, handler in (
            ("Actualizar", "Copiar adentro el Remote Control de esta máquina", "rc-btn-cancel", self._update),
            ("Renombrar", "Cambiar el nombre", "rc-btn-cancel", self._dialog_rename),
            ("Borrar", "Borrar el contenedor con todo", "rc-btn-danger", self._dialog_delete),
        ):
            btn = Gtk.Button(label=label)
            btn.set_tooltip_text(tip)
            btn.add_css_class(css)
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_, h=handler: h())
            actions.append(btn)
        bottom.append(actions)
        panel.append(bottom)
        return panel

    def _build_domain(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("rc-card")
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Dominio propio", xalign=0)
        title.add_css_class("rc-card-title")
        head.append(title)
        self.dom_state = Gtk.Label(label="Comprobando…")
        self.dom_state.add_css_class("rc-domain-state")
        self.dom_state.add_css_class("busy")
        self.dom_state.set_valign(Gtk.Align.CENTER)
        head.append(self.dom_state)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        head.append(spacer)
        account = Gtk.Button(icon_name="emblem-system-symbolic")
        account.set_tooltip_text("Cuenta de tu proveedor de dominios")
        account.add_css_class("rc-icon-btn")
        account.connect("clicked", lambda *_: self._account_dialog())
        head.append(account)
        card.append(head)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.dom_entry = Gtk.Entry()
        self.dom_entry.add_css_class("rc-field")
        self.dom_entry.add_css_class("rc-mono")
        self.dom_entry.set_hexpand(True)
        self.dom_entry.set_size_request(120, -1)
        row.append(self.dom_entry)
        self.dom_zone = Gtk.Label(xalign=0)
        self.dom_zone.add_css_class("rc-mono")
        self.dom_zone.add_css_class("rc-hint-text")
        row.append(self.dom_zone)
        self.dom_btn = Gtk.Button(label="Activar")
        self.dom_btn.add_css_class("rc-btn-save")
        self.dom_btn.connect("clicked", lambda *_: self._toggle_domain())
        row.append(self.dom_btn)
        card.append(row)
        self.dom_hint = Gtk.Label(xalign=0, wrap=True)
        self.dom_hint.add_css_class("rc-hint-text")
        self.dom_hint.set_max_width_chars(40)
        card.append(self.dom_hint)
        self._probe_reach()
        return card

    def _probe_reach(self) -> None:
        def work():
            return domains.direct_reach(domains.public_ipv4())

        def done(reach, error):
            self._reach = reach if not error else domains.Reach(False, error, domains.FALLBACK_PORT)
            self._paint_domain()

        _bg(work, done)

    def _paint_domain(self) -> None:
        b = self._box()
        if b is None:
            return
        cfg = domains.load_config()
        host = router.load_routes().get(b.name)
        zone = cfg.zone if cfg else ""
        busy = b.name in self._busy
        # The name is set with the tunnel off; on, it is the one in use.
        on = bool(self.tunnel.get("running")) and b.running
        for cls in ("on", "busy", "off"):
            self.dom_state.remove_css_class(cls)
        self.dom_hint.remove_css_class("warn")
        if host:
            sub = host[: -len(zone) - 1] if zone and host.endswith("." + zone) else host
            self.dom_entry.set_text(sub)
            self.dom_entry.set_sensitive(False)
            self.dom_zone.set_label(f".{zone}" if zone else "")
            self.dom_btn.set_label("Quitar")
            self.dom_btn.remove_css_class("rc-btn-save")
            self.dom_btn.add_css_class("rc-btn-danger")
            self.dom_btn.set_sensitive(not busy and not on)
            self.dom_state.add_css_class("on")
            self.dom_state.set_label("Activo")
            port = self._reach.public_port if self._reach else 443
            tail = "apaga el túnel para cambiarlo" if on else ("router activo" if router.service_active() else "se usa al encender el túnel")
            self.dom_hint.set_label(f"{router.public_url(host, port)}  ·  {tail}")
            return
        self.dom_btn.set_label("Activar")
        self.dom_btn.remove_css_class("rc-btn-danger")
        self.dom_btn.add_css_class("rc-btn-save")
        self.dom_zone.set_label(f".{zone}" if zone else "")
        if self._shown_domain_for != b.name:
            self.dom_entry.set_text(b.name)
            self._shown_domain_for = b.name
        if cfg is None or not zone:
            self.dom_state.add_css_class("off")
            self.dom_state.set_label("Sin cuenta")
            self.dom_hint.set_label("Configura tu proveedor de dominios con el ⚙.")
            self.dom_entry.set_sensitive(False)
            self.dom_btn.set_sensitive(False)
        elif self._reach is None:
            self.dom_state.add_css_class("busy")
            self.dom_state.set_label("Comprobando…")
            self.dom_hint.set_label("Comprobando si tu PC es alcanzable desde internet…")
            self.dom_entry.set_sensitive(False)
            self.dom_btn.set_sensitive(False)
        elif not self._reach.ok:
            self.dom_state.add_css_class("off")
            self.dom_state.set_label("No disponible")
            self.dom_hint.add_css_class("warn")
            self.dom_hint.set_label(f"{self._reach.why}.")
            self.dom_entry.set_sensitive(False)
            self.dom_btn.set_sensitive(False)
        else:
            self.dom_state.add_css_class("on")
            self.dom_state.set_label("Disponible")
            self.dom_hint.set_label("Apaga el túnel para configurar el dominio." if on else f"{self._reach.why}.")
            self.dom_entry.set_sensitive(not busy and not on)
            self.dom_btn.set_sensitive(not busy and not on)

    def _toggle_domain(self) -> None:
        b = self._box()
        if b is None:
            return
        name = b.name
        had = router.load_routes().get(name)
        tunnel_on = bool(self.tunnel.get("running"))

        def restart_tunnel():
            # Its URL changes (domain ↔ trycloudflare): the tunnel picks it up on start.
            if tunnel_on:
                multi.tunnel_stop(name)
                return multi.tunnel_start(name)
            return None

        if had:
            def work():
                router.unassign(name)
                return restart_tunnel()
            self._do(name, work, f"{had} quitado", lambda url: url and self.tunnel.update(running=True, url=url))
            return
        sub = self.dom_entry.get_text().strip().lower()

        def work():
            url = router.assign(name, sub)
            return restart_tunnel() or url

        self.dom_hint.set_label("Apuntando el nombre y pidiendo el certificado HTTPS (puede tardar unos minutos)…")
        self._do(name, work, "Dominio activo", lambda url: tunnel_on and self.tunnel.update(running=True, url=url))

    def _account_dialog(self) -> None:
        from remote_control.window import RemoteControlWindow

        RemoteControlWindow._show_domain_dialog(self, account_only=True, on_saved=self._paint_domain)

    def _row(self, title: str | None, entry: Gtk.Entry, actions) -> Gtk.Widget:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if title:
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("rc-field-label")
            wrap.append(label)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry.set_editable(False)
        entry.set_hexpand(True)
        entry.add_css_class("rc-field")
        entry.add_css_class("rc-mono")
        row.append(entry)
        for icon, tip, handler in actions:
            btn = Gtk.Button(icon_name=icon)
            btn.set_tooltip_text(tip)
            btn.add_css_class("rc-icon-btn")
            btn.connect("clicked", handler)
            if tip == "Nueva contraseña":
                self.regen_btn = btn
            row.append(btn)
        wrap.append(row)
        return wrap

    # --- state ---------------------------------------------------------------

    def _tick(self) -> bool:
        self.refresh()
        return True

    def refresh(self) -> None:
        if not multi.available():
            return

        def work():
            boxes = multi.list_boxes()
            box = next((b for b in boxes if b.name == self.selected), None)
            tunnel = multi.tunnel_status(box.name) if box and box.running and box.name not in self._busy else None
            auto = multi.autostart(box.name) if box and box.running else None
            return boxes, tunnel, auto

        def done(result, error):
            if error:
                return
            boxes, tunnel, auto = result
            self._paint_list(boxes)
            if tunnel is not None:
                self.tunnel = tunnel
            if auto is not None:
                self._set_switch(self.auto_switch, auto)
            self._paint_panel()

        _bg(work, done)

    def _paint_list(self, boxes: list[multi.Box]) -> None:
        names = [b.name for b in boxes]
        if names != [b.name for b in self.boxes]:
            child = self.list.get_first_child()
            while child is not None:
                self.list.remove(child)
                child = self.list.get_first_child()
            for b in boxes:
                self.list.append(self._list_row(b))
        self.boxes = boxes
        # Update the rows' state and size in place.
        for i, b in enumerate(boxes):
            row = self.list.get_row_at_index(i)
            if row is not None:
                row.dot.set_label("●" if b.running else "○")
                for cls in ("on", "off"):
                    row.dot.remove_css_class(cls)
                row.dot.add_css_class("on" if b.running else "off")
                row.sub.set_label(f"{multi.BASES.get(b.base, b.base)} · {multi.human_size(b.size)}")
        if self.selected not in names:
            self.selected = names[0] if names else None
        if self.selected:
            idx = names.index(self.selected)
            if self.list.get_selected_row() is not self.list.get_row_at_index(idx):
                self.list.select_row(self.list.get_row_at_index(idx))
        self.detail.set_visible_child_name("panel" if self.selected else "empty")

    def _list_row(self, b: multi.Box) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.name = b.name
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.add_css_class("rc-box-row")
        row.dot = Gtk.Label(label="●")
        row.dot.add_css_class("rc-box-dot")
        box.append(row.dot)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=b.name, xalign=0)
        name.add_css_class("rc-box-name")
        texts.append(name)
        row.sub = Gtk.Label(xalign=0)
        row.sub.add_css_class("rc-hint-text")
        texts.append(row.sub)
        box.append(texts)
        row.set_child(box)
        return row

    def _on_row(self, _list, row) -> None:
        if row is None or row.name == self._shown:
            return
        self._shown = row.name
        self.selected = row.name
        self.tunnel = {}
        self._qr_for = ""
        self.pass_entry.set_text("")
        self._paint_panel()
        self.refresh()
        self._load_password()

    def _box(self) -> multi.Box | None:
        return next((b for b in self.boxes if b.name == self.selected), None)

    def _paint_panel(self) -> None:
        b = self._box()
        if b is None:
            return
        busy = b.name in self._busy
        on = bool(self.tunnel.get("running")) and b.running
        self.name_label.set_label(b.name)
        self.info_label.set_label(f"{multi.BASES.get(b.base, b.base)} · {multi.human_size(b.size)}")
        for cls in ("on", "busy", "off"):
            self.state_chip.remove_css_class(cls)
            self.hero.remove_css_class(cls)
        if busy:
            kind, text = "busy", getattr(self, "_busy_text", "") or "Un momento…"
        elif not b.running:
            kind, text = "off", "Contenedor apagado"
        elif on:
            kind, text = "on", "En línea"
        else:
            kind, text = "off", "Túnel apagado"
        self.state_chip.add_css_class(kind)
        self.state_chip.set_label(text)
        self.hero.add_css_class(kind)
        self.power_btn.set_label("Apagar contenedor" if b.running else "Encender contenedor")
        self.power_btn.set_sensitive(not busy)
        self._set_switch(self.switch, on)
        # Off: the tunnel and its container; on starts the container if needed.
        self.switch.set_sensitive(not busy)
        self.auto_switch.set_sensitive(b.running and not busy)
        # Its password changes with the tunnel off, container on or not (multi.password).
        self.regen_btn.set_sensitive(not on and not busy)
        url = (self.tunnel.get("url") or "") if on else ""
        if self.url_entry.get_text() != url:
            self.url_entry.set_text(url)
        # Nothing answers there with the tunnel off.
        self.local_entry.set_placeholder_text("Aparece al encender el túnel")
        self.local_entry.set_text(f"http://localhost:{b.port}/" if on else "")
        if url and self._qr_for != url:
            png = control.qr_png(url, scale=5)
            if png:
                self.qr_picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
                self._qr_for = url
        self.qr_stack.set_visible_child_name("qr" if url and self._qr_for == url else "empty")
        self._paint_domain()

    @staticmethod
    def _set_switch(switch: Gtk.Switch, value: bool) -> None:
        # Set from state, not from a click: the handlers see _syncing and skip.
        switch._syncing = True
        switch.set_active(value)
        switch.set_state(value)
        switch._syncing = False

    # --- actions -------------------------------------------------------------

    def _do(self, name: str, work, ok: str | None = None, after=None) -> None:
        """A slow action on one container: shown as busy until it ends."""
        self._busy.add(name)
        self._paint_panel()

        def done(result, error):
            self._busy.discard(name)
            self._busy_text = ""
            if error:
                self._toast(f"No se pudo: {error.splitlines()[0]}", 6)
            elif ok:
                self._toast(ok)
            if after and not error:
                after(result)
            self.refresh()

        _bg(work, done)

    def _toggle_power(self) -> None:
        b = self._box()
        if b is None:
            return
        if b.running:
            self.tunnel = {}
            self._do(b.name, lambda: multi.stop(b.name), f"{b.name} apagado")
        else:
            self._do(b.name, lambda: multi.start(b.name), f"{b.name} encendido", lambda _r: self._load_password())

    def _on_switch(self, switch: Gtk.Switch, state: bool) -> bool:
        if getattr(switch, "_syncing", False):
            return False
        b = self._box()
        if b is None:
            return True
        if state:
            def started(url):
                self.tunnel = {"running": True, "url": url}
            self._do(b.name, lambda: multi.tunnel_start(b.name), "Túnel en línea", started)
        else:
            self._confirm(
                "¿Apagar el túnel?",
                f"La URL pública de {b.name} dejará de funcionar y se apaga el contenedor. Sus terminales vuelven al encenderlo.",
                "Apagar",
                lambda: self._do(b.name, lambda: multi.tunnel_stop(b.name), "Túnel apagado", lambda _r: self.tunnel.clear()),
            )
        return True

    def _on_autostart(self, switch: Gtk.Switch, state: bool) -> bool:
        if getattr(switch, "_syncing", False):
            return False
        b = self._box()
        if b is None:
            return True
        msg = "Se encenderá con su túnel al iniciar sesión" if state else "Ya no se enciende solo"
        self._do(b.name, lambda: multi.set_autostart(b.name, state), msg)
        return True

    def _load_password(self) -> None:
        b = self._box()
        if b is None:
            return
        name = b.name
        _bg(lambda: multi.password(name), lambda pw, err: name == self.selected and not err and self.pass_entry.set_text(pw))

    def _new_password(self) -> None:
        b = self._box()
        if b is None:
            return
        self._do(b.name, lambda: multi.password(b.name, "new"), "Nueva contraseña", lambda pw: self.pass_entry.set_text(pw))

    def _update(self) -> None:
        b = self._box()
        if b is None:
            return
        if not b.running:
            self._toast("Enciende el contenedor primero")
            return
        def step(line: str) -> None:
            # What it is doing, in the status chip (the environment can take minutes).
            if line.strip():
                self._busy_text = line.strip()[:60]
                GLib.idle_add(lambda: self._paint_panel() and False)

        self._do(b.name, lambda: multi.install_rc(b.name, log=step), "Remote Control actualizado adentro (aplica al reiniciar su túnel)")

    def _copy(self, text: str, message: str) -> None:
        display = Gdk.Display.get_default()
        if text and display is not None:
            display.get_clipboard().set(text)
            self._toast(message)

    def _open(self, url: str) -> None:
        if url:
            Gio.AppInfo.launch_default_for_uri(url, None)

    def _toast(self, title: str, timeout: int = 3) -> None:
        toast = Adw.Toast(title=title)
        toast.set_timeout(timeout)
        self.toasts.add_toast(toast)

    # --- dialogs (CRUD) --------------------------------------------------------

    def _dialog(self, title: str, danger: bool = False) -> tuple[Adw.Dialog, Gtk.Box]:
        dialog = Adw.Dialog()
        dialog.set_content_width(400)
        dialog.set_follows_content_size(True)
        dialog.add_css_class("rc-sheet")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("rc-dialog")
        if not danger:
            card.add_css_class("rc-domain-dialog")
        card.set_size_request(380, -1)
        head = Gtk.Label(label=title)
        head.add_css_class("rc-dialog-title")
        card.append(head)
        dialog.set_child(card)
        return dialog, card

    def _buttons(self, card: Gtk.Box, dialog: Adw.Dialog, label: str, css: str, on_ok) -> Gtk.Button:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_homogeneous(True)
        row.set_margin_top(6)
        cancel = Gtk.Button(label="Cancelar")
        cancel.add_css_class("rc-btn-cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        row.append(cancel)
        ok = Gtk.Button(label=label)
        ok.add_css_class(css)
        ok.connect("clicked", lambda *_: on_ok())
        row.append(ok)
        card.append(row)
        ok.cancel = cancel
        return ok

    def _confirm(self, title: str, body: str, yes: str, action) -> None:
        dialog, card = self._dialog(title, danger=True)
        text = Gtk.Label(label=body, wrap=True, justify=Gtk.Justification.CENTER)
        text.add_css_class("rc-dialog-body")
        text.set_max_width_chars(44)
        card.append(text)

        def go():
            dialog.close()
            action()

        self._buttons(card, dialog, yes, "rc-btn-stop", go)
        dialog.connect("closed", lambda *_: self._paint_panel())
        dialog.present(self)

    def _field(self, card: Gtk.Box, label: str, widget: Gtk.Widget) -> None:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name = Gtk.Label(label=label, xalign=0)
        name.add_css_class("rc-field-label")
        wrap.append(name)
        wrap.append(widget)
        card.append(wrap)

    def _dialog_create(self) -> None:
        dialog, card = self._dialog("Nuevo contenedor")
        name = Gtk.Entry(placeholder_text="backend")
        name.add_css_class("rc-field")
        name.add_css_class("rc-mono")
        self._field(card, "Nombre", name)
        base_ids = list(multi.BASES)
        base = Gtk.DropDown.new_from_strings(list(multi.BASES.values()))
        self._field(card, "Sistema", base)
        # While it is made: a spinner, the step it is on and the seconds.
        progress = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner = Gtk.Spinner()
        spinner.set_visible(False)
        progress.append(spinner)
        status = Gtk.Label(wrap=True, xalign=0)
        status.add_css_class("rc-hint-text")
        status.set_max_width_chars(46)
        status.set_hexpand(True)
        progress.append(status)
        card.append(progress)
        ok = None
        state = {"busy": False, "step": "", "start": 0.0}

        def paint() -> bool:
            if not state["busy"]:
                return False
            secs = int(time.monotonic() - state["start"])
            status.set_label(f"{state['step']}  ·  {secs}s")
            return True

        def busy(on: bool) -> None:
            state["busy"] = on
            spinner.set_visible(on)
            spinner.set_spinning(on)
            ok.set_label("Creando…" if on else "Crear")
            for widget in (ok, ok.cancel, name, base):
                widget.set_sensitive(not on)
            # Not closed half-made (Esc, a click outside).
            dialog.set_can_close(not on)

        def create():
            value = name.get_text().strip().lower()
            if not multi.valid_name(value):
                status.add_css_class("warn")
                status.set_label("Nombre inválido: minúsculas, números y guiones.")
                return
            if any(b.name == value for b in self.boxes):
                status.add_css_class("warn")
                status.set_label(f"Ya existe {value}.")
                return
            if state["busy"]:
                return
            status.remove_css_class("warn")
            chosen = base_ids[base.get_selected()]
            state["step"] = f"Creando {value}… (la primera vez tarda unos minutos)"
            state["start"] = time.monotonic()
            busy(True)
            paint()
            GLib.timeout_add(500, paint)

            def log(line: str) -> None:
                line = line.strip()
                if line.startswith(("STEP", "Creando", "Copiando", "Preparando")) or line.endswith("…"):
                    state["step"] = line[:70]

            def done(_box, error):
                busy(False)
                if error:
                    status.add_css_class("warn")
                    status.set_label(error.splitlines()[0])
                    return
                dialog.close()
                self.selected = value
                self._toast(f"{value} listo")
                self.refresh()

            _bg(lambda: multi.create(value, chosen, log=log), done)

        ok = self._buttons(card, dialog, "Crear", "rc-btn-save", create)
        name.connect("activate", lambda *_: create())
        dialog.present(self)

    def _dialog_rename(self) -> None:
        b = self._box()
        if b is None:
            return
        dialog, card = self._dialog(f"Renombrar {b.name}")
        name = Gtk.Entry(text=b.name)
        name.add_css_class("rc-field")
        name.add_css_class("rc-mono")
        self._field(card, "Nuevo nombre", name)
        status = Gtk.Label(wrap=True, xalign=0)
        status.add_css_class("rc-hint-text")
        status.add_css_class("warn")
        card.append(status)

        def rename():
            value = name.get_text().strip().lower()
            try:
                multi.rename(b.name, value)
            except multi.MultiError as exc:
                status.set_label(str(exc))
                return
            dialog.close()
            self.selected = value
            self._toast(f"Ahora se llama {value}")
            self.refresh()

        self._buttons(card, dialog, "Renombrar", "rc-btn-save", rename)
        name.connect("activate", lambda *_: rename())
        dialog.present(self)

    def _dialog_delete(self) -> None:
        b = self._box()
        if b is None:
            return
        self._confirm(
            f"¿Borrar {b.name}?",
            f"Se borra el contenedor con todo lo que tiene ({multi.human_size(b.size)}): archivos, programas "
            "instalados y sesiones. No se puede deshacer.",
            "Borrar",
            lambda: self._do(b.name, lambda: multi.remove(b.name), f"{b.name} borrado"),
        )
