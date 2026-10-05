#!/bin/bash
# Espera a que termine rclone y luego borra el binario, la config y temporales.
# No desmonta Windows ni borra nada de Google Drive.
set -u

log() { printf '%s %s\n' "$(date '+%F %T')" "$*"; }

log "esperando a que rclone termine..."
while pgrep -x rclone >/dev/null 2>&1; do
  sleep 10
done
log "rclone ya no está corriendo; limpiando"

rm -f -- "$HOME/.local/bin/rclone"
rm -rf -- "$HOME/.config/rclone"
rm -f -- /tmp/rclone.zip /tmp/rclone-imgs.log /tmp/rclone-imgs.out /tmp/rc.b64
rm -rf -- /tmp/rclone-*-linux-amd64
rm -f -- /tmp/copyimgs.sh /tmp/start-rclone.sh /tmp/sendvid.py

if command -v rclone >/dev/null 2>&1; then
  log "aviso: sigue habiendo un rclone en PATH: $(command -v rclone)"
else
  log "ok: no queda rclone en PATH"
fi

log "limpieza lista"
