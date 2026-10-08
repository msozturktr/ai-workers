#!/usr/bin/env bash
# ai-workers'i calistirilabilir uygulama olarak kurar:
#   ~/.local/bin/ai-workers            komut
#   systemd --user ai-workers.service  pano servisi (girislerde otomatik)
#   ~/.local/share/applications/...    uygulama menusu kisayolu
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
DESK_DIR="$HOME/.local/share/applications"
PY_BIN="$(command -v python3)"

mkdir -p "$BIN_DIR" "$UNIT_DIR" "$DESK_DIR"

# 1) komut
cat > "$BIN_DIR/ai-workers" <<EOF
#!/usr/bin/env bash
exec "$PY_BIN" "$APP_DIR/cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/ai-workers"
echo "komut      : $BIN_DIR/ai-workers"

# 2) servis
cat > "$UNIT_DIR/ai-workers.service" <<EOF
[Unit]
Description=ai-workers kota panosu
Documentation=file://$APP_DIR/README.md
After=network-online.target

[Service]
Type=simple
ExecStart=$PY_BIN $APP_DIR/dashboard.py --port 8765
WorkingDirectory=$APP_DIR
Restart=on-failure
RestartSec=5
# yalnizca 127.0.0.1'e baglanir; disa acik degildir
PrivateTmp=yes
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now ai-workers.service >/dev/null 2>&1 || \
  systemctl --user restart ai-workers.service
echo "servis     : $(systemctl --user is-active ai-workers.service) / $(systemctl --user is-enabled ai-workers.service 2>/dev/null || echo '?')"

# 3) menu kisayolu
cat > "$DESK_DIR/ai-workers.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ai-workers
GenericName=Model kota panosu
Comment=Ucretsiz API isci havuzunun kalan kullanimi
Exec=$BIN_DIR/ai-workers open
Icon=$APP_DIR/icon.svg
Terminal=false
Categories=Development;
Keywords=ai;llm;kota;quota;gemini;groq;openrouter;claude;
StartupNotify=false
EOF
update-desktop-database "$DESK_DIR" >/dev/null 2>&1 || true
echo "kisayol    : $DESK_DIR/ai-workers.desktop"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "UYARI: $BIN_DIR PATH'te degil. fish icin:"
     echo "       fish_add_path $BIN_DIR" ;;
esac

echo
echo "kurulum tamam -> ai-workers doctor"
