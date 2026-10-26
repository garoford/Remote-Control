#!/usr/bin/env bash
set -euo pipefail

# ttyd forks each connection with the pty masters of its other connections
# still open (no CLOEXEC), and tmux clients started here would keep them.
# A browser tab that closes then leaves a terminal nobody reads: the tmux
# server blocks writing to it and every tab, on every device, freezes.
# Keep only stdio (255 is bash reading this script).
for fd in /proc/$$/fd/*; do
  fd="${fd##*/}"
  if (( fd > 2 && fd != 255 )); then
    eval "exec ${fd}>&-" 2>/dev/null || true
  fi
done

# ttyd --url-arg passes ?arg= from the tab. Only accept our token shape.
name="${1:-}"
if [[ ! "$name" =~ ^rc[a-z0-9]{10,32}$ ]]; then
  printf '\r\nSin pestaña.\r\n'
  exit 0
fi

conf="${XDG_CACHE_HOME:-$HOME/.cache}/cf-quick-tunnel/tmux.tab.conf"
socket="cf-remote"

args=(-L "$socket")
if [[ -f "$conf" ]]; then
  args+=(-f "$conf")
fi

run_dir="$(dirname "$conf")"

# Second ?arg= is the browser's device id (extra_keys.js). The device that
# opened the tab owns its pane. When that pane runs herdr, any other device
# gets its own herdr client in a side session, so switching spaces or tabs on
# one device does not move the others (herdr >= 0.9 keeps views per client).
dev="${2:-}"
if [[ ! "$dev" =~ ^d[a-z0-9]{8,32}$ ]]; then
  dev=""
fi

# Third ?arg= is the browser's profile ("p" + id, "p" alone is the primary).
# The terminal size belongs to the profile that opened the tab: its devices
# resize it (tmux.tab.conf hooks), anyone else attaches with ignore-size.
prof="${3:-}"
if [[ ! "$prof" =~ ^p([a-z0-9][a-z0-9_-]{0,31})?$ ]]; then
  prof=""
fi

# Connecting never creates a tab: the server starts it when someone asks for
# one (tab_session.js → /rc-fs/op openTab). A missing session was closed from
# some device, or tmux restarted; the page moves to a live tab or to its
# empty screen (tab_session.js).
if ! tmux "${args[@]}" has-session -t "=$name" 2>/dev/null; then
  if [[ -e "$run_dir/closed/$name" ]]; then
    printf '\r\nEsta pestaña se cerró.\r\n'
  else
    printf '\r\nEsta pestaña ya no existe.\r\n'
  fi
  exit 0
fi

size_of() {
  tmux "${args[@]}" display-message -p -t "=$1:" '#{window_width} #{window_height}' 2>/dev/null || true
}

# A window that is not "manual" follows whoever attaches, ignore-size or not.
# Pin it at its current size; the owner's clients resize it from the hooks.
pin_size() {
  local mode w h
  mode="$(tmux "${args[@]}" show-options -w -t "=$1:" -qv window-size 2>/dev/null || true)"
  [[ "$mode" == "manual" ]] && return 0
  read -r w h <<<"$(size_of "$1")"
  [[ -n "${w:-}" ]] && tmux "${args[@]}" resize-window -t "=$1:" -x "$w" -y "$h" 2>/dev/null || true
}

attach_flags=()
owner_prof="$(tmux "${args[@]}" show-options -t "=$name:" -qv @rc_prof 2>/dev/null || true)"
if [[ -z "$owner_prof" && -n "$prof" ]]; then
  # The first profile to connect owns the tab: whoever asked for it, or
  # whoever comes back first to a tab opened before this existed.
  tmux "${args[@]}" set-option -t "=$name:" @rc_prof "$prof" 2>/dev/null || true
  owner_prof="$prof"
fi
if [[ -n "$prof" && "$prof" != "$owner_prof" ]]; then
  attach_flags=(-f ignore-size)
fi
# Whose this client is, so its ignore-size can follow when the tab changes
# owner (herdr.set_tab_owner). The client's name is this terminal.
client_tty="$(tty 2>/dev/null || true)"
if [[ -n "$prof" && "$client_tty" == /dev/* ]]; then
  tmux "${args[@]}" set-option -s "@rc_cprof_${client_tty//[^a-z0-9]/_}" "$prof" 2>/dev/null || true
fi
pin_size "$name"

pane_cmd() {
  tmux "${args[@]}" display-message -p -t "=$name:" '#{pane_current_command}' 2>/dev/null || true
}

herdr_bin="$(command -v herdr || echo "$HOME/.local/bin/herdr")"

# This device's own herdr client. herdr is exempt from the owner's size:
# each device's view takes its own screen size, whatever its profile.
make_view() {
  local w h
  if ! tmux "${args[@]}" has-session -t "=$view" 2>/dev/null; then
    read -r w h <<<"$(size_of "$name")"
    tmux "${args[@]}" new-session -d -s "$view" -x "${w:-80}" -y "${h:-24}" -- env -u HERDR_ENV \
      -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_SOCKET_PATH "$herdr_bin" \
      2>/dev/null || return 1
  fi
}

if [[ -n "$dev" ]]; then
  view="hv-$dev-$name"
  owner="$(tmux "${args[@]}" show-options -t "=$name:" -qv @rc_dev 2>/dev/null || true)"
  if [[ -z "$owner" ]]; then
    tmux "${args[@]}" set-option -t "=$name:" @rc_dev "$dev" 2>/dev/null || true
    owner="$dev"
  fi
  if [[ "$owner" != "$dev" && -x "$herdr_bin" ]]; then
    # Any other device never shares the pane while it runs herdr, even when
    # herdr starts after it attached: a watcher moves this client to its own
    # view (switch-client, no detach, nothing printed). The view sizes to
    # this screen, so the client drops ignore-size first.
    me="$(tty 2>/dev/null || true)"
    shell_pid=$$
    (
      while sleep 1; do
        kill -0 "$shell_pid" 2>/dev/null || exit 0
        tmux "${args[@]}" has-session -t "=$name" 2>/dev/null || exit 0
        # display-message -c ignores the client for formats (it answers for
        # the most recently active one), so look this client up by name.
        where="$(tmux "${args[@]}" list-clients -F '#{client_name} #{client_session}' 2>/dev/null \
          | awk -v me="$me" '$1 == me { print $2 }' || true)"
        if [[ "$where" == "$name" && "$(pane_cmd)" == "herdr" ]] && make_view; then
          tmux "${args[@]}" refresh-client -t "$me" -f '!ignore-size' 2>/dev/null || true
          tmux "${args[@]}" switch-client -c "$me" -t "=$view" 2>/dev/null || true
        fi
      done
    ) &
    watcher=$!
    # One attach per pass. It returns only when the session it is on ends:
    # this device's herdr client quit (a new view opens if the tab still
    # runs herdr) or the tab was closed.
    while tmux "${args[@]}" has-session -t "=$name" 2>/dev/null; do
      # The browser went away (ttyd closed the terminal): stop, do not spin.
      [[ -t 0 ]] || break
      if [[ "$(pane_cmd)" == "herdr" ]] && make_view; then
        tmux "${args[@]}" attach-session -t "=$view" || true
      else
        tmux "${args[@]}" attach-session "${attach_flags[@]}" -t "=$name" || true
      fi
      sleep 0.5
    done
    kill "$watcher" 2>/dev/null || true
    exit 0
  fi
  tmux "${args[@]}" kill-session -t "=$view" 2>/dev/null || true
fi

# herdr is exempt from the owner's size: the device that runs it sizes it.
[[ "$(pane_cmd)" == "herdr" ]] && attach_flags=()

exec tmux "${args[@]}" attach-session "${attach_flags[@]}" -t "=$name"
