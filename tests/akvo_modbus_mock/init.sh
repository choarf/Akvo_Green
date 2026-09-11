#!/usr/bin/env bash

set -e

# ============================================================
# AKVO Modbus Test Runner
#
# Starts:
#   1. Virtual serial connection
#   2. Mock Modbus slave
#   3. Modbus client test
# ============================================================

MASTER_PORT="/tmp/akvo_modbus_master"
SLAVE_PORT="/tmp/akvo_modbus_slave"

VIRTUAL_SERIAL_SCRIPT="./start_virtual_serial.sh"
MOCK_SLAVE="./mock_slave.py"
TEST_CLIENT="./test_modbus_client.py"

SLAVE_ID=1

# ------------------------------------------------------------
# Cleanup function
# ------------------------------------------------------------
cleanup() {
    echo
    echo "============================================================"
    echo " Cleaning up"
    echo "============================================================"

    if [ -n "$SLAVE_PID" ] && kill -0 "$SLAVE_PID" 2>/dev/null; then
        echo "Stopping mock slave (PID $SLAVE_PID)..."
        kill "$SLAVE_PID" 2>/dev/null || true
    fi

    if [ -n "$VIRTUAL_PID" ] && kill -0 "$VIRTUAL_PID" 2>/dev/null; then
        echo "Stopping virtual serial (PID $VIRTUAL_PID)..."
        kill "$VIRTUAL_PID" 2>/dev/null || true
    fi

    wait 2>/dev/null || true

    echo "Cleanup complete."
}

trap cleanup EXIT INT TERM

# ------------------------------------------------------------
# Check files
# ------------------------------------------------------------
echo "============================================================"
echo " AKVO Modbus Test"
echo "============================================================"

if [ ! -f "$VIRTUAL_SERIAL_SCRIPT" ]; then
    echo "ERROR: $VIRTUAL_SERIAL_SCRIPT not found."
    exit 1
fi

if [ ! -f "$MOCK_SLAVE" ]; then
    echo "ERROR: $MOCK_SLAVE not found."
    exit 1
fi

if [ ! -f "$TEST_CLIENT" ]; then
    echo "ERROR: $TEST_CLIENT not found."
    exit 1
fi

# ------------------------------------------------------------
# Make sure virtual serial script is executable
# ------------------------------------------------------------
chmod +x "$VIRTUAL_SERIAL_SCRIPT"

# ------------------------------------------------------------
# Remove stale serial devices
# ------------------------------------------------------------
echo
echo "Removing stale virtual serial devices..."

rm -f "$MASTER_PORT" "$SLAVE_PORT"

# ------------------------------------------------------------
# Start virtual serial
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Starting virtual serial"
echo "============================================================"

"$VIRTUAL_SERIAL_SCRIPT" &

VIRTUAL_PID=$!

echo "Virtual serial PID: $VIRTUAL_PID"

# ------------------------------------------------------------
# Wait for virtual serial devices
# ------------------------------------------------------------
echo
echo "Waiting for virtual serial ports..."

for i in {1..50}; do

    if [ -e "$MASTER_PORT" ] && [ -e "$SLAVE_PORT" ]; then
        echo "Virtual serial ports ready."
        break
    fi

    sleep 0.1

done

if [ ! -e "$MASTER_PORT" ] || [ ! -e "$SLAVE_PORT" ]; then
    echo
    echo "ERROR: Virtual serial ports were not created."
    echo
    echo "Expected:"
    echo "  $MASTER_PORT"
    echo "  $SLAVE_PORT"
    exit 1
fi

echo
echo "Master: $MASTER_PORT"
echo "Slave : $SLAVE_PORT"

# ------------------------------------------------------------
# Start mock Modbus slave
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Starting mock Modbus slave"
echo "============================================================"

python3 "$MOCK_SLAVE" \
    --port "$SLAVE_PORT" \
    --slave "$SLAVE_ID" &

SLAVE_PID=$!

echo "Mock slave PID: $SLAVE_PID"

# ------------------------------------------------------------
# Give slave time to initialize
# ------------------------------------------------------------
sleep 1

# ------------------------------------------------------------
# Check slave process
# ------------------------------------------------------------
if ! kill -0 "$SLAVE_PID" 2>/dev/null; then
    echo
    echo "ERROR: Mock Modbus slave stopped unexpectedly."
    exit 1
fi

# ------------------------------------------------------------
# Run Modbus client test
# ------------------------------------------------------------
echo
echo "============================================================"
echo " Running Modbus client test"
echo "============================================================"

python3 "$TEST_CLIENT" \
    --port "$MASTER_PORT"

TEST_RESULT=$?

# ------------------------------------------------------------
# Result
# ------------------------------------------------------------
echo
echo "============================================================"

if [ "$TEST_RESULT" -eq 0 ]; then
    echo " MODBUS TEST: PASSED"
else
    echo " MODBUS TEST: FAILED"
fi

echo "============================================================"

exit "$TEST_RESULT"