#!/usr/bin/env bash
set -euo pipefail

MASTER=/tmp/akvo_modbus_master
SLAVE=/tmp/akvo_modbus_slave
PIDFILE=/tmp/akvo_modbus_socat.pid
LOGFILE=/tmp/akvo_modbus_socat.log

rm -f "$MASTER" "$SLAVE" "$PIDFILE"

command -v socat >/dev/null || {
    echo "ERROR: socat is not installed."
    echo "Install with: sudo apt install socat"
    exit 1
}

socat \
  PTY,link="$MASTER",raw,echo=0,mode=666 \
  PTY,link="$SLAVE",raw,echo=0,mode=666 \
  >"$LOGFILE" 2>&1 &

echo $! > "$PIDFILE"

for _ in $(seq 1 30); do
    if [[ -e "$MASTER" && -e "$SLAVE" ]]; then
        echo "Virtual serial link ready."
        echo "MASTER=$MASTER"
        echo "SLAVE=$SLAVE"
        exit 0
    fi
    sleep 0.1
done

echo "ERROR: virtual serial ports were not created."
cat "$LOGFILE" || true
exit 1
