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

# The profile this terminal belongs to: sets id (empty for the primary user)
# and home (that profile's folder). Callers declare both local.
_rc_profile() {
  # The account is the terminal's owner's, never the device that typed last
  # (that only decides who owns the terminals a device opens):
  # - an explicit RC_PROFILE, empty included (the primary user): an agent
  #   launching another one picks its account (agent_context.py), and a
  #   CLI's own shells keep the account it runs with;
  # - a herdr pane: its owner, the profile on its mark (pane-profile/<pane>,
  #   herdr.py). A pane with no owner yet is brand new: it belongs to the
  #   profile that just opened it, the active one, noted here for good;
  # - a Remote Control tab: its owner, @rc_prof on its tmux session;
  # - any other terminal: the primary user.
  local pin=""
  id="" home=""
  if (( ${+RC_PROFILE} )); then
    id="$RC_PROFILE"
  elif [[ "${HERDR_PANE_ID-}" =~ '^w[0-9A-Za-z]+:p[0-9A-Za-z]+$' ]]; then
    pin="$_RC_BASE/pane-profile/$HERDR_PANE_ID"
    if [[ -f "$pin" ]]; then
      IFS= read -r id < "$pin"
    else
      [[ -r "$_RC_ACTIVE" ]] && IFS=$'\t' read -r id home < "$_RC_ACTIVE"
      mkdir -p "${pin:h}" 2>/dev/null && print -r -- "$id" > "$pin" 2>/dev/null
    fi
  elif [[ "${TMUX-}" == */cf-remote,* ]]; then
    id="$(tmux show-options -qv @rc_prof 2>/dev/null)"
    id="${id#p}"
  fi
  if [[ -n "$id" ]]; then
    home="$_RC_BASE/profiles/$id"
    [[ "$id" =~ '^[a-z0-9][a-z0-9_-]*$' && -d "$home" ]] || { id=""; home=""; }
  fi
}

_rc_cli() {
  local cli="$1"
  shift
  local id="" home=""
  # Sounds of a Remote Control terminal go to its devices, not this
  # computer's speakers (sound.py records that silent sink).
  if [[ -n "${HERDR_PANE_ID-}" || "${TMUX-}" == */cf-remote,* ]]; then
    local -x PULSE_SINK=rc_terminal_audio
  fi
  _rc_profile
  # Every profile's chats show up in everyone's resume list
  # (rc_sharechats.py hardlinks them across HOMEs).
  if [[ -r "$_RC_BASE/rc_sharechats.py" ]] && (( $+commands[python3] )); then
    python3 "$_RC_BASE/rc_sharechats.py" "$_RC_BASE" 2>/dev/null
  fi
  # Remote Control tabs also keep Cursor Agent's scrollback (rc_keephistory.py).
  # Not in herdr panes: herdr spots agents by their process, and behind the
  # python3 wrapper it would not see Cursor Agent (nor would `rc agents`).
  local -a pre
  pre=()
  if [[ ( "$cli" == agent || "$cli" == cursor-agent ) && -z "${HERDR_PANE_ID-}" && -r "$RC_KEEPHISTORY" ]] && (( $+commands[python3] )); then
    pre=(python3 "$RC_KEEPHISTORY")
  fi
  if [[ -z "$id" ]]; then
    RC_PROFILE="" ARGV0="$cli" command "${pre[@]}" "$cli" "$@"
    return
  fi
  # Its agents' git and rsync sign in with the profile's SSH keys too.
  local -a ssh_opts
  _rc_ssh_opts
  if (( ${#ssh_opts} )); then
    local -x GIT_SSH_COMMAND="ssh ${(j: :)${(@q)ssh_opts}}" RSYNC_RSH="ssh ${(j: :)${(@q)ssh_opts}}"
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

# Credentials are the profile's own.
# - git and gh: its ~/.gitconfig (identity, aliases, credential helper and the
#   credentials it stores) and its gh login live in its HOME, the same one its
#   AI CLIs run with.
# - SSH: ssh never reads $HOME (it takes the system user's ~/.ssh), so a
#   profile with keys of its own in its HOME's .ssh gets them passed
#   explicitly, and only them: git clone/pull/push, ssh, scp, sftp and rsync
#   then sign in as that user, never as the primary. A profile with no keys of
#   its own keeps using the system's. ssh-keygen there makes its key in the
#   profile's .ssh (by default it would overwrite the primary's).

# The ssh options for $home's keys (into the caller's ssh_opts), none if it has none.
_rc_ssh_opts() {
  ssh_opts=()
  local dir="$home/.ssh" k
  local -a keys
  for k in "$dir"/id_*(N.); do
    [[ "$k" == *.pub ]] || keys+=("$k")
  done
  (( ${#keys} )) || return 0
  # Its config, or none: the system's could add the primary's IdentityFile.
  if [[ -f "$dir/config" ]]; then
    ssh_opts=(-F "$dir/config")
  else
    ssh_opts=(-F /dev/null)
  fi
  ssh_opts+=(-o IdentitiesOnly=yes -o IdentityAgent=none)
  for k in $keys; do
    ssh_opts+=(-i "$k")
  done
  # Hosts it trusts first (new ones are noted there), then the system's.
  ssh_opts+=(-o "UserKnownHostsFile=$dir/known_hosts $HOME/.ssh/known_hosts")
}

_rc_vcs() {
  local tool="$1"
  shift
  local id="" home=""
  local -a ssh_opts
  _rc_profile
  if [[ -z "$id" ]]; then
    command "$tool" "$@"
    return
  fi
  _rc_ssh_opts
  if (( ${#ssh_opts} )); then
    HOME="$home" XDG_CONFIG_HOME="$home/.config" GIT_SSH_COMMAND="ssh ${(j: :)${(@q)ssh_opts}}" command "$tool" "$@"
  else
    HOME="$home" XDG_CONFIG_HOME="$home/.config" command "$tool" "$@"
  fi
}

_rc_ssh() {
  local tool="$1"
  shift
  local id="" home=""
  local -a ssh_opts
  _rc_profile
  [[ -n "$id" ]] && _rc_ssh_opts
  if (( ! ${#ssh_opts} )); then
    command "$tool" "$@"
  elif [[ "$tool" == rsync ]]; then
    RSYNC_RSH="ssh ${(j: :)${(@q)ssh_opts}}" command rsync "$@"
  else
    command "$tool" "${ssh_opts[@]}" "$@"
  fi
}

_rc_keygen() {
  local id="" home="" arg prev="" kind=ed25519
  _rc_profile
  # Making a key (no -f, and not -l/-R/-F… on an existing one) in a profile:
  # its .ssh, not the primary's key.
  if [[ -n "$id" ]] && ! (( ${@[(I)-[flRFyeiDK]*]} )); then
    mkdir -p "$home/.ssh" && chmod 700 "$home/.ssh"
    for arg in "$@"; do
      [[ "$prev" == -t ]] && kind="$arg"
      prev="$arg"
    done
    command ssh-keygen "$@" -f "$home/.ssh/id_$kind"
    return
  fi
  command ssh-keygen "$@"
}

for _rc_name in git gh; do
  (( $+commands[$_rc_name] )) || continue
  eval "function $_rc_name { _rc_vcs $_rc_name \"\$@\" }"
done
for _rc_name in ssh scp sftp rsync; do
  (( $+commands[$_rc_name] )) || continue
  eval "function $_rc_name { _rc_ssh $_rc_name \"\$@\" }"
done
(( $+commands[ssh-keygen] )) && function ssh-keygen { _rc_keygen "$@" }

for _rc_name in claude agent cursor-agent codex gemini agy aider opencode crush goose qwen amp copilot pi; do
  (( $+commands[$_rc_name] )) || continue
  eval "function $_rc_name { _rc_cli $_rc_name \"\$@\" }"
done
unset _rc_name
