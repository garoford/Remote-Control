"""HTTPS on the public port in the direct mode (domains.py), in the proxy process.

Plays cloudflared's part: takes TLS from the internet and hands the plain
bytes to the proxy's own local port, so every route, the password gate and
the WebSockets work exactly as through the tunnel. The gate does not trust
loopback connections, so going through 127.0.0.1 opens nothing.

While it runs it also keeps the name pointing here (a home connection's IP
may change) and renews the certificate before it expires.
"""

from __future__ import annotations

import socket
import ssl
import sys
import threading
import time
from pathlib import Path

IP_CHECK_S = 300
RENEW_CHECK_S = 12 * 3600


def _pipe(left: socket.socket, right: socket.socket) -> None:
    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except (OSError, ValueError):
            pass
        # TLS has no half-close: one side done ends the connection. shutdown()
        # first: close() alone does not wake the other pump blocked in recv.
        # (The plain socket's shutdown: SSLSocket's would drop its TLS state
        # under that pump's feet.)
        for sock in (left, right):
            try:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    other = threading.Thread(target=pump, args=(right, left), daemon=True)
    other.start()
    pump(left, right)
    other.join()


class TlsFront:
    def __init__(self, listen: tuple[str, int], upstream: tuple[str, int], cert: Path, key: Path) -> None:
        self.listen = listen
        self.upstream = upstream
        self.cert = cert
        self.key = key
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self.ctx.set_alpn_protocols(["http/1.1"])
        self.reload()

    def reload(self) -> None:
        # New connections use the new certificate; open ones keep theirs.
        self.ctx.load_cert_chain(str(self.cert), str(self.key))

    def start(self) -> None:
        family = socket.AF_INET6 if ":" in self.listen[0] else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(self.listen)
        sock.listen(128)
        threading.Thread(target=self._accept, args=(sock,), name="rc-tls", daemon=True).start()

    def _accept(self, sock: socket.socket) -> None:
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
            tls = self.ctx.wrap_socket(conn, server_side=True)
            tls.settimeout(None)
        except (OSError, ssl.SSLError):
            conn.close()
            return
        try:
            up = socket.create_connection(self.upstream, timeout=5)
            up.settimeout(None)
        except OSError:
            tls.close()
            return
        _pipe(tls, up)


def keep_domain(front: TlsFront | None) -> None:
    """Follow this machine's public IP and renew the certificate (thread)."""
    from remote_control import domains

    last_ip = ""
    last_renew = time.time()
    while True:
        time.sleep(IP_CHECK_S)
        cfg = domains.load_config()
        if cfg is None:
            continue
        try:
            ip = domains.public_ipv4()
            if ip and ip != last_ip:
                if last_ip:
                    print(f"domain: IP {last_ip} → {ip}", file=sys.stderr)
                    prov = domains.provider_for(cfg)
                    zone, name = domains.locate(cfg, prov)
                    prov.point(zone, name, ip)
                last_ip = ip
            if front is not None and time.time() - last_renew > RENEW_CHECK_S:
                last_renew = time.time()
                domains.serve_direct(cfg, ip, log=lambda msg: print(f"domain: {msg}", file=sys.stderr))
                front.reload()
        except Exception as exc:
            print(f"domain: {exc}", file=sys.stderr)
