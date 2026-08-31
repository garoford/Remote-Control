#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"
APP_SHARE="$SHARE/remote-control"
ICON_BASE="$SHARE/icons/hicolor"
DESKTOP_DIR="$SHARE/applications"

mkdir -p "$APP_SHARE" "$BIN" "$DESKTOP_DIR"
mkdir -p "$ICON_BASE/scalable/apps"
mkdir -p "$ICON_BASE/256x256/apps" "$ICON_BASE/128x128/apps" "$ICON_BASE/64x64/apps"

rsync -a --delete "$ROOT/remote_control/" "$APP_SHARE/remote_control/"

cat > "$BIN/remote-control" <<'EOF'
#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "share" / "remote-control"))
from remote_control.app import main

if __name__ == "__main__":
    raise SystemExit(main())
EOF
chmod 755 "$BIN/remote-control"

install -m 644 "$ROOT/data/icons/dev.garoford.RemoteControl.svg" \
  "$ICON_BASE/scalable/apps/dev.garoford.RemoteControl.svg"

if command -v convert >/dev/null 2>&1; then
  convert -background none -resize 256x256 \
    "$ROOT/data/icons/dev.garoford.RemoteControl.svg" \
    "$ICON_BASE/256x256/apps/dev.garoford.RemoteControl.png" || true
  convert -background none -resize 128x128 \
    "$ROOT/data/icons/dev.garoford.RemoteControl.svg" \
    "$ICON_BASE/128x128/apps/dev.garoford.RemoteControl.png" || true
  convert -background none -resize 64x64 \
    "$ROOT/data/icons/dev.garoford.RemoteControl.svg" \
    "$ICON_BASE/64x64/apps/dev.garoford.RemoteControl.png" || true
fi

desktop-file-install --dir="$DESKTOP_DIR" \
  "$ROOT/data/dev.garoford.RemoteControl.desktop"
# GNOME's session PATH often lacks ~/.local/bin, so TryExec=remote-control
# hides the launcher. Pin the installed wrapper with an absolute path.
desktop-file-edit \
  --set-key=Exec --set-value="$BIN/remote-control" \
  "$DESKTOP_DIR/dev.garoford.RemoteControl.desktop"
desktop-file-edit \
  --set-key=TryExec --set-value="$BIN/remote-control" \
  "$DESKTOP_DIR/dev.garoford.RemoteControl.desktop"

update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
if [[ -f "$ICON_BASE/index.theme" ]]; then
  gtk-update-icon-cache -f "$ICON_BASE" >/dev/null 2>&1 || true
fi

# Make GNOME pick the new launcher without a session restart.
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP_DIR/dev.garoford.RemoteControl.desktop" \
    metadata::trusted true >/dev/null 2>&1 || true
fi

printf '%s\n' "Instalado: $BIN/remote-control"
printf '%s\n' "Launcher: $DESKTOP_DIR/dev.garoford.RemoteControl.desktop"
