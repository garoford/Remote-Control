ZDOTDIR="$RC_USER_ZDOT"
[[ -r "$RC_USER_ZDOT/.zlogin" ]] && source "$RC_USER_ZDOT/.zlogin"
# Shells started from here (subshells, scripts) use the user's files only.
[[ "$RC_USER_ZDOT" == "$HOME" ]] && unset ZDOTDIR || ZDOTDIR="$RC_USER_ZDOT"
unset RC_ZDOT RC_USER_ZDOT
