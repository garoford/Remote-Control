"""Your own domain for each multi container: one HTTPS router on this machine.

A single process listens on the public HTTPS port (443, or 8443 with the
router forwarding 443 to it: domains.direct_reach) and looks at the name the
browser asked for (SNI): backend.example.com goes to container "backend",
ia.example.com to "ia". TLS ends here, with one Let's Encrypt certificate
per name (acme.py, DNS-01 through the account's API), and the plain bytes go
to the container's local port, where its own Remote Control asks for its
password. The provider's key never enters a container.

A name is only taken when it is free: a record there that is not ours (any
type but an A/AAAA to this machine) means it is used for something else.

Routes live in ~/.config/remote-control/multi-domains.json; the router is a
systemd user service (remote-control-router), restarted on every change,
that also follows this machine's public IP and renews the certificates.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import acme, domains, multi
from .gate import _write_private, preference_path
from .tls_front import _pipe

UNIT = "remote-control-router.service"
RELOAD_S = 15
IP_CHECK_S = 300
RENEW_CHECK_S = 12 * 3600


def routes_path() -> Path:
    return preference_path().with_name("multi-domains.json")


def load_routes() -> dict[str, str]:
    """container name -> its host name."""
    try:
        data = json.loads(routes_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save_routes(routes: dict[str, str]) -> None:
    _write_private(routes_path(), json.dumps(routes, indent=2, sort_keys=True) + "\n")


def public_url(host: str, port: int) -> str:
    return f"https://{host}" + ("" if port == 443 else f":{port}") + "/"


# --- give / take a name --------------------------------------------------------------------


def _account() -> tuple[domains.DomainConfig, domains.Provider]:
    cfg = domains.load_config()
    if cfg is None or not cfg.provider or not cfg.zone:
        raise domains.DomainError("Primero configura la cuenta de tu proveedor de dominios")
    return cfg, domains.provider_for(cfg)


def assign(box: str, sub: str, log=lambda _msg: None) -> str:
    """Point sub.<zone> at this machine for container ``box``; its URL."""
    cfg, prov = _account()
    sub = sub.strip().lower().strip(".")
    host = domains.compose_host(sub, cfg.zone)
    if not sub or not domains.valid_host(host):
        raise domains.DomainError("subdominio inválido")
    routes = load_routes()
    taken = next((b for b, h in routes.items() if h == host and b != box), None)
    if taken:
        raise domains.DomainError(f"{host} ya es del contenedor {taken}")
    public = domains.public_ipv4()
    reach = domains.direct_reach(public)
    if not reach.ok:
        raise domains.DomainError(reach.why)
    zone, name = domains.split_host(host, [cfg.zone])
    foreign = [(t, v) for t, v in prov.records_at(zone, name) if not (t in ("A", "AAAA") and v == public)]
    if foreign and routes.get(box) != host:
        used = ", ".join(f"{t} {v}" for t, v in foreign[:3])
        raise domains.DomainError(f"{host} ya se usa ({used}): elige otro subdominio")
    log(f"{host} → {public}")
    prov.point(zone, name, public)
    acme.ensure_cert(
        host,
        zone,
        lambda value: prov.set_record(zone, domains.challenge_name(name), "TXT", value, ttl=300),
        lambda: prov.delete_record(zone, domains.challenge_name(name), "TXT"),
        email=cfg.email,
        directory=acme.STAGING if os.environ.get("RC_ACME_STAGING") else acme.DIRECTORY,
        log=log,
    )
    old = routes.get(box)
    routes[box] = host
    save_routes(routes)
    if old and old != host:
        _release(prov, cfg, old, public)
    url = public_url(host, reach.public_port)
    _tell_container(box, url)
    ensure_service()
    return url


def unassign(box: str) -> None:
    routes = load_routes()
    host = routes.pop(box, None)
    if host is None:
        return
    save_routes(routes)
    try:
        cfg, prov = _account()
        _release(prov, cfg, host, domains.public_ipv4())
    except domains.DomainError:
        pass
    _tell_container(box, None)
    ensure_service()


def rename(old: str, new: str) -> None:
    routes = load_routes()
    if old in routes:
        routes[new] = routes.pop(old)
        save_routes(routes)
        ensure_service()


def _release(prov: domains.Provider, cfg: domains.DomainConfig, host: str, public: str | None) -> None:
    """Drop the A record of a name we no longer use (only if it is ours)."""
    try:
        zone, name = domains.split_host(host, [cfg.zone])
        if public and ("A", public) in prov.records_at(zone, name):
            prov.delete_record(zone, name, "A")
    except domains.DomainError:
        pass


def _tell_container(box: str, url: str | None) -> None:
    """The container's Remote Control serves its URL without cloudflared (tunnel.py).

    Written through ``podman cp``, so it works with the container stopped (its
    tunnel off, which is when the name is changed). Empty means no domain.
    """
    try:
        multi._write_file(box, f"{multi.HOME}/.config/remote-control/public-url", url or "")
    except multi.MultiError:
        pass


# --- the systemd user service ----------------------------------------------------------------


def unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT


def ensure_service() -> None:
    """Running (and restarted to read the routes) while any container has a name."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    run = lambda *args: subprocess.run([systemctl, "--user", *args], capture_output=True, check=False)  # noqa: E731
    if not load_routes():
        run("disable", "--now", UNIT)
        return
    wrapper = Path.home() / ".local" / "bin" / "remote-control"
    exec_start = f"{wrapper} router" if wrapper.is_file() else f"{sys.executable} -m remote_control router"
    unit = unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "[Unit]\n"
        "Description=Remote Control: dominio propio de los contenedores\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={exec_start}\n"
        f"Environment=PYTHONPATH={Path(__file__).resolve().parent.parent}\n"
        "Restart=on-failure\n"
        "RestartSec=10\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    run("daemon-reload")
    run("enable", UNIT)
    run("restart", UNIT)


def service_active() -> bool:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    res = subprocess.run([systemctl, "--user", "is-active", UNIT], capture_output=True, text=True, check=False)
    return res.stdout.strip() == "active"


# --- the router itself -----------------------------------------------------------------------


class Router:
    def __init__(self, port: int) -> None:
        self.port = port
        self.table: dict[str, int] = {}
        self.contexts: dict[str, ssl.SSLContext] = {}
        self.base = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.base.minimum_version = ssl.TLSVersion.TLSv1_2
        self.base.sni_callback = self._sni
        self._mtime = 0.0

    def reload(self, ports: dict[str, int] | None = None) -> None:
        """Routes and container ports; certificates re-read (renewals)."""
        routes = load_routes()
        if ports is None:
            try:
                ports = {b.name: b.port for b in multi.list_boxes()}
            except multi.MultiError:
                ports = {}
        table, contexts = {}, {}
        for box, host in routes.items():
            cert, key = acme.cert_paths(host)
            if box not in ports or not cert.is_file():
                continue
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.set_alpn_protocols(["http/1.1"])
            ctx.load_cert_chain(str(cert), str(key))
            table[host], contexts[host] = ports[box], ctx
        self.table, self.contexts = table, contexts
        if contexts:
            # OpenSSL wants a certificate before SNI picks the right one.
            first = next(iter(routes.values()))
            if first in contexts:
                cert, key = acme.cert_paths(first)
                self.base.load_cert_chain(str(cert), str(key))

    def _sni(self, sock, server_name, _ctx):
        ctx = self.contexts.get((server_name or "").lower())
        if ctx is None:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        sock.context = ctx
        sock.rc_host = server_name.lower()
        return None

    def redirect_target(self, request: bytes, public_port: int) -> bytes | None:
        """What port 80 answers: a redirect to the same page on HTTPS, for our names."""
        try:
            head = request.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            line, *headers = head.split("\r\n")
            path = line.split(" ")[1]
        except (IndexError, UnicodeDecodeError):
            return None
        host = next((h.split(":", 1)[1].strip() for h in headers if h.lower().startswith("host:")), "")
        host = host.split(":", 1)[0].lower()
        if host not in self.table or not path.startswith("/"):
            return None
        where = f"https://{host}" + ("" if public_port == 443 else f":{public_port}") + path
        return (
            "HTTP/1.1 301 Moved Permanently\r\n"
            f"Location: {where}\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n"
        ).encode("latin-1")

    def serve_http(self, port: int = 80, public_port: int = 443, listen_host: str = "0.0.0.0") -> None:
        """Port 80, when this user may open it: everything goes to HTTPS."""
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((listen_host, port))
        except OSError:
            print(f"router: sin puerto {port} (solo HTTPS)", file=sys.stderr, flush=True)
            return
        sock.listen(64)

        def answer(conn: socket.socket) -> None:
            try:
                conn.settimeout(10)
                reply = self.redirect_target(conn.recv(8192), public_port)
                conn.sendall(reply or b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                conn.close()

        while True:
            try:
                conn, _addr = sock.accept()
            except OSError:
                time.sleep(0.2)
                continue
            threading.Thread(target=answer, args=(conn,), daemon=True).start()

    def serve(self, listen_host: str = "0.0.0.0") -> None:
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((listen_host, self.port))
        sock.listen(128)
        while True:
            try:
                conn, _addr = sock.accept()
            except OSError:
                time.sleep(0.2)
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(15)
            tls = self.base.wrap_socket(conn, server_side=True)
            tls.settimeout(None)
        except (OSError, ssl.SSLError):
            conn.close()
            return
        port = self.table.get(getattr(tls, "rc_host", ""))
        if port is None:
            tls.close()
            return
        try:
            up = socket.create_connection(("127.0.0.1", port), timeout=5)
            up.settimeout(None)
        except OSError:
            tls.close()
            return
        _pipe(tls, up)


def _keep(router: Router) -> None:
    """Routes and ports every few seconds; the IP and the certificates now and then."""
    last_ip = domains.public_ipv4() or ""
    last_ip_check = last_renew = time.time()
    while True:
        time.sleep(RELOAD_S)
        try:
            router.reload()
            now = time.time()
            if now - last_ip_check < IP_CHECK_S and now - last_renew < RENEW_CHECK_S:
                continue
            cfg, prov = _account()
            routes = load_routes()
            if now - last_ip_check >= IP_CHECK_S:
                last_ip_check = now
                ip = domains.public_ipv4()
                if ip and ip != last_ip:
                    print(f"router: IP {last_ip} → {ip}", file=sys.stderr)
                    for host in routes.values():
                        zone, name = domains.split_host(host, [cfg.zone])
                        prov.point(zone, name, ip)
                    last_ip = ip
            if now - last_renew >= RENEW_CHECK_S:
                last_renew = now
                for host in routes.values():
                    zone, name = domains.split_host(host, [cfg.zone])
                    acme.ensure_cert(
                        host,
                        zone,
                        lambda value, z=zone, n=name: prov.set_record(z, domains.challenge_name(n), "TXT", value, ttl=300),
                        lambda z=zone, n=name: prov.delete_record(z, domains.challenge_name(n), "TXT"),
                        email=cfg.email,
                    )
        except Exception as exc:
            print(f"router: {exc}", file=sys.stderr)


def main(_argv: list[str] | None = None) -> int:
    router = Router(domains.local_https_port())
    router.reload()
    print(f"router: :{router.port} → {router.table}", file=sys.stderr, flush=True)
    threading.Thread(target=_keep, args=(router,), daemon=True).start()
    # http://name (typed without https://) lands on HTTPS. 443 only: with the
    # fallback port the router of the house forwards 443, not 80.
    if router.port == 443:
        threading.Thread(target=router.serve_http, daemon=True).start()
    router.serve()
    return 0
