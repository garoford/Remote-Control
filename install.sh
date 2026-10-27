#!/usr/bin/env bash
# Install Remote Control from GitHub. Safe to re-run.
# curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/refs/heads/main/install.sh | bash
set -euo pipefail

REPO_URL="${REMOTE_CONTROL_REPO_URL:-https://github.com/garoford/Remote-Control.git}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/remote-control"
SRC="$SHARE/src"

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

warn() { printf 'aviso: %s\n' "$*" >&2; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

as_root() {
  if ((EUID == 0)); then
    "$@"
  else
    sudo "$@"
  fi
}

# `sudo ./install.sh` would install everything into /root, and the user then
# gets "command not found". Re-run as that user; packages still go through
# sudo, command by command.
if ((EUID == 0)) && [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
  self="${BASH_SOURCE[0]:-}"
  [[ -n "$self" && -f "$self" && "$self" != /dev/fd/* ]] \
    || die "no uses sudo: se instalaría para root. Córrelo como tu usuario (pedirá la contraseña de sudo cuando haga falta)."
  uid="$(id -u "$SUDO_USER")"
  log "Instalando para $SUDO_USER, no para root…"
  # That user's systemd session (SSH login), for the auto-update timer.
  session=()
  [[ -S "/run/user/$uid/bus" ]] && session=(XDG_RUNTIME_DIR="/run/user/$uid"
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus")
  exec sudo -u "$SUDO_USER" -H env "${session[@]}" bash "$self" "$@"
fi

# Everything Remote Control uses. The tunnel needs tmux, ttyd and cloudflared;
# the window GTK 4 + libadwaita. The rest adds features: fontTools/brotli
# (phone font), Pillow (image previews), zsh (per-profile AI CLIs), wl-copy
# (clipboard), dig (DNS check), ImageMagick (launcher icons).
PKGS_dnf=(git python3 python3-pip rsync curl desktop-file-utils tmux ttyd zsh
  python3-gobject gtk4 libadwaita python3-fonttools python3-brotli python3-pillow
  wl-clipboard bind-utils ImageMagick)
PKGS_apt=(git python3 python3-pip rsync curl ca-certificates gnupg desktop-file-utils tmux ttyd zsh
  python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-fonttools python3-brotli python3-pil
  wl-clipboard dnsutils imagemagick)
PKGS_pacman=(git python python-pip rsync curl desktop-file-utils tmux ttyd zsh
  python-gobject gtk4 libadwaita python-fonttools python-brotli python-pillow
  wl-clipboard bind imagemagick cloudflared)
PKGS_zypper=(git python3 python3-pip rsync curl desktop-file-utils tmux ttyd zsh
  python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 python3-fonttools python3-Brotli python3-Pillow
  wl-clipboard bind-utils ImageMagick)

PM=""
for pm in dnf apt-get pacman zypper; do
  if need_cmd "$pm"; then
    PM="$pm"
    break
  fi
done

PY_CHECK='
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
import brotli, fontTools, PIL
'

deps_ok() {
  local c
  for c in git python3 rsync curl desktop-file-install tmux ttyd zsh wl-copy dig convert; do
    need_cmd "$c" || return 1
  done
  python3 -c "$PY_CHECK" >/dev/null 2>&1
}

# One package at a time where the manager would refuse the whole list over a
# name that does not exist in this distro release.
install_pkgs() {
  if deps_ok; then
    return
  fi
  local p
  case "$PM" in
    dnf)
      log "Instalando paquetes (dnf)…"
      if dnf --version 2>/dev/null | grep -q dnf5; then
        as_root dnf install -y --skip-unavailable "${PKGS_dnf[@]}" || warn "dnf no instaló todo"
      else
        as_root dnf install -y --setopt=strict=0 "${PKGS_dnf[@]}" || warn "dnf no instaló todo"
      fi
      ;;
    apt-get)
      log "Instalando paquetes (apt)…"
      as_root apt-get update -q || true
      for p in "${PKGS_apt[@]}"; do
        as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$p" >/dev/null || warn "apt: no pude instalar $p"
      done
      ;;
    pacman)
      log "Instalando paquetes (pacman)…"
      for p in "${PKGS_pacman[@]}"; do
        as_root pacman -S --needed --noconfirm "$p" >/dev/null || warn "pacman: no pude instalar $p"
      done
      ;;
    zypper)
      log "Instalando paquetes (zypper)…"
      for p in "${PKGS_zypper[@]}"; do
        as_root zypper --non-interactive install "$p" >/dev/null || warn "zypper: no pude instalar $p"
      done
      ;;
    *)
      warn "No reconozco el gestor de paquetes: instala a mano git python3 rsync curl tmux ttyd zsh GTK4 libadwaita PyGObject."
      ;;
  esac
  need_cmd git && need_cmd python3 && need_cmd rsync || die "Faltan git, python3 o rsync."
}

# Debian/Ubuntu's ttyd package starts ttyd.service, a login prompt on
# 127.0.0.1:7681. Remote Control runs its own ttyd: turn that one off when
# it came with the package this script installed.
HAD_TTYD=0
need_cmd ttyd && HAD_TTYD=1
quiet_ttyd_service() {
  ((HAD_TTYD)) && return 0
  need_cmd systemctl || return 0
  systemctl is-enabled ttyd.service >/dev/null 2>&1 || systemctl is-active ttyd.service >/dev/null 2>&1 || return 0
  log "Apagando ttyd.service (vino con el paquete ttyd; Remote Control usa su propio ttyd)…"
  as_root systemctl disable --now ttyd.service >/dev/null 2>&1 || warn "no pude apagar ttyd.service"
}

uname_arch() {
  case "$(uname -m)" in
    x86_64 | amd64) printf amd64 ;;
    aarch64 | arm64) printf arm64 ;;
    armv7l) printf arm ;;
    *) printf '%s' "$(uname -m)" ;;
  esac
}

# A release binary where the distro has no package. /usr/local/bin when sudo
# works (the desktop session's PATH often lacks ~/.local/bin), else there.
install_binary() {
  local name="$1" url="$2" tmp
  tmp="$(mktemp)"
  if ! curl -fsSL "$url" -o "$tmp"; then
    rm -f "$tmp"
    warn "no pude descargar $name de $url"
    return 1
  fi
  chmod 755 "$tmp"
  if as_root install -m 755 "$tmp" "/usr/local/bin/$name" 2>/dev/null; then
    :
  else
    mkdir -p "$HOME/.local/bin"
    install -m 755 "$tmp" "$HOME/.local/bin/$name"
  fi
  rm -f "$tmp"
}

install_ttyd() {
  need_cmd ttyd && return
  local arch
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    aarch64 | arm64) arch=aarch64 ;;
    *) arch="$(uname -m)" ;;
  esac
  log "Instalando ttyd (release de GitHub)…"
  install_binary ttyd "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.$arch" || true
}

# Not in Fedora or Debian repos: Cloudflare's own repo, else its release.
install_cloudflared() {
  need_cmd cloudflared && return
  log "Instalando cloudflared…"
  case "$PM" in
    dnf)
      curl -fsSL https://pkg.cloudflare.com/cloudflared.repo | as_root tee /etc/yum.repos.d/cloudflared.repo >/dev/null \
        && as_root dnf install -y cloudflared || true
      ;;
    apt-get)
      as_root mkdir -p --mode=0755 /usr/share/keyrings \
        && curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | as_root tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null \
        && echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
          | as_root tee /etc/apt/sources.list.d/cloudflared.list >/dev/null \
        && as_root apt-get update -q \
        && as_root apt-get install -y -q cloudflared >/dev/null || true
      ;;
  esac
  need_cmd cloudflared && return
  install_binary cloudflared \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(uname_arch)" || true
}

# herdr: workspaces for AI agents (its official installer, to ~/.local/bin).
install_herdr() {
  if need_cmd herdr || [[ -x "$HOME/.local/bin/herdr" ]]; then
    return
  fi
  log "Instalando herdr…"
  curl -fsSL https://herdr.dev/install.sh | sh || warn "no pude instalar herdr"
}

# Python packages the distro may lack (pip, per user, no sudo).
install_py_fallbacks() {
  local -a pip=()
  python3 -c "import fontTools" >/dev/null 2>&1 || pip+=(fonttools)
  python3 -c "import brotli" >/dev/null 2>&1 || pip+=(brotli)
  python3 -c "import PIL" >/dev/null 2>&1 || pip+=(pillow)
  python3 -c "import textual, segno" >/dev/null 2>&1 || pip+=(textual segno)
  # Let's Encrypt for your own domain (acme.py).
  python3 -c "import cryptography" >/dev/null 2>&1 || pip+=(cryptography)
  ((${#pip[@]})) || return 0
  log "Instalando con pip: ${pip[*]}"
  python3 -m pip install --user --quiet "${pip[@]}" 2>/dev/null \
    || python3 -m pip install --user --quiet --break-system-packages "${pip[@]}" \
    || warn "pip no pudo instalar ${pip[*]}"
}

# ~/.local/bin (remote-control, herdr) on PATH: zsh never reads ~/.profile,
# and Ubuntu's only adds it at a login where the folder already existed.
# New AI CLIs take the active profile also in herdr panes and plain
# terminals, and `rc` is on PATH (profiles.zsh, written by the tunnel).
hook_shells() {
  local rc zrc="${ZDOTDIR:-$HOME}/.zshrc"
  local path_line='[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || export PATH="$HOME/.local/bin:$PATH"'
  for rc in "$HOME/.bashrc" "$zrc"; do
    [[ "$rc" == "$zrc" ]] && ! need_cmd zsh && continue
    grep -qF "# Remote Control: PATH" "$rc" 2>/dev/null \
      || printf '\n# Remote Control: PATH\n%s\n' "$path_line" >>"$rc"
  done
  need_cmd zsh || return 0
  if ! grep -qF "remote-control/profiles.zsh" "$zrc" 2>/dev/null; then
    printf '\n# Remote Control: AI CLI profiles and `rc`\n%s\n' \
      '[[ -r ~/.local/share/remote-control/profiles.zsh ]] && source ~/.local/share/remote-control/profiles.zsh' >>"$zrc"
  fi
}

report() {
  local c missing="" optional=""
  for c in tmux ttyd cloudflared; do
    need_cmd "$c" || [[ -x "$HOME/.local/bin/$c" ]] || missing+=" $c"
  done
  python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Adw, Gtk' \
    >/dev/null 2>&1 || missing+=" GTK4/libadwaita(PyGObject)"
  for c in herdr zsh wl-copy dig convert; do
    need_cmd "$c" || [[ -x "$HOME/.local/bin/$c" ]] || optional+=" $c"
  done
  python3 -c "import brotli, fontTools, PIL, textual, segno" >/dev/null 2>&1 || optional+=" (módulos python)"
  log ""
  if [[ -n "$missing" ]]; then
    warn "faltan dependencias necesarias:$missing"
  else
    log "Dependencias: todas instaladas."
  fi
  [[ -z "$optional" ]] || warn "faltan opcionales:$optional"
}

checkout_src() {
  local here="" self="${BASH_SOURCE[0]:-}"
  if [[ -n "$self" && -f "$self" && "$self" != /dev/fd/* ]]; then
    here="$(cd "$(dirname "$self")" && pwd)"
  fi

  # Running from a real checkout (not curl | bash): install that tree so
  # unpushed work is what gets installed. Still keep SHARE/src as a git clone
  # so later updates can fetch origin/main.
  if [[ -n "$here" && -d "$here/.git" && -f "$here/app/install.sh" ]]; then
    if [[ ! -d "$SRC/.git" ]]; then
      mkdir -p "$SHARE"
      git clone --branch main --single-branch "$here" "$SRC"
    fi
    git -C "$SRC" remote set-url origin "$(git -C "$here" remote get-url origin)"
    git -C "$SRC" fetch --quiet origin main 2>/dev/null || true
    # Prefer the working tree we launched from.
    TREE="$here"
    return
  fi

  if [[ -e "$SRC" && ! -d "$SRC/.git" ]]; then
    die "$SRC existe y no es un clone git. Muévelo y reintenta."
  fi

  if [[ -d "$SRC/.git" ]]; then
    git -C "$SRC" remote set-url origin "$REPO_URL"
    git -C "$SRC" fetch --quiet origin main
    git -C "$SRC" checkout -q main
    git -C "$SRC" reset --hard origin/main >/dev/null
  else
    mkdir -p "$SHARE"
    git clone --branch main --single-branch "$REPO_URL" "$SRC"
  fi
  TREE="$SRC"
}

install_pkgs
quiet_ttyd_service
install_ttyd
install_cloudflared
install_herdr
install_py_fallbacks
TREE=""
checkout_src
[[ -f "$TREE/app/install.sh" ]] || die "No encuentro app/install.sh en $TREE"
bash "$TREE/app/install.sh"
hook_shells
report

log ""
log "Listo. Arranca con:  remote-control"
[[ ":$PATH:" == *":$HOME/.local/bin:"* ]] \
  || log "En esta terminal aún no está en PATH: abre otra o corre  exec \$SHELL -l"
log "Sin escritorio (servidor): abre el CLI."
log ""
log "Auto-update: cada 3 min + al abrir/enfocar la app."
log "Para pararlo:  systemctl --user disable --now remote-control-update.timer"
