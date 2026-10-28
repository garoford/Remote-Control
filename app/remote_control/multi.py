"""Multi: isolated workspaces, one Podman container each, each with its own tunnel.

Every container is a whole Linux (systemd inside, `sudo` without password,
dnf/apt): whatever is installed there stays there. Its disk is the
container's overlay layer on this machine's disk, so it grows with what it
holds and has no fixed size. Rootless Podman: root inside is not root here.

Inside runs a normal Remote Control (copied from this machine's installed
copy; "update" copies it again without recreating the container), as user
`rc`, with its own tunnel, URL and password. What the desktop gave it,
solved inside:

- sound: a PipeWire of its own (user session, lingering), so sound.py
  captures herdr's and the CLIs' sounds there and sends them to the pages;
- clipboard: there is no desktop, copies go through the browser (OSC 52);
- start with the system: Podman's restart service brings the container up
  and a unit inside starts its tunnel (autostart).

Containers carry labels (LABEL…); nothing else of Podman's is touched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

LABEL = "dev.garoford.remote-control"
PREFIX = "rc-"
IMAGE = "localhost/remote-control-multi"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
FIRST_PORT = 17700
USER = "rc"
UID = 1000
HOME = f"/home/{USER}"
RC_BIN = f"{HOME}/.local/bin/remote-control"
AUTOSTART_FLAG = f"{HOME}/.config/remote-control/container-autostart"
# Shown in the UI, in this order.
BASES = {"fedora": "Fedora 44", "ubuntu": "Ubuntu 24.04"}

COMMON_TAIL = f"""
# pam_systemd (systemd-pam / libpam-systemd) gives the lingering user session its
# XDG_RUNTIME_DIR, where PipeWire lives. rtkit has nothing to do in a container.
RUN (systemctl mask rtkit-daemon.service || true) \
 && mkdir -p /var/lib/systemd/linger && touch /var/lib/systemd/linger/{USER} \\
 && printf '%s\\n' '{USER} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/{USER} && chmod 440 /etc/sudoers.d/{USER}
# The tunnel at boot when the container's autostart is on (multi.set_autostart).
RUN printf '%s\\n' '[Unit]' 'Description=Remote Control tunnel' \\
      'After=network-online.target user@{UID}.service' 'Wants=network-online.target' \\
      'ConditionPathExists={AUTOSTART_FLAG}' \\
      '[Service]' 'Type=oneshot' 'RemainAfterExit=yes' 'User={USER}' \\
      'Environment=HOME={HOME} XDG_RUNTIME_DIR=/run/user/{UID} LANG=C.UTF-8 RC_LISTEN_HOST=0.0.0.0' \\
      'ExecStart={RC_BIN} start' 'ExecStop={RC_BIN} stop' \\
      '[Install]' 'WantedBy=multi-user.target' > /etc/systemd/system/remote-control.service \\
 && systemctl enable remote-control.service
USER {USER}
RUN curl -fsSL https://herdr.dev/install.sh | sh || true
# The PC's terminal (zsh, Oh My Posh, Node, Bun, the AI CLIs), once in the
# image: each new container starts with it instead of installing it again.
COPY container_setup.sh /tmp/container_setup.sh
RUN bash /tmp/container_setup.sh
USER root
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
"""

CONTAINERFILES = {
    "fedora": f"""FROM registry.fedoraproject.org/fedora:44
RUN dnf -y install --setopt=install_weak_deps=False --setopt=max_parallel_downloads=20 --nodocs systemd systemd-pam dbus-broker sudo git python3 python3-pip \\
      python3-cryptography python3-pillow python3-fonttools python3-brotli rsync curl tmux ttyd zsh \\
      bind-utils procps-ng which findutils hostname iproute pipewire pipewire-pulseaudio wireplumber ffmpeg-free \\
      pulseaudio-utils glibc-langpack-en \\
 && curl -fsSL https://pkg.cloudflare.com/cloudflared.repo -o /etc/yum.repos.d/cloudflared.repo \\
 && dnf -y install cloudflared && dnf clean all
RUN useradd -m -u {UID} -s /bin/zsh {USER}
RUN su - {USER} -c 'python3 -m pip install --user --quiet textual segno'
""" + COMMON_TAIL,
    "ubuntu": f"""FROM docker.io/library/ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv libpam-systemd dbus-user-session \\
      sudo git python3 python3-pip python3-cryptography python3-pil python3-fonttools python3-brotli rsync \\
      curl ca-certificates gnupg tmux ttyd zsh dnsutils procps iproute2 pipewire pipewire-pulse wireplumber \\
      pulseaudio-utils locales ffmpeg \\
 && mkdir -p --mode=0755 /usr/share/keyrings \\
 && curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg \\
 && echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \\
      > /etc/apt/sources.list.d/cloudflared.list \\
 && apt-get update && apt-get install -y cloudflared && rm -rf /var/lib/apt/lists/*
RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -m -u {UID} -s /bin/zsh {USER}
RUN su - {USER} -c 'python3 -m pip install --user --quiet --break-system-packages textual segno'
""" + COMMON_TAIL,
}


class MultiError(RuntimeError):
    pass


def mode_path() -> Path:
    from .gate import preference_path

    return preference_path().with_name("multi")


def enabled() -> bool:
    """The window and the CLI manage containers instead of this machine's tunnel."""
    return mode_path().is_file()


def set_enabled(on: bool) -> None:
    path = mode_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("on\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


@dataclass
class Box:
    """One workspace container."""

    name: str
    base: str
    state: str  # running, exited, created…
    port: int
    size: int  # bytes written inside (what it grew)
    created: str

    @property
    def running(self) -> bool:
        return self.state == "running"


# --- Podman ------------------------------------------------------------------------------


def available() -> bool:
    return shutil.which("podman") is not None


def podman(*args: str, timeout: float | None = 120, check: bool = True, log=None) -> str:
    if not available():
        raise MultiError("Falta Podman: instálalo (dnf install podman / apt install podman)")
    try:
        if log is not None:
            proc = subprocess.Popen(["podman", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out = []
            assert proc.stdout is not None
            for line in proc.stdout:
                out.append(line)
                log(line.rstrip("\n"))
            code = proc.wait(timeout=timeout)
            text = "".join(out)
        else:
            res = subprocess.run(["podman", *args], capture_output=True, text=True, timeout=timeout, check=False)
            code, text = res.returncode, res.stdout + (res.stderr if res.returncode else "")
    except subprocess.TimeoutExpired as exc:
        raise MultiError(f"podman {args[0]} tardó demasiado") from exc
    if check and code != 0:
        raise MultiError(f"podman {args[0]}: {text.strip().splitlines()[-1] if text.strip() else code}")
    return text


def container(name: str) -> str:
    return PREFIX + name


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def image(base: str) -> str:
    return f"{IMAGE}:{base}"


SETUP_SCRIPT = Path(__file__).resolve().parent / "container_setup.sh"
RECIPE_LABEL = "io.remote-control.recipe"


def recipe(base: str) -> str:
    """What the image is built from: a new recipe means a new image (the
    containers made from the old one keep it)."""
    digest = hashlib.sha256(CONTAINERFILES[base].encode())
    digest.update(SETUP_SCRIPT.read_bytes())
    return digest.hexdigest()[:16]


def has_image(base: str) -> bool:
    res = subprocess.run(
        ["podman", "image", "inspect", "--format", "{{index .Labels \"" + RECIPE_LABEL + "\"}}", image(base)],
        capture_output=True, text=True, check=False,
    )
    return res.returncode == 0 and res.stdout.strip() == recipe(base)


# --- living next to Docker ---------------------------------------------------------------
# Docker sets the kernel's FORWARD policy to DROP. Rootful Podman's bridge
# (netavark's nftables rules) then has no way out: containers get no DNS and
# no internet. Podman's own answer is netavark's iptables driver, whose rules
# let Podman's networks (and only them) through next to Docker's.
NET_CONF = Path("/etc/containers/containers.conf.d/50-remote-control.conf")
NET_CONF_TEXT = (
    "# Remote Control (multi.py): Docker drops forwarded traffic on this machine;\n"
    "# netavark's iptables rules let Podman's own networks out next to Docker's.\n"
    "[network]\n"
    'firewall_driver = "iptables"\n'
)


def docker_blocks_forwarding() -> bool:
    """Rootful Podman next to a Docker that drops forwarded traffic."""
    iptables = shutil.which("iptables")
    if os.geteuid() != 0 or not shutil.which("docker") or not iptables:
        return False
    out = subprocess.run([iptables, "-S", "FORWARD"], capture_output=True, text=True, check=False).stdout
    return "-P FORWARD DROP" in out


def ensure_network(log=lambda _line: None) -> None:
    if not docker_blocks_forwarding():
        return
    try:
        if NET_CONF.read_text(encoding="utf-8") == NET_CONF_TEXT:
            return
    except OSError:
        pass
    NET_CONF.parent.mkdir(parents=True, exist_ok=True)
    NET_CONF.write_text(NET_CONF_TEXT, encoding="utf-8")
    log("Red de Podman ajustada para convivir con Docker")


def build_image(base: str, log=lambda _line: None) -> None:
    """The base image: that distro plus what Remote Control needs inside."""
    if base not in CONTAINERFILES:
        raise MultiError(f"base desconocida: {base}")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "Containerfile").write_text(CONTAINERFILES[base], encoding="utf-8")
        shutil.copy(SETUP_SCRIPT, Path(tmp) / "container_setup.sh")
        # The host's network while building: only packages are fetched, and it
        # works whatever the firewall does to the containers' bridge.
        podman("build", "--network", "host", "--label", f"{RECIPE_LABEL}={recipe(base)}", "-t", image(base), tmp, timeout=3600, log=log)


def prepare_image(base: str, log=lambda _line: None) -> None:
    """The image, built once. The installer starts this in the background;
    a create meanwhile waits for that build instead of doing its own."""
    if base not in CONTAINERFILES:
        raise MultiError(f"base desconocida: {base}")
    ensure_network(log)
    if has_image(base):
        return
    lock = Path.home() / ".cache" / "remote-control" / f"image-{base}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"Terminando de preparar la imagen {BASES[base]}…")
            fcntl.flock(held, fcntl.LOCK_EX)
        if not has_image(base):
            log(f"Preparando la imagen {BASES[base]} (solo la primera vez)…")
            build_image(base, log)


def list_boxes() -> list[Box]:
    out = podman("ps", "-a", "--size", "--filter", f"label={LABEL}", "--format", "json")
    boxes = []
    for item in json.loads(out or "[]"):
        labels = item.get("Labels") or {}
        # The container's own name first: a rename changes it, not the label.
        name = (item.get("Names") or [""])[0].removeprefix(PREFIX) or labels.get(f"{LABEL}.name", "")
        size = item.get("Size") or {}
        boxes.append(
            Box(
                name=name,
                base=labels.get(f"{LABEL}.base", ""),
                state=item.get("State", ""),
                port=int(labels.get(f"{LABEL}.port") or 0),
                size=int(size.get("rwSize") or 0) if isinstance(size, dict) else 0,
                created=item.get("CreatedAt", ""),
            )
        )
    return sorted(boxes, key=lambda b: b.name)


def get(name: str) -> Box:
    for box in list_boxes():
        if box.name == name:
            return box
    raise MultiError(f"no existe el contenedor {name}")


def _free_port(taken: set[int]) -> int:
    port = FIRST_PORT
    while True:
        if port not in taken:
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    pass
        port += 1


# --- lifecycle ---------------------------------------------------------------------------


def create(name: str, base: str = "fedora", log=lambda _line: None) -> Box:
    if not valid_name(name):
        raise MultiError("nombre inválido: minúsculas, números y guiones (máx. 31)")
    if base not in CONTAINERFILES:
        raise MultiError(f"base desconocida: {base}")
    boxes = list_boxes()
    if any(b.name == name for b in boxes):
        raise MultiError(f"ya existe un contenedor {name}")
    prepare_image(base, log)
    port = _free_port({b.port for b in boxes})
    log(f"Creando {name}…")
    podman(
        "create",
        "--name", container(name),
        "--hostname", name,
        "--label", f"{LABEL}=1",
        "--label", f"{LABEL}.name={name}",
        "--label", f"{LABEL}.base={base}",
        "--label", f"{LABEL}.port={port}",
        "--systemd", "always",
        # Its local URL from this machine (the tunnel itself needs no port).
        "--publish", f"127.0.0.1:{port}:7681",
        image(base),
    )
    start(name, log)
    install_rc(name, log, env=False)
    return get(name)


def start(name: str, log=lambda _line: None) -> None:
    ensure_network(log)
    podman("start", container(name))
    # systemd up (and the user session with PipeWire) before anything runs in it.
    deadline = time.time() + 60
    while time.time() < deadline:
        state = podman("exec", container(name), "systemctl", "is-system-running", check=False).strip()
        if state in ("running", "degraded"):
            break
        time.sleep(0.5)
    log(f"{name} encendido")


def stop(name: str) -> None:
    podman("stop", "--time", "20", container(name), timeout=60)


def remove(name: str) -> None:
    from . import router

    podman("rm", "--force", "--volumes", container(name), timeout=90)
    # Its name in your domain goes too (DNS record, route).
    router.unassign(name)


def rename(old: str, new: str) -> None:
    if not valid_name(new):
        raise MultiError("nombre inválido: minúsculas, números y guiones (máx. 31)")
    if any(b.name == new for b in list_boxes()):
        raise MultiError(f"ya existe un contenedor {new}")
    from . import router

    # The name label is fixed at creation; the container name is what we read first.
    podman("rename", container(old), container(new))
    router.rename(old, new)


# --- inside ------------------------------------------------------------------------------


def run(name: str, *args: str, user: str = USER, timeout: float | None = 120, check: bool = True, log=None) -> str:
    """A command inside, as its user; ``log`` gets each line as it comes."""
    env = ["--env", f"HOME={HOME}", "--env", f"USER={USER}", "--env", f"XDG_RUNTIME_DIR=/run/user/{UID}", "--env", "LANG=C.UTF-8",
           "--env", "RC_LISTEN_HOST=0.0.0.0"]
    if user == "root":
        env = []
    return podman("exec", "--user", user, "--workdir", HOME if user != "root" else "/", *env, container(name), *args, timeout=timeout, check=check, log=log)


def install_rc(name: str, log=lambda _line: None, env: bool = True) -> None:
    """This machine's installed Remote Control, copied in (create and update).
    A new container already has the environment from its image (env=False)."""
    package = Path(__file__).resolve().parent
    log("Copiando Remote Control…")
    run(name, "mkdir", "-p", f"{HOME}/.local/share/remote-control", f"{HOME}/.local/bin")
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "remote_control"
        shutil.copytree(package, stage, ignore=shutil.ignore_patterns("__pycache__"))
        run(name, "rm", "-rf", f"{HOME}/.local/share/remote-control/remote_control")
        podman("cp", str(stage), f"{container(name)}:{HOME}/.local/share/remote-control/remote_control")
        wrapper = Path(tmp) / "remote-control"
        wrapper.write_text(
            "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path.home() / '.local' / 'share' / 'remote-control'))\n"
            "from remote_control.app import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n",
            encoding="utf-8",
        )
        podman("cp", str(wrapper), f"{container(name)}:{RC_BIN}")
    run(name, "chown", "-R", f"{USER}:{USER}", f"{HOME}/.local", user="root")
    run(name, "chmod", "755", RC_BIN)
    # Its running tunnel serves the new page from the next reload.
    run(name, RC_BIN, "page", check=False)
    log("Remote Control listo")
    if env:
        setup_env(name, log)


ENV_DONE = f"{HOME}/.config/remote-control/env-done"


def setup_env(name: str, log=lambda _line: None, force: bool = False) -> None:
    """The PC's terminal inside: zsh, Oh My Posh, plugins, tools, Node, Bun and
    the AI CLIs (container_setup.sh). Only when that script changed since its
    last complete run here (or something was left to install): an update is
    otherwise just copying Remote Control."""
    script = f"{HOME}/.local/share/remote-control/remote_control/container_setup.sh"
    digest = hashlib.sha256(SETUP_SCRIPT.read_bytes()).hexdigest()[:16]
    if not force:
        try:
            if run(name, "cat", ENV_DONE, check=False).strip() == digest:
                log("Entorno al día")
                return
        except MultiError:
            pass
    log("Preparando el entorno (zsh, Node, CLIs de IA)…")
    # Streamed: it takes minutes the first time, each step shows as it starts.
    out = run(name, "bash", script, timeout=1800, check=False, log=log)
    if "Sin instalar" not in out and "Entorno listo" in out:
        run(name, "sh", "-c", f'printf %s "$1" > {ENV_DONE}', "sh", digest, check=False)


def tunnel_status(name: str) -> dict:
    try:
        return json.loads(run(name, RC_BIN, "status", "--json", timeout=20).strip().splitlines()[-1])
    except (MultiError, ValueError, IndexError):
        return {"running": False, "url": None}


def tunnel_start(name: str) -> str:
    """The URL once it is public (Remote Control's own start waits for it).
    A stopped container is started first."""
    if not get(name).running:
        start(name)
    from . import router

    if name in router.load_routes() and not router.service_active():
        # Its own domain: the router brings HTTPS to it (no cloudflared inside).
        router.ensure_service()
    out = run(name, RC_BIN, "start", timeout=300)
    urls = re.findall(r"https?://\S+", out)
    if not urls:
        raise MultiError(out.strip().splitlines()[-1] if out.strip() else "no salió la URL")
    return urls[-1]


def tunnel_stop(name: str) -> None:
    """The tunnel, and with it the whole container: nothing of it keeps
    running unseen. Its tabs come back on the next start (workspaces.py)."""
    try:
        run(name, RC_BIN, "stop", timeout=60)
    except MultiError:
        pass
    stop(name)


GATE_FILE = f"{HOME}/.config/remote-control/gate"


def password(name: str, action: str = "show", value: str = "") -> str:
    """Its password; readable and changeable with the container stopped too
    (its file through ``podman cp``), so turning the tunnel off never locks it."""
    if get(name).running:
        args = [RC_BIN, "password", action] + ([value] if action == "set" else [])
        return run(name, *args, timeout=20).strip()
    from .gate import generate_password, without_spaces

    if action == "show":
        return _read_file(name, GATE_FILE).strip()
    new = generate_password() if action == "new" else without_spaces(value)
    if not new:
        raise MultiError("la contraseña no puede quedar vacía")
    _write_file(name, GATE_FILE, new + "\n")
    return new


def _read_file(name: str, path: str) -> str:
    """A file of a container, running or not (podman cp hands a tar)."""
    import io
    import tarfile

    res = subprocess.run(["podman", "cp", f"{container(name)}:{path}", "-"], capture_output=True, check=False, timeout=30)
    if res.returncode != 0:
        return ""
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
        member = next((m for m in tar.getmembers() if m.isfile()), None)
        data = tar.extractfile(member).read() if member else b""
    return data.decode("utf-8", "replace")


def _write_file(name: str, path: str, text: str) -> None:
    """Write a private file of its user (uid 1000, 0600), even with it stopped."""
    import io
    import tarfile

    data = text.encode("utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(Path(path).name)
        info.size, info.mode, info.uid, info.gid = len(data), 0o600, UID, UID
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(data))
    # --archive=false: keep the tar's owner (its user), not the container's root.
    res = subprocess.run(["podman", "cp", "--archive=false", "-", f"{container(name)}:{Path(path).parent}"], input=buf.getvalue(),
                         capture_output=True, check=False, timeout=30)
    if res.returncode != 0:
        raise MultiError("no se pudo guardar: " + res.stderr.decode("utf-8", "replace").strip()[-200:])


def autostart(name: str) -> bool:
    proc = subprocess.run(["podman", "exec", container(name), "test", "-e", AUTOSTART_FLAG], check=False)
    return proc.returncode == 0


def set_autostart(name: str, on: bool) -> None:
    """On: Podman brings the container up at login and the unit inside starts
    its tunnel. Needs Podman's restart service enabled for this user."""
    if on:
        run(name, "sh", "-c", f"mkdir -p {Path(AUTOSTART_FLAG).parent} && touch {AUTOSTART_FLAG}")
        podman("update", "--restart", "always", container(name))
        subprocess.run(["systemctl", "--user", "enable", "podman-restart.service"], capture_output=True, check=False)
    else:
        run(name, "rm", "-f", AUTOSTART_FLAG)
        podman("update", "--restart", "no", container(name))


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# --- `remote-control multi` --------------------------------------------------------------

CLI_USAGE = """uso: remote-control multi COMANDO

  on | off                      la ventana y el CLI administran contenedores (o esta máquina)
  list                          los contenedores, su estado y tamaño
  create NOMBRE [--base fedora|ubuntu]
  prepare [--base fedora|ubuntu] dejar lista la imagen (el instalador lo hace en segundo plano)
  start | stop NOMBRE           encender / apagar el contenedor
  rm NOMBRE                     borrarlo con todo lo que tiene
  rename NOMBRE NUEVO
  shell NOMBRE                  una terminal adentro (usuario rc, sudo libre)
  tunnel NOMBRE on|off|status   su túnel web
  password NOMBRE [show|new|set CLAVE]
  update NOMBRE                 copiar adentro el Remote Control de esta máquina
  autostart NOMBRE on|off       encenderlo con su túnel al iniciar sesión
  domain NOMBRE SUBDOMINIO|off  su nombre en tu dominio (HTTPS por el router de esta máquina;
                                la cuenta del proveedor:  remote-control domain set …)
"""


def cli(argv: list[str]) -> int:
    import sys

    def out(line: str) -> None:
        print(line, flush=True)

    cmd = argv[0] if argv else "list"
    args = argv[1:]
    try:
        if cmd in ("-h", "--help", "help"):
            print(CLI_USAGE)
            return 0
        if cmd in ("on", "off"):
            set_enabled(cmd == "on")
            print("Modo multi: " + ("activado" if cmd == "on" else "desactivado"))
            return 0
        if cmd == "list":
            boxes = list_boxes()
            if not boxes:
                print("Sin contenedores. Crea uno:  remote-control multi create NOMBRE")
                return 0
            for b in boxes:
                mark = "●" if b.running else "○"
                print(f"{mark} {b.name:<20} {BASES.get(b.base, b.base):<14} {human_size(b.size):>9}  local http://localhost:{b.port}/")
            return 0
        if cmd == "prepare":
            base = args[args.index("--base") + 1] if "--base" in args else "fedora"
            prepare_image(base, log=out)
            print(f"Imagen {BASES.get(base, base)} lista")
            return 0
        if not args:
            print(CLI_USAGE, file=sys.stderr)
            return 2
        name = args[0]
        if cmd == "create":
            base = args[args.index("--base") + 1] if "--base" in args else "fedora"
            box = create(name, base, log=out)
            print(f"Listo: {box.name} ({BASES.get(box.base, box.base)}). Túnel:  remote-control multi tunnel {box.name} on")
        elif cmd == "start":
            start(name, log=out)
        elif cmd == "stop":
            stop(name)
        elif cmd == "rm":
            remove(name)
            print(f"{name} borrado")
        elif cmd == "rename":
            rename(name, args[1])
        elif cmd == "shell":
            if not get(name).running:
                start(name)
            os.execvp("podman", ["podman", "exec", "-it", "--user", USER, "--workdir", HOME,
                                 "--env", f"HOME={HOME}", "--env", f"XDG_RUNTIME_DIR=/run/user/{UID}", "--env", "LANG=C.UTF-8",
                                 "--env", f"TERM={os.environ.get('TERM', 'xterm-256color')}",
                                 container(name), "zsh", "-l"])
        elif cmd == "tunnel":
            action = args[1] if len(args) > 1 else "status"
            if action == "on":
                print(tunnel_start(name))
            elif action == "off":
                tunnel_stop(name)
            else:
                st = tunnel_status(name)
                print(f"encendido: {st.get('url')}" if st.get("running") else "apagado")
        elif cmd == "password":
            action = args[1] if len(args) > 1 else "show"
            print(password(name, action, args[2] if len(args) > 2 else ""))
        elif cmd == "update":
            install_rc(name, log=out)
        elif cmd == "domain":
            from . import domains, router

            sub = args[1] if len(args) > 1 else ""
            if sub == "off":
                router.unassign(name)
                print(f"{name} sin dominio propio (vuelve a su URL de trycloudflare.com al reiniciar su túnel)")
            elif sub:
                try:
                    url = router.assign(name, sub, log=out)
                except domains.DomainError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
                print(f"{name}: {url}  (reinicia su túnel para usarlo)")
            else:
                print(router.load_routes().get(name, "sin dominio propio"))
        elif cmd == "autostart":
            set_autostart(name, len(args) > 1 and args[1] == "on")
        else:
            print(CLI_USAGE, file=sys.stderr)
            return 2
    except MultiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
