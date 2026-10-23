ZDOTDIR="$RC_USER_ZDOT"
[[ -r "$RC_USER_ZDOT/.zshrc" ]] && source "$RC_USER_ZDOT/.zshrc"
ZDOTDIR="$RC_ZDOT"

# CLI wrappers (profiles and Cursor Agent's scrollback). ~/.zshrc normally
# loads this file already; the guard inside makes a second load a no-op.
[[ -r "$HOME/.local/share/remote-control/profiles.zsh" ]] && source "$HOME/.local/share/remote-control/profiles.zsh"

# A non-login shell reads nothing after this file.
if [[ ! -o login ]]; then
  [[ "$RC_USER_ZDOT" == "$HOME" ]] && unset ZDOTDIR || ZDOTDIR="$RC_USER_ZDOT"
  unset RC_ZDOT RC_USER_ZDOT
fi
