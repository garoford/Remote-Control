# Remote Control tab shells start here (ZDOTDIR points at this folder) so they
# can wrap CLIs that wipe the scrollback. Each file loads the user's own zsh
# file first; the user's setup stays exactly as it is.
RC_ZDOT="$ZDOTDIR"
ZDOTDIR="$HOME"
[[ -r "$HOME/.zshenv" ]] && source "$HOME/.zshenv"
# .zshenv may move the rest of the user's files somewhere else.
RC_USER_ZDOT="${ZDOTDIR:-$HOME}"
ZDOTDIR="$RC_ZDOT"
