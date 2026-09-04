#!/usr/bin/env bash
set -e
MASTER=/tmp/akvo_modbus_master
SLAVE=/tmp/akvo_modbus_slave
PIDFILE=/tmp/akvo_modbus_socat.pid
rm -f "$MASTER" "$SLAVE"
socat -d -d PTY,link="$MASTER",raw,echo=0,mode=666 PTY,link="$SLAVE",raw,echo=0,mode=666 >/tmp/akvo_modbus_socat.log 2>&1 &
echo $! > "$PIDFILE"
for i in {1..20}; do
  [[ -e "$MASTER" && -e "$SLAVE" ]] && { echo "Virtual link ready: $MASTER <-> $SLAVE"; exit 0; }
  sleep 0.1
done
cat /tmp/akvo_modbus_socat.log
exit 1
