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
    password_strength,
    read_password,
    without_spaces,
    write_password,
)
from remote_control.tunnel import TunnelService, host_resolves
from remote_control.updater import check_and_apply, running_from_install


class RemoteControlWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title("Remote Control")
        self.set_default_size(360, 540)
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

        canvas = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        canvas.add_css_class("rc-canvas")
        canvas.set_hexpand(True)
        canvas.set_vexpand(True)
        canvas.set_margin_top(8)
        canvas.set_margin_bottom(14)
        canvas.set_margin_start(18)
        canvas.set_margin_end(18)
        self.toasts.set_child(canvas)

        canvas.append(self._build_heading())
        canvas.append(self._build_panel())
        canvas.append(self._build_url_card())
        canvas.append(self._build_error())
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        canvas.append(spacer)
        canvas.append(self._build_hint())

        self.set_content(toolbar)

    def _build_heading(self) -> Gtk.Widget:
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.add_css_class("rc-heading")

        kicker = Gtk.Label(label="ACCESO REMOTO")
        kicker.add_css_class("rc-kicker")
        kicker.set_halign(Gtk.Align.CENTER)
        head.append(kicker)

        title = Gtk.Label(label="Túnel web")
        title.add_css_class("rc-title")
        title.set_halign(Gtk.Align.CENTER)
        head.append(title)

        subtitle = Gtk.Label(
            label="Enciende el switch para publicar tu terminal\nen una URL temporal.",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        subtitle.add_css_class("rc-subtitle")
        head.append(subtitle)
        return head

    def _build_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        panel.add_css_class("rc-panel")
        panel.append(self._build_password())
        panel.append(self._build_switch())
        panel.append(self._build_status())
        return panel

    def _build_status(self) -> Gtk.Widget:
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status.add_css_class("rc-status")
        status.set_halign(Gtk.Align.CENTER)

        self.status_dot = Gtk.Box()
        self.status_dot.add_css_class("rc-dot")
        self.status_dot.set_valign(Gtk.Align.CENTER)
        status.append(self.status_dot)

        self.status_label = Gtk.Label(label="Apagado", xalign=0)
        self.status_label.add_css_class("rc-status-label")
        self.status_label.add_css_class("off")
        status.append(self.status_label)
        return status

    def _build_password(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("rc-pass")
        label = Gtk.Label(label="CONTRASEÑA", xalign=0)
        label.add_css_class("rc-pass-label")
        box.append(label)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_placeholder_text("Opcional")
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.password_entry.set_hexpand(True)
        self.password_entry.add_css_class("rc-pass-entry")
        self.password_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic"
        )
        saved = read_password()
        clean = without_spaces(saved)
        if clean != saved:
            try:
                write_password(clean)
            except OSError:
                pass
        self.password_entry.set_text(clean)
        self.password_entry.connect("icon-press", self._on_password_icon)
        self.password_entry.connect("changed", self._on_password_changed)
        row.append(self.password_entry)

        self.copy_password_btn = Gtk.Button(label="Copiar")
        self.copy_password_btn.add_css_class("rc-copy-btn")
        self.copy_password_btn.set_valign(Gtk.Align.CENTER)
        self.copy_password_btn.connect("clicked", self._on_copy_password)
        row.append(self.copy_password_btn)
        box.append(row)

        self.strength_label = Gtk.Label(xalign=0)
        self.strength_label.add_css_class("rc-strength")
        self.strength_label.set_halign(Gtk.Align.START)
        box.append(self.strength_label)
        self._refresh_strength()
        return box

    def _build_switch(self) -> Gtk.Widget:
        row = Gtk.Box(halign=Gtk.Align.CENTER)
        row.add_css_class("rc-power")
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_halign(Gtk.Align.CENTER)
        self.switch.connect("state-set", self._on_switch_state_set)
        row.append(self.switch)
        return row

    def _build_hint(self) -> Gtk.Widget:
        hint = Gtk.Label(
            label="Cloudflare Quick Tunnel  ·  ttyd Night Owl",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        hint.add_css_class("rc-hint")
        hint.set_margin_top(4)
        return hint

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
        self.url_card.set_visible(True)
        self._set_switch(True)
        if self._local_dns_ok(url):
            self._set_status("on", "En línea")
        else:
            self._set_status("busy", "Túnel ok · DNS…")
            self._toast("URL lista; el DNS de esta PC aún no")
        return False

    def _on_start_failed(self, message: str) -> bool:
        self._busy = False
        self.switch.set_sensitive(True)
        self._current_url = None
        self.url_card.set_visible(False)
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
        self.url_card.set_visible(False)
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
            self.url_card.set_visible(True)
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
        self.url_card.set_visible(False)
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
        for cls in ("none", "weak", "ok", "strong"):
            self.strength_label.remove_css_class(cls)
        self.strength_label.add_css_class(kind)
        self.copy_password_btn.set_sensitive(bool(text))

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
