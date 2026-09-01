#!/usr/bin/env bash
# Install Remote Control from GitHub. Safe to re-run.
# curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/refs/heads/main/install.sh | bash
set -euo pipefail

REPO_URL="${REMOTE_CONTROL_REPO_URL:-https://github.com/garoford/Remote-Control.git}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/remote-control"
SRC="$SHARE/src"

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

install_pkgs() {
  local -a missing=()
  need_cmd git || missing+=(git)
  need_cmd python3 || missing+=(python3)
  need_cmd rsync || missing+=(rsync)
  need_cmd desktop-file-install || missing+=(desktop-file-utils)

  if ((${#missing[@]})); then
    if need_cmd dnf; then
      log "Instalando paquetes: ${missing[*]}"
      sudo dnf install -y "${missing[@]}"
    else
      die "Faltan comandos: ${missing[*]}. Instálalos e inténtalo de nuevo."
    fi
  fi
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
    git -C "$SRC" fetch --quiet origin main || true
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
TREE=""
checkout_src
[[ -f "$TREE/app/install.sh" ]] || die "No encuentro app/install.sh en $TREE"
bash "$TREE/app/install.sh"

log ""
log "Listo. Arranca con:  remote-control"
log "Si no está en PATH:  $HOME/.local/bin/remote-control"
log ""
log "Auto-update: cada 3 min + al abrir/enfocar la app."
log "Para pararlo:  systemctl --user disable --now remote-control-update.timer"
