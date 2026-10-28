#!/usr/bin/env bash
# Install Remote Control from GitHub. Safe to re-run.
# curl -fsSL https://raw.githubusercontent.com/garoford/Remote-Control/refs/heads/main/install.sh | bash
# Asks Normal or Multi in a terminal; pick it ahead with  | bash -s -- --multi  (or --normal).
# Uninstall:  remote-control uninstall   or   | bash -s -- --uninstall [--keep-data] [--yes]
set -euo pipefail

REPO_URL="${REMOTE_CONTROL_REPO_URL:-https://github.com/garoford/Remote-Control.git}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/remote-control"
SRC="$SHARE/src"
# web_ports (install) and u_services (uninstall): VPS ports 80/443 without root.
SYSCTL_PORTS=/etc/sysctl.d/99-remote-control-ports.conf

# --- look ----------------------------------------------------------------
# A terminal gets colors, a spinner per step and a summary; the commands'
# own output goes to a log. Piped or redirected (a server's log), plain lines.
UI=0
[[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]] && UI=1
if ((UI)); then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[0m' TEAL=$'\e[38;2;33;199;168m' BLUE=$'\e[38;2;130;170;255m'
  GREEN=$'\e[38;2;34;218;110m' YELLOW=$'\e[38;2;255;235;149m' RED=$'\e[38;2;239;83;80m' MUTED=$'\e[38;2;138;155;179m'
else
  B="" D="" R="" TEAL="" BLUE="" GREEN="" YELLOW="" RED="" MUTED=""
fi

LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/remote-control"
LOG="$LOG_DIR/install.log"
mkdir -p "$LOG_DIR"
: >"$LOG"
# What a step leaves for the screen (it runs in a subshell): a note, warnings,
# the reason it stopped, values for later steps.
STATE="$(mktemp -d)"
KEEPALIVE=""
cleanup() {
  [[ -n "$KEEPALIVE" ]] && kill "$KEEPALIVE" 2>/dev/null
  ((UI)) && printf '\e[?25h'
  rm -rf "$STATE"
}
trap cleanup EXIT

log() { printf '%s\n' "$*"; }
warn() {
  printf 'aviso: %s\n' "$*" >>"$LOG"
  printf '%s\n' "$*" >>"$STATE/warns"
}
IN_STEP=0
die() {
  printf 'error: %s\n' "$*" >>"$LOG"
  if ((IN_STEP)); then
    printf '%s\n' "$*" >"$STATE/die"
  else
    printf '%serror:%s %s\n' "$RED" "$R" "$*" >&2
  fi
  exit 1
}
# Why a step had nothing to do ("ya estaba"), shown beside its check mark.
note() { printf '%s' "$*" >"$STATE/note"; }

STEP=0
STEPS=0
SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)

banner() {
  local distro=""
  [[ -r /etc/os-release ]] && distro="$(. /etc/os-release && printf '%s' "${NAME:-} ${VERSION_ID:-}")"
  if ((UI)); then
    printf '\n  %s%s ▶_ %s  %sRemote Control%s\n' "$B" "$TEAL" "$R" "$B" "$R"
    printf '        %sinstalador · %s · %s%s%s\n\n' "$MUTED" "${distro:-linux}" "${PM:-?}" "$( ((HEADLESS)) && echo ' · servidor sin escritorio')" "$R"
  else
    printf 'Remote Control: instalador (%s, %s)\n' "${distro:-linux}" "${PM:-?}"
  fi
}

# step "Title" function: runs it with its output in the log, a spinner meanwhile.
step() {
  local title="$1" fn="$2" rc=0 pid i=0 start=$SECONDS secs note="" w
  STEP=$((STEP + 1))
  rm -f "$STATE/note" "$STATE/warns" "$STATE/die"
  printf '\n== [%d/%d] %s ==\n' "$STEP" "$STEPS" "$title" >>"$LOG"
  local from
  from="$(wc -l <"$LOG")"
  local num
  num="$(printf '%s[%d/%d]%s' "$D" "$STEP" "$STEPS" "$R")"
  ((UI)) || printf '[%d/%d] %s… ' "$STEP" "$STEPS" "$title"
  # In the background so `set -e` still applies inside the step.
  ( set -e; IN_STEP=1; "$fn" ) >>"$LOG" 2>&1 </dev/null &
  pid=$!
  if ((UI)); then
    printf '\e[?25l'
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s %s%s%s %s %s%ds%s\e[K' "$num" "$BLUE" "${SPIN[i++ % ${#SPIN[@]}]}" "$R" "$title" "$MUTED" $((SECONDS - start)) "$R"
      sleep 0.1
    done
  fi
  wait "$pid" || rc=$?
  secs=$((SECONDS - start))
  [[ -f "$STATE/note" ]] && note="$(<"$STATE/note")"
  [[ -z "$note" ]] && ((secs)) && note="${secs}s"
  local mark color
  if ((rc)); then
    mark="✗" color="$RED"
  elif [[ -s "$STATE/warns" ]]; then
    mark="!" color="$YELLOW"
  else
    mark="✓" color="$GREEN"
  fi
  if ((UI)); then
    # Padded by characters, not bytes (printf %-30s miscounts "Módulos").
    local pad=$((30 - ${#title}))
    ((pad < 1)) && pad=1
    printf '\r  %s %s%s%s %s%*s %s%s%s\e[K\n' "$num" "$color" "$mark" "$R" "$title" "$pad" "" "$MUTED" "$note" "$R"
    printf '\e[?25h'
  else
    local word="ok"
    [[ $mark == ! ]] && word="con avisos"
    [[ $mark == ✗ ]] && word="falló"
    printf '%s%s\n' "$word" "${note:+ ($note)}"
  fi
  if [[ -s "$STATE/warns" ]]; then
    while IFS= read -r w; do
      printf '        %s↳ %s%s\n' "$YELLOW" "$w" "$R"
      printf '%s\n' "$w" >>"$STATE/all-warns"
    done <"$STATE/warns"
  fi
  if ((rc)); then
    local why="falló (código $rc)"
    [[ -s "$STATE/die" ]] && why="$(<"$STATE/die")"
    printf '\n  %s%s✗ No se pudo terminar:%s %s\n\n' "$B" "$RED" "$R" "$why"
    printf '  %sLo último de este paso (log completo: %s):%s\n' "$MUTED" "$LOG" "$R"
    local line
    tail -n "+$((from + 1))" "$LOG" | tail -n 12 | while IFS= read -r line; do printf '    %s%s%s\n' "$D" "$line" "$R"; done
    printf '\n'
    exit 1
  fi
}

# Packages need root: ask once, visibly, before any spinner; then keep the
# sudo ticket alive so no prompt hides behind one.
ask_sudo() {
  ((EUID == 0)) && return 0
  need_cmd sudo || return 0
  sudo -n true 2>/dev/null && return 0
  printf '  %s🔑 Hace falta tu contraseña para instalar paquetes del sistema.%s\n' "$YELLOW" "$R"
  if sudo -v; then
    (while kill -0 $$ 2>/dev/null; do sudo -n true 2>/dev/null; sleep 50; done) &
    KEEPALIVE=$!
    printf '\n'
  else
    printf '  %sSin sudo: se intentará lo que no lo necesita.%s\n\n' "$MUTED" "$R"
  fi
}

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

# A server without a desktop (no display, not booting to graphical.target)
# has no use for the GTK window: only `remote-control cli`. Its toolkit and
# the Wayland clipboard stay out.
HEADLESS=0
if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && ! systemctl get-default 2>/dev/null | grep -q graphical; then
  HEADLESS=1
  # ImageMagick only draws the launcher's icons.
  GUI_PKGS=" python3-gobject gtk4 libadwaita python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 wl-clipboard imagemagick ImageMagick "
  without_gui() {
    local p
    for p in "$@"; do
      [[ "$GUI_PKGS" == *" $p "* ]] || printf '%s\n' "$p"
    done
  }
  mapfile -t PKGS_dnf < <(without_gui "${PKGS_dnf[@]}")
  mapfile -t PKGS_apt < <(without_gui "${PKGS_apt[@]}")
  mapfile -t PKGS_pacman < <(without_gui "${PKGS_pacman[@]}")
  mapfile -t PKGS_zypper < <(without_gui "${PKGS_zypper[@]}")
fi

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
  for c in git python3 rsync curl desktop-file-install tmux ttyd zsh dig; do
    need_cmd "$c" || return 1
  done
  if ((HEADLESS)); then
    python3 -c "import brotli, fontTools, PIL" >/dev/null 2>&1
  else
    need_cmd wl-copy && python3 -c "$PY_CHECK" >/dev/null 2>&1
  fi
}

# One package at a time where the manager would refuse the whole list over a
# name that does not exist in this distro release.
install_pkgs() {
  if deps_ok; then
    note "ya estaban"
    return
  fi
  local p
  case "$PM" in
    dnf)
      if dnf --version 2>/dev/null | grep -q dnf5; then
        as_root dnf install -y --skip-unavailable "${PKGS_dnf[@]}" || warn "dnf no instaló todo"
      else
        as_root dnf install -y --setopt=strict=0 "${PKGS_dnf[@]}" || warn "dnf no instaló todo"
      fi
      ;;
    apt-get)
      as_root apt-get update -q || true
      # One apt run (one download, one dpkg pass) with what this release has,
      # and without the "recommended" extras (fonts, X, docs…).
      local have=()
      for p in "${PKGS_apt[@]}"; do
        if apt-cache show "$p" >/dev/null 2>&1; then have+=("$p"); else warn "apt: $p no existe aquí"; fi
      done
      as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${have[@]}" >/dev/null ||
        warn "apt no instaló todo"
      ;;
    pacman)
      local have=()
      for p in "${PKGS_pacman[@]}"; do
        if pacman -Si "$p" >/dev/null 2>&1; then have+=("$p"); else warn "pacman: $p no existe aquí"; fi
      done
      as_root pacman -S --needed --noconfirm "${have[@]}" >/dev/null || warn "pacman no instaló todo"
      ;;
    zypper)
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
  log "Apagando ttyd.service (vino con el paquete ttyd; Remote Control usa su propio ttyd)"
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
  quiet_ttyd_service
  if need_cmd ttyd; then
    note "ya estaba"
    return
  fi
  local arch
  case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    aarch64 | arm64) arch=aarch64 ;;
    *) arch="$(uname -m)" ;;
  esac
  install_binary ttyd "https://github.com/tsl0922/ttyd/releases/latest/download/ttyd.$arch" || true
  need_cmd ttyd || [[ -x "$HOME/.local/bin/ttyd" ]] || warn "ttyd no quedó instalado"
}

# Not in Fedora or Debian repos: Cloudflare's own repo, else its release.
install_cloudflared() {
  if need_cmd cloudflared; then
    note "ya estaba"
    return
  fi
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
  need_cmd cloudflared || [[ -x "$HOME/.local/bin/cloudflared" ]] || warn "cloudflared no quedó instalado"
}

# herdr: workspaces for AI agents (its official installer, to ~/.local/bin).
install_herdr() {
  if need_cmd herdr || [[ -x "$HOME/.local/bin/herdr" ]]; then
    note "ya estaba"
    return
  fi
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
  if ((${#pip[@]} == 0)); then
    note "ya estaban"
    return 0
  fi
  note "${pip[*]}"
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

# One line of the summary box: label padded by characters (not bytes, "Túnel").
row() {
  local label="$1"
  shift
  printf '  %s  %s%*s %s\n' "${TEAL}│${R}" "$label" $((15 - ${#label})) "" "$*"
}

# The end: what is there, what is missing, how to start.
summary() {
  local c missing="" optional="" w
  for c in tmux ttyd cloudflared; do
    need_cmd "$c" || [[ -x "$HOME/.local/bin/$c" ]] || missing+=" $c"
  done
  ((HEADLESS)) || python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Adw, Gtk' \
    >/dev/null 2>&1 || missing+=" GTK4/libadwaita(PyGObject)"
  for c in herdr zsh dig $( ((HEADLESS)) || echo wl-copy convert); do
    need_cmd "$c" || [[ -x "$HOME/.local/bin/$c" ]] || optional+=" $c"
  done
  python3 -c "import brotli, fontTools, PIL, textual, segno, cryptography" >/dev/null 2>&1 || optional+=" (módulos python)"
  local onpath=1
  [[ ":$PATH:" == *":$HOME/.local/bin:"* ]] || onpath=0
  if ((UI)); then
    local bar="${TEAL}│${R}"
    printf '\n  %s╭─%s %s%sListo%s %s─────────────────────────────────────────────%s\n' "$TEAL" "$R" "$B" "$GREEN" "$R" "$TEAL" "$R"
    printf '  %s\n' "$bar"
    row "Abrir" "${B}${TEAL}remote-control${R}"
    if [[ "$MODE" == multi ]]; then
      row "Modo" "${TEAL}multi${R}  ${MUTED}(contenedores desde la ventana o  remote-control multi)${R}"
      row "Contenedores" "${TEAL}remote-control multi create NOMBRE${R}"
    fi
    row "Por SSH" "${TEAL}remote-control cli${R}  ${MUTED}(servidor, sin escritorio)${R}"
    ((onpath)) || row "PATH" "${YELLOW}abre otra terminal o corre${R}  exec \$SHELL -l"
    printf '  %s\n' "$bar"
    if [[ -n "$missing" ]]; then
      row "Faltan" "${RED}✗${R}$missing"
    else
      row "Dependencias" "${GREEN}✓ todas instaladas${R}"
    fi
    [[ -z "$optional" ]] || row "Opcionales" "${YELLOW}!${R}$optional"
    row "Auto-update" "${MUTED}cada 3 min y al abrir la app${R}"
    row "Log" "${MUTED}$LOG${R}"
    printf '  %s\n' "$bar"
    printf '  %s╰────────────────────────────────────────────────────────%s\n\n' "$TEAL" "$R"
  else
    log ""
    if [[ -n "$missing" ]]; then log "aviso: faltan dependencias necesarias:$missing"; else log "Dependencias: todas instaladas."; fi
    [[ -z "$optional" ]] || log "aviso: faltan opcionales:$optional"
    log "Listo. Arranca con:  remote-control   (por SSH: remote-control cli)"
    if [[ "$MODE" == multi ]]; then
      log "Modo multi: contenedores desde la ventana o  remote-control multi"
      log "Contenedores:  remote-control multi create NOMBRE   (imagen: $IMAGE_LOG)"
    fi
    ((onpath)) || log "En esta terminal aún no está en PATH: abre otra o corre  exec \$SHELL -l"
    log "Auto-update: cada 3 min + al abrir/enfocar la app."
    log "Para pararlo:  systemctl --user disable --now remote-control-update.timer"
    log "Log: $LOG"
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
    git -C "$SRC" fetch --quiet origin main 2>/dev/null || true
    # Prefer the working tree we launched from.
    printf '%s' "$here" >"$STATE/tree"
    note "desde ${here/#"$HOME"/\~}"
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
  printf '%s' "$SRC" >"$STATE/tree"
  note "$(git -C "$SRC" log -1 --format='%h · %cr' 2>/dev/null)"
}

install_app() {
  local tree
  tree="$(<"$STATE/tree")"
  [[ -f "$tree/app/install.sh" ]] || die "No encuentro app/install.sh en $tree"
  bash "$tree/app/install.sh"
}

# --- uninstall ---------------------------------------------------------------
# install.sh --uninstall [--keep-data] [--yes]   (also `remote-control uninstall`)
# Everything this script and the app put on the machine, in reverse; the
# system packages stay (other programs may use them), and so do the user's
# pasted files (~/Remote Control) and herdr (a tool of its own).

KEEP_DATA=0
ASSUME_YES=0
RCPY=(env PYTHONPATH="$SHARE" python3)
LABEL="dev.garoford.remote-control"

has_package() { [[ -d "$SHARE/remote_control" ]]; }

u_stop() {
  if has_package; then
    "${RCPY[@]}" -c 'from remote_control.tunnel import TunnelService; TunnelService().stop()' || true
    # The "this machine is shared" notes in every profile's CLAUDE.md/AGENTS.md…
    "${RCPY[@]}" -c 'from remote_control import agent_context; agent_context.clear()' || true
  fi
  tmux -L cf-remote kill-server 2>/dev/null || true
  pkill -f "remote_control[.]proxy" 2>/dev/null || true
  pkill -f "ttyd --interface 127[.]0[.]0[.]1" 2>/dev/null || true
  pkill -f "cloudflared tunnel --url http://127[.]0[.]0[.]1" 2>/dev/null || true
}

u_dns() {
  if ((KEEP_DATA)); then
    note "se mantienen (--keep-data)"
    return
  fi
  has_package && [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/remote-control/domain.json" ]] || {
    note "no había"
    return
  }
  # Only records Remote Control made: the containers' names and this machine's.
  "${RCPY[@]}" - <<'PY' || warn "no pude quitar los registros DNS (quítalos en tu proveedor)"
from remote_control import domains, router
cfg = domains.load_config()
gone = []
for box, host in list(router.load_routes().items()):
    router.unassign(box)
    gone.append(host)
if cfg and cfg.host and not cfg.problems():
    prov = domains.provider_for(cfg)
    zone, name = domains.locate(cfg, prov)
    ip = domains.public_ipv4()
    if ip and ("A", ip) in prov.records_at(zone, name):
        prov.delete_record(zone, name, "A")
        gone.append(cfg.host)
    prov.unforward(zone, name)
print("quitados:", ", ".join(gone) or "ninguno")
PY
  note "$(tail -n 1 "$LOG" | sed 's/^quitados: //')"
}

u_containers() {
  if ! need_cmd podman; then
    note "sin Podman"
    return
  fi
  local ids
  ids="$(podman ps -aq --filter "label=$LABEL")"
  [[ -z "$ids" ]] || podman rm -f -v $ids >/dev/null
  podman images -q localhost/remote-control-multi | sort -u | xargs -r podman rmi -f >/dev/null || true
  note "$(printf '%s\n' "$ids" | grep -c . || true) borrados"
}

u_services() {
  local units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  if need_cmd systemctl; then
    systemctl --user disable --now remote-control-update.timer remote-control-update.service \
      remote-control-router.service >/dev/null 2>&1 || true
  fi
  rm -f "$units"/remote-control-update.service "$units"/remote-control-update.timer "$units"/remote-control-router.service
  need_cmd systemctl && systemctl --user daemon-reload 2>/dev/null || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/dev.garoford.RemoteControl-autostart.desktop"
  # The VPS port setting (web_ports) goes back to the system default.
  if [[ -f "$SYSCTL_PORTS" ]]; then
    as_root rm -f "$SYSCTL_PORTS" && as_root sysctl -q -w net.ipv4.ip_unprivileged_port_start=1024 \
      || warn "no pude quitar $SYSCTL_PORTS"
  fi
  # The *.trycloudflare.com DNS route (tunnel.py) is system-wide.
  local dropin=/etc/systemd/resolved.conf.d/99-remote-control-trycloudflare.conf
  if [[ -f "$dropin" ]]; then
    as_root rm -f "$dropin" && { as_root systemctl reload systemd-resolved 2>/dev/null || as_root systemctl restart systemd-resolved 2>/dev/null || true; } \
      || warn "no pude quitar $dropin"
  fi
}

u_files() {
  local data="${XDG_DATA_HOME:-$HOME/.local/share}" cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
  if ((KEEP_DATA)); then
    # Everything but the profiles (their logins) and the settings.
    find "$SHARE" -mindepth 1 -maxdepth 1 ! -name profiles -exec rm -rf {} +
  else
    rm -rf "$SHARE" "$cfg/remote-control"
  fi
  rm -rf "$HOME/.cache/cf-quick-tunnel" "${XDG_STATE_HOME:-$HOME/.local/state}/remote-control"
  rm -f "$HOME/.local/bin/remote-control" "$data/applications/dev.garoford.RemoteControl.desktop"
  find "$data/icons/hicolor" -name 'dev.garoford.RemoteControl.*' -delete 2>/dev/null || true
  need_cmd update-desktop-database && update-desktop-database "$data/applications" >/dev/null 2>&1 || true
}

# The two blocks hook_shells() added (a comment line and the line under it).
u_shell() {
  local rc
  for rc in "$HOME/.bashrc" "${ZDOTDIR:-$HOME}/.zshrc"; do
    [[ -f "$rc" ]] || continue
    sed -i -e '/^# Remote Control: PATH$/{N;d;}' -e '/^# Remote Control: AI CLI profiles and `rc`$/{N;d;}' "$rc"
  done
}

uninstall() {
  local a answer boxes=0 profs=""
  for a in "$@"; do
    case "$a" in
      --keep-data) KEEP_DATA=1 ;;
      --yes | -y) ASSUME_YES=1 ;;
    esac
  done
  # Killing the tunnel's tmux would kill this very script.
  if [[ -n "${RC_KEEPHISTORY:-}" || "${TMUX:-}" == */cf-remote,* ]]; then
    die "estás dentro de Remote Control: desinstala desde otra terminal (SSH o la PC misma)."
  fi
  # The log of a run that deletes ~/.cache/remote-control goes elsewhere.
  LOG="${TMPDIR:-/tmp}/remote-control-uninstall.log"
  : >"$LOG"
  need_cmd podman && boxes="$(podman ps -aq --filter "label=$LABEL" | grep -c . || true)"
  [[ -d "$SHARE/profiles" ]] && profs="$(find "$SHARE/profiles" -mindepth 1 -maxdepth 1 -type d -printf '%f ')"

  if ((UI)); then
    printf '\n  %s%s ▶_ %s  %sRemote Control%s\n        %sdesinstalar%s\n\n' "$B" "$RED" "$R" "$B" "$R" "$MUTED" "$R"
    printf '  %sSe borra:%s\n' "$B" "$R"
  else
    printf 'Remote Control: desinstalar\nSe borra:\n'
  fi
  printf '    • la app, el lanzador, el auto-update y el túnel\n'
  ((boxes)) && printf '    • %s%d contenedor(es)%s con todo lo que tienen\n' "$RED" "$boxes" "$R"
  if ((!KEEP_DATA)); then
    printf '    • tu contraseña y la cuenta de dominios (~/.config/remote-control)\n'
    [[ -n "$profs" ]] && printf '    • los perfiles %s%s%scon sus logins de IA\n' "$RED" "$profs" "$R"
    printf '    • los registros DNS que creó Remote Control\n'
  fi
  printf '  %sSe queda:%s paquetes del sistema, herdr y ~/Remote Control (tus archivos)\n' "$B" "$R"
  ((KEEP_DATA)) || printf '  %s(--keep-data conserva la contraseña, la cuenta y los perfiles)%s\n' "$MUTED" "$R"
  printf '\n'
  if ((!ASSUME_YES)); then
    [[ -r /dev/tty ]] || die "sin terminal para confirmar: usa --yes"
    printf '  Escribe %ssi%s para desinstalar: ' "$B" "$R"
    read -r answer </dev/tty || answer=""
    [[ "${answer,,}" == si || "${answer,,}" == sí ]] || { printf '\n  Cancelado: no se borró nada.\n\n'; exit 1; }
    printf '\n'
  fi
  [[ -f /etc/systemd/resolved.conf.d/99-remote-control-trycloudflare.conf || -f "$SYSCTL_PORTS" ]] && ask_sudo
  STEPS=6
  step "Túnel y sesiones" u_stop
  step "Dominio propio" u_dns
  step "Contenedores" u_containers
  step "Servicios" u_services
  step "Archivos" u_files
  step "Shell" u_shell
  if ((UI)); then
    printf '\n  %s╭─%s %s%sDesinstalado%s\n' "$TEAL" "$R" "$B" "$GREEN" "$R"
    row "Se quedó" "paquetes del sistema, herdr, ~/Remote Control"
    ((KEEP_DATA)) && row "Datos" "contraseña, cuenta de dominios y perfiles (--keep-data)"
    row "Reinstalar" "${TEAL}curl -fsSL …/install.sh | bash${R}"
    row "Log" "${MUTED}$LOG${R}"
    printf '  %s╰────────────────────────────────────────────────────────%s\n\n' "$TEAL" "$R"
  else
    log "Desinstalado. Se quedó: paquetes del sistema, herdr y ~/Remote Control. Log: $LOG"
  fi
  exit 0
}

for arg in "$@"; do
  [[ "$arg" == --uninstall ]] && uninstall "$@"
done

# Normal: Remote Control on this system. Multi: isolated containers (multi.py),
# each with its own tunnel, managed from the window or `remote-control multi`.
MODE="${REMOTE_CONTROL_MODE:-}"
for arg in "$@"; do
  case "$arg" in
    --multi) MODE=multi ;;
    --normal) MODE=normal ;;
  esac
done

choose_mode() {
  [[ -n "$MODE" ]] && return
  MODE=normal
  ((UI)) && [[ -r /dev/tty ]] || return 0
  local pick
  printf '  %s¿Cómo lo instalo?%s\n\n' "$B" "$R"
  printf '    %s1%s  %sNormal%s   conecta y comparte\n' "$TEAL" "$R" "$B" "$R"
  printf '    %s2%s  %sMulti%s    Podman, %srecomendado para servidores%s\n\n' "$TEAL" "$R" "$B" "$R" "$GREEN" "$R"
  printf '  Elige %s[1]%s: ' "$MUTED" "$R"
  read -r pick </dev/tty || pick=""
  [[ "$pick" == 2 || "${pick,,}" == multi ]] && MODE=multi
  printf '\n'
}

install_podman() {
  if need_cmd podman; then
    note "ya estaba"
    return
  fi
  case "$PM" in
    dnf) as_root dnf install -y podman ;;
    apt-get) as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -q podman ;;
    pacman) as_root pacman -S --needed --noconfirm podman ;;
    zypper) as_root zypper --non-interactive install podman ;;
  esac
  need_cmd podman || die "no pude instalar Podman"
}

# On a VPS (its public IP on its own interface) your own domain is served
# right here (router.py): https://name/ needs 443 and http:// needs 80, which
# a normal user may not open. Let it, for good (sysctl.d). root can already;
# a PC at home is skipped (its router forwards 443 to the fallback port).
web_ports() {
  if ((EUID == 0)); then
    note "root: ya puede"
    return
  fi
  local pub cur
  pub="$(curl -fsS -m 8 https://api.ipify.org 2>/dev/null || true)"
  if [[ -z "$pub" ]] || ! ip -4 -o addr 2>/dev/null | grep -qw -- "$pub"; then
    note "no es un VPS, se omite"
    return
  fi
  cur="$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo 1024)"
  if ((cur <= 80)); then
    note "ya estaban"
    return
  fi
  printf '%s\n' "# Remote Control: HTTPS (443) and its redirect (80) without root" \
    "net.ipv4.ip_unprivileged_port_start = 80" | as_root tee "$SYSCTL_PORTS" >/dev/null
  as_root sysctl -q -w net.ipv4.ip_unprivileged_port_start=80
  note "80 y 443 sin root"
}

# No container is made here: the user creates them from the window or the
# CLI. The image they start from (a whole Linux, a few minutes) is built in
# the background meanwhile, so the installer ends right away and the first
# create finds it ready (or waits for that same build).
IMAGE_LOG="$HOME/.cache/remote-control/image.log"
multi_ready() {
  local rc="$HOME/.local/bin/remote-control"
  "$rc" multi on >/dev/null
  setsid nohup "$rc" multi prepare --base fedora >"$IMAGE_LOG" 2>&1 </dev/null &
  note "imagen preparándose en segundo plano"
}

banner
choose_mode
deps_ok && need_cmd ttyd && need_cmd cloudflared && [[ "$MODE" != multi ]] || ask_sudo
STEPS=8
[[ "$MODE" == multi ]] && STEPS=11
step "Paquetes del sistema" install_pkgs
step "ttyd" install_ttyd
step "cloudflared" install_cloudflared
step "herdr" install_herdr
step "Módulos de Python" install_py_fallbacks
step "Código de Remote Control" checkout_src
step "App y lanzador" install_app
step "PATH y perfiles de la shell" hook_shells
if [[ "$MODE" == multi ]]; then
  step "Podman" install_podman
  step "Puertos web (VPS)" web_ports
  step "Modo multi" multi_ready
fi
summary
