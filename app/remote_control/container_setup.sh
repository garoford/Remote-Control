#!/usr/bin/env bash
# Inside a multi container (multi.py), as its user: the same terminal as on
# the PC. zsh with Oh My Posh (Night Owl) and the usual plugins, the command
# line tools, Node and Bun, and the AI CLIs. Run on create and on every
# update; whatever is already there is left alone, so it is quick then.
#
# The settings go in ~/.config/remote-control/zshrc, rewritten each time;
# ~/.zshrc only sources it, so what the user adds there stays.

set -u
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
FAILED=()

have() { command -v "$1" >/dev/null 2>&1; }
step() { printf '%s\n' "$*"; }
try() {
  # One part failing (no network for one site…) does not stop the rest.
  local what="$1"
  shift
  "$@" >/dev/null 2>&1 || FAILED+=("$what")
}

mkdir -p "$HOME/.local/bin" "$HOME/.zsh" "$HOME/.config/oh-my-posh" "$HOME/.config/remote-control"

# --- system packages -------------------------------------------------------------
step "Herramientas del sistema…"
if have dnf; then
  try "paquetes" sudo dnf -y -q install zsh git curl unzip tar which fzf bat ripgrep jq htop nano vim-enhanced less
elif have apt-get; then
  try "apt update" sudo apt-get update -q
  try "paquetes" sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
    zsh git curl unzip tar fzf bat ripgrep jq htop nano vim less
  # Debian/Ubuntu call bat "batcat".
  have bat || { have batcat && ln -sf "$(command -v batcat)" "$HOME/.local/bin/bat"; }
fi

# --- prompt and plugins --------------------------------------------------------
step "Oh My Posh y plugins de zsh…"
have oh-my-posh || try "oh-my-posh" bash -c 'curl -fsSL https://ohmyposh.dev/install.sh | bash -s -- -d "$HOME/.local/bin"'
THEME="$HOME/.config/oh-my-posh/night-owl.omp.json"
[[ -s "$THEME" ]] || try "tema night-owl" curl -fsSL -o "$THEME" \
  https://raw.githubusercontent.com/JanDeDobbeleer/oh-my-posh/main/themes/night-owl.omp.json
for repo in zsh-autosuggestions zsh-syntax-highlighting zsh-history-substring-search zsh-completions; do
  [[ -d "$HOME/.zsh/$repo" ]] || try "$repo" git clone -q --depth 1 "https://github.com/zsh-users/$repo" "$HOME/.zsh/$repo"
done

# --- Node (nvm) and Bun --------------------------------------------------------
step "Node y Bun…"
export NVM_DIR="$HOME/.nvm"
[[ -s "$NVM_DIR/nvm.sh" ]] || try "nvm" bash -c 'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | PROFILE=/dev/null bash'
if [[ -s "$NVM_DIR/nvm.sh" ]]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  have node || try "node" nvm install --lts
fi
[[ -x "$HOME/.bun/bin/bun" ]] || try "bun" bash -c 'curl -fsSL https://bun.sh/install | SHELL=/bin/false bash'

# --- AI CLIs --------------------------------------------------------------------
step "CLIs de IA (Claude Code, Codex, Cursor Agent)…"
have claude || try "claude" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
have codex || { have npm && try "codex" npm install -g @openai/codex; }
have cursor-agent || try "cursor-agent" bash -c 'curl -fsSL https://cursor.com/install | bash'

# --- zsh settings ---------------------------------------------------------------
step "Configuración de zsh…"
cat >"$HOME/.config/remote-control/zshrc" <<'ZSHRC'
# Remote Control (multi container): rewritten on every update. Your own
# lines go in ~/.zshrc, after the line that loads this file.
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"

command -v oh-my-posh >/dev/null && eval "$(oh-my-posh init zsh --config ~/.config/oh-my-posh/night-owl.omp.json)"

HISTFILE=~/.zsh_history
HISTSIZE=50000
SAVEHIST=50000
setopt SHARE_HISTORY HIST_IGNORE_ALL_DUPS HIST_IGNORE_SPACE HIST_REDUCE_BLANKS HIST_VERIFY APPEND_HISTORY INC_APPEND_HISTORY

fpath=(~/.zsh/zsh-completions/src $fpath)
autoload -Uz compinit && compinit
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}'
zstyle ':completion:*' menu select
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
zstyle ':completion:*:descriptions' format '%F{cyan}── %d ──%f'
zstyle ':completion:*' group-name ''

for plugin in zsh-autosuggestions zsh-syntax-highlighting zsh-history-substring-search; do
  [[ -r ~/.zsh/$plugin/$plugin.zsh ]] && source ~/.zsh/$plugin/$plugin.zsh
done
bindkey '^[[A' history-substring-search-up
bindkey '^[[B' history-substring-search-down
bindkey '^[[1;5C' forward-word
bindkey '^[[1;5D' backward-word
bindkey '^[[H' beginning-of-line
bindkey '^[[F' end-of-line
bindkey '^[[3~' delete-char

setopt AUTO_CD CORRECT NO_BEEP INTERACTIVE_COMMENTS GLOB_DOTS

alias ls='ls --color=auto'
alias ll='ls -lah'
alias la='ls -A'
alias grep='grep --color=auto'
alias df='df -h'
alias du='du -h'
alias free='free -h'
alias ..='cd ..'
alias ...='cd ../..'
alias cls='clear'

# fzf 0.48+ brings its own zsh setup.
command -v fzf >/dev/null && source <(fzf --zsh 2>/dev/null)

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"
export BUN_INSTALL="$HOME/.bun"
[ -s "$BUN_INSTALL/_bun" ] && source "$BUN_INSTALL/_bun"

[[ -r ~/.local/share/remote-control/profiles.zsh ]] && source ~/.local/share/remote-control/profiles.zsh
ZSHRC
LINE='[[ -r ~/.config/remote-control/zshrc ]] && source ~/.config/remote-control/zshrc'
grep -qF "$LINE" "$HOME/.zshrc" 2>/dev/null || printf '%s\n\n# Lo tuyo va aquí abajo.\n' "$LINE" >>"$HOME/.zshrc"

if ((${#FAILED[@]})); then
  step "Sin instalar (se reintenta al actualizar): ${FAILED[*]}"
fi
step "Entorno listo"
