#!/usr/bin/env bash
# Makes the Pi open the local history dashboard in Chromium at every boot,
# on its own screen (HDMI/touch display, or the VNC desktop).
#
# Run ON THE PI, as the desktop user (pi):
#   tools/setup_kiosk.sh            # full screen kiosk (default)
#   tools/setup_kiosk.sh --window   # normal maximized window instead
#   tools/setup_kiosk.sh --off      # stop opening it at boot
# From the dev machine:
#   ssh pi@<pi> 'cd Akvo_Green && tools/setup_kiosk.sh'
#
# Needs: Raspberry Pi OS with desktop + auto-login (the default image), the
# akvo-history-web service with web_enabled=1. Adds one XDG autostart entry
# (~/.config/autostart/akvo-kiosk.desktop); the system's own desktop autostart
# (panel, file manager) is not touched. Takes effect at the next boot/login.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$HOME/.config/autostart/akvo-kiosk.desktop"
ARGS=""
case "${1:-}" in
  "") ;;
  --window) ARGS=" --window" ;;
  --off)
    rm -f "$ENTRY"
    echo "Removed $ENTRY - Chromium will no longer open at boot."
    exit 0 ;;
  *) echo "usage: $0 [--window|--off]" >&2; exit 2 ;;
esac

command -v chromium >/dev/null || { echo "error: chromium not installed (sudo apt install chromium)" >&2; exit 1; }
if ! grep -qs "^autologin-user=" /etc/lightdm/lightdm.conf; then
  echo "warning: desktop auto-login looks off - Chromium only starts once someone logs in." \
       "Turn it on with: sudo raspi-config  (System Options > Boot / Auto Login > Desktop Autologin)" >&2
fi
if ! systemctl is-enabled --quiet akvo-history-web 2>/dev/null; then
  echo "warning: the akvo-history-web service isn't installed - run ./install.sh --skip-certs first." >&2
fi

chmod +x "$HERE/kiosk_launch.sh"
mkdir -p "$(dirname "$ENTRY")"
cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Akvo Green - historial local
Comment=Opens the local history dashboard at login (tools/setup_kiosk.sh)
Exec=$HERE/kiosk_launch.sh$ARGS
X-GNOME-Autostart-enabled=true
EOF
echo "Installed $ENTRY"
echo "Chromium will open http://localhost:<web_port>/#actual ${ARGS:+in a window }at the next boot."
echo "Start it now on the Pi's screen without rebooting: log in on the desktop and run $HERE/kiosk_launch.sh$ARGS"
