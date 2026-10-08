#!/usr/bin/env bash
# Opens the local history dashboard (history_web.py) in Chromium on the Pi's
# own screen. Started at desktop login by ~/.config/autostart/akvo-kiosk.desktop
# (installed by tools/setup_kiosk.sh) - not meant to be run over SSH.
#
#   tools/kiosk_launch.sh [--window] [--url URL]
#
#   default    full screen, no browser bars (kiosk); Alt+F4 closes it
#   --window   a normal, maximized Chromium window instead
#
# Waits for the dashboard to answer first (it starts as its own service and
# may come up after the desktop), so the screen never shows a "can't connect"
# page after boot.
set -uo pipefail

URL="http://localhost:8080/#actual"
MODE="--kiosk"
while [ $# -gt 0 ]; do
  case "$1" in
    --window) MODE="--start-maximized" ;;
    --url) URL="${2:?--url needs a value}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# Port from config.json's web.port, if the URL is the default one.
CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config_data/config.json"
if [[ "$URL" == http://localhost:8080/* ]] && [ -f "$CONFIG" ]; then
  PORT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("web", {}).get("port", 8080))' "$CONFIG" 2>/dev/null || echo 8080)"
  URL="http://localhost:${PORT}/#actual"
fi

# Up to 2 minutes for the dashboard service; open anyway after that (the page
# shows its own error and the user can reload).
for _ in $(seq 1 60); do
  curl -fs -o /dev/null "${URL%%#*}api/info" && break
  sleep 2
done

# Its own browser profile: the normal Chromium profile may restore old tabs
# over the dashboard at start, and the kiosk shouldn't touch someone's own
# browsing. Session restore is also wiped so it always opens on the home page.
PROFILE="$HOME/.config/akvo-kiosk"
mkdir -p "$PROFILE/Default"
rm -rf "$PROFILE/Default/Sessions" "$PROFILE/Default/Current Session" "$PROFILE/Default/Current Tabs" \
       "$PROFILE/Default/Last Session" "$PROFILE/Default/Last Tabs"
# A power cut leaves the profile marked as crashed, which would show a
# "restore pages?" bar over the dashboard - clear that flag before starting.
PREFS="$PROFILE/Default/Preferences"
if [ -f "$PREFS" ]; then
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
fi

# Native Wayland under labwc (Raspberry Pi OS Bookworm/Trixie), X11 on older
# X desktops - decided here rather than left to Chromium's own guess, which
# needs XDG_SESSION_TYPE and falls back to X11 without it.
PLATFORM="--ozone-platform=x11"
[ -n "${WAYLAND_DISPLAY:-}" ] && PLATFORM="--ozone-platform=wayland"

exec chromium "$MODE" "$URL" "$PLATFORM" --user-data-dir="$PROFILE" \
  --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble \
  --disable-features=Translate --password-store=basic --check-for-update-interval=31536000
