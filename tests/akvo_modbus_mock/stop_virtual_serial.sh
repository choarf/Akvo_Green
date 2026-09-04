#!/usr/bin/env bash
PIDFILE=/tmp/akvo_modbus_socat.pid
if [[ -f "$PIDFILE" ]]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
rm -f /tmp/akvo_modbus_master /tmp/akvo_modbus_slave
echo 'Virtual link stopped.'
