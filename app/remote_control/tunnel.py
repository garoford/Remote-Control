"""Start and stop the Cloudflare Quick Tunnel + ttyd session."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
REGISTERED_RE = re.compile(r"Registered tunnel connection")


def dns_via_doh(host: str, timeout: float = 3.0) -> bool:
    """True if 1.1.1.1 already has A or AAAA for host."""
    for qtype in ("A", "AAAA"):
        url = f"https://1.1.1.1/dns-query?name={host}&type={qtype}"
        req = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        answers = data.get("Answer") or []
        if any(item.get("data") for item in answers):
            return True
    return False


def host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def flush_resolved_cache() -> None:
    resolvectl = shutil.which("resolvectl")
    if not resolvectl:
        return
    subprocess.run(
        [resolvectl, "flush-caches"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def dns_via_dig(host: str, server: str) -> bool:
    """Ask a recursive resolver directly. Does not touch systemd-resolved."""
    dig = shutil.which("dig")
    if not dig:
        return False
    for qtype in ("A", "AAAA"):
        result = subprocess.run(
            [dig, f"@{server}", "+time=2", "+tries=1", "+short", host, qtype],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if line[0].isdigit() or ":" in line:
                return True
    return False


def public_resolvers_ready(host: str) -> bool:
    """1.1.1.1 is fast; Chrome here uses 8.8.8.8 via resolved. Wait for both."""
    if not dns_via_doh(host):
        return False
    return dns_via_dig(host, "8.8.8.8") or dns_via_dig(host, "8.8.4.4")


DEFAULT_PORT = 7681
NIGHT_OWL_THEME = (
    '{"background":"#011627","foreground":"#d6deeb","cursor":"#80A4C2",'
    '"cursorAccent":"#011627","selectionBackground":"#1d3b53",'
    '"selectionInactiveBackground":"#0b2942","black":"#011627",'
    '"red":"#EF5350","green":"#22DA6E","yellow":"#ADDB67","blue":"#82AAFF",'
    '"magenta":"#C792EA","cyan":"#21C7A8","white":"#FFFFFF",'
    '"brightBlack":"#575656","brightRed":"#EF5350","brightGreen":"#22DA6E",'
    '"brightYellow":"#FFEB95","brightBlue":"#82AAFF",'
    '"brightMagenta":"#C792EA","brightCyan":"#7FDBCA","brightWhite":"#FFFFFF"}'
)


class TunnelError(RuntimeError):
    pass


@dataclass(frozen=True)
class TunnelStatus:
    running: bool
    url: str | None
    port: int
    ttyd_pid: int | None
    cloudflared_pid: int | None
    proxy_pid: int | None


class TunnelService:
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.home = Path.home()
        self.user = os.environ.get("USER") or os.getlogin()
        self.run_dir = self.home / ".cache" / "cf-quick-tunnel"
        self.font_dir = self.run_dir / "fonts"
        self.log_file = self.run_dir / "cloudflared.log"
        self.ttyd_log = self.run_dir / "ttyd.log"
        self.url_file = self.run_dir / "PUBLIC-URL.txt"
        self.pid_dir = self.run_dir / "pids"
        self.ttyd_pid_file = self.pid_dir / "ttyd.pid"
        self.cf_pid_file = self.pid_dir / "cloudflared.pid"
        self.proxy_pid_file = self.pid_dir / "proxy.pid"
        self.ttyd_port_file = self.pid_dir / "ttyd.port"
        self.proxy_log = self.run_dir / "proxy.log"
        self.assets_dir = self.run_dir / "rc-assets"
        self.ttyd_port = self.port + 1
        self.font_reg_url = ""
        self.font_bold_url = ""
        self.ttyd_index = self.run_dir / "ttyd-index.html"
        fonts = self.home / ".local" / "share" / "fonts" / "FiraCode"
        self.font_reg_ttf = fonts / "FiraCodeNerdFontMono-Regular.ttf"
        self.font_bold_ttf = fonts / "FiraCodeNerdFontMono-Bold.ttf"
        self.font_reg_woff = self.font_dir / "FiraCodeNerdFontMono-Regular.woff2"
        self.font_bold_woff = self.font_dir / "FiraCodeNerdFontMono-Bold.woff2"

    def ensure_dirs(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.pid_dir.mkdir(parents=True, exist_ok=True)
        self.font_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def missing_binaries(self) -> list[str]:
        missing: list[str] = []
        if not self.which("ttyd"):
            missing.append("ttyd")
        if not self.which("cloudflared"):
            missing.append("cloudflared")
        if not self.which("tmux"):
            missing.append("tmux")
        return missing

    def status(self) -> TunnelStatus:
        ttyd_pid = self._alive_pid(self.ttyd_pid_file)
        cf_pid = self._alive_pid(self.cf_pid_file)
        proxy_pid = self._alive_pid(self.proxy_pid_file)
        running = bool(ttyd_pid and cf_pid and proxy_pid)
        url = None
        if self.url_file.is_file():
            text = self.url_file.read_text(encoding="utf-8").strip()
            if text:
                url = text
        if running and not url:
            url = self._url_from_log()
        return TunnelStatus(
            running=running,
            url=url if running else None,
            port=self.port,
            ttyd_pid=ttyd_pid,
            cloudflared_pid=cf_pid,
            proxy_pid=proxy_pid,
        )

    def start(self) -> str:
        missing = self.missing_binaries()
        if missing:
            raise TunnelError(
                "Falta instalar: " + ", ".join(missing) + "."
            )
        self.ensure_dirs()
        self.stop(silent=True)
        self.ttyd_port = self._pick_internal_port()
        self.ttyd_port_file.write_text(str(self.ttyd_port), encoding="utf-8")
        self._prepare_tab_session()
        self._prepare_rc_assets()
        self._prepare_ttyd_index()
        self._start_ttyd()
        self._start_proxy()
        self._start_cloudflared()
        url = self._wait_for_url(timeout=60)
        self._wait_until_public(url, timeout=70)
        self.url_file.write_text(url + "\n", encoding="utf-8")
        self.url_file.chmod(0o600)
        return url

    def stop(self, silent: bool = False) -> None:
        self._kill_pidfile(self.proxy_pid_file)
        self._kill_pidfile(self.ttyd_pid_file)
        self._kill_pidfile(self.cf_pid_file)
        self._pkill("remote_control.proxy")
        self._pkill("ttyd --interface 127.0.0.1 --port")
        self._pkill(f"cloudflared tunnel --url http://127.0.0.1:{self.port}")
        self.ttyd_port_file.unlink(missing_ok=True)
        subprocess.run(
            ["tmux", "-L", "cf-remote", "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        for path in (
            self.ttyd_pid_file,
            self.cf_pid_file,
            self.proxy_pid_file,
            self.url_file,
        ):
            path.unlink(missing_ok=True)
        if not silent:
            self.log_file.unlink(missing_ok=True)

    def _start_ttyd(self) -> None:
        user_shell = self._resolve_user_shell()
        lang = os.environ.get("LANG", "en_US.UTF-8")
        env = {
            "HOME": str(self.home),
            "USER": self.user,
            "LOGNAME": self.user,
            "SHELL": user_shell,
            "RC_SHELL": user_shell,
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "LANG": lang,
            "LC_ALL": lang,
            "PATH": f"{self.home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        }
        runtime = Path(f"/run/user/{os.getuid()}")
        if runtime.is_dir():
            env["XDG_RUNTIME_DIR"] = str(runtime)
        ssh_sock = os.environ.get("SSH_AUTH_SOCK")
        if ssh_sock and Path(ssh_sock).is_socket():
            env["SSH_AUTH_SOCK"] = ssh_sock

        cmd = [
            "ttyd",
            "--interface",
            "127.0.0.1",
            "--port",
            str(self.ttyd_port),
            "--writable",
            "--url-arg",
            "--cwd",
            str(self.home),
            "--terminal-type",
            "xterm-256color",
            "-t",
            "fontSize=15",
            "-t",
            "fontFamily=FiraCode Nerd Font Mono",
            "-t",
            "fontWeight=400",
            "-t",
            "fontWeightBold=700",
            "-t",
            "cursorBlink=true",
            "-t",
            "scrollback=20000",
            "-t",
            f"theme={NIGHT_OWL_THEME}",
        ]
        if self.ttyd_index.is_file() and self.ttyd_index.stat().st_size > 0:
            cmd.extend(["--index", str(self.ttyd_index)])
        cmd.append(str(self.run_dir / "tab_session.sh"))

        with self.ttyd_log.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        self.ttyd_pid_file.write_text(str(proc.pid), encoding="utf-8")
        deadline = time.time() + 4
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = self.ttyd_log.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise TunnelError("ttyd no arrancó.\n" + tail)
            if self._port_open(self.ttyd_port):
                return
            time.sleep(0.1)
        raise TunnelError("ttyd no abrió el puerto.")

    def _start_proxy(self) -> None:
        pkg_root = Path(__file__).resolve().parent.parent
        env = os.environ.copy()
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(pkg_root) + (os.pathsep + prev if prev else "")
        cmd = [
            sys.executable,
            "-m",
            "remote_control.proxy",
            "--listen",
            f"127.0.0.1:{self.port}",
            "--upstream",
            f"127.0.0.1:{self.ttyd_port}",
            "--assets",
            str(self.assets_dir),
            "--tmux-socket",
            "cf-remote",
        ]
        with self.proxy_log.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        self.proxy_pid_file.write_text(str(proc.pid), encoding="utf-8")
        deadline = time.time() + 4
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = self.proxy_log.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise TunnelError("El sidecar no arrancó.\n" + tail)
            if self._port_open(self.port):
                return
            time.sleep(0.1)
        raise TunnelError("El sidecar no abrió el puerto.")

    def _start_cloudflared(self) -> None:
        self.log_file.write_text("", encoding="utf-8")
        with self.log_file.open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [
                    "cloudflared",
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{self.port}",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.cf_pid_file.write_text(str(proc.pid), encoding="utf-8")
        time.sleep(0.8)
        if proc.poll() is not None:
            tail = self.log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise TunnelError("cloudflared no arrancó.\n" + tail)

    def _wait_for_url(self, timeout: int = 60) -> str:
        deadline = time.time() + timeout
        url = None
        while time.time() < deadline:
            if url is None:
                url = self._url_from_log()
            if url and self._tunnel_registered():
                return url
            if not self._alive_pid(self.cf_pid_file):
                tail = self.log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise TunnelError("cloudflared se detuvo.\n" + tail)
            time.sleep(0.4)
        if url:
            return url
        tail = self.log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise TunnelError("No salió la URL a tiempo.\n" + tail)

    def _tunnel_registered(self) -> bool:
        if not self.log_file.is_file():
            return False
        text = self.log_file.read_text(encoding="utf-8", errors="replace")
        return bool(REGISTERED_RE.search(text))

    def _wait_until_public(self, url: str, timeout: int = 70) -> None:
        """Don't mark ready until the resolver Chrome uses can see the name.

        1.1.1.1 publishes first. If we getaddrinfo() before 8.8.8.8 has the
        record, systemd-resolved caches NXDOMAIN and Chrome stays broken.
        """
        host = urlparse(url).hostname or ""
        if not host:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._alive_pid(self.cf_pid_file):
                tail = self.log_file.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise TunnelError("cloudflared se detuvo antes de publicar DNS.\n" + tail)
            if public_resolvers_ready(host):
                flush_resolved_cache()
                time.sleep(0.5)
                if host_resolves(host):
                    return
                time.sleep(2.0)
                flush_resolved_cache()
                time.sleep(0.3)
                if host_resolves(host):
                    return
                # Keep waiting; do not poll getaddrinfo in a tight loop.
            time.sleep(0.8)
        flush_resolved_cache()

    def _url_from_log(self) -> str | None:
        if not self.log_file.is_file():
            return None
        text = self.log_file.read_text(encoding="utf-8", errors="replace")
        matches = URL_RE.findall(text)
        return matches[-1] if matches else None

    def _prepare_ttyd_index(self) -> None:
        html = ""
        if self.ttyd_index.is_file() and self.ttyd_index.stat().st_size > 0:
            html = self.ttyd_index.read_text(encoding="utf-8", errors="replace")
        if not html:
            html = self._fetch_ttyd_html()
        if not html:
            return
        html = self._strip_injects(html)
        inject = self._font_css() + self._web_inject()
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + inject, 1)
        else:
            html = inject + html
        self.ttyd_index.write_text(html, encoding="utf-8")

    def _fetch_ttyd_html(self) -> str:
        ttyd = self.which("ttyd")
        if not ttyd:
            return ""
        probe = self._free_port()
        proc = subprocess.Popen(
            [ttyd, "--interface", "127.0.0.1", "--port", str(probe), "/bin/true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        html = ""
        try:
            for _ in range(30):
                try:
                    with socket.create_connection(("127.0.0.1", probe), timeout=0.2):
                        pass
                    result = subprocess.run(
                        ["curl", "-fsS", f"http://127.0.0.1:{probe}/"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout:
                        html = result.stdout
                        break
                except OSError:
                    pass
                time.sleep(0.1)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        return html

    def _strip_void(self, html: str, tag: str, ident: str) -> str:
        token = f'<{tag} id="{ident}"'
        start = html.find(token)
        if start < 0:
            return html
        end = html.find(">", start)
        if end < 0:
            return html
        return html[:start] + html[end + 1 :]

    def _strip_tagged(self, html: str, tag: str, ident: str) -> str:
        open_tag = f'<{tag} id="{ident}"'
        start = html.find(open_tag)
        if start < 0:
            return html
        close = f"</{tag}>"
        end = html.find(close, start)
        if end < 0:
            return html
        return html[:start] + html[end + len(close) :]

    def _strip_injects(self, html: str) -> str:
        html = self._strip_void(html, "link", "rc-font-preload-reg")
        html = self._strip_void(html, "link", "rc-font-preload-bold")
        html = self._strip_tagged(html, "style", "cf-remote-theme")
        html = self._strip_tagged(html, "style", "rc-extra-keys-css")
        html = self._strip_tagged(html, "script", "rc-tab-session-js")
        html = self._strip_tagged(html, "script", "rc-extra-keys-js")
        html = self._strip_tagged(html, "script", "rc-cache-js")
        token = '<meta id="rc-viewport"'
        start = html.find(token)
        if start >= 0:
            end = html.find(">", start)
            if end >= 0:
                html = html[:start] + html[end + 1 :]
        return html

    def _web_inject(self) -> str:
        root = Path(__file__).with_name("web")
        css_path = root / "extra_keys.css"
        keys_js = root / "extra_keys.js"
        tab_js = root / "tab_session.js"
        parts: list[str] = [
            '<meta id="rc-viewport" name="viewport" content="width=device-width,'
            "initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover,"
            'interactive-widget=resizes-content">'
        ]
        if css_path.is_file():
            parts.append(
                f'<style id="rc-extra-keys-css">{css_path.read_text(encoding="utf-8")}</style>'
            )
        if tab_js.is_file():
            parts.append(
                f'<script id="rc-tab-session-js">{tab_js.read_text(encoding="utf-8")}</script>'
            )
        if keys_js.is_file():
            parts.append(
                f'<script id="rc-extra-keys-js">{keys_js.read_text(encoding="utf-8")}</script>'
            )
        parts.append(
            '<script id="rc-cache-js" src="/rc-assets/cache.js?v=1.2.2"></script>'
        )
        return "".join(parts)

    def _prepare_tab_session(self) -> None:
        root = Path(__file__).with_name("web")
        wrapper_src = root / "tab_session.sh"
        conf_src = root / "tmux.tab.conf"
        wrapper = self.run_dir / "tab_session.sh"
        conf = self.run_dir / "tmux.tab.conf"
        if wrapper_src.is_file():
            wrapper.write_text(wrapper_src.read_text(encoding="utf-8"), encoding="utf-8")
            wrapper.chmod(0o755)
        if conf_src.is_file():
            conf.write_text(conf_src.read_text(encoding="utf-8"), encoding="utf-8")

    def _ensure_woff2_fonts(self) -> None:
        if self.font_reg_woff.is_file() and self.font_bold_woff.is_file():
            return
        if not (self.font_reg_ttf.is_file() and self.font_bold_ttf.is_file()):
            return
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            return
        for src, dst in (
            (self.font_reg_ttf, self.font_reg_woff),
            (self.font_bold_ttf, self.font_bold_woff),
        ):
            font = TTFont(str(src))
            font.flavor = "woff2"
            font.save(str(dst))

    def _prepare_rc_assets(self) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        for stale in self.assets_dir.glob("font-*.woff2"):
            stale.unlink(missing_ok=True)
        self._ensure_woff2_fonts()
        self.font_reg_url = ""
        self.font_bold_url = ""
        if self.font_reg_woff.is_file():
            self.font_reg_url = self._publish_font(self.font_reg_woff, "regular")
        if self.font_bold_woff.is_file():
            self.font_bold_url = self._publish_font(self.font_bold_woff, "bold")
        web = Path(__file__).with_name("web")
        for name in ("cache.js", "sw.js"):
            src = web / name
            if src.is_file():
                shutil.copy2(src, self.assets_dir / name)
        fonts = [url for url in (self.font_reg_url, self.font_bold_url) if url]
        (self.assets_dir / "manifest.json").write_text(
            json.dumps({"fonts": fonts}),
            encoding="utf-8",
        )

    def _publish_font(self, src: Path, weight: str) -> str:
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
        name = f"font-{weight}-{digest}.woff2"
        shutil.copy2(src, self.assets_dir / name)
        return f"/rc-assets/{name}"

    def _font_css(self) -> str:
        preloads = ""
        if self.font_reg_url:
            preloads += (
                f'<link id="rc-font-preload-reg" rel="preload" as="font" '
                f'type="font/woff2" crossorigin href="{self.font_reg_url}">'
            )
        if self.font_bold_url:
            preloads += (
                f'<link id="rc-font-preload-bold" rel="preload" as="font" '
                f'type="font/woff2" crossorigin href="{self.font_bold_url}">'
            )
        faces = ""
        stack = (
            "'FiraCode Nerd Font Mono',ui-monospace,'Cascadia Mono',"
            "'Courier New',monospace"
        )
        if self.font_reg_url:
            faces += (
                "@font-face{font-family:'FiraCode Nerd Font Mono';font-style:normal;"
                f"font-weight:400;font-display:swap;src:url('{self.font_reg_url}') "
                "format('woff2');}"
            )
        if self.font_bold_url:
            faces += (
                "@font-face{font-family:'FiraCode Nerd Font Mono';font-style:normal;"
                f"font-weight:700;font-display:swap;src:url('{self.font_bold_url}') "
                "format('woff2');}"
            )
        return (
            f"{preloads}"
            '<style id="cf-remote-theme">'
            f"{faces}"
            "html,body{background:#011627;margin:0;height:100%;}"
            "body,.xterm,.xterm-viewport,.xterm-rows,.xterm-screen,"
            f".xterm-helper-textarea{{font-family:{stack}!important;"
            "font-feature-settings:'liga' 1,'calt' 1;}</style>"
        )

    def _pick_internal_port(self) -> int:
        candidate = self.port + 1
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                return self._free_port()

    def _port_open(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False

    def _resolve_user_shell(self) -> str:
        candidates: list[str] = []
        try:
            import pwd

            login = pwd.getpwuid(os.getuid()).pw_shell
            if login:
                candidates.append(login)
        except Exception:
            pass
        if os.environ.get("SHELL"):
            candidates.append(os.environ["SHELL"])
        candidates.extend(
            [
                "/usr/bin/zsh",
                "/bin/zsh",
                "/usr/bin/bash",
                "/bin/bash",
                "/usr/bin/fish",
                "/bin/fish",
                "/bin/sh",
            ]
        )
        skip = {"nologin", "false", "sync", "halt", "shutdown"}
        for candidate in candidates:
            name = Path(candidate).name
            if name in skip:
                continue
            if os.access(candidate, os.X_OK):
                return candidate
            resolved = shutil.which(candidate)
            if resolved and os.access(resolved, os.X_OK):
                return resolved
        raise TunnelError("No encontré un shell ejecutable.")

    def _shell_login_args(self, shell: str) -> list[str]:
        name = Path(shell).name
        if name in {"sh", "dash", "ash"}:
            return ["-i"]
        return ["-il"]

    def _free_port(self) -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _alive_pid(self, path: Path) -> int | None:
        if not path.is_file():
            return None
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    def _kill_pidfile(self, path: Path) -> None:
        pid = self._alive_pid(path)
        if not pid:
            path.unlink(missing_ok=True)
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            path.unlink(missing_ok=True)
            return
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        path.unlink(missing_ok=True)

    def _pkill(self, pattern: str) -> None:
        subprocess.run(
            ["pkill", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
