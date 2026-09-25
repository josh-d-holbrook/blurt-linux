#!/bin/sh
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/blurt-linux"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
for command in python3 parec pactl xdotool; do
    command -v "$command" >/dev/null || { echo "Missing $command. See README.md."; exit 1; }
done
/usr/bin/python3 - <<'PY'
import gi
import cairo
from Xlib import display
for name, version in [('Gtk','3.0'), ('PangoCairo','1.0'), ('Secret','1'), ('AyatanaAppIndicator3','0.1')]:
    gi.require_version(name, version)
PY
mkdir -p "$DEST" "$APPS" "$AUTOSTART"
if [ -f "$DEST/blurt.py" ]; then
    BACKUP="$DEST/backup-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP"
    cp "$DEST/blurt.py" "$DEST/core.py" "$BACKUP/"
    for file in recovery.py appearance.py; do
        if [ -f "$DEST/$file" ]; then cp "$DEST/$file" "$BACKUP/"; fi
    done
fi
for file in blurt.py core.py recovery.py appearance.py README.md LICENSE; do
    install -m 644 "$SOURCE/$file" "$DEST/$file"
done
cat > "$APPS/blurt-linux.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Blurt Linux
Comment=Dictate with AssemblyAI
Exec=/usr/bin/python3 "$DEST/blurt.py"
Icon=audio-input-microphone
Terminal=false
Categories=Utility;Audio;
StartupNotify=false
EOF
cp "$APPS/blurt-linux.desktop" "$AUTOSTART/blurt-linux.desktop"
echo "Installed Blurt Linux. It will start when you log in."
echo "Open Blurt Linux from your application menu to add your AssemblyAI key."
