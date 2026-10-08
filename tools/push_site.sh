#!/usr/bin/env bash
# Deploy the gateway code plus one site's config (and optionally its certs) to a Pi.
#
#   tools/push_site.sh <site> <user@pi> [--certs] [--remote-dir DIR]
#
#   <site>        key under sites/ (e.g. akvo); "root" = the repo-root config_data/ (site 1)
#   --certs       also copy sites/<site>/certs/* to the Pi's gateway/certs/
#   --remote-dir  install location on the Pi, relative to its home (default: Akvo_Green)
#
# Code is synced with rsync, never touching the Pi's config_data/, data/, logs/,
# gateway/certs/ or .venv/. config.json is copied into config_data/ (the gateway
# live-reloads it); the akvo-green service is restarted only when code changed,
# the akvo-history-web dashboard (if installed) on every push.
# First install on a new Pi: run this, then ./install.sh --skip-certs on the Pi.
set -euo pipefail

SITE="${1:?usage: tools/push_site.sh <site> <user@pi> [--certs] [--remote-dir DIR]}"
PI="${2:?usage: tools/push_site.sh <site> <user@pi> [--certs] [--remote-dir DIR]}"
shift 2
CERTS=0
REMOTE_DIR="Akvo_Green"
while [ $# -gt 0 ]; do
  case "$1" in
    --certs) CERTS=1 ;;
    --remote-dir) REMOTE_DIR="${2:?--remote-dir needs a value}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$SITE" = "root" ]; then
  CONFIG="$ROOT/config_data/config.json"
  CERT_DIR="$ROOT/gateway/certs"
else
  CONFIG="$ROOT/sites/$SITE/config_data/config.json"
  CERT_DIR="$ROOT/sites/$SITE/certs"
fi
[ -f "$CONFIG" ] || { echo "error: $CONFIG not found - build it first (config_manager.py build --config-dir ...)" >&2; exit 1; }

python3 - "$CONFIG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
n = sum(len(d["sensors"]) for d in c["devices"])
print(f"site config: gateway {c['gateway']['gateway_id']}, client {c['aws']['client_id']}, "
      f"topics {c['aws']['topic_pub']} / {c['aws']['topic_system']}, {n} sensors")
PY

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
echo "done. The gateway reloads config.json within 5 s."
