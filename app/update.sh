#!/usr/bin/env bash
# Refresh the installed copy from origin/main. Does not touch a live tunnel.
# Exit 0  = already current, locked, or network/error (quiet)
# Exit 10 = files were updated (caller may toast)
set -euo pipefail

SHARE="${XDG_DATA_HOME:-$HOME/.local/share}/remote-control"
SRC="$SHARE/src"
LOCK="$SHARE/update.lock"
LOG="$SHARE/update.log"
UPDATED=10

mkdir -p "$SHARE"
exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

log() {
  printf '%s %s\n' "$(date -Iseconds)" "$*" >>"$LOG"
}

trim_log() {
  if [[ -f "$LOG" ]]; then
    tail -n 200 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  fi
}

if [[ ! -d "$SRC/.git" ]]; then
  log "skip: no git clone at $SRC"
  trim_log
  exit 0
fi

if ! git -C "$SRC" fetch --quiet origin main 2>>"$LOG"; then
  log "fetch failed"
  trim_log
  exit 0
fi

LOCAL="$(git -C "$SRC" rev-parse HEAD)"
REMOTE="$(git -C "$SRC" rev-parse origin/main)"
if [[ "$LOCAL" == "$REMOTE" ]]; then
  exit 0
fi

log "updating ${LOCAL:0:12} -> ${REMOTE:0:12}"
git -C "$SRC" checkout -q main 2>>"$LOG" || git -C "$SRC" checkout -q -B main origin/main
git -C "$SRC" reset --hard origin/main >>"$LOG" 2>&1
bash "$SRC/app/install.sh" >>"$LOG" 2>&1
log "updated to $(git -C "$SRC" rev-parse --short HEAD)"
trim_log
exit "$UPDATED"
