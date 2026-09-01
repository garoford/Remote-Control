from __future__ import annotations

import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from remote_control.tunnel import TunnelService
from remote_control.updater import check_and_apply, running_from_install


class RemoteControlWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title("Remote Control")
        self.set_default_size(360, 460)
        self.set_resizable(False)
        self.add_css_class("rc-window")

        self.tunnel = TunnelService()
        self._busy = False
        self._current_url: str | None = None
        self._syncing_switch = False
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
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="Remote Control", subtitle="Túnel de terminal")
        )
        toolbar.add_top_bar(header)

        self.toasts = Adw.ToastOverlay()
        toolbar.set_content(self.toasts)

        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        canvas.add_css_class("rc-canvas")
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        canvas.set_margin_top(18)
        canvas.set_margin_bottom(16)
        canvas.set_margin_start(20)
        canvas.set_margin_end(20)
        self.toasts.set_child(canvas)

        canvas.append(self._build_hero())
        canvas.append(self._build_url_card())
        canvas.append(self._build_idle())
        canvas.append(self._build_error())

        hint = Gtk.Label(
            label="Cloudflare Quick Tunnel  ·  ttyd Night Owl",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        hint.add_css_class("rc-hint")
        hint.set_margin_top(4)
        canvas.append(hint)

        self.set_content(toolbar)

    def _build_hero(self) -> Gtk.Widget:
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero.add_css_class("rc-hero")
        hero.set_halign(Gtk.Align.FILL)
        hero.set_hexpand(True)

        kicker = Gtk.Label(label="ACCESO REMOTO")
        kicker.add_css_class("rc-kicker")
        kicker.set_halign(Gtk.Align.CENTER)
        hero.append(kicker)

        title = Gtk.Label(label="Túnel web")
        title.add_css_class("rc-title")
        title.set_halign(Gtk.Align.CENTER)
        hero.append(title)

        subtitle = Gtk.Label(
            label="Enciende el switch para publicar tu terminal\nen una URL temporal.",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        subtitle.add_css_class("rc-subtitle")
        hero.append(subtitle)

        self.switch = Gtk.Switch()
        self.switch.set_halign(Gtk.Align.CENTER)
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.connect("state-set", self._on_switch_state_set)
        switch_wrap = Gtk.Box(halign=Gtk.Align.CENTER)
        switch_wrap.add_css_class("rc-switch-wrap")
        switch_wrap.append(self.switch)
        hero.append(switch_wrap)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status.add_css_class("rc-status")
        status.set_halign(Gtk.Align.CENTER)

        self.status_dot = Gtk.Box()
        self.status_dot.add_css_class("rc-dot")
        self.status_dot.set_valign(Gtk.Align.CENTER)
        status.append(self.status_dot)

        self.status_label = Gtk.Label(label="Apagado")
        self.status_label.add_css_class("rc-status-label")
        self.status_label.add_css_class("off")
        status.append(self.status_label)
        hero.append(status)
        return hero

    def _build_url_card(self) -> Gtk.Widget:
        self.url_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.url_card.add_css_class("rc-url-card")
        self.url_card.set_visible(False)

        label = Gtk.Label(label="URL PÚBLICA", xalign=0)
        label.add_css_class("rc-url-label")
        self.url_card.append(label)

        self.url_entry = Gtk.Entry()
        self.url_entry.set_editable(False)
        self.url_entry.set_hexpand(True)
        self.url_entry.add_css_class("rc-url-entry")
        self.url_entry.set_placeholder_text("https://….trycloudflare.com")
        self.url_card.append(self.url_entry)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_homogeneous(True)

        copy_btn = Gtk.Button(label="Copiar")
        copy_btn.add_css_class("rc-copy-btn")
        copy_btn.connect("clicked", self._on_copy)
        actions.append(copy_btn)

        open_btn = Gtk.Button(label="Abrir")
        open_btn.add_css_class("rc-open-btn")
        open_btn.connect("clicked", self._on_open)
        actions.append(open_btn)

        self.url_card.append(actions)
        return self.url_card

    def _build_idle(self) -> Gtk.Widget:
        self.idle_label = Gtk.Label(
            label="El túnel está apagado. Nadie puede entrar\nhasta que lo enciendas.",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        self.idle_label.add_css_class("rc-idle")
        return self.idle_label

    def _build_error(self) -> Gtk.Widget:
        self.error_label = Gtk.Label(wrap=True, xalign=0)
        self.error_label.add_css_class("rc-error")
        self.error_label.set_visible(False)
        return self.error_label

    def _on_switch_state_set(self, switch: Gtk.Switch, state: bool) -> bool:
        if self._syncing_switch or self._busy:
            return True
        if state:
            self._start_async()
            return True
        if self.tunnel.status().running or self._current_url:
            self._show_stop_dialog()
            return True
        return False

    def _start_async(self) -> None:
        self._busy = True
        self._set_error(None)
        self._set_status("busy", "Encendiendo…")
        self._set_switch(True)
        self.switch.set_sensitive(False)
        self.url_card.set_visible(True)
        self.idle_label.set_visible(False)
        self.url_entry.set_text("Creando túnel y esperando DNS…")

        def work() -> None:
            try:
                url = self.tunnel.start()
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
        self.url_card.set_visible(True)
        self.idle_label.set_visible(False)
        self._set_status("on", "En línea")
        self._set_switch(True)
        self._toast("Túnel listo")
        return False

    def _on_start_failed(self, message: str) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._current_url = None
        self.url_card.set_visible(False)
        self.idle_label.set_visible(True)
        self._set_status("off", "Apagado")
        self._set_switch(False)
        self._set_error(message)
        return False

    def _show_stop_dialog(self) -> None:
        dialog = Adw.Dialog()
        dialog.set_content_width(360)
        dialog.set_follows_content_size(True)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("rc-dialog")
        card.set_halign(Gtk.Align.FILL)

        icon_wrap = Gtk.Box(halign=Gtk.Align.CENTER)
        icon_wrap.add_css_class("rc-dialog-icon")
        icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        icon.set_pixel_size(26)
        icon.set_margin_top(13)
        icon.set_margin_bottom(13)
        icon.set_margin_start(13)
        icon.set_margin_end(13)
        icon_wrap.append(icon)
        card.append(icon_wrap)

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
        self.url_card.set_visible(False)
        self.idle_label.set_visible(True)
        self._set_status("off", "Apagado")
        self._set_switch(False)
        self._toast("Túnel apagado")
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

    def _on_open(self, *_args) -> None:
        if not self._current_url:
            return
        Gio.AppInfo.launch_default_for_uri(self._current_url, None)

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
            self.url_card.set_visible(True)
            self.idle_label.set_visible(False)
            self._set_status("on", "En línea")
            self._set_switch(True)
            if changed:
                self.url_entry.select_region(0, -1)
            return
        if self._current_url and not status.running:
            self._toast("El túnel se detuvo")
        self._current_url = None
        self.url_card.set_visible(False)
        self.idle_label.set_visible(True)
        self._set_status("off", "Apagado")
        self._set_switch(False)

    def _set_switch(self, active: bool) -> None:
        self._syncing_switch = True
        self.switch.set_state(active)
        self.switch.set_active(active)
        self._syncing_switch = False

    def _set_status(self, kind: str, text: str) -> None:
        self.status_label.set_label(text)
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
