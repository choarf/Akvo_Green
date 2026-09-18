#!/usr/bin/env bash
# Kills every leftover Akvo Green process (real gateway, mock test harness,
# virtual-serial socat links) and frees the UART so a fresh run can open it.
#
# Why this is needed: edge_node_improved.py's own Ctrl+C handler
# (EdgeNode.start()'s `except KeyboardInterrupt: self.stop()`) only runs if
# the interrupt lands while that try block is active. init_system() blocks
# on self.mqtt.connect() (which retries forever) *before* that block is
# entered - a Ctrl+C during that window kills the process without ever
# calling stop(), so self.modbus.disconnect() never runs. Separately, every
# MQTT connect attempt (including retries) builds a brand-new
# io.EventLoopGroup/ClientBootstrap (see MQTTManager.connect() in
# edge_node_improved.py) without shutting down the previous one, and awscrt's
# underlying native I/O threads aren't Python daemon threads - they don't
# reliably die with the interpreter, which can leave the process itself
# lingering, still holding the serial port open. The mock test harness
# (tests/edge_node_mock/) has the same exposure one level up: if
# run_edge_node_test.py/stress_test.py is killed before reaching their own
# `finally` block (e.g. SIGKILL, a crashed terminal), their socat child and
# virtual PTY pair (/tmp/akvo_edge_node_master|slave) are orphaned.
#
# This script doesn't fix that root cause (see the note printed at the end)
# - it's the reset button: run it, then start a fresh iteration.
#
# Usage:
#   ./cleanup.sh              # stop everything, remove stale virtual-serial state
#   ./cleanup.sh --dry-run    # show what would be stopped/removed, change nothing
#
# Safe to run any time, including when nothing is running (every step is a
# no-op if there's nothing to clean up).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_JSON="$HERE/config_data/config.json"
SYSTEMD_SERVICE="akvo-green"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

log() { echo ""; echo "=== $* ==="; }
did_something=0

# Sends SIGTERM to every PID matching a pgrep pattern, waits up to 5s, then
# SIGKILL's any that are still alive - so a process gets a chance to run its
# own cleanup (e.g. run_edge_node_test.py's `finally` block) before being
# forced.
stop_matching() {
    local label="$1" pattern="$2"
    local pids
    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [[ -z "$pids" ]] && return 0

    did_something=1
    echo "$label: found PID(s) $pids"
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  (dry-run: would send SIGTERM, then SIGKILL after 5s if still alive)"
        return 0
    fi

    kill -TERM $pids 2>/dev/null || true
    for _ in {1..10}; do
        pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
        [[ -z "$pids" ]] && { echo "  stopped cleanly."; return 0; }
        sleep 0.5
    done
    echo "  still alive after 5s, sending SIGKILL: $pids"
    kill -KILL $pids 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 1. The real gateway - via systemd if that's how it's running, so
#    Restart=on-failure doesn't just bring it straight back up.
# ---------------------------------------------------------------------------

log "Real edge node"

if systemctl is-active --quiet "$SYSTEMD_SERVICE" 2>/dev/null; then
    did_something=1
    echo "Managed by systemd ($SYSTEMD_SERVICE) - stopping via systemctl, not a raw kill" \
         "(Restart=on-failure would otherwise just relaunch it)."
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  (dry-run: would run 'sudo systemctl stop $SYSTEMD_SERVICE')"
    else
        sudo systemctl stop "$SYSTEMD_SERVICE"
        echo "  stopped."
    fi
else
    stop_matching "Real edge node (manual run)" "python3? .*edge_node_improved\.py"
fi

# ---------------------------------------------------------------------------
# 2. Mock test harness processes (tests/edge_node_mock/)
# ---------------------------------------------------------------------------

log "Mock test harness"

stop_matching "run_edge_node_test.py" "python3? .*run_edge_node_test\.py"
stop_matching "stress_test.py" "python3? .*stress_test\.py"
stop_matching "mock_devices_slave.py" "python3? .*mock_devices_slave\.py"

# ---------------------------------------------------------------------------
# 3. Virtual-serial socat links - both naming schemes in this repo:
#    tests/*/start_virtual_serial.sh (akvo_modbus_master/slave) and
#    run_edge_node_test.py's own (akvo_edge_node_master/slave).
# ---------------------------------------------------------------------------

log "Virtual serial links (socat)"

stop_matching "socat (virtual serial link)" "socat .*akvo_(modbus|edge_node)_(master|slave)"

STALE_PATHS=(
    /tmp/akvo_modbus_master /tmp/akvo_modbus_slave
    /tmp/akvo_modbus_socat.pid /tmp/akvo_modbus_socat.log
    /tmp/akvo_edge_node_master /tmp/akvo_edge_node_slave
)
for p in "${STALE_PATHS[@]}"; do
    [[ -e "$p" ]] || continue
    did_something=1
    echo "Stale file: $p"
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "  (dry-run: would remove)"
    else
        rm -f "$p"
        echo "  removed."
    fi
done

# ---------------------------------------------------------------------------
# 4. Real UART - whatever's left holding modbus.port open (config.json's
#    real device, e.g. /dev/ttyUSB0). Skipped for a virtual (/tmp/...) port:
#    step 3 above already cleaned that up.
# ---------------------------------------------------------------------------

log "Real UART"

MODBUS_PORT="$(python3 -c "
import json
try:
    print(json.load(open('$CONFIG_JSON'))['modbus']['port'])
except Exception:
    pass
" 2>/dev/null)"

if [[ -z "$MODBUS_PORT" ]]; then
    echo "Couldn't read modbus.port from ${CONFIG_JSON#$HERE/} - skipping."
elif [[ "$MODBUS_PORT" != /dev/* ]]; then
    echo "modbus.port is '$MODBUS_PORT' (not a real device) - nothing to check here."
elif [[ ! -e "$MODBUS_PORT" ]]; then
    echo "$MODBUS_PORT doesn't exist (device unplugged?) - nothing to check."
else
    holders="$(fuser "$MODBUS_PORT" 2>/dev/null || true)"
    if [[ -z "$holders" ]]; then
        echo "$MODBUS_PORT is free."
    else
        did_something=1
        echo "$MODBUS_PORT is held open by PID(s):$holders"
        if command -v lsof >/dev/null 2>&1; then
            lsof "$MODBUS_PORT" 2>/dev/null | tail -n +2 | sed 's/^/  /'
        fi
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "  (dry-run: would fuser -k $MODBUS_PORT)"
        else
            fuser -k -TERM "$MODBUS_PORT" 2>/dev/null || true
            sleep 1
            if [[ -n "$(fuser "$MODBUS_PORT" 2>/dev/null || true)" ]]; then
                fuser -k -KILL "$MODBUS_PORT" 2>/dev/null || true
            fi
            if [[ -z "$(fuser "$MODBUS_PORT" 2>/dev/null || true)" ]]; then
                echo "  $MODBUS_PORT is now free."
            else
                echo "  WARNING: still held after SIGKILL - check manually with: fuser -v $MODBUS_PORT"
            fi
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "Done"

if [[ $did_something -eq 0 ]]; then
    echo "Nothing to clean up - no leftover processes or stale files found."
else
    echo "Cleanup complete. A new iteration can be started now."
fi

echo ""
echo "If this keeps happening: it's usually a Ctrl+C landing while" \
     "edge_node_improved.py is still blocked in init_system()'s initial" \
     "MQTT connect (before its own KeyboardInterrupt handler is active), or" \
     "orphaned awscrt I/O threads from repeated MQTT reconnect attempts" \
     "(MQTTManager.connect() allocates a new EventLoopGroup/ClientBootstrap" \
     "on every retry without shutting the previous one down). Worth fixing" \
     "at the source if this script becomes a regular necessity."
