"""HTTP/WebSocket sidecar in front of ttyd.

Serves cacheable fonts + JS, /rc-history, and proxies everything else
(including the tty WebSocket) to ttyd on the internal port.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from remote_control.history import history_payload

ASSET_CACHE = "public, max-age=31536000, immutable"
SCRIPT_CACHE = "public, max-age=300"
MAX_HEADER = 1024 * 1024


def _safe_asset(assets: Path, name: str) -> Path | None:
    if not name or ".." in name or name.startswith("/") or "\\" in name:
        return None
    try:
        root = assets.resolve()
        path = (root / name).resolve()
    except OSError:
        return None
    if path != root and root not in path.parents:
        return None
    if path.is_file():
        return path
    return None


def _header_map(header_block: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in header_block.split(b"\r\n")[1:]:
        if b":" not in raw:
            continue
        key, value = raw.split(b":", 1)
        out[key.decode("latin1").lower()] = value.decode("latin1").strip()
    return out


def _read_request(sock: socket.socket) -> tuple[bytes, bytes] | None:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > MAX_HEADER:
            return None
    header, _, rest = data.partition(b"\r\n\r\n")
    headers = _header_map(header)
    need = int(headers.get("content-length") or 0)
    body = rest
    while len(body) < need:
        chunk = sock.recv(min(65536, need - len(body)))
        if not chunk:
            break
        body += chunk
    return header, body[:need] if need else body


def _http_response(
    status: str,
    body: bytes,
    content_type: str,
    extra: list[tuple[str, str]] | None = None,
) -> bytes:
    headers = [
        f"HTTP/1.1 {status}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    for key, value in extra or []:
        headers.append(f"{key}: {value}")
    headers.append("")
    headers.append("")
    return ("\r\n".join(headers)).encode("latin1") + body


def _splice(left: socket.socket, right: socket.socket) -> None:
    def pump(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    first = threading.Thread(target=pump, args=(left, right), daemon=True)
    second = threading.Thread(target=pump, args=(right, left), daemon=True)
    first.start()
    second.start()
    first.join()
    second.join()


class Sidecar:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        assets: Path,
        tmux_socket: str = "cf-remote",
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.assets = assets
        self.tmux_socket = tmux_socket
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.listen_host, self.listen_port))
        sock.listen(64)
        sock.settimeout(0.5)
        self._sock = sock
        try:
            while not self._stop.is_set():
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                thread = threading.Thread(
                    target=self._handle, args=(conn,), daemon=True
                )
                thread.start()
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            parsed = _read_request(conn)
            if parsed is None:
                return
            header, body = parsed
            first = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
            parts = first.split(" ")
            if len(parts) < 2:
                conn.sendall(_http_response("400 Bad Request", b"bad request", "text/plain"))
                return
            method, raw_target = parts[0], parts[1]
            parsed_url = urlparse(raw_target)
            path = unquote(parsed_url.path)
            if method in {"GET", "HEAD"} and path.startswith("/rc-assets/"):
                self._serve_asset(conn, path[len("/rc-assets/") :], method == "HEAD")
                return
            if method == "GET" and path == "/rc-history":
                self._serve_history(conn, parse_qs(parsed_url.query))
                return
            self._proxy(conn, header, body)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve_asset(self, conn: socket.socket, name: str, head_only: bool) -> None:
        path = _safe_asset(self.assets, name)
        if path is None:
            conn.sendall(_http_response("404 Not Found", b"not found", "text/plain"))
            return
        data = b"" if head_only else path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".woff2":
            ctype = "font/woff2"
        elif path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        extra = [("Cache-Control", ASSET_CACHE if path.suffix == ".woff2" else SCRIPT_CACHE)]
        if path.name == "sw.js":
            extra.append(("Service-Worker-Allowed", "/"))
        if head_only:
            lines = [
                "HTTP/1.1 200 OK",
                f"Content-Type: {ctype}",
                f"Content-Length: {path.stat().st_size}",
                "Connection: close",
            ]
            for key, value in extra:
                lines.append(f"{key}: {value}")
            lines.extend(["", ""])
            conn.sendall("\r\n".join(lines).encode("latin1"))
            return
        conn.sendall(_http_response("200 OK", data, ctype, extra))

    def _serve_history(self, conn: socket.socket, query: dict[str, list[str]]) -> None:
        tab = (query.get("tab") or [""])[0]
        raw_fp = (query.get("fp") or [""])[0]
        fingerprint: list[str] = []
        if raw_fp:
            try:
                parsed = json.loads(raw_fp)
                if isinstance(parsed, list):
                    fingerprint = [str(item) for item in parsed][:8]
            except json.JSONDecodeError:
                fingerprint = [raw_fp]
        payload = history_payload(tab, fingerprint, socket=self.tmux_socket)
        if payload is None:
            conn.sendall(
                _http_response("404 Not Found", b'{"error":"no session"}', "application/json")
            )
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.sendall(
            _http_response(
                "200 OK",
                body,
                "application/json; charset=utf-8",
                [("Cache-Control", "no-store")],
            )
        )

    def _proxy(self, conn: socket.socket, header: bytes, body: bytes) -> None:
        upstream = socket.create_connection(
            (self.upstream_host, self.upstream_port), timeout=10
        )
        try:
            upstream.sendall(header + b"\r\n\r\n" + body)
            _splice(conn, upstream)
        finally:
            try:
                upstream.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remote Control ttyd sidecar")
    parser.add_argument("--listen", default="127.0.0.1:7681")
    parser.add_argument("--upstream", default="127.0.0.1:7682")
    parser.add_argument("--assets", required=True)
    parser.add_argument("--tmux-socket", default="cf-remote")
    args = parser.parse_args(argv)

    def split_addr(value: str) -> tuple[str, int]:
        host, port = value.rsplit(":", 1)
        return host, int(port)

    listen_host, listen_port = split_addr(args.listen)
    up_host, up_port = split_addr(args.upstream)
    sidecar = Sidecar(
        listen_host,
        listen_port,
        up_host,
        up_port,
        Path(args.assets),
        tmux_socket=args.tmux_socket,
    )
    try:
        sidecar.serve_forever()
    except KeyboardInterrupt:
        sidecar.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
