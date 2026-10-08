#!/usr/bin/env bash
# Installs ai-workers as an executable application:
#   ~/.local/bin/ai-workers            command
#   systemd --user ai-workers.service  dashboard service (automatic on login)
#   ~/.local/share/applications/...    desktop app entry (app menu, Wayland window icon)
#   ~/.local/share/icons/hicolor/...    app icon
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
DESK_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
APP_ID="io.github.msozturktr.AiWorkers"
PY_BIN="$(command -v python3)"

mkdir -p "$BIN_DIR" "$UNIT_DIR" "$DESK_DIR" "$ICON_DIR"

# 1) command
cat > "$BIN_DIR/ai-workers" <<EOF
#!/usr/bin/env bash
exec "$PY_BIN" "$APP_DIR/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/ai-workers"
echo "command  : $BIN_DIR/ai-workers"

# 2) service
cat > "$UNIT_DIR/ai-workers.service" <<EOF
[Unit]
Description=ai-workers dashboard (quota + activity log)
Documentation=file://$APP_DIR/README.md
After=network-online.target

[Service]
Type=simple
ExecStart=$PY_BIN $APP_DIR/dashboard.py --port 8765
WorkingDirectory=$APP_DIR
Restart=on-failure
RestartSec=5
# binds only to 127.0.0.1; not exposed externally
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now ai-workers.service >/dev/null 2>&1 || \
  systemctl --user restart ai-workers.service
echo "service  : $(systemctl --user is-active ai-workers.service) / $(systemctl --user is-enabled ai-workers.service 2>/dev/null || echo '?')"

# 3) desktop app: the .desktop file name must equal the app id so Wayland compositors
#    match the window to its icon and menu entry
install -m 644 "$APP_DIR/icon.svg" "$ICON_DIR/$APP_ID.svg"
rm -f "$DESK_DIR/ai-workers.desktop"  # entry from older versions
cat > "$DESK_DIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ai-workers
GenericName=AI worker pool monitor
Comment=Live activity log and remaining quota of the ai-workers MCP server
Exec=$BIN_DIR/ai-workers app
Icon=$APP_ID
Terminal=false
Categories=Development;Monitor;
Keywords=ai;llm;mcp;claude;quota;activity;log;gemini;groq;openrouter;
StartupNotify=true
StartupWMClass=$APP_ID
EOF
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
update-desktop-database "$DESK_DIR" >/dev/null 2>&1 || true
echo "app      : $DESK_DIR/$APP_ID.desktop"
if ! "$PY_BIN" -c 'import gi; gi.require_version("Gtk","3.0"); gi.require_version("WebKit2","4.1")' 2>/dev/null; then
  echo "NOTE: desktop window needs python-gobject + webkit2gtk-4.1; until then 'ai-workers app' opens the browser"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "WARNING: $BIN_DIR not in PATH. for fish:"
     echo "       fish_add_path $BIN_DIR" ;;
esac

echo
echo "installation complete -> ai-workers doctor"
