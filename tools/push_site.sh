#!/usr/bin/env bash
# Deploy the gateway code plus one site's config (and optionally its certs) to a Pi.
#
#   tools/push_site.sh <site> [<user@pi>] [--certs] [--remote-dir DIR] [--no-modbus-tool] [--no-kiosk] [--new-hw] [--overlay-ok]
#
#   <site>            key under sites/ (e.g. akvo); "root" = the repo-root config_data/ (site 1)
#   <user@pi>         optional: without it the Pi is found by name, then through AWS (see below)
#   --new-hw          the site moved to another Pi: accept its serial and re-register it
#   --overlay-ok      push even though the Pi runs the read-only overlay (lost at next reboot)
#   --certs           also copy sites/<site>/certs/* to the Pi's gateway/certs/
#   --remote-dir      install location on the Pi, relative to its home (default: Akvo_Green)
#   --no-modbus-tool  skip the AKVO Modbus Tool (see below)
#   --no-kiosk        don't set up / refresh the Chromium kiosk (see below)
#
# Code is synced with rsync, never touching the Pi's config_data/, data/, logs/,
# gateway/certs/ or .venv/; when gateway/requirements*.txt changed, the Pi's
# .venv gets the new pinned packages (pip, no sudo). config.json is copied
# into config_data/ (the gateway live-reloads it); the akvo-green service is
# restarted only when code changed,
# the akvo-history-web dashboard and akvo-oled display (if installed) on every push.
# WiFi: when the site's config.json has a "wifi" section (system.csv's
# wifi1_ssid/wifi2_ssid), writes those networks into NetworkManager as profiles
# akvo-wifi1 (preferred) / akvo-wifi2, with passwords from the git-ignored
# sites/<site>/wifi.csv (config_data/wifi.csv for root) - see tools/wifi_profiles.py.
# Also installs the AKVO Modbus Tool (desktop app for scanning/reading/writing
# Modbus registers on site, from ../Tools/AkvoModbus or $AKVO_MODBUS_DIR) as a
# .deb - only when its source changed since the last install on that Pi.
# Kiosk: on a Pi with a desktop (chromium installed) and akvo-history-web,
# runs tools/setup_kiosk.sh (opens the local dashboard full screen at every
# boot) and opens - or reopens, to show the new page - it on the Pi's screen now
# when someone is logged in on the desktop. No sudo.
# Which Pi: sites/<site>/pi.csv (hostname,user,serial,mac) ties the site to one
# physical Pi by its serial number (/proc/device-tree/serial-number). Without
# <user@pi> the push goes to <user>@<hostname>.local (mDNS), or else to the IP
# the gateway last reported to AWS (latest/system.json). Every push compares the
# Pi's serial with pi.csv and stops on a mismatch, so a site's config can't land
# on another site's Pi; the first push writes pi.csv (commit it). The Pi's
# hostname is set to pi.csv's (default: the site key), so <key>.local works.
# Overlay: with the read-only overlay file system on (raspi-config
# enable_overlayfs, / is an overlay in RAM) everything pushed is gone at the
# next reboot, so the push stops unless --overlay-ok; turn it off first with
# sudo raspi-config nonint disable_overlayfs && sudo reboot.
# First install on a new Pi: run this, then ./install.sh --skip-certs on the Pi.
set -euo pipefail

USAGE="usage: tools/push_site.sh <site> [<user@pi>] [--certs] [--remote-dir DIR] [--no-modbus-tool] [--no-kiosk] [--new-hw] [--overlay-ok]"
SITE="${1:?$USAGE}"
shift
PI=""
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then PI="$1"; shift; fi
NEW_HW=0
OVERLAY_OK=0
CERTS=0
MODBUS_TOOL=1
KIOSK=1
REMOTE_DIR="Akvo_Green"
while [ $# -gt 0 ]; do
  case "$1" in
    --certs) CERTS=1 ;;
    --remote-dir) REMOTE_DIR="${2:?--remote-dir needs a value}"; shift ;;
    --no-modbus-tool) MODBUS_TOOL=0 ;;
    --no-kiosk) KIOSK=0 ;;
    --new-hw) NEW_HW=1 ;;
    --overlay-ok) OVERLAY_OK=1 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SITE" = "root" ]; then
  CONFIG="$ROOT/config_data/config.json"
  CERT_DIR="$ROOT/gateway/certs"
  WIFI_CSV="$ROOT/config_data/wifi.csv"
  PI_CSV="$ROOT/config_data/pi.csv"
else
  CONFIG="$ROOT/sites/$SITE/config_data/config.json"
  CERT_DIR="$ROOT/sites/$SITE/certs"
  WIFI_CSV="$ROOT/sites/$SITE/wifi.csv"
  PI_CSV="$ROOT/sites/$SITE/pi.csv"
fi
[ -f "$CONFIG" ] || { echo "error: $CONFIG not found - build it first (config_manager.py build --config-dir ...)" >&2; exit 1; }

python3 - "$CONFIG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
n = sum(len(d["sensors"]) for d in c["devices"])
print(f"site config: gateway {c['gateway']['gateway_id']}, client {c['aws']['client_id']}, "
      f"topics {c['aws']['topic_pub']} / {c['aws']['topic_system']}, {n} sensors")
PY

# WiFi profiles are built locally before anything is sent, so a missing
# password stops the push before the Pi is touched. 2 = no "wifi" section.
set +e; WIFI_SCRIPT="$(python3 "$ROOT/tools/wifi_profiles.py" "$CONFIG" "$WIFI_CSV")"; WIFI_RC=$?; set -e
[ "$WIFI_RC" = 0 ] || [ "$WIFI_RC" = 2 ] || exit 1

# ---- Which Pi: pi.csv (hostname,user,serial,mac) -------------------------
pi_field() {  # pi_field <column> - from pi.csv's first row, empty if absent
  [ -f "$PI_CSV" ] || return 0
  python3 - "$PI_CSV" "$1" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1], newline="")))
print((rows[0].get(sys.argv[2]) or "").strip() if rows else "")
PY
}
HW_HOST="$(pi_field hostname)"; HW_USER="$(pi_field user)"; HW_SERIAL="$(pi_field serial)"
[ -n "$HW_HOST" ] || { [ "$SITE" = "root" ] || HW_HOST="$SITE"; }
SSH_PROBE=(-o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

if [ -z "$PI" ]; then
  [ -n "$HW_USER" ] || { echo "error: no <user@pi> given and no user in $PI_CSV - pass it once (e.g. pi@192.168.68.124)" >&2; exit 1; }
  if [ -n "$HW_HOST" ] && ssh "${SSH_PROBE[@]}" "$HW_USER@$HW_HOST.local" true 2>/dev/null; then
    PI="$HW_USER@$HW_HOST.local"
  else
    # The gateway reports its IP to AWS every system_interval (latest/system.json).
    BUCKET="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["aws"].get("data_bucket") or "")' "$CONFIG")"
    IP=""
    [ -n "$BUCKET" ] && IP="$(aws s3 cp "s3://$BUCKET/latest/system.json" - 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip_address") or "", d.get("ts") or "")' 2>/dev/null || true)"
    [ -n "${IP%% *}" ] || { echo "error: $HW_HOST.local doesn't answer and AWS has no IP for $SITE - pass <user@pi>" >&2; exit 1; }
    echo "   $HW_HOST.local doesn't answer - using the IP last reported to AWS: ${IP%% *} (at ${IP#* })"
    PI="$HW_USER@${IP%% *}"
  fi
fi
ssh "${SSH_PROBE[@]}" "$PI" true 2>/dev/null || ssh -o ConnectTimeout=8 "$PI" true \
  || { echo "error: can't reach $PI over ssh" >&2; exit 1; }
# shellcheck disable=SC2016
read -r R_SERIAL R_HOST R_MAC R_OVERLAY R_MODEL <<<"$(ssh "$PI" 'printf "%s %s %s %s %s" \
  "$(tr -d "\0" < /proc/device-tree/serial-number 2>/dev/null || echo -)" "$(hostname)" \
  "$(cat /sys/class/net/$(ip route show default 2>/dev/null | awk "{print \$5; exit}")/address 2>/dev/null || echo -)" \
  "$(awk "\$2 == \"/\" {t = \$3} END {print (t == \"overlay\" ? \"yes\" : \"no\")}" /proc/mounts)" \
  "$(tr -d "\0" < /proc/device-tree/model 2>/dev/null | tr " " "_" || echo -)"')"
echo "== Pi: $PI - ${R_MODEL//_/ }, serial $R_SERIAL, hostname $R_HOST"
if [ -n "$HW_SERIAL" ] && [ "$HW_SERIAL" != "$R_SERIAL" ] && [ "$NEW_HW" = 0 ]; then
  echo "error: this Pi (serial $R_SERIAL) is not $SITE's Pi (serial $HW_SERIAL in $PI_CSV)." >&2
  echo "       Wrong address? If $SITE really moved to this Pi, push again with --new-hw." >&2
  exit 1
fi
if [ "$R_OVERLAY" = yes ]; then
  if [ "$OVERLAY_OK" = 0 ]; then
    echo "error: $PI runs the read-only overlay file system - this push would be lost at its next reboot." >&2
    echo "       Turn it off first: ssh -t $PI 'sudo raspi-config nonint disable_overlayfs && sudo reboot'" >&2
    echo "       then push again (and re-enable it afterwards if wanted), or pass --overlay-ok to push anyway." >&2
    exit 1
  fi
  echo "   WARNING: overlay file system on - everything pushed now is lost at the next reboot"
fi
if [ -z "$HW_SERIAL" ] || [ "$HW_SERIAL" != "$R_SERIAL" ]; then
  printf 'hostname,user,serial,mac\n%s,%s,%s,%s\n' "${HW_HOST:-$R_HOST}" "${PI%@*}" "$R_SERIAL" "$R_MAC" > "$PI_CSV"
  echo "   registered this Pi in $PI_CSV - commit it"
fi
# Hostname = the site's, so <host>.local finds it on the network (avahi/mDNS).
if [ -n "$HW_HOST" ] && [ "$R_HOST" != "$HW_HOST" ]; then
  echo "== hostname: $R_HOST -> $HW_HOST (reach it as ${PI%@*}@$HW_HOST.local)"
  ssh -t "$PI" "sudo raspi-config nonint do_hostname '$HW_HOST' && sudo hostnamectl set-hostname '$HW_HOST' \
    && sudo systemctl restart avahi-daemon" \
    || echo "   warning: setting the hostname failed - the push goes on"
fi

echo "== code -> $PI:$REMOTE_DIR"
ssh "$PI" "mkdir -p '$REMOTE_DIR/config_data' '$REMOTE_DIR/gateway/certs'"
CHANGES="$(rsync -rlptz --delete --itemize-changes \
  --exclude .git/ --exclude .venv/ --exclude config_data/ --exclude data/ --exclude logs/ \
  --exclude reports/ --exclude sites/ --exclude gateway/certs/ --exclude __pycache__/ \
  "$ROOT/" "$PI:$REMOTE_DIR/")"
[ -n "$CHANGES" ] && echo "$CHANGES" | sed 's/^/   /' || echo "   code already up to date"

echo "== config.json -> $PI:$REMOTE_DIR/config_data/"
scp -q "$CONFIG" "$PI:$REMOTE_DIR/config_data/config.json"

if [ "$CERTS" = 1 ]; then
  [ -f "$CERT_DIR/private.pem.key" ] || { echo "error: no certs in $CERT_DIR" >&2; exit 1; }
  echo "== certs -> $PI:$REMOTE_DIR/gateway/certs/"
  scp -q "$CERT_DIR"/{AmazonRootCA1.pem,certificate.pem.crt,private.pem.key} "$PI:$REMOTE_DIR/gateway/certs/"
  ssh "$PI" "chmod 600 '$REMOTE_DIR/gateway/certs/private.pem.key'"
fi

if [ "$WIFI_RC" = 0 ]; then
  echo "== WiFi -> NetworkManager on $PI: $(python3 -c 'import json,sys; print(", ".join(json.load(open(sys.argv[1]))["wifi"]["networks"]) or "none (akvo-wifi* profiles removed)")' "$CONFIG")"
  # Never on a command line, so the passwords don't show up in ps: copied into a
  # private (mode 600) temp file on the Pi, run with a terminal so sudo can ask
  # for a password if this Pi needs one, and removed straight after.
  REMOTE_TMP="$(ssh "$PI" 'umask 077; mktemp')"
  printf '%s' "$WIFI_SCRIPT" | ssh "$PI" "cat > '$REMOTE_TMP'"
  ssh -t "$PI" "sudo bash '$REMOTE_TMP'; rc=\$?; rm -f '$REMOTE_TMP'; exit \$rc" \
    || { ssh "$PI" "rm -f '$REMOTE_TMP'"; echo "error: installing the WiFi profiles failed" >&2; exit 1; }
  echo "   saved; the Pi switches to them when its current network drops or on reboot"
fi
unset WIFI_SCRIPT

# New pinned Python packages: update the Pi's venv before the restart below.
# Only on a Pi that install.sh already set up (it has .venv); no sudo needed.
if echo "$CHANGES" | grep -q 'gateway/requirements' && ssh "$PI" "test -x '$REMOTE_DIR/.venv/bin/pip'"; then
  echo "== Python packages changed: updating $REMOTE_DIR/.venv"
  ssh "$PI" "cd '$REMOTE_DIR' && .venv/bin/pip install -q -r gateway/requirements.txt" \
    || { echo "error: installing gateway/requirements.txt on the Pi failed" >&2; exit 1; }
  ssh "$PI" "cd '$REMOTE_DIR' && .venv/bin/pip install -q -r gateway/requirements-oled.txt" \
    || echo "   warning: luma.oled didn't install - only the OLED display is affected"
fi

if [ -n "$CHANGES" ] && ssh "$PI" "systemctl is-enabled --quiet akvo-green" 2>/dev/null; then
  echo "== code changed: restarting akvo-green"
  ssh -t "$PI" "sudo systemctl restart akvo-green"
fi
# The local dashboard reads web.* and the page files only at start, and a
# restart can't disturb the gateway - so always restart it when installed.
if ssh "$PI" "systemctl is-enabled --quiet akvo-history-web" 2>/dev/null; then
  echo "== restarting akvo-history-web (local dashboard)"
  ssh -t "$PI" "sudo systemctl restart akvo-history-web"
elif [ -n "$CHANGES" ]; then
  echo "   note: akvo-history-web is not installed on this Pi - run ./install.sh --skip-certs there once"
fi
# Same for the OLED display: it reads display.* and the sensor list only at start.
if ssh "$PI" "systemctl is-enabled --quiet akvo-oled" 2>/dev/null; then
  echo "== restarting akvo-oled (OLED display)"
  ssh -t "$PI" "sudo systemctl restart akvo-oled"
fi

# Kiosk: Chromium showing the local dashboard on the Pi's own screen.
if [ "$KIOSK" = 1 ]; then
  if ! ssh "$PI" "command -v chromium >/dev/null && systemctl is-enabled --quiet akvo-history-web" 2>/dev/null; then
    echo "   note: kiosk skipped - no chromium (Lite image) or akvo-history-web not installed"
  else
    echo "== kiosk: local dashboard in Chromium at every boot"
    ssh "$PI" "cd '$REMOTE_DIR' && tools/setup_kiosk.sh >/dev/null" \
      || echo "   warning: tools/setup_kiosk.sh failed on the Pi"
    # Open it now (or reopen it so the screen shows the pushed page) when a
    # desktop session is running; Wayland (labwc) or X11.
    ssh "$PI" "cd '$REMOTE_DIR' && R=/run/user/\$(id -u) && W=\$(ls \$R 2>/dev/null | grep -m1 '^wayland-[0-9]*\$'); \
      if [ -z \"\$W\" ] && ! pgrep -u \"\$USER\" -x Xorg >/dev/null && ! pgrep -u \"\$USER\" -f '[l]xsession' >/dev/null; then \
        echo '   nobody logged in on the desktop - it opens at the next boot'; exit 0; fi; \
      pkill -u \"\$USER\" -f '[u]ser-data-dir=.*akvo-kiosk' && sleep 2; \
      XDG_RUNTIME_DIR=\$R WAYLAND_DISPLAY=\$W DISPLAY=:0 setsid -f tools/kiosk_launch.sh >/dev/null 2>&1 < /dev/null; \
      echo '   opened on the Pi screen'" \
      || echo "   warning: couldn't open the kiosk now - it opens at the next boot"
  fi
fi

if [ "$MODBUS_TOOL" = 1 ]; then
  BUILD="$(mktemp -d)"
  trap 'rm -rf "$BUILD"' EXIT
  set +e; OUT="$("$ROOT/tools/build_modbus_tool.sh" "$BUILD")"; RC=$?; set -e
  if [ "$RC" = 3 ]; then
    echo "   note: AKVO Modbus Tool not found (../Tools/AkvoModbus or \$AKVO_MODBUS_DIR) - skipped"
  elif [ "$RC" != 0 ]; then
    echo "error: building the AKVO Modbus Tool failed (or use --no-modbus-tool)" >&2; exit 1
  else
    DEB="$(echo "$OUT" | sed -n 1p)"; HASH="$(echo "$OUT" | sed -n 2p)"
    if [ "$(ssh "$PI" "cat /opt/akvo-modbus/.source-hash 2>/dev/null")" = "$HASH" ]; then
      echo "== AKVO Modbus Tool already up to date"
    else
      echo "== AKVO Modbus Tool -> $PI (/opt/akvo-modbus, command akvo-modbus)"
      scp -q "$DEB" "$PI:/tmp/akvo-modbus.deb"
      ssh -t "$PI" "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --reinstall /tmp/akvo-modbus.deb >/dev/null && rm -f /tmp/akvo-modbus.deb"
      # Its Python packages go in the Pi user's own venv (first time: a few minutes).
      ssh "$PI" "akvo-modbus-setup" | tail -n 2
    fi
  fi
fi
echo "done. The gateway reloads config.json within 5 s."
