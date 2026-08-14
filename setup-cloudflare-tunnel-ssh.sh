#!/usr/bin/env bash
# Cloudflare Quick Tunnel (trycloudflare.com) + terminal web (ttyd).
# SIN dominio propio. SIN cuenta Cloudflare obligatoria.
#
# Uso:
#   ./setup-cloudflare-tunnel-ssh.sh
#   ./setup-cloudflare-tunnel-ssh.sh 7681
#
# Abre un shell en el navegador vía https://xxxx.trycloudflare.com
# (Quick Tunnel solo habla HTTP; no se puede exponer SSH crudo sin dominio.)
# La sesión es tmux `cf-remote` + zsh + Oh My Posh (night-owl) + FiraCode Nerd Font.

set -euo pipefail

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
CYAN=$'\033[0;36m'
BOLD=$'\033[1m'
NC=$'\033[0m'

info()  { printf '%s\n' "${CYAN}[INFO]${NC} $*"; }
ok()    { printf '%s\n' "${GREEN}[OK]${NC} $*"; }
warn()  { printf '%s\n' "${YELLOW}[WARN]${NC} $*"; }
err()   { printf '%s\n' "${RED}[ERROR]${NC} $*" >&2; }

PORT="${1:-7681}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  err "Puerto inválido: $PORT"
  exit 1
fi

if [[ -n "${SUDO_USER:-}" ]]; then
  REAL_USER="$SUDO_USER"
  REAL_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
else
  REAL_USER="$(id -un)"
  REAL_HOME="$HOME"
fi

if [[ "$REAL_USER" == "root" ]]; then
  err "Ejecuta como usuario normal (el script usa sudo solo cuando hace falta)."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMUX_CONF="$SCRIPT_DIR/tmux.night-owl.conf"
TMUX_SESSION="cf-remote"
TMUX_SOCKET="cf-remote"

RUN_DIR="$REAL_HOME/.cache/cf-quick-tunnel"
FONT_DIR="$RUN_DIR/fonts"
LOG_FILE="$RUN_DIR/cloudflared.log"
URL_FILE="$RUN_DIR/PUBLIC-URL.txt"
PID_DIR="$RUN_DIR/pids"
TTYD_PID_FILE="$PID_DIR/ttyd.pid"
CF_PID_FILE="$PID_DIR/cloudflared.pid"
TTYD_INDEX="$RUN_DIR/ttyd-index.html"
FONT_REG_TTF="$REAL_HOME/.local/share/fonts/FiraCode/FiraCodeNerdFontMono-Regular.ttf"
FONT_BOLD_TTF="$REAL_HOME/.local/share/fonts/FiraCode/FiraCodeNerdFontMono-Bold.ttf"
FONT_REG_WOFF="$FONT_DIR/FiraCodeNerdFontMono-Regular.woff2"
FONT_BOLD_WOFF="$FONT_DIR/FiraCodeNerdFontMono-Bold.woff2"

# Night Owl — misma paleta que Oh My Posh
NIGHT_OWL_THEME='{"background":"#011627","foreground":"#d6deeb","cursor":"#80A4C2","cursorAccent":"#011627","selectionBackground":"#1d3b53","selectionInactiveBackground":"#0b2942","black":"#011627","red":"#EF5350","green":"#22DA6E","yellow":"#ADDB67","blue":"#82AAFF","magenta":"#C792EA","cyan":"#21C7A8","white":"#FFFFFF","brightBlack":"#575656","brightRed":"#EF5350","brightGreen":"#22DA6E","brightYellow":"#FFEB95","brightBlue":"#82AAFF","brightMagenta":"#C792EA","brightCyan":"#7FDBCA","brightWhite":"#FFFFFF"}'

mkdir -p "$RUN_DIR" "$PID_DIR" "$FONT_DIR"

cleanup_old() {
  if [[ -f "$TTYD_PID_FILE" ]] && kill -0 "$(cat "$TTYD_PID_FILE")" 2>/dev/null; then
    info "Matando ttyd anterior (pid $(cat "$TTYD_PID_FILE"))..."
    kill "$(cat "$TTYD_PID_FILE")" 2>/dev/null || true
  fi
  if [[ -f "$CF_PID_FILE" ]] && kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
    info "Matando cloudflared anterior (pid $(cat "$CF_PID_FILE"))..."
    kill "$(cat "$CF_PID_FILE")" 2>/dev/null || true
  fi
  # por si quedaron huérfanos de corridas previas
  pkill -f "ttyd .*localhost:${PORT}" 2>/dev/null || true
  pkill -f "ttyd --interface 127.0.0.1 --port ${PORT}" 2>/dev/null || true
  pkill -f "cloudflared tunnel --url http://127.0.0.1:${PORT}" 2>/dev/null || true
  # Socket propio: el tmux default hereda POSH_*/CURSOR_* de Cursor y OMP no inicia.
  tmux -L "$TMUX_SOCKET" kill-server 2>/dev/null || true
  rm -f "$TTYD_PID_FILE" "$CF_PID_FILE" "$LOG_FILE" "$URL_FILE"
}

install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    ok "cloudflared ya instalado: $(cloudflared --version 2>&1 | head -n1)"
    return
  fi
  info "Instalando cloudflared..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) CF_ARCH="amd64" ;;
    aarch64|arm64) CF_ARCH="arm64" ;;
    *) err "Arquitectura no soportada: $ARCH"; exit 1 ;;
  esac
  TMP_RPM="$(mktemp /tmp/cloudflared-XXXXXX.rpm)"
  curl -fsSL -o "$TMP_RPM" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.rpm"
  sudo dnf install -y "$TMP_RPM"
  rm -f "$TMP_RPM"
  ok "cloudflared instalado"
}

install_ttyd() {
  if command -v ttyd >/dev/null 2>&1; then
    ok "ttyd ya instalado: $(ttyd --version 2>&1 | head -n1)"
    return
  fi
  info "Instalando ttyd..."
  if sudo dnf install -y ttyd; then
    ok "ttyd instalado vía dnf"
    return
  fi

  warn "dnf no trajo ttyd; bajando binario de GitHub..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) TTYD_ASSET="ttyd.x86_64" ;;
    aarch64|arm64) TTYD_ASSET="ttyd.aarch64" ;;
    *) err "Arquitectura no soportada para ttyd: $ARCH"; exit 1 ;;
  esac
  TMP_BIN="$(mktemp /tmp/ttyd-XXXXXX)"
  curl -fsSL -o "$TMP_BIN" \
    "https://github.com/tsl0922/ttyd/releases/latest/download/${TTYD_ASSET}"
  chmod +x "$TMP_BIN"
  sudo install -m 755 "$TMP_BIN" /usr/local/bin/ttyd
  rm -f "$TMP_BIN"
  ok "ttyd instalado en /usr/local/bin/ttyd"
}

install_tmux_conf() {
  if [[ ! -f "$TMUX_CONF" ]]; then
    err "Falta $TMUX_CONF"
    exit 1
  fi
  # Solo `tmux -f` este archivo. No pisar ~/.tmux.conf del usuario.
  ok "tmux del túnel: $TMUX_CONF"
}

wait_for_url() {
  local tries=60
  local i url
  for ((i = 1; i <= tries; i++)); do
    if [[ -f "$LOG_FILE" ]]; then
      url="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | tail -n1 || true)"
      if [[ -n "$url" ]]; then
        echo "$url"
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

ensure_woff2_fonts() {
  if [[ -s "$FONT_REG_WOFF" && -s "$FONT_BOLD_WOFF" ]]; then
    return 0
  fi
  if [[ ! -s "$FONT_REG_TTF" || ! -s "$FONT_BOLD_TTF" ]]; then
    warn "No encontré FiraCode Nerd Font Mono en $REAL_HOME/.local/share/fonts/FiraCode"
    return 1
  fi
  info "Convirtiendo FiraCode Nerd Font Mono a woff2 (una vez)..."
  python3 - "$FONT_REG_TTF" "$FONT_REG_WOFF" "$FONT_BOLD_TTF" "$FONT_BOLD_WOFF" <<'PY'
import sys
from fontTools.ttLib import TTFont
for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    font = TTFont(src)
    font.flavor = "woff2"
    font.save(dst)
PY
  ok "Fuentes woff2 en $FONT_DIR"
}

# Index de ttyd + FiraCode Nerd Font embebida + fondo Night Owl.
prepare_ttyd_index() {
  local probe_port tpid i
  probe_port="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"
  ttyd --interface 127.0.0.1 --port "$probe_port" /bin/true >/dev/null 2>&1 &
  tpid=$!
  for ((i = 1; i <= 30; i++)); do
    if curl -fsS "http://127.0.0.1:${probe_port}/" -o "$TTYD_INDEX" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  kill "$tpid" 2>/dev/null || true
  wait "$tpid" 2>/dev/null || true

  if [[ ! -s "$TTYD_INDEX" ]]; then
    warn "No pude generar index de ttyd; seguirá sin Nerd Font forzada."
    rm -f "$TTYD_INDEX"
    return 0
  fi

  ensure_woff2_fonts || true

  python3 - "$TTYD_INDEX" "$FONT_REG_WOFF" "$FONT_BOLD_WOFF" <<'PY'
import base64
import sys
from pathlib import Path

path = Path(sys.argv[1])
html = path.read_text(encoding="utf-8", errors="surrogateescape")
reg = Path(sys.argv[2])
bold = Path(sys.argv[3])

if reg.is_file() and bold.is_file():
    src_reg = f"url(data:font/woff2;base64,{base64.b64encode(reg.read_bytes()).decode('ascii')}) format('woff2')"
    src_bold = f"url(data:font/woff2;base64,{base64.b64encode(bold.read_bytes()).decode('ascii')}) format('woff2')"
else:
    cdn = "https://cdn.jsdelivr.net/gh/mshaugh/nerdfont-webfonts@v3.3.0/build/fonts"
    src_reg = f"url('{cdn}/FiraCodeNerdFontMono-Regular.woff2') format('woff2')"
    src_bold = f"url('{cdn}/FiraCodeNerdFontMono-Bold.woff2') format('woff2')"

inject = f"""<style id="cf-remote-theme">
@font-face{{font-family:'FiraCode Nerd Font Mono';font-style:normal;font-weight:400;font-display:block;
src:{src_reg};}}
@font-face{{font-family:'FiraCode Nerd Font Mono';font-style:normal;font-weight:700;font-display:block;
src:{src_bold};}}
html,body{{background:#011627;margin:0;height:100%;}}
body,.xterm,.xterm-viewport,.xterm-rows,.xterm-screen,.xterm-helper-textarea{{
font-family:'FiraCode Nerd Font Mono',ui-monospace,monospace!important;
font-feature-settings:'liga' 1,'calt' 1;
}}
</style>"""
marker = "id=\"cf-remote-theme\""
if marker in html:
    start = html.find("<style id=\"cf-remote-theme\">")
    end = html.find("</style>", start)
    if start != -1 and end != -1:
        html = html[:start] + html[end + len("</style>"):]
if "<head>" in html:
    html = html.replace("<head>", "<head>" + inject, 1)
else:
    html = inject + html
path.write_text(html, encoding="utf-8", errors="surrogateescape")
PY
  ok "UI ttyd Night Owl + FiraCode Nerd Font Mono: $TTYD_INDEX"
}

printf '%s\n' "${BOLD}Cloudflare Quick Tunnel + terminal remota (sin dominio)${NC}"
echo
info "Usuario shell : $REAL_USER"
info "Puerto local  : $PORT (solo localhost)"
info "Sesión tmux   : $TMUX_SESSION (socket $TMUX_SOCKET, zsh + Oh My Posh night-owl)"
warn "Quick Tunnel = HTTP. Control remoto por navegador (ttyd), no SSH crudo."
echo
read -r -p "¿Continuar? [Y/n] " CONFIRM
CONFIRM="${CONFIRM:-Y}"
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  warn "Cancelado."
  exit 0
fi

cleanup_old
install_cloudflared
install_ttyd
install_tmux_conf
prepare_ttyd_index

info "Arrancando ttyd en 127.0.0.1:${PORT} (tmux $TMUX_SESSION)..."
# env -i: si se lanza desde Cursor/un zsh con Oh My Posh, POSH_* / CURSOR_*
# se heredan y `oh-my-posh init` no imprime nada → prompt Fedora [%n@%m].
CLEAN_PATH="$REAL_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
CLEAN_LANG="${LANG:-en_US.UTF-8}"
TTYD_ENV=(
  env -i
  "HOME=$REAL_HOME"
  "USER=$REAL_USER"
  "LOGNAME=$REAL_USER"
  "SHELL=/usr/bin/zsh"
  "TERM=xterm-256color"
  "COLORTERM=truecolor"
  "LANG=$CLEAN_LANG"
  "LC_ALL=$CLEAN_LANG"
  "PATH=$CLEAN_PATH"
)
if [[ -d "/run/user/$(id -u "$REAL_USER")" ]]; then
  TTYD_ENV+=("XDG_RUNTIME_DIR=/run/user/$(id -u "$REAL_USER")")
fi
if [[ -n "${SSH_AUTH_SOCK:-}" && -S "${SSH_AUTH_SOCK:-}" ]]; then
  TTYD_ENV+=("SSH_AUTH_SOCK=$SSH_AUTH_SOCK")
fi

# -W writable; xterm.js: FiraCode + Night Owl; tmux -u UTF-8
TTYD_CMD=(
  "${TTYD_ENV[@]}"
  ttyd
  --interface 127.0.0.1
  --port "$PORT"
  --writable
  --cwd "$REAL_HOME"
  --terminal-type xterm-256color
  -t "fontSize=15"
  -t "fontFamily=FiraCode Nerd Font Mono"
  -t "fontWeight=400"
  -t "fontWeightBold=700"
  -t "cursorBlink=true"
  -t "theme=${NIGHT_OWL_THEME}"
)
if [[ -s "$TTYD_INDEX" ]]; then
  TTYD_CMD+=(--index "$TTYD_INDEX")
fi
TTYD_CMD+=(
  tmux -L "$TMUX_SOCKET" -u -f "$TMUX_CONF"
  new-session -A -s "$TMUX_SESSION"
  -c "$REAL_HOME"
  --
  /usr/bin/zsh -il
)

nohup "${TTYD_CMD[@]}" >"$RUN_DIR/ttyd.log" 2>&1 &
echo $! >"$TTYD_PID_FILE"
sleep 1

if ! kill -0 "$(cat "$TTYD_PID_FILE")" 2>/dev/null; then
  err "ttyd no arrancó. Mira: $RUN_DIR/ttyd.log"
  cat "$RUN_DIR/ttyd.log" >&2 || true
  exit 1
fi
ok "ttyd corriendo (pid $(cat "$TTYD_PID_FILE"))"

info "Creando Cloudflare Quick Tunnel..."
: >"$LOG_FILE"
nohup cloudflared tunnel --url "http://127.0.0.1:${PORT}" \
  >"$LOG_FILE" 2>&1 &
echo $! >"$CF_PID_FILE"
sleep 1

if ! kill -0 "$(cat "$CF_PID_FILE")" 2>/dev/null; then
  err "cloudflared no arrancó. Mira: $LOG_FILE"
  cat "$LOG_FILE" >&2 || true
  exit 1
fi

info "Esperando URL trycloudflare.com..."
PUBLIC_URL="$(wait_for_url || true)"

if [[ -z "${PUBLIC_URL:-}" ]]; then
  err "No salió la URL a tiempo. Log:"
  tail -n 40 "$LOG_FILE" >&2 || true
  exit 1
fi

echo "$PUBLIC_URL" >"$URL_FILE"
chmod 600 "$URL_FILE"

cat <<EOF

${GREEN}${BOLD}========================================
  Quick Tunnel — LISTO
========================================${NC}

${BOLD}URL pública (ábrela en el navegador):${NC}
  ${CYAN}${PUBLIC_URL}${NC}

${BOLD}También guardada en:${NC}
  ${URL_FILE}

${BOLD}En el browser:${NC}
  tmux ${TMUX_SESSION} + zsh + Oh My Posh night-owl
  fuente FiraCode Nerd Font Mono

${BOLD}PIDs:${NC}
  ttyd        : $(cat "$TTYD_PID_FILE")
  cloudflared : $(cat "$CF_PID_FILE")

${BOLD}Parar el túnel (la sesión tmux se queda):${NC}
  kill \$(cat $TTYD_PID_FILE) \$(cat $CF_PID_FILE)

${YELLOW}Nota:${NC} la URL de Quick Tunnel cambia cada vez que reinicias.
      No es SSH clásico; es terminal en el browser (sin dominio).

EOF

info "Logs: tail -f $LOG_FILE"
ok "Listo. Manda esta URL a quien va a conectar: $PUBLIC_URL"
