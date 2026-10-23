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

# zsh tabs load the Remote Control zdot folder, which wraps CLIs that wipe
# the scrollback (see rc_keephistory.py). Same as workspaces.tab_env_args.
env_args=()
run_dir="$(dirname "$conf")"
if [[ "$(basename "$shell")" == "zsh" && -f "$run_dir/zdot/.zshrc" && -f "$run_dir/rc_keephistory.py" ]]; then
  env_args=(-e "ZDOTDIR=$run_dir/zdot" -e "RC_KEEPHISTORY=$run_dir/rc_keephistory.py")
fi

exec tmux "${args[@]}" new-session -A "${env_args[@]}" -s "$name" -- "$shell" -il
