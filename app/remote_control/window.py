from __future__ import annotations

import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from urllib.parse import urlparse

from remote_control.gate import (
    STRENGTH_LABEL,
    generate_password,
    password_strength,
    read_password,
    without_spaces,
    write_password,
)
from remote_control import autostart, control, domains
from remote_control.tunnel import TunnelService, host_resolves
from remote_control.updater import check_and_apply, running_from_install


# Width of the right column (the QR, and the domain card under it).
QR_COLUMN = 320


class RemoteControlWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title("Remote Control")
        # Two columns like `remote-control cli`: controls left, QR right.
        self.set_default_size(820, -1)
        self.set_resizable(False)
        self.add_css_class("rc-window")

        self.tunnel = TunnelService()
        self._busy = False
        self._current_url: str | None = None
        self._syncing_switch = False
        self._filtering_password = False
        self._last_update_check = 0.0
        self._update_debounce = 25.0

        self._load_css()
        self._build()
        self._refresh_from_status()
        GLib.timeout_add_seconds(2, self._poll_status)
        self.connect("notify::is-active", self._on_is_active)
        self._maybe_check_update(force=True)

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        css_path = Path(__file__).with_name("style.css")
        provider.load_from_path(str(css_path))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            # Above the user's own ~/.config/gtk-4.0/gtk.css: a full theme
            # there would otherwise repaint our cards and dialogs.
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 1,
        )

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.add_css_class("rc-header")
        header.set_title_widget(Adw.WindowTitle(title="Remote Control", subtitle=""))
        toolbar.add_top_bar(header)

        self.toasts = Adw.ToastOverlay()
        toolbar.set_content(self.toasts)

        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        canvas.add_css_class("rc-canvas")
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        canvas.set_margin_top(4)
        canvas.set_margin_bottom(14)
        canvas.set_margin_start(16)
        canvas.set_margin_end(16)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(canvas)
        self.toasts.set_child(scroll)

        # A dashboard: the tunnel across the top, what you share in the
        # middle (links and password beside the QR), settings at the bottom.
        canvas.append(self._build_hero())

        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        links = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        links.set_hexpand(True)
        links.append(self._build_url_card())
        links.append(self._build_password())
        middle.append(links)
        middle.append(self._build_qr_card())
        canvas.append(middle)

        settings = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        # Same split as the row above: the edges line up.
        autostart_card = self._build_autostart()
        autostart_card.set_hexpand(True)
        settings.append(autostart_card)
        domain_card = self._build_domain()
        domain_card.set_size_request(QR_COLUMN, -1)
        domain_card.set_hexpand(False)
        settings.append(domain_card)
        canvas.append(settings)
        canvas.append(self._build_error())

        self.set_content(toolbar)

    # --- hero: what this does, its state and the power switch ---------------

    def _build_hero(self) -> Gtk.Widget:
        self.hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.hero.add_css_class("rc-card")
        self.hero.add_css_class("rc-hero")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        badge = Gtk.Box()
        badge.add_css_class("rc-badge")
        badge.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        icon.set_pixel_size(22)
        # Centered in a fixed square (an expanding icon stretched it into a bar).
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        badge.set_size_request(46, 46)
        badge.set_halign(Gtk.Align.START)
        badge.set_homogeneous(True)
        badge.append(icon)
        top.append(badge)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label="Túnel web", xalign=0)
        title.add_css_class("rc-hero-title")
        texts.append(title)
        top.append(texts)

        self.switch = Gtk.Switch()
        self.switch.add_css_class("rc-switch")
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.connect("state-set", self._on_switch_state_set)
        top.append(self.switch)
        self.hero.append(top)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        status.add_css_class("rc-chip")
        status.add_css_class("rc-status")
        self.status_dot = Gtk.Box()
        self.status_dot.add_css_class("rc-dot")
        self.status_dot.set_valign(Gtk.Align.CENTER)
        status.append(self.status_dot)
        self.status_label = Gtk.Label(label="Apagado", xalign=0)
        self.status_label.add_css_class("rc-status-label")
        self.status_label.add_css_class("off")
        status.append(self.status_label)
        chips.append(status)

        self.lock_chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.lock_chip.add_css_class("rc-chip")
        lock_icon = Gtk.Image.new_from_icon_name("system-lock-screen-symbolic")
        lock_icon.set_pixel_size(12)
        self.lock_chip.append(lock_icon)
        self.lock_label = Gtk.Label(label="Con contraseña")
        self.lock_chip.append(self.lock_label)
        chips.append(self.lock_chip)
        # Beside the badge, under the title: the hero is one row across the top.
        texts.append(chips)
        return self.hero

    # --- password ------------------------------------------------------------

    def _build_password(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("rc-card")

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label="Contraseña", xalign=0)
        label.add_css_class("rc-card-title")
        label.set_hexpand(True)
        head.append(label)
        self.strength_label = Gtk.Label(xalign=1)
        self.strength_label.add_css_class("rc-strength")
        head.append(self.strength_label)
        box.append(head)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_placeholder_text("Sin contraseña")
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.password_entry.set_hexpand(True)
        self.password_entry.add_css_class("rc-field")
        self.password_entry.add_css_class("rc-mono")
        self.password_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
        self.password_entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Mostrar")
        saved = read_password()
        clean = without_spaces(saved)
        if not clean:
            # First run (or an emptied file): start with a strong random one.
            clean = generate_password()
        if clean != saved:
            try:
                write_password(clean)
            except OSError:
                pass
        self.password_entry.set_text(clean)
        self.password_entry.connect("icon-press", self._on_password_icon)
        self.password_entry.connect("changed", self._on_password_changed)
        row.append(self.password_entry)

        self.regen_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self.regen_btn.set_tooltip_text("Generar otra contraseña")
        self.regen_btn.add_css_class("rc-icon-btn")
        self.regen_btn.connect("clicked", self._on_regen_password)
        row.append(self.regen_btn)

        self.copy_password_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        self.copy_password_btn.set_tooltip_text("Copiar contraseña")
        self.copy_password_btn.add_css_class("rc-icon-btn")
        self.copy_password_btn.add_css_class("accent")
        self.copy_password_btn.connect("clicked", self._on_copy_password)
        row.append(self.copy_password_btn)
        box.append(row)

        meter = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        meter.add_css_class("rc-meter")
        self.meter_bars = []
        for _ in range(4):
            bar = Gtk.Box()
            bar.add_css_class("rc-meter-bar")
            bar.set_hexpand(True)
            meter.append(bar)
            self.meter_bars.append(bar)
        box.append(meter)
        self._refresh_strength()
        return box

    def _on_regen_password(self, *_args) -> None:
        self.password_entry.set_text(generate_password())
        self._toast("Nueva contraseña generada")

    # --- start with the system (autostart.py) ---------------------------------

    def _build_autostart(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("rc-card")
        title = Gtk.Label(label="Iniciar con el sistema", xalign=0)
        title.add_css_class("rc-card-title")
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        box.append(title)
        self.autostart_switch = Gtk.Switch()
        self.autostart_switch.add_css_class("rc-switch")
        self.autostart_switch.set_valign(Gtk.Align.CENTER)
        self.autostart_switch.set_active(autostart.is_enabled())
        self.autostart_switch.connect("state-set", self._on_autostart_set)
        box.append(self.autostart_switch)
        return box

    def _on_autostart_set(self, switch: Gtk.Switch, state: bool) -> bool:
        try:
            autostart.set_enabled(state)
        except OSError as exc:
            self._toast(f"No se pudo: {exc.strerror or exc}")
            switch.set_state(autostart.is_enabled())
            return True
        self._toast("Se encenderá al iniciar sesión" if state else "Ya no se enciende al iniciar sesión")
        return False

    # --- your own domain (domains.py) -----------------------------------------

    def _build_domain(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.add_css_class("rc-card")
        # Looks switched off until the checks pass (_paint_domain).
        box.add_css_class("rc-domain-off")
        self.domain_card = box
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.set_hexpand(True)
        texts.set_valign(Gtk.Align.CENTER)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Dominio propio", xalign=0)
        title.add_css_class("rc-card-title")
        head.append(title)
        self.domain_state = Gtk.Label(label="No disponible")
        self.domain_state.add_css_class("rc-domain-state")
        self.domain_state.set_valign(Gtk.Align.CENTER)
        head.append(self.domain_state)
        texts.append(head)
        self.domain_label = Gtk.Label(xalign=0, wrap=True)
        # Wraps inside the column instead of widening it.
        self.domain_label.set_max_width_chars(28)
        self.domain_label.add_css_class("rc-hint-text")
        texts.append(self.domain_label)
        box.append(texts)
        self.domain_btn = Gtk.Button(icon_name="emblem-system-symbolic")
        self.domain_btn.set_tooltip_text("Configurar dominio")
        self.domain_btn.add_css_class("rc-icon-btn")
        self.domain_btn.set_valign(Gtk.Align.CENTER)
        self.domain_btn.connect("clicked", lambda *_: self._show_domain_dialog())
        box.append(self.domain_btn)
        self._paint_domain()
        return box

    def _set_domain_state(self, kind: str, text: str, detail: str) -> bool:
        """kind: on (in use), busy (checking) or off (looks disabled)."""
        for cls in ("on", "busy", "off"):
            self.domain_state.remove_css_class(cls)
        self.domain_state.add_css_class(kind)
        self.domain_state.set_label(text)
        self.domain_label.set_label(detail)
        if kind == "on":
            self.domain_card.remove_css_class("rc-domain-off")
        else:
            self.domain_card.add_css_class("rc-domain-off")
        return False

    def _paint_domain(self) -> None:
        """Active only once the settings are complete and this machine is
        reachable from the internet (domains.direct_reach); off until then."""
        cfg = domains.load_config()
        if cfg is None or not cfg.host:
            self._set_domain_state("off", "No disponible", "Sin configurar")
            return
        problems = cfg.problems()
        if problems:
            self._set_domain_state("off", "No disponible", problems[0])
            return
        provider = domains.PROVIDERS.get(cfg.provider, cfg.provider)
        self._set_domain_state("busy", "Comprobando…", f"{cfg.host} · {provider}")
        self._domain_check = getattr(self, "_domain_check", 0) + 1
        ticket = self._domain_check

        def work() -> None:
            try:
                reach = domains.direct_reach(domains.public_ipv4())
            except Exception as exc:
                reach = domains.Reach(False, str(exc), domains.FALLBACK_PORT)
            GLib.idle_add(done, reach)

        def done(reach: domains.Reach) -> bool:
            if ticket != self._domain_check:
                return False  # a newer check is running
            if reach.ok:
                return self._set_domain_state("on", "Disponible", f"{cfg.host} · {provider}")
            return self._set_domain_state("off", "No disponible", reach.why)

        threading.Thread(target=work, daemon=True).start()

    def _show_domain_dialog(self, account_only: bool = False, on_saved=None) -> None:
        """The provider account and this machine's name. ``account_only`` (the
        multi window, multi_window.py): just the account and its domain; each
        container picks its own name there."""
        saved = domains.load_config()
        cfg = saved or domains.DomainConfig(provider="namecom")
        dialog = Adw.Dialog()
        dialog.set_content_width(380)
        dialog.set_follows_content_size(True)
        # Our own colors, not the system theme's (style.css).
        dialog.add_css_class("rc-sheet")

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("rc-dialog")
        card.add_css_class("rc-domain-dialog")
        card.set_size_request(360, -1)

        title = Gtk.Label(label="Cuenta de dominios" if account_only else "Dominio propio")
        title.add_css_class("rc-dialog-title")
        card.append(title)

        def field(label: str, widget: Gtk.Widget) -> Gtk.Widget:
            wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            name = Gtk.Label(label=label, xalign=0)
            name.add_css_class("rc-field-label")
            wrap.append(name)
            wrap.append(widget)
            return wrap

        # 1. Provider and its key.
        provider_ids = list(domains.PROVIDERS)
        provider = Gtk.DropDown.new_from_strings(list(domains.PROVIDERS.values()))
        provider.set_selected(provider_ids.index(cfg.provider) if cfg.provider in provider_ids else 0)
        card.append(field("Proveedor", provider))

        creds_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.append(creds_box)
        entries: dict[str, Gtk.Entry] = {}

        load = Gtk.Button(label="Cargar mis dominios")
        load.add_css_class("rc-btn-cancel")
        card.append(load)

        # 2. Which of the account's domains, and the name under it.
        pick_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.append(pick_box)
        zones: list[str] = []
        zone_list = Gtk.StringList()
        zone = Gtk.DropDown(model=zone_list)
        pick_box.append(field("Dominio", zone))
        sub = Gtk.Entry(placeholder_text="vacío = el dominio mismo")
        sub.add_css_class("rc-field")
        sub.add_css_class("rc-mono")
        sub_field = field("Subdominio", sub)
        pick_box.append(sub_field)
        preview = Gtk.Label(xalign=0, wrap=True)
        preview.add_css_class("rc-hint-text")
        pick_box.append(preview)
        sub_field.set_visible(not account_only)
        preview.set_visible(not account_only)

        # The domain is served here (no redirect), so it can only be saved once
        # this machine is known to be reachable from the internet.
        reach_ok = [account_only]
        reach_label = Gtk.Label(label="Comprobando si tu PC es alcanzable desde internet…", xalign=0, wrap=True)
        reach_label.add_css_class("rc-hint-text")
        reach_label.set_max_width_chars(44)
        reach_label.set_visible(not account_only)
        card.append(reach_label)

        def reached(reach: domains.Reach) -> bool:
            reach_ok[0] = reach.ok
            if reach.ok:
                reach_label.set_label(f"✓ {reach.why}.")
                reach_label.remove_css_class("warn")
            else:
                reach_label.set_label(f"No disponible: {reach.why}.")
                reach_label.add_css_class("warn")
            paint_save()
            return False

        def probe() -> None:
            try:
                reach = domains.direct_reach(domains.public_ipv4())
            except Exception as exc:
                reach = domains.Reach(False, str(exc), domains.FALLBACK_PORT)
            GLib.idle_add(reached, reach)

        if not account_only:
            threading.Thread(target=probe, daemon=True).start()

        result = Gtk.Label(wrap=True, xalign=0)
        result.add_css_class("rc-hint-text")
        result.set_visible(False)
        card.append(result)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_homogeneous(True)
        buttons.set_margin_top(6)
        remove = Gtk.Button(label="Quitar" if saved else "Cancelar")
        remove.add_css_class("rc-btn-cancel")
        buttons.append(remove)
        save = Gtk.Button(label="Guardar")
        save.add_css_class("rc-btn-save")
        buttons.append(save)
        card.append(buttons)

        def say(message: str, warn: bool = False) -> None:
            result.set_label(message)
            result.set_visible(bool(message))
            if warn:
                result.add_css_class("warn")
            else:
                result.remove_css_class("warn")

        def chosen_host() -> str:
            if not zones:
                return ""
            return domains.compose_host("" if account_only else sub.get_text(), zones[zone.get_selected()])

        def paint_preview(*_args) -> None:
            host = chosen_host()
            preview.set_label(f"Quedará en {host}" if host else "")

        def set_zones(items: list[str], selected: str = "") -> None:
            zones[:] = items
            zone_list.splice(0, zone_list.get_n_items(), items)
            if selected in items:
                zone.set_selected(items.index(selected))
            pick_box.set_visible(bool(items))
            paint_save()
            paint_preview()

        def paint_save() -> None:
            save.set_sensitive(bool(zones) and reach_ok[0])

        def paint_creds(*_args) -> None:
            # Keep what was typed when switching providers back and forth.
            for key, entry in entries.items():
                cfg.creds[key] = entry.get_text()
            entries.clear()
            child = creds_box.get_first_child()
            while child is not None:
                creds_box.remove(child)
                child = creds_box.get_first_child()
            for key, label, secret, _required in domains.FIELDS[provider_ids[provider.get_selected()]]:
                entry = Gtk.Entry(text=cfg.creds.get(key, ""))
                entry.add_css_class("rc-field")
                entry.add_css_class("rc-mono")
                if secret:
                    entry.set_visibility(False)
                    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
                entries[key] = entry
                creds_box.append(field(label, entry))

        def on_provider(*_args) -> None:
            paint_creds()
            # Another account: its domains have to be loaded again.
            set_zones([])
            say("")

        def on_load(*_args) -> None:
            pid = provider_ids[provider.get_selected()]
            creds = {key: entry.get_text().strip() for key, entry in entries.items()}
            load.set_sensitive(False)
            say("Consultando tu cuenta…")

            def work() -> None:
                try:
                    items = domains.account_domains(pid, creds)
                    GLib.idle_add(loaded, items, "")
                except Exception as exc:
                    GLib.idle_add(loaded, [], str(exc))

            threading.Thread(target=work, daemon=True).start()

        def loaded(items: list[str], error: str) -> bool:
            load.set_sensitive(True)
            if error:
                say(error, warn=True)
                return False
            set_zones(items, zones[zone.get_selected()] if zones else cfg.zone)
            say(f"{len(items)} dominio(s) en la cuenta.")
            return False

        def on_remove(*_args) -> None:
            if domains.load_config():
                domains.clear_config()
                self._toast("Cuenta quitada" if account_only else "Dominio quitado: vuelve trycloudflare.com")
                if on_saved:
                    on_saved()
                else:
                    self._paint_domain()
            dialog.close()

        def on_save(*_args) -> None:
            new = domains.DomainConfig(
                provider=provider_ids[provider.get_selected()],
                # Account only: never the bare domain as this machine's name;
                # keep the one it had, if any, else none.
                host=(saved.host if saved and saved.zone == zones[zone.get_selected()] else "") if account_only else chosen_host(),
                zone=zones[zone.get_selected()],
                creds={key: entry.get_text().strip() for key, entry in entries.items()},
                mode="direct",
                email=cfg.email,
            )
            save.set_sensitive(False)
            say("Comprobando…")

            def work() -> None:
                try:
                    message = domains.check(new)
                    domains.save_config(new)
                    GLib.idle_add(done, message, True)
                except Exception as exc:
                    GLib.idle_add(done, str(exc), False)

            threading.Thread(target=work, daemon=True).start()

        def done(message: str, ok: bool) -> bool:
            save.set_sensitive(True)
            if ok:
                if on_saved:
                    on_saved()
                    self._toast("Cuenta guardada", timeout=4)
                    dialog.close()
                    return False
                self._paint_domain()
                running = self.tunnel.status().running
                self._toast("Dominio guardado" + (": apaga y enciende el túnel para usarlo" if running else ""), timeout=4)
                dialog.close()
                return False
            say(message, warn=True)
            return False

        paint_creds()
        # Editing a saved one: its domain is there without asking the API again.
        if saved and saved.zone:
            set_zones([saved.zone], saved.zone)
            sub.set_text(saved.host[: -len(saved.zone)].rstrip(".") if saved.host.endswith(saved.zone) else "")
        else:
            sub.set_text("rc")
            set_zones([])
        # Set before "changed" is connected below.
        paint_preview()
        provider.connect("notify::selected", on_provider)
        zone.connect("notify::selected", paint_preview)
        sub.connect("changed", paint_preview)
        load.connect("clicked", on_load)
        remove.connect("clicked", on_remove)
        save.connect("clicked", on_save)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(880)
        scroll.set_child(card)
        dialog.set_child(scroll)
        dialog.present(self)

    # --- URLs (shown while the tunnel is up) ---------------------------------

    def _url_row(self, title: str, entry: Gtk.Entry, actions: list[tuple[str, str, object]]) -> Gtk.Widget:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
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
            row.append(btn)
        wrap.append(row)
        return wrap

    def _build_url_card(self) -> Gtk.Widget:
        self.url_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.url_card.add_css_class("rc-card")
        self.url_card.set_vexpand(True)

        head = Gtk.Label(label="Compartir", xalign=0)
        head.add_css_class("rc-card-title")
        self.url_card.append(head)

        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("Aparece al encender el túnel")
        self.url_card.append(
            self._url_row(
                "URL pública",
                self.url_entry,
                [("edit-copy-symbolic", "Copiar URL pública", self._on_copy), ("web-browser-symbolic", "Abrir", self._on_open)],
            )
        )


        # The address this PC serves (what the tunnel publishes), to tell apart
        # several running copies or open it without going through Cloudflare.
        self.local_entry = Gtk.Entry()
        self.local_entry.set_text(self._local_url())
        self.url_card.append(
            self._url_row(
                "URL local",
                self.local_entry,
                [("edit-copy-symbolic", "Copiar URL local", self._on_copy_local), ("web-browser-symbolic", "Abrir URL local", self._on_open_local)],
            )
        )
        return self.url_card

    # --- QR of the public URL (only that one: the local URL is useless elsewhere)

    def _build_qr_card(self) -> Gtk.Widget:
        self.qr_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.qr_card.add_css_class("rc-card")
        self.qr_card.add_css_class("rc-qr-card")
        # Same width on and off, so the window keeps its shape.
        self.qr_card.set_size_request(QR_COLUMN, -1)
        self.qr_card.set_hexpand(False)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Abrir en el celular", xalign=0)
        title.add_css_class("rc-card-title")
        title.set_hexpand(True)
        head.append(title)
        self.qr_card.append(head)
        # The column stays put: with the tunnel off it says how to get the QR.
        self.qr_stack = Gtk.Stack()
        self.qr_stack.set_vexpand(True)
        self.qr_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        frame = Gtk.Box()
        frame.add_css_class("rc-qr")
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_valign(Gtk.Align.CENTER)
        self.qr_picture = Gtk.Picture()
        self.qr_picture.set_size_request(196, 196)
        self.qr_picture.set_can_shrink(True)
        frame.append(self.qr_picture)
        self.qr_stack.add_named(frame, "qr")
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.add_css_class("rc-qr-empty")
        empty.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name("phone-symbolic")
        icon.set_pixel_size(40)
        empty.append(icon)
        self.qr_empty_label = Gtk.Label(label="Enciende el túnel para ver el QR", wrap=True, justify=Gtk.Justification.CENTER)
        self.qr_empty_label.set_max_width_chars(24)
        self.qr_empty_label.add_css_class("rc-hint-text")
        empty.append(self.qr_empty_label)
        self.qr_stack.add_named(empty, "empty")
        self.qr_stack.set_visible_child_name("empty")
        self.qr_card.append(self.qr_stack)
        self._qr_for = ""
        return self.qr_card

    def _paint_qr(self) -> None:
        """Show the QR card while the tunnel is up (and segno is installed)."""
        url = self._current_url
        if not url:
            self.qr_empty_label.set_label("Enciende el túnel para ver el QR")
            self.qr_stack.set_visible_child_name("empty")
            return
        if self._qr_for != url:
            # Its natural size is the picture's: scale 5 ≈ 200 px.
            png = control.qr_png(url, scale=5)
            if png is None:
                self.qr_empty_label.set_label("Instala 'segno' para ver el QR")
                self.qr_stack.set_visible_child_name("empty")
                return
            self.qr_picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
            self._qr_for = url
        self.qr_stack.set_visible_child_name("qr")

    def _local_url(self) -> str:
        return f"http://localhost:{self.tunnel.port}/"

    def _on_copy_local(self, *_args) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(self._local_url())
        self.local_entry.select_region(0, -1)
        self._toast("URL local copiada")

    def _on_open_local(self, *_args) -> None:
        Gio.AppInfo.launch_default_for_uri(self._local_url(), None)

    def _build_error(self) -> Gtk.Widget:
        self.error_label = Gtk.Label(wrap=True, xalign=0)
        self.error_label.add_css_class("rc-error")
        self.error_label.set_visible(False)
        return self.error_label

    def _on_switch_state_set(self, _switch: Gtk.Switch, state: bool) -> bool:
        if self._syncing_switch or self._busy:
            return True
        if state:
            self._start_async()
            return True
        if self.tunnel.status().running or self._current_url:
            if control.inside_tunnel():
                self._toast("Estás dentro del túnel: apágalo desde la PC")
                return True
            self._show_stop_dialog()
            return True
        return False

    def _start_async(self) -> None:
        self._busy = True
        self._set_error(None)
        self._set_status("busy", "Encendiendo…")
        self._set_switch(True)
        self.switch.set_sensitive(False)
        password = self.password_entry.get_text()
        try:
            write_password(password)
        except OSError:
            pass
        self._set_password_locked(True)

        def work() -> None:
            try:
                url = self.tunnel.start(password or None)
                GLib.idle_add(self._on_started, url)
            except Exception as exc:
                GLib.idle_add(self._on_start_failed, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_started(self, url: str) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._current_url = url
        self.url_entry.set_text(url)
        self.url_entry.select_region(0, -1)
        self._paint_qr()
        self._set_switch(True)
        if self._local_dns_ok(url):
            self._set_status("on", "En línea")
        else:
            self._set_status("busy", "Túnel ok · DNS…")
            self._toast("URL lista; el DNS de esta PC aún no")
        if self.tunnel.domain_warning:
            self._set_error(self.tunnel.domain_warning)
        self._paint_domain()
        return False

    def _on_start_failed(self, message: str) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._current_url = None
        self.url_entry.set_text("")
        self._paint_qr()
        self._set_status("off", "Apagado")
        self._set_switch(False)
        self._set_password_locked(False)
        self._set_error(message)
        return False

    def _show_stop_dialog(self) -> None:
        dialog = Adw.Dialog()
        dialog.set_content_width(360)
        dialog.set_follows_content_size(True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("rc-dialog")
        card.set_halign(Gtk.Align.FILL)

        title = Gtk.Label(label="¿Apagar el túnel?", justify=Gtk.Justification.CENTER)
        title.add_css_class("rc-dialog-title")
        card.append(title)

        body = Gtk.Label(
            label="La URL pública dejará de funcionar.\nQuien esté conectado perderá el acceso.",
            justify=Gtk.Justification.CENTER,
            wrap=True,
        )
        body.add_css_class("rc-dialog-body")
        card.append(body)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        buttons.set_homogeneous(True)
        buttons.set_margin_top(6)

        cancel = Gtk.Button(label="Cancelar")
        cancel.add_css_class("rc-btn-cancel")
        cancel.connect("clicked", lambda *_: dialog.close())
        buttons.append(cancel)

        stop = Gtk.Button(label="Apagar")
        stop.add_css_class("rc-btn-stop")
        stop.connect("clicked", lambda *_: self._confirm_stop(dialog))
        buttons.append(stop)

        card.append(buttons)
        dialog.set_child(card)
        dialog.present(self)

    def _confirm_stop(self, dialog: Adw.Dialog) -> None:
        dialog.close()
        self._stop_async()

    def _stop_async(self) -> None:
        self._busy = True
        self._set_error(None)
        self._set_status("busy", "Apagando…")
        self.switch.set_sensitive(False)

        def work() -> None:
            try:
                self.tunnel.stop()
                GLib.idle_add(self._on_stopped)
            except Exception as exc:
                GLib.idle_add(self._on_stop_failed, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_stopped(self) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._current_url = None
        self.url_entry.set_text("")
        self._paint_qr()
        self._set_status("off", "Apagado")
        self._set_switch(False)
        self._set_password_locked(False)
        return False

    def _on_stop_failed(self, message: str) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._set_error(message)
        return False

    def _on_copy(self, *_args) -> None:
        if not self._current_url:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(self._current_url)
        self.url_entry.select_region(0, -1)
        self._toast("URL copiada")

    def _local_dns_ok(self, url: str | None = None) -> bool:
        target = url or self._current_url or ""
        host = urlparse(target).hostname or ""
        return bool(host and host_resolves(host))

    def _on_open(self, *_args) -> None:
        if not self._current_url:
            return
        if self._local_dns_ok():
            Gio.AppInfo.launch_default_for_uri(self._current_url, None)
            return
        Gio.AppInfo.launch_default_for_uri(
            f"http://127.0.0.1:{self.tunnel.port}/", None
        )
        self._toast("DNS de esta PC aún no; abrí en local")

    def _poll_status(self) -> bool:
        if self._busy:
            return True
        self._refresh_from_status()
        return True

    def _refresh_from_status(self) -> None:
        status = self.tunnel.status()
        if status.running and status.url:
            changed = status.url != self._current_url
            self._current_url = status.url
            self.url_entry.set_text(status.url)
            self._paint_qr()
            if self._local_dns_ok(status.url):
                self._set_status("on", "En línea")
            else:
                self._set_status("busy", "Túnel ok · DNS…")
            self._set_switch(True)
            if changed:
                self.url_entry.select_region(0, -1)
            self._set_password_locked(True)
            return
        self._current_url = None
        self.url_entry.set_text("")
        self._paint_qr()
        self._set_status("off", "Apagado")
        self._set_switch(False)
        self._set_password_locked(False)

    def _on_password_changed(self, *_args) -> None:
        # Strip spaces on change. insert-text warns on this PyGObject build,
        # and changed runs before the new text is painted.
        if self._filtering_password:
            return
        self._commit_password()

    def _commit_password(self) -> None:
        text = self.password_entry.get_text()
        clean = without_spaces(text)
        if clean != text:
            self._filtering_password = True
            try:
                self.password_entry.set_text(clean)
            finally:
                self._filtering_password = False
            text = clean
        try:
            write_password(text)
        except OSError:
            pass
        self._refresh_strength()

    def _refresh_strength(self) -> None:
        text = self.password_entry.get_text()
        kind = password_strength(text)
        self.strength_label.set_label(STRENGTH_LABEL[kind])
        filled = {"none": 0, "weak": 1, "ok": 2, "strong": 4}[kind]
        for cls in ("none", "weak", "ok", "strong"):
            self.strength_label.remove_css_class(cls)
            for bar in self.meter_bars:
                bar.remove_css_class(cls)
        self.strength_label.add_css_class(kind)
        for i, bar in enumerate(self.meter_bars):
            if i < filled:
                bar.add_css_class(kind)
        self.copy_password_btn.set_sensitive(bool(text))
        self.lock_label.set_label("Con contraseña" if text else "Sin contraseña")
        self.lock_chip.remove_css_class("warn")
        if not text:
            self.lock_chip.add_css_class("warn")

    def _on_copy_password(self, *_args) -> None:
        text = self.password_entry.get_text()
        if not text:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(text)
        self._toast("Contraseña copiada")

    def _on_password_icon(self, entry: Gtk.Entry, _pos: Gtk.EntryIconPosition) -> None:
        visible = not entry.get_visibility()
        entry.set_visibility(visible)
        entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY,
            "view-conceal-symbolic" if visible else "view-reveal-symbolic",
        )

    def _set_password_locked(self, locked: bool) -> None:
        self.password_entry.set_sensitive(not locked)
        self.regen_btn.set_sensitive(not locked)
        # The domain is chosen at start: not while the tunnel runs.
        self.domain_btn.set_sensitive(not locked)
        self.domain_btn.set_tooltip_text("Apaga el túnel para cambiar el dominio" if locked else "Configurar dominio")

    def _set_switch(self, active: bool) -> None:
        self._syncing_switch = True
        self.switch.set_state(active)
        self.switch.set_active(active)
        self._syncing_switch = False

    def _set_status(self, kind: str, text: str) -> None:
        self.status_label.set_label(text)
        for cls in ("on", "off", "busy"):
            self.hero.remove_css_class(cls)
        self.hero.add_css_class(kind)
        for cls in ("on", "off", "busy"):
            self.status_label.remove_css_class(cls)
            self.status_dot.remove_css_class(cls)
        self.status_label.add_css_class(kind)
        self.status_dot.add_css_class(kind)

    def _set_error(self, message: str | None) -> None:
        if not message:
            self.error_label.set_visible(False)
            self.error_label.set_label("")
            return
        # Keep the UI tidy: first line only, full text in tooltip.
        first = message.strip().splitlines()[0]
        self.error_label.set_label(first)
        self.error_label.set_tooltip_text(message)
        self.error_label.set_visible(True)

    def _on_is_active(self, *_args) -> None:
        if self.is_active():
            self._maybe_check_update()

    def _maybe_check_update(self, force: bool = False) -> None:
        if not running_from_install():
            return
        now = time.monotonic()
        if not force and (now - self._last_update_check) < self._update_debounce:
            return
        self._last_update_check = now
        threading.Thread(target=self._run_update_check, daemon=True).start()

    def _run_update_check(self) -> None:
        try:
            updated = check_and_apply()
        except Exception:
            return
        if updated:
            GLib.idle_add(self._on_app_updated)

    def _on_app_updated(self) -> bool:
        self._toast("Actualizado; reinicia la app para cargar todo.", timeout=6)
        return False

    def _toast(self, title: str, timeout: int = 2) -> None:
        toast = Adw.Toast(title=title)
        toast.set_timeout(timeout)
        self.toasts.add_toast(toast)
