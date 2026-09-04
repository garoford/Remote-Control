"""HTTP/WebSocket sidecar in front of ttyd.

Serves cacheable fonts + JS, /rc-scrollback, /rc-history, /rc-scroll,
reserved clipboard uploads, and proxies everything else (including the
tty WebSocket) to ttyd on the internal port.
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

from remote_control.history import (
    cancel_copy_mode,
    history_payload,
    scroll_history,
    scrollback_payload,
)
from remote_control.paste import PasteError, paste_dir, reserve_paste_file, write_paste_file
from remote_control.mobile import (
    load_manifest,
    manifest_for_client,
    pick_mobile_font_url,
    request_is_mobile,
    rewrite_index_for_mobile,
)

ASSET_CACHE = "public, max-age=31536000, immutable"
SCRIPT_CACHE = "public, max-age=300"
MAX_HEADER = 1024 * 1024
_API_EXTRA = [
    ("Cache-Control", "no-store"),
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, X-RC-Paste-Name"),
]


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


def _rewrite_connection_close(header: bytes) -> bytes:
    lines = header.split(b"\r\n")
    if not lines:
        return header
    out = [lines[0]]
    for line in lines[1:]:
        if not line:
            continue
        if line.lower().startswith(b"connection:"):
            continue
        out.append(line)
    out.append(b"Connection: close")
    return b"\r\n".join(out)


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
    te = (headers.get("transfer-encoding") or "").lower()
    if "chunked" in te:
        body = _read_chunked(sock, rest)
        return header, body or b""
    need = int(headers.get("content-length") or 0)
    body = rest
    while len(body) < need:
        chunk = sock.recv(min(65536, need - len(body)))
        if not chunk:
            break
        body += chunk
    return header, body[:need] if need else body


def _read_chunked(sock: socket.socket, initial: bytes) -> bytes | None:
    buf = initial
    out = b""
    while True:
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return out or None
            buf += chunk
        line, _, buf = buf.partition(b"\r\n")
        try:
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        except ValueError:
            return None
        if size == 0:
            return out
        while len(buf) < size + 2:
            chunk = sock.recv(min(65536, size + 2 - len(buf)))
            if not chunk:
                return None
            buf += chunk
        out += buf[:size]
        buf = buf[size:]
        if buf.startswith(b"\r\n"):
            buf = buf[2:]


def _read_http_response(
    sock: socket.socket,
) -> tuple[str, dict[str, str], bytes] | None:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        data += chunk
        if len(data) > MAX_HEADER:
            return None
    header, _, rest = data.partition(b"\r\n\r\n")
    first = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
    parts = first.split(" ", 2)
    if len(parts) < 2:
        status = "502 Bad Gateway"
    else:
        status = parts[1] + (" " + parts[2] if len(parts) > 2 else "")
    headers = _header_map(header)
    te = (headers.get("transfer-encoding") or "").lower()
    if "chunked" in te:
        body = _read_chunked(sock, rest)
        if body is None:
            return None
        return status, headers, body
    if "content-length" in headers:
        need = int(headers["content-length"])
        body = rest
        while len(body) < need:
            chunk = sock.recv(min(65536, need - len(body)))
            if not chunk:
                break
            body += chunk
        return status, headers, body[:need]
    body = rest
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        body += chunk
    return status, headers, body


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
        paste_home: Path | None = None,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.assets = assets
        self.tmux_socket = tmux_socket
        self.paste_home = paste_home
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        try:
            paste_dir(self.paste_home).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

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
            if path != "/" and path.endswith("/"):
                path = path.rstrip("/")
            headers = _header_map(header)
            if method == "OPTIONS" and path.startswith("/rc-"):
                self._serve_options(conn)
                return
            if method in {"GET", "HEAD"} and path.startswith("/rc-assets/"):
                self._serve_asset(
                    conn,
                    path[len("/rc-assets/") :],
                    method == "HEAD",
                    headers,
                )
                return
            if method == "GET" and path.rstrip("/").endswith("/rc-scrollback"):
                self._serve_scrollback(conn, parse_qs(parsed_url.query))
                return
            if method == "GET" and path.rstrip("/").endswith("/rc-history"):
                self._serve_history(conn, parse_qs(parsed_url.query))
                return
            if method == "POST" and path == "/rc-copy-cancel":
                self._serve_copy_cancel(conn, parse_qs(parsed_url.query))
                return
            if method == "POST" and path == "/rc-scroll":
                self._serve_scroll(conn, parse_qs(parsed_url.query))
                return
            if method == "POST" and path == "/rc-paste-reserve":
                self._serve_paste_reserve(conn, body)
                return
            if method == "PUT" and path == "/rc-paste-file":
                name = (parse_qs(parsed_url.query).get("name") or [""])[0]
                if not name:
                    name = headers.get("x-rc-paste-name", "")
                self._serve_paste_write(conn, body, name)
                return
            if path.startswith("/rc-") and not path.startswith("/rc-assets/"):
                conn.sendall(
                    _http_response(
                        "404 Not Found",
                        b'{"error":"not found"}',
                        "application/json; charset=utf-8",
                        _API_EXTRA,
                    )
                )
                return
            self._proxy(conn, header, body, headers)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve_asset(
        self,
        conn: socket.socket,
        name: str,
        head_only: bool,
        req_headers: dict[str, str] | None = None,
    ) -> None:
        path = _safe_asset(self.assets, name)
        if path is None:
            conn.sendall(_http_response("404 Not Found", b"not found", "text/plain"))
            return
        extra = [("Access-Control-Allow-Origin", "*")]
        if path.name == "manifest.json":
            extra.append(("Cache-Control", "no-store"))
            extra.append(("Vary", "User-Agent, Sec-CH-UA-Mobile"))
        else:
            extra.append(
                ("Cache-Control", ASSET_CACHE if path.suffix == ".woff2" else SCRIPT_CACHE)
            )
        if path.name == "sw.js":
            extra.append(("Service-Worker-Allowed", "/"))
        if path.name == "manifest.json":
            try:
                raw_man = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw_man = {}
            if not isinstance(raw_man, dict):
                raw_man = {}
            payload = manifest_for_client(
                raw_man,
                request_is_mobile(req_headers or {}),
                self.assets,
            )
            data = json.dumps(payload).encode("utf-8")
            ctype = "application/json; charset=utf-8"
            if head_only:
                data = b""
            conn.sendall(_http_response("200 OK", data if not head_only else b"", ctype, extra))
            return
        data = b"" if head_only else path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".woff2":
            ctype = "font/woff2"
        elif path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
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

    def _serve_options(self, conn: socket.socket) -> None:
        conn.sendall(
            _http_response(
                "204 No Content",
                b"",
                "text/plain",
                _API_EXTRA + [("Access-Control-Max-Age", "86400")],
            )
        )

    def _serve_scrollback(self, conn: socket.socket, query: dict[str, list[str]]) -> None:
        tab = (query.get("tab") or [""])[0]
        raw_since = (query.get("since") or [""])[0]
        raw_w = (query.get("w") or [""])[0]
        since: int | None = None
        width: int | None = None
        if raw_since != "":
            try:
                since = int(raw_since)
            except ValueError:
                since = None
        if raw_w != "":
            try:
                width = int(raw_w)
            except ValueError:
                width = None
        payload = scrollback_payload(tab, since, width, socket=self.tmux_socket)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.sendall(
            _http_response(
                "200 OK",
                body,
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

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
            body = b'{"mode":"none","lines":[],"all":[],"count":0}'
            conn.sendall(
                _http_response(
                    "200 OK",
                    body,
                    "application/json; charset=utf-8",
                    _API_EXTRA,
                )
            )
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.sendall(
            _http_response(
                "200 OK",
                body,
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

    def _serve_copy_cancel(self, conn: socket.socket, query: dict[str, list[str]]) -> None:
        tab = (query.get("tab") or [""])[0]
        cancelled = cancel_copy_mode(tab, socket=self.tmux_socket)
        body = json.dumps({"ok": True, "cancelled": cancelled}).encode("utf-8")
        conn.sendall(
            _http_response(
                "200 OK",
                body,
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

    def _serve_scroll(self, conn: socket.socket, query: dict[str, list[str]]) -> None:
        tab = (query.get("tab") or [""])[0]
        raw = (query.get("lines") or ["0"])[0]
        try:
            lines = int(raw)
        except ValueError:
            lines = 0
        payload = scroll_history(tab, lines, socket=self.tmux_socket)
        if payload is None:
            body = b'{"ok":false,"moved":0}'
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        conn.sendall(
            _http_response(
                "200 OK",
                body,
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

    def _paste_error(self, conn: socket.socket, exc: PasteError) -> None:
        reason = str(exc)
        status = "400 Bad Request"
        if reason == "too large":
            status = "413 Payload Too Large"
        elif reason == "not reserved":
            status = "404 Not Found"
        elif reason == "exists":
            status = "409 Conflict"
        conn.sendall(
            _http_response(
                status,
                json.dumps({"error": reason}).encode("utf-8"),
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

    def _paste_ok(self, conn: socket.socket, path: Path) -> None:
        conn.sendall(
            _http_response(
                "200 OK",
                json.dumps({"path": str(path), "name": path.name}).encode("utf-8"),
                "application/json; charset=utf-8",
                _API_EXTRA,
            )
        )

    def _serve_paste_reserve(self, conn: socket.socket, body: bytes) -> None:
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        name = str(payload.get("name") or "")
        try:
            path = reserve_paste_file(name, home=self.paste_home)
        except PasteError as exc:
            self._paste_error(conn, exc)
            return
        except OSError:
            conn.sendall(
                _http_response(
                    "500 Internal Server Error",
                    b'{"error":"write failed"}',
                    "application/json; charset=utf-8",
                    _API_EXTRA,
                )
            )
            return
        self._paste_ok(conn, path)

    def _serve_paste_write(self, conn: socket.socket, body: bytes, name: str) -> None:
        try:
            path = write_paste_file(name, body, home=self.paste_home)
        except PasteError as exc:
            self._paste_error(conn, exc)
            return
        except OSError:
            conn.sendall(
                _http_response(
                    "500 Internal Server Error",
                    b'{"error":"write failed"}',
                    "application/json; charset=utf-8",
                    _API_EXTRA,
                )
            )
            return
        self._paste_ok(conn, path)

    def _proxy(
        self,
        conn: socket.socket,
        header: bytes,
        body: bytes,
        req_headers: dict[str, str] | None = None,
    ) -> None:
        first = header.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = first.split(" ")
        method = parts[0] if parts else ""
        raw_target = parts[1] if len(parts) > 1 else "/"
        parsed_req = urlparse(raw_target)
        path = unquote(parsed_req.path)
        headers = req_headers or _header_map(header)
        upgrade = "upgrade" in (headers.get("connection") or "").lower()
        if (
            not upgrade
            and method in {"GET", "HEAD"}
            and path in {"/", "/index.html"}
        ):
            self._proxy_html(conn, header, body, headers, parse_qs(parsed_req.query))
            return
        if not upgrade:
            header = _rewrite_connection_close(header)
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

    def _mobile_font_url(self) -> str:
        return pick_mobile_font_url(load_manifest(self.assets), self.assets)

    def _inject_boot_history(self, html: str, query: dict[str, list[str]]) -> str:
        tab = (query.get("arg") or [""])[0]
        payload = {
            "size": 0,
            "w": 0,
            "h": 0,
            "alt": 0,
            "mode": "none",
            "lines": [],
        }
        if tab:
            payload = scrollback_payload(tab, None, None, socket=self.tmux_socket)
        script = (
            '<script id="rc-boot-scrollback">window.__rcBootScrollback='
            + json.dumps(payload, ensure_ascii=False)
            + ";if(typeof window.applyScrollback===\"function\")"
            + "window.applyScrollback(window.__rcBootScrollback);</script>"
        )
        if "</head>" in html:
            return html.replace("</head>", script + "</head>", 1)
        return script + html

    def _proxy_html(
        self,
        conn: socket.socket,
        header: bytes,
        body: bytes,
        req_headers: dict[str, str],
        query: dict[str, list[str]] | None = None,
    ) -> None:
        header = _rewrite_connection_close(header)
        upstream = socket.create_connection(
            (self.upstream_host, self.upstream_port), timeout=10
        )
        try:
            upstream.sendall(header + b"\r\n\r\n" + body)
            raw = _read_http_response(upstream)
            if raw is None:
                return
            status, resp_headers, resp_body = raw
            ctype = resp_headers.get("content-type") or ""
            is_html = "html" in ctype.lower() or not ctype
            if is_html:
                text = resp_body.decode("utf-8", "replace")
                if request_is_mobile(req_headers):
                    text = rewrite_index_for_mobile(text, self._mobile_font_url())
                text = self._inject_boot_history(text, query or {})
                resp_body = text.encode("utf-8")
            skip = {
                "content-length",
                "transfer-encoding",
                "connection",
                "content-encoding",
                "content-type",
                "cache-control",
            }
            extra = [
                (key.title() if key != "vary" else "Vary", value)
                for key, value in resp_headers.items()
                if key not in skip
            ]
            extra.append(("Cache-Control", "no-store"))
            if request_is_mobile(req_headers):
                extra.append(("Vary", "User-Agent, Sec-CH-UA-Mobile"))
            out_type = "text/html; charset=utf-8" if is_html else ctype
            conn.sendall(
                _http_response(
                    status,
                    resp_body,
                    out_type,
                    extra,
                )
            )
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
