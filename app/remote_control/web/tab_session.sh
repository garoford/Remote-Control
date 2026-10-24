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

# Second ?arg= is the browser's device id (extra_keys.js). The device that
# opened the tab owns its pane. When that pane runs herdr, any other device
# gets its own herdr client in a side session, so switching spaces or tabs on
# one device does not move the others (herdr >= 0.9 keeps views per client).
dev="${2:-}"
if [[ ! "$dev" =~ ^d[a-z0-9]{8,32}$ ]]; then
  dev=""
fi

# A tab closed from any device stays closed: another device's socket
# reconnecting must not start it again (history.close_session marks it).
if [[ -e "$run_dir/closed/$name" ]] && ! tmux "${args[@]}" has-session -t "=$name" 2>/dev/null; then
  printf '\r\nEsta pestaña se cerró.\r\n'
  exit 0
fi

if [[ -n "$dev" ]] && ! tmux "${args[@]}" has-session -t "=$name" 2>/dev/null; then
  if tmux "${args[@]}" new-session -d "${env_args[@]}" -s "$name" -- "$shell" -il 2>/dev/null; then
    tmux "${args[@]}" set-option -t "=$name:" @rc_dev "$dev" 2>/dev/null || true
  fi
fi

if [[ -n "$dev" ]]; then
  view="hv-$dev-$name"
  owner="$(tmux "${args[@]}" show-options -t "=$name:" -qv @rc_dev 2>/dev/null || true)"
  if [[ -z "$owner" ]]; then
    tmux "${args[@]}" set-option -t "=$name:" @rc_dev "$dev" 2>/dev/null || true
    owner="$dev"
  fi
  cmd="$(tmux "${args[@]}" display-message -p -t "=$name:" '#{pane_current_command}' 2>/dev/null || true)"
  herdr_bin="$(command -v herdr || echo "$HOME/.local/bin/herdr")"
  if [[ "$owner" != "$dev" && "$cmd" == "herdr" && -x "$herdr_bin" ]]; then
    # Returns when this herdr client quits; then show the shared pane.
    tmux "${args[@]}" new-session -A -s "$view" -- env -u HERDR_ENV -u HERDR_PANE_ID \
      -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_SOCKET_PATH "$herdr_bin" || true
    # The tab was closed meanwhile: do not bring it back.
    tmux "${args[@]}" has-session -t "=$name" 2>/dev/null || exit 0
  else
    tmux "${args[@]}" kill-session -t "=$view" 2>/dev/null || true
  fi
fi

exec tmux "${args[@]}" new-session -A "${env_args[@]}" -s "$name" -- "$shell" -il
