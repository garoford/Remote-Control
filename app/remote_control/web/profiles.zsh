# Remote Control — profiles for AI CLIs.
#
# Loaded from ~/.zshrc (one line) so it also works inside herdr panes. A new
# AI CLI started here runs with the account of the profile active in Remote
# Control: the primary user as always, or an extra profile whose folder
# becomes the CLI's HOME (so it logs in and keeps its tokens apart). Programs
# already running are never touched; herdr, tmux and shells are never wrapped.
# With no extra profile active the CLI runs exactly as before.

[[ -n "$_RC_PROFILES_LOADED" ]] && return
_RC_PROFILES_LOADED=1

_RC_BASE="$HOME/.local/share/remote-control"
_RC_ACTIVE="$_RC_BASE/profile-active"

# `rc agents` and friends (agents.py). herdr belongs to the primary user: its
# socket stays reachable from a CLI that runs with a profile's HOME.
[[ -d "$_RC_BASE/bin" ]] && path=("$_RC_BASE/bin" $path)
# herdr's installer puts it in ~/.local/bin, not on every distro's PATH.
(( ${path[(Ie)$HOME/.local/bin]} )) || path+=("$HOME/.local/bin")
export HERDR_SOCKET_PATH="${HERDR_SOCKET_PATH:-$HOME/.config/herdr/herdr.sock}"

_rc_cli() {
  local cli="$1"
  shift
  # An explicit RC_PROFILE wins, empty included (the primary user): an agent
  # launching another one picks its account (agent_context.py), and a CLI's
  # own shells keep the account it runs with.
  local id="${RC_PROFILE-}" home=""
  if (( ! ${+RC_PROFILE} )) && [[ -r "$_RC_ACTIVE" ]]; then
    IFS=$'\t' read -r id home < "$_RC_ACTIVE"
  fi
  if [[ -n "$id" ]]; then
    home="$_RC_BASE/profiles/$id"
    [[ "$id" =~ '^[a-z0-9][a-z0-9_-]*$' && -d "$home" ]] || { id=""; home=""; }
  fi
  # Remote Control tabs also keep Cursor Agent's scrollback (rc_keephistory.py).
  local -a pre
  pre=()
  if [[ ( "$cli" == agent || "$cli" == cursor-agent ) && -r "$RC_KEEPHISTORY" ]] && (( $+commands[python3] )); then
    pre=(python3 "$RC_KEEPHISTORY")
  fi
  if [[ -z "$id" ]]; then
    RC_PROFILE="" ARGV0="$cli" command "${pre[@]}" "$cli" "$@"
    return
  fi
  HOME="$home" \
    XDG_CONFIG_HOME="$home/.config" \
    XDG_DATA_HOME="$home/.local/share" \
    XDG_STATE_HOME="$home/.local/state" \
    XDG_CACHE_HOME="$home/.cache" \
    RC_PROFILE="$id" \
    DISABLE_AUTOUPDATER=1 \
    ARGV0="$cli" \
    command "${pre[@]}" "$cli" "$@"
}

for _rc_name in claude agent cursor-agent codex gemini agy aider opencode crush goose qwen amp copilot; do
  (( $+commands[$_rc_name] )) || continue
  eval "function $_rc_name { _rc_cli $_rc_name \"\$@\" }"
done
unset _rc_name
