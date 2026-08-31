#!/usr/bin/env bash
set -euo pipefail

# ttyd --url-arg passes ?arg= from the tab. Only accept our token shape.
name="${1:-}"
if [[ ! "$name" =~ ^rc[a-z0-9]{10,32}$ ]]; then
  name="rcanon${$}"
fi

shell="${RC_SHELL:-${SHELL:-/bin/bash}}"
conf="${XDG_CACHE_HOME:-$HOME/.cache}/cf-quick-tunnel/tmux.tab.conf"
socket="cf-remote"

if [[ ! -x "$shell" ]]; then
  shell="/bin/bash"
fi

args=(-L "$socket")
if [[ -f "$conf" ]]; then
  args+=(-f "$conf")
fi

exec tmux "${args[@]}" new-session -A -s "$name" -- "$shell" -il
