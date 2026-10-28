"""Local environments: the dev servers running on this machine, and the ones
published (proxy.py serves them).

Everything listening on TCP is read from `ss`, whatever the address (127.0.0.1,
::1, 127.x.x.x, 0.0.0.0, a LAN or container network…). Each port gets a quick
HTTP request: what answers as a web server is a local environment, with its
page title and a guess of its framework; the rest (databases, SSH…) is only
shown on request. Ports of Remote Control itself are left out.

A published one gets its own address, so it runs at the root and on an origin
of its own (not Remote Control's), the way it does on localhost. Picked here:

- "sub": <slug>.<your domain>, when the domain has its *.name (DNS and a
  wildcard certificate: tunnel.py in direct mode, router.py for containers).
- "tunnel": a quick tunnel of its own (https://….trycloudflare.com).
- "path": /p/<slug>/ on this tunnel, the fallback (cloudflared missing or not
  answering); see the routing notes further down.

A "sub" one can also get a quick tunnel next to its subdomain (cf_*), just in
case: another way in to the same environment, with the same key.

Each has a key: its link carries it (?rc-key=…) and opens it without the
password, for that environment only.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

from .gate import preference_path

PROBE_TIMEOUT = 0.8
SCAN_TTL = 2.0
TUNNEL_TIMEOUT = 45
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
TRYCLOUDFLARE_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
# Ours, or plumbing that is never a web app of the user's.
OWN_PROCESSES = {"ttyd", "cloudflared", "tmux", "herdr"}
OWN_MARKERS = ("remote_control", "remote-control")
# Runtimes a dev server runs on: listed first, as "desarrollo".
DEV_PROCESSES = re.compile(
    r"^(node|nodejs|bun|deno|npm|npx|pnpm|yarn|vite|next-server|python\d*(\.\d+)?|uvicorn|gunicorn|flask|php(-fpm)?\d*|ruby|rails|puma|"
    r"java|dotnet|go|air|cargo|hugo|jekyll|caddy|esbuild|webpack|astro|nuxt|expo|ng|vue-cli-service|docker-proxy|conmon|rootlessport)$"
)
CONTAINER_PROCESSES = ("docker-proxy", "conmon", "rootlessport", "pasta", "slirp4netns")
KEY_PARAM = "rc-key"
KEY_COOKIE = "rc_dev_key"


def shares_path(home: Path | None = None) -> Path:
    return preference_path(home).with_name("local-shares.json")


def subdomains_path(home: Path | None = None) -> Path:
    return preference_path(home).with_name("subdomains")


def tunnels_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".cache" / "remote-control" / "local-tunnels"


def subdomain_base(home: Path | None = None) -> str:
    """"host" or "host:port" whose *.host is ready, or ""."""
    try:
        return subdomains_path(home).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_subdomain_base(base: str | None, home: Path | None = None) -> None:
    path = subdomains_path(home)
    if base:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(base + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


# --- what is published --------------------------------------------------------------

_shares_cache: tuple[float, dict] = (-1.0, {})
_lock = threading.Lock()
FIELDS = {"addr": "127.0.0.1", "label": "", "kind": "path", "host": "", "url": "", "key": "", "pid": 0, "note": "", "cf_host": "", "cf_url": "", "cf_pid": 0}


def load_shares(home: Path | None = None) -> dict[str, dict]:
    """{slug: {port, addr, label, kind, host, url, key, pid, note}}, re-read when it changes."""
    global _shares_cache
    path = shares_path(home)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    if home is None and _shares_cache[0] == mtime:
        return _shares_cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    shares = {}
    for slug, item in (raw.items() if isinstance(raw, dict) else []):
        if SLUG_RE.match(str(slug)) and isinstance(item, dict) and isinstance(item.get("port"), int):
            shares[str(slug)] = {"port": item["port"], **{k: type(d)(item.get(k) or d) for k, d in FIELDS.items()}}
    if home is None:
        _shares_cache = (mtime, shares)
    return shares


def save_shares(shares: dict[str, dict], home: Path | None = None) -> None:
    path = shares_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    # The keys open the environments: only this user reads them.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(shares, indent=1))
    tmp.replace(path)


def _stop_tunnel(share_: dict) -> None:
    for pid in (share_.get("pid") or 0, share_.get("cf_pid") or 0):
        if pid and "cloudflared" in _cmdline(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass


def clear_shares(home: Path | None = None) -> None:
    """With the tunnel off nothing stays published."""
    for share_ in load_shares(home).values():
        _stop_tunnel(share_)
    shares_path(home).unlink(missing_ok=True)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32].strip("-")
    return slug or "local"


def start_tunnel(slug: str, proxy_port: int, home: Path | None = None) -> tuple[str, int]:
    """A quick tunnel of its own to this proxy; (its URL, cloudflared's pid).
    localhost (not 127.0.0.1) keeps it out of tunnel.py's pkill pattern."""
    exe = shutil.which("cloudflared")
    if not exe:
        raise OSError("sin cloudflared")
    folder = tunnels_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / f"{slug}.log"
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [exe, "tunnel", "--no-autoupdate", "--url", f"http://localhost:{proxy_port}"],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    deadline = time.time() + TUNNEL_TIMEOUT
    url = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        text = log.read_text(encoding="utf-8", errors="replace")
        url = url or next(iter(TRYCLOUDFLARE_RE.findall(text)), "")
        if url and "Registered tunnel connection" in text:
            return url + "/", proc.pid
        time.sleep(0.4)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    raise OSError("cloudflared no dio una URL a tiempo")


def share(
    port: int,
    on: bool,
    label: str = "",
    addr: str = "127.0.0.1",
    home: Path | None = None,
    proxy_port: int = 0,
) -> dict[str, dict]:
    with _lock:
        shares = load_shares(home)
        for slug in [s for s, v in shares.items() if v["port"] == port]:
            _stop_tunnel(shares.pop(slug))
        if on:
            slug = base = slugify(label or f"puerto-{port}")
            if slug in shares:
                slug = f"{base}-{port}"
            item = {"port": port, **FIELDS, "addr": addr, "label": label, "key": secrets.token_urlsafe(18)}
            sub = subdomain_base(home)
            if sub:
                item.update(kind="sub", host=f"{slug}.{sub.split(':', 1)[0]}", url=f"https://{slug}.{sub}/")
            elif proxy_port:
                try:
                    url, pid = start_tunnel(slug, proxy_port, home)
                    item.update(kind="tunnel", host=url.split("//", 1)[1].rstrip("/"), url=url, pid=pid)
                except OSError as exc:
                    item["note"] = f"Sin túnel propio ({exc}): va por /p/{slug}/"
            shares[slug] = item
        save_shares(shares, home)
        return shares


def share_tunnel(port: int, on: bool, label: str = "", addr: str = "127.0.0.1", home: Path | None = None, proxy_port: int = 0) -> str:
    """The extra quick tunnel of a "sub" one, on or off; "" when done, else why
    not. Switched on before it is published, it is published first."""
    if on and not any(v["port"] == port for v in load_shares(home).values()):
        share(port, True, label, addr, home, proxy_port)
    with _lock:
        shares = load_shares(home)
        slug = next((s for s, v in shares.items() if v["port"] == port), "")
        if not slug:
            return ""
        item = dict(shares[slug])
        if on and item["kind"] != "sub":
            return "Ya va por un túnel propio" if item["kind"] == "tunnel" else ""
        _stop_tunnel({"cf_pid": item["cf_pid"]})
        item.update(cf_host="", cf_url="", cf_pid=0)
        error = ""
        if on:
            try:
                url, pid = start_tunnel(slug, proxy_port, home)
                item.update(cf_host=url.split("//", 1)[1].rstrip("/"), cf_url=url, cf_pid=pid)
            except OSError as exc:
                error = f"Sin túnel de Cloudflare: {exc}"
        shares[slug] = item
        save_shares(shares, home)
        return error


def share_for_host(host: str) -> tuple[str, dict] | None:
    """The published environment that owns this host name (subdomain or its own tunnel)."""
    host = host.lower()
    if not host:
        return None
    for slug, item in load_shares().items():
        if host in (item["host"], item["cf_host"]) and host:
            return slug, item
    return None


def link(slug: str, item: dict) -> str:
    """What the panel copies: the address with its key ("path": no key, the password)."""
    if item["kind"] == "path" or not item["url"]:
        return f"/p/{slug}/"
    return f"{item['url']}?{KEY_PARAM}={item['key']}"


# --- what is listening ------------------------------------------------------------

def _split_addr(local: str) -> tuple[str, int] | None:
    host, _, port = local.rpartition(":")
    if not port.isdigit():
        return None
    host = host.split("%", 1)[0].strip("[]")
    return ("0.0.0.0" if host == "*" else host), int(port)


def listening() -> list[dict]:
    """[{port, addr, process, pid}] for every listening TCP socket."""
    ss = shutil.which("ss")
    if not ss:
        return []
    try:
        out = subprocess.run([ss, "-ltnpH"], capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        local = _split_addr(cols[3])
        if not local:
            continue
        users = " ".join(cols[5:])
        proc = re.search(r'\(\("([^"]*)",pid=(\d+)', users)
        found.append({"addr": local[0], "port": local[1], "process": proc.group(1) if proc else "", "pid": int(proc.group(2)) if proc else 0})
    return found


def addr_kind(addr: str) -> str:
    if addr in ("127.0.0.1", "::1"):
        return "localhost"
    if addr.startswith("127."):
        return "loopback"
    if addr in ("0.0.0.0", "::"):
        return "todas"
    if addr.startswith("::ffff:127."):
        return "loopback"
    return "red"


def _connect_host(addr: str) -> str:
    if addr in ("0.0.0.0", "*"):
        return "127.0.0.1"
    if addr == "::":
        return "::1"
    return addr


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _containers() -> dict[int, str]:
    """Published host port → container name (Docker and Podman)."""
    ports: dict[int, str] = {}
    for tool in ("docker", "podman"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "ps", "--format", "{{.Names}}\t{{.Ports}}"], capture_output=True, text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            name, _, spec = line.partition("\t")
            for match in re.finditer(r":(\d+)->", spec):
                ports.setdefault(int(match.group(1)), name)
    return ports


# --- what answers -------------------------------------------------------------------

FRAMEWORKS = (
    ("/@vite/client", "Vite"),
    ("__NEXT_DATA__", "Next.js"),
    ("/_next/", "Next.js"),
    ("/_nuxt/", "Nuxt"),
    ("__nuxt", "Nuxt"),
    ("__sveltekit", "SvelteKit"),
    ("/_app/immutable", "SvelteKit"),
    ("data-astro", "Astro"),
    ("astro-island", "Astro"),
    ("__remixContext", "Remix"),
    ("ng-version", "Angular"),
    ("<app-root", "Angular"),
    ("/sockjs-node", "Webpack"),
    ("webpack-dev-server", "Webpack"),
    ("static/js/bundle.js", "Create React App"),
    ("/livereload.js", "LiveReload"),
    ("csrfmiddlewaretoken", "Django"),
)
SERVERS = (
    ("werkzeug", "Flask"),
    ("wsgiserver", "Django"),
    ("uvicorn", "FastAPI/Uvicorn"),
    ("hypercorn", "Hypercorn"),
    ("gunicorn", "Gunicorn"),
    ("simplehttp", "Python http.server"),
    ("php", "PHP"),
    ("deno", "Deno"),
    ("bun", "Bun"),
    ("kestrel", ".NET"),
    ("jetty", "Java/Jetty"),
    ("caddy", "Caddy"),
    ("nginx", "nginx"),
    ("apache", "Apache"),
    ("traefik", "Traefik"),
)


def _guess(headers: dict[str, str], body: str) -> str:
    powered = headers.get("x-powered-by", "")
    if "next.js" in powered.lower():
        return "Next.js"
    for marker, name in FRAMEWORKS:
        if marker in body:
            return name
    if "laravel_session" in headers.get("set-cookie", "").lower():
        return "Laravel"
    if "express" in powered.lower():
        return "Express"
    server = (headers.get("server", "") + " " + powered).lower()
    for marker, name in SERVERS:
        if marker in server:
            return name
    return ""


def probe(addr: str, port: int) -> dict | None:
    """One GET /: {status, title, framework, server} when it speaks HTTP."""
    host = _connect_host(addr)
    deadline = time.monotonic() + PROBE_TIMEOUT
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT) as sock:
            sock.sendall(
                f"GET / HTTP/1.1\r\nHost: localhost:{port}\r\nAccept: text/html,*/*\r\n"
                "User-Agent: remote-control\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            while len(data) < 65536 and time.monotonic() < deadline:
                sock.settimeout(max(0.05, deadline - time.monotonic()))
                try:
                    chunk = sock.recv(16384)
                except (TimeoutError, socket.timeout):
                    break
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    if not data.startswith(b"HTTP/"):
        return None
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin1", "replace").split("\r\n")
    status = lines[0].split(" ", 2)[1] if len(lines[0].split(" ")) > 1 else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        key = key.strip().lower()
        headers[key] = (headers[key] + "; " if key in headers else "") + value.strip()
    text = body.decode("utf-8", "replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return {
        "status": int(status) if status.isdigit() else 0,
        "title": " ".join(unescape(title.group(1)).split())[:80] if title else "",
        "framework": _guess(headers, text),
        "server": headers.get("server", "")[:40],
    }


def _is_own(item: dict, own_ports: set[int]) -> bool:
    if item["port"] in own_ports or item["process"] in OWN_PROCESSES:
        return True
    cmd = _cmdline(item["pid"]) if item["pid"] else ""
    return any(m in cmd for m in OWN_MARKERS)


_scan_cache: tuple[float, list] = (0.0, [])


def scan(own_ports: set[int] | None = None, use_cache: bool = True) -> list[dict]:
    """Every port that is listening, one entry each, web servers first."""
    global _scan_cache
    if use_cache and time.monotonic() - _scan_cache[0] < SCAN_TTL:
        return _scan_cache[1]
    own = set(own_ports or ())
    by_port: dict[int, dict] = {}
    for item in listening():
        if _is_own(item, own):
            continue
        entry = by_port.setdefault(item["port"], {"port": item["port"], "addrs": [], "process": item["process"], "pid": item["pid"]})
        if item["addr"] not in entry["addrs"]:
            entry["addrs"].append(item["addr"])
        if not entry["process"] and item["process"]:
            entry["process"], entry["pid"] = item["process"], item["pid"]
    boxes = _containers() if any(e["process"].startswith(CONTAINER_PROCESSES) for e in by_port.values()) else {}
    home = str(Path.home())
    entries = list(by_port.values())

    def fill(entry: dict) -> dict:
        addr = next((a for a in entry["addrs"] if addr_kind(a) in ("localhost", "loopback")), entry["addrs"][0])
        entry["addr"] = addr
        entry["kinds"] = sorted({addr_kind(a) for a in entry["addrs"]})
        entry["web"] = probe(addr, entry["port"])
        cwd = _cwd(entry["pid"]) if entry["pid"] else ""
        entry["cwd"] = ("~" + cwd[len(home):]) if cwd.startswith(home) else cwd
        entry["container"] = boxes.get(entry["port"], "")
        project = Path(cwd).name if cwd and cwd not in (home, "/") else ""
        entry["name"] = entry["container"] or project or entry["process"] or f"puerto {entry['port']}"
        web = entry["web"] or {}
        entry["dev"] = bool(web.get("framework") and web["framework"] not in ("nginx", "Apache", "Traefik")) or bool(
            DEV_PROCESSES.match(entry["process"]) or entry["container"] or (project and cwd.startswith(home) and "/." not in cwd[len(home):])
        )
        return entry

    with ThreadPoolExecutor(max_workers=32) as pool:
        entries = list(pool.map(fill, entries))
    # A multi container's own Remote Control is not an environment of the user's.
    entries = [e for e in entries if not e["container"].startswith("rc-")]
    entries.sort(key=lambda e: (e["web"] is None, not e["dev"], e["port"]))
    _scan_cache = (time.monotonic(), entries)
    return entries


# --- stopping one -------------------------------------------------------------------

STOP_WAIT = 4.0


def _uid(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return -1


def _port_open(port: int, pids: set[int]) -> bool:
    return any(i["port"] == port and (not pids or i["pid"] in pids) for i in listening())


def _stop_container(name: str) -> str:
    for tool in ("podman", "docker"):
        exe = shutil.which(tool)
        if not exe:
            continue
        try:
            done = subprocess.run([exe, "stop", "-t", "5", name], capture_output=True, text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return ""
    return f"No pude detener el contenedor {name}"


def stop(port: int, own_ports: set[int] | None = None) -> str:
    """Stops what listens on the port (its process, or its container) and drops
    its publication. "" when done, else why not. Only this user's processes,
    read again here: the page only names the port."""
    share(port, False)
    _scan_cache_clear()
    own = set(own_ports or ())
    found = [i for i in listening() if i["port"] == port]
    if not found:
        return ""
    if any(_is_own(i, own) for i in found):
        return "Ese puerto es de Remote Control"
    if any(i["process"].startswith(CONTAINER_PROCESSES) for i in found):
        name = _containers().get(port, "")
        if name.startswith("rc-"):
            return "Ese puerto es de Remote Control"
        if name:
            return _stop_container(name)
    pids = {i["pid"] for i in found if i["pid"]}
    if not pids:
        return "No veo su proceso: es de otro usuario o del sistema"
    if any(pid in (1, os.getpid()) or _uid(pid) != os.getuid() for pid in pids):
        return "Su proceso es de otro usuario o del sistema"
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except OSError as exc:
                return f"No pude detenerlo ({exc.strerror or exc})"
        deadline = time.monotonic() + STOP_WAIT
        while time.monotonic() < deadline:
            if not _port_open(port, pids):
                return ""
            time.sleep(0.2)
    return "Sigue escuchando después de detenerlo"


def _scan_cache_clear() -> None:
    global _scan_cache
    _scan_cache = (0.0, [])


def host_aliases() -> list[str]:
    """Names in /etc/hosts that point at this machine (myapp.test, api.local…)."""
    names = []
    try:
        lines = Path("/etc/hosts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    skip = {"localhost", "localhost.localdomain", "localhost4", "localhost6", "ip6-localhost", "ip6-loopback", socket.gethostname()}
    for line in lines:
        cols = line.split("#", 1)[0].split()
        if len(cols) >= 2 and (cols[0].startswith("127.") or cols[0] == "::1"):
            names += [n for n in cols[1:] if n not in skip and n not in names and "." in n and "localhost" not in n and "localdomain" not in n]
    return names


def payload(own_ports: set[int] | None = None, fresh: bool = False) -> dict:
    shares = load_shares()
    by_port = {v["port"]: slug for slug, v in shares.items()}

    def shown(slug: str) -> dict | None:
        if not slug:
            return None
        item = shares[slug]
        alive = item["kind"] != "tunnel" or "cloudflared" in _cmdline(item["pid"])
        cf = None
        if item["cf_url"]:
            cf = {"url": item["cf_url"], "link": f"{item['cf_url']}?{KEY_PARAM}={item['key']}", "alive": "cloudflared" in _cmdline(item["cf_pid"])}
        return {"kind": item["kind"], "url": item["url"] or f"/p/{slug}/", "link": link(slug, item), "note": item["note"], "alive": alive, "cf": cf}

    items = []
    for entry in scan(own_ports, use_cache=not fresh):
        slug = by_port.get(entry["port"], "")
        items.append({**entry, "slug": slug, "share": shown(slug)})
    # Published but not listening now: still shown, so it can be switched off.
    listed = {e["port"] for e in items}
    for slug, v in shares.items():
        if v["port"] not in listed:
            items.append({"port": v["port"], "addrs": [v["addr"]], "addr": v["addr"], "kinds": [addr_kind(v["addr"])], "process": "", "pid": 0,
                          "web": None, "cwd": "", "container": "", "name": v["label"] or f"puerto {v['port']}", "slug": slug, "share": shown(slug), "down": True, "dev": True})
    return {"items": items, "aliases": host_aliases(), "subdomains": bool(subdomain_base())}


# --- serving a published one (proxy.py) -----------------------------------------------
# /p/<slug>/x reaches the server's /x. Dev servers ask for their files from the
# root (/@vite/client, /_next/…, /assets/…) and client routers read the page's
# path, so the page moves to the root right away (history.replaceState, from
# the script injected first in its HTML) and a cookie (rc_dev) sends the root
# paths that are not Remote Control's own to that server. Its home "/" is
# "/?rc-dev=<slug>", since "/" alone is Remote Control. Requests whose page
# (Referer) is still under /p/<slug>/ go there too.

COOKIE = "rc_dev"
DEV_PARAM = "rc-dev"
# Remote Control's own paths: never a dev server's without the /p/ prefix.
OWN_PATHS = ("/", "/index.html", "/token", "/ws")
LOCAL_ORIGIN = re.compile(rb"(?:https?|wss?)://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?=[/\"'])", re.I)


def _cookie(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def route(raw_target: str, headers: dict[str, str]) -> tuple[str, dict, str, bool] | None:
    """(slug, share, upstream target, entering) for a request that belongs to
    a published environment; None when it is Remote Control's."""
    # Only the fallback ones: the others have a host of their own.
    shares = {s: v for s, v in load_shares().items() if v["kind"] == "path"}
    if not shares:
        return None
    path, sep, query = raw_target.partition("?")
    if path.startswith("/p/"):
        slug, _, rest = path[3:].partition("/")
        if slug in shares:
            return slug, shares[slug], "/" + rest + (sep + query), True
        return None
    if path.startswith("/rc-"):
        return None
    params = dict(p.partition("=")[::2] for p in query.split("&") if p)
    if path == "/" and params.get(DEV_PARAM) in shares:
        slug = params[DEV_PARAM]
        rest = "&".join(p for p in query.split("&") if p and not p.startswith(DEV_PARAM + "="))
        return slug, shares[slug], "/" + ("?" + rest if rest else ""), False
    referer = re.sub(r"^https?://[^/]+", "", headers.get("referer", ""))
    if referer.startswith("/p/"):
        slug = referer[3:].partition("/")[0]
        if slug in shares and path not in OWN_PATHS:
            return slug, shares[slug], raw_target, False
    slug = _cookie(headers.get("cookie", ""), COOKIE)
    if slug not in shares:
        return None
    upgrade = "upgrade" in headers.get("connection", "").lower()
    # ttyd's terminal is the "tty" WebSocket; any other one (Vite's HMR on "/",
    # webpack's on "/ws"…) is the dev server's.
    if upgrade and "tty" not in headers.get("sec-websocket-protocol", "").split(", "):
        return slug, shares[slug], raw_target, False
    if path in OWN_PATHS:
        return None
    return slug, shares[slug], raw_target, False


def request_head(method: str, target: str, header: bytes, share_: dict, upgrade: bool, wants_html: bool) -> bytes:
    """The request as the dev server expects it from a browser on localhost."""
    local = f"localhost:{share_['port']}"
    out = [f"{method} {target} HTTP/1.1".encode()]
    for line in header.split(b"\r\n")[1:]:
        key = line.split(b":", 1)[0].strip().lower()
        if not line or key in (b"host", b"connection", b"keep-alive", b"accept-encoding" if wants_html else b"-"):
            continue
        if key == b"origin":
            line = f"Origin: http://{local}".encode()
        elif key == b"referer":
            line = LOCAL_ORIGIN.sub(b"", line)
            line = re.sub(rb"^(Referer:\s*)https?://[^/]+(/p/[^/]+)?", f"Referer: http://{local}".encode(), line, flags=re.I)
        elif key == b"cookie":
            kept = [c for c in line.split(b":", 1)[1].split(b";") if c.strip() and c.split(b"=", 1)[0].strip() not in (b"rc_gate", COOKIE.encode(), KEY_COOKIE.encode())]
            if not kept:
                continue
            line = b"Cookie:" + b";".join(kept)
        out.append(line)
    out.append(f"Host: {local}".encode())
    if wants_html:
        out.append(b"Accept-Encoding: identity")
    out.append(b"Connection: Upgrade" if upgrade else b"Connection: close")
    return b"\r\n".join(out) + b"\r\n\r\n"


def home_url(slug: str) -> str:
    return f"/?{DEV_PARAM}={slug}"


def fix_location(value: str, slug: str, hosted: bool = False) -> str:
    value = LOCAL_ORIGIN.sub(b"", value.encode("latin1") + b"/").decode("latin1")[:-1] or "/"
    if hosted:
        return value
    if value == "/" or value.startswith("/?") and DEV_PARAM not in value:
        return home_url(slug) + ("&" + value[2:] if value.startswith("/?") else "")
    return value


def inject_script(slug: str, hosted: bool = False) -> str:
    """Absolute localhost URLs in its code (fetch, WebSocket, EventSource, XHR)
    go to where the page is; under /p/ it also moves the page to the root."""
    move = (
        "function fix(u){var x;try{x=new URL(u,location.href)}catch(e){return u}"
        "if(x.origin!==location.origin)return u;"
        "if(x.pathname===P||x.pathname.indexOf(P+'/')===0)x.pathname=x.pathname.slice(P.length)||'/';"
        "if(x.pathname==='/'&&x.search.indexOf(K)<0)x.search=x.search?x.search+'&'+K:'?'+K;"
        "return x.pathname+x.search+x.hash}"
        "var H=history,ps=H.pushState,rs=H.replaceState;"
        "H.pushState=function(s,t,u){return ps.call(H,s,t,u==null?u:fix(u))};"
        "H.replaceState=function(s,t,u){return rs.call(H,s,t,u==null?u:fix(u))};"
        "rs.call(H,H.state,'',fix(location.href));"
        "document.addEventListener('click',function(e){var a=e.target&&e.target.closest&&e.target.closest('a[href]');"
        "if(a&&a.origin===location.origin&&a.pathname==='/'&&a.search.indexOf(K)<0)a.href=fix(a.href)},true);"
    )
    return (
        "<script>(function(){var P=" + json.dumps("/p/" + slug) + ",K=" + json.dumps(DEV_PARAM + "=" + slug) + ";"
        + ("" if hosted else move)
        + "var L=/^(localhost|127\\.\\d+\\.\\d+\\.\\d+|0\\.0\\.0\\.0|\\[::1\\])$/;"
        "function here(u,ws){var x;try{x=new URL(u,location.href)}catch(e){return u}"
        "if(!L.test(x.hostname))return u;x.host=location.host;"
        "x.protocol=ws?(location.protocol==='https:'?'wss:':'ws:'):location.protocol;return x.href}"
        "var W=window.WebSocket;if(W){var N=function(u,p){return p===undefined?new W(here(u,1)):new W(here(u,1),p)};"
        "N.prototype=W.prototype;['CONNECTING','OPEN','CLOSING','CLOSED'].forEach(function(k){N[k]=W[k]});window.WebSocket=N}"
        "var F=window.fetch;if(F)window.fetch=function(i,o){return F.call(this,typeof i==='string'?here(i):i,o)};"
        "var O=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){arguments[1]=here(u);return O.apply(this,arguments)};"
        "var E=window.EventSource;if(E){var S=function(u,o){return new E(here(u),o)};S.prototype=E.prototype;window.EventSource=S}"
        "})();</script>"
    )


def rewrite_html(body: bytes, slug: str, hosted: bool = False) -> bytes:
    body = LOCAL_ORIGIN.sub(b"", body)
    home = home_url(slug).encode()
    if not hosted:
        body = re.sub(rb"""(\shref=)(["'])/\2""", lambda m: m.group(1) + m.group(2) + home + m.group(2), body, flags=re.I)
    script = inject_script(slug, hosted).encode()
    head = re.search(rb"<head[^>]*>", body, re.I)
    if head:
        return body[: head.end()] + script + body[head.end():]
    return script + body
