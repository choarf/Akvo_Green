#!/usr/bin/env bash
# Run the edge-node test suites. See CLAUDE.md's Testing section for what
# each suite actually covers.
#
# Usage:
#   ./run_tests.sh unit                     # tests/test_edge_node (fast, no hardware/network)
#   ./run_tests.sh communication             # just the Wifi/AWS/Modbus connect-retry-reboot tests
#   ./run_tests.sh integration [--duration N] [--allow-real-mqtt]
#   ./run_tests.sh all         [--duration N] [--allow-real-mqtt]
#
# integration/all refuse to run when tests/edge_node_mock/harness_config.json
# has fake_mqtt=false, since that makes a REAL connection to AWS IoT Core and
# publishes real MQTT messages - pass --allow-real-mqtt to do that on purpose.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./run_tests.sh unit                     # tests/test_edge_node (fast, no hardware/network)
  ./run_tests.sh communication             # just the Wifi/AWS/Modbus connect-retry-reboot tests
  ./run_tests.sh integration [--duration N] [--allow-real-mqtt]
  ./run_tests.sh all         [--duration N] [--allow-real-mqtt]

  unit         Runs the fast pytest suite in tests/test_edge_node
               (edge_node_improved.py / config/schema.py / domain/sensors.py
               logic - alarms, register decoding, config validation, reload
               diffing, retry backoff). No hardware/network needed. Already
               includes the communication-manager tests below.

  communication  Runs just tests/test_edge_node/test_communication_managers.py:
               connect/disconnect/retry/reboot-escalation for the three
               independent communication layers (WifiManager, MQTTManager/
               AWS, ModbusManager) - no real sockets/serial/AWS used.

  integration  Runs tests/edge_node_mock/run_edge_node_test.py: the real,
               unmodified EdgeNode against a mock multi-slave Modbus server
               over a virtual serial port (socat), for --duration seconds
               (default 30).

  all          Runs unit, then integration.

  --duration N       Seconds the integration run stays up (default: 30).
  --allow-real-mqtt  Required to run integration/all when
                      tests/edge_node_mock/harness_config.json has
                      fake_mqtt=false - that mode makes a REAL connection to
                      AWS IoT Core and publishes real MQTT messages, not a
                      virtual one. Without this flag, such a run is refused.

  -h, --help   Show this help.
EOF
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HERE/tests/test_edge_node"
MOCK_DIR="$HERE/tests/edge_node_mock"

MODE="${1:-}"
if [[ "$MODE" == "-h" || "$MODE" == "--help" || -z "$MODE" ]]; then
    usage
    exit $([[ "$MODE" == "-h" || "$MODE" == "--help" ]] && echo 0 || echo 2)
fi
shift || true

DURATION=30
ALLOW_REAL_MQTT=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --allow-real-mqtt)
            ALLOW_REAL_MQTT=1
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

run_unit() {
    echo "=== UNIT: tests/test_edge_node ==="
    (cd "$UNIT_DIR" && python3 -m pytest -v)
}

run_communication() {
    echo "=== COMMUNICATION: WiFi/AWS/Modbus connect-retry-reboot ==="
    (cd "$UNIT_DIR" && python3 -m pytest -v test_communication_managers.py)
}

run_integration() {
    echo "=== MOCKED/VIRTUAL INTEGRATION: tests/edge_node_mock ==="

    if [[ $ALLOW_REAL_MQTT -eq 0 ]]; then
        fake_mqtt="$(python3 -c "import json; print(json.load(open('$MOCK_DIR/harness_config.json')).get('fake_mqtt', True))")"
        if [[ "$fake_mqtt" != "True" ]]; then
            echo "Refusing to run: $MOCK_DIR/harness_config.json has fake_mqtt=false," >&2
            echo "which connects to a REAL AWS IoT Core endpoint and publishes real MQTT" >&2
            echo "messages. Re-run with --allow-real-mqtt to do that on purpose, or fix" >&2
            echo "harness_config.json back to fake_mqtt=true for a fully virtual test." >&2
            exit 1
        fi
    fi

    (cd "$MOCK_DIR" && python3 run_edge_node_test.py --duration "$DURATION")
}

case "$MODE" in
    unit)
        run_unit
        ;;
    communication)
        run_communication
        ;;
    integration)
        run_integration
        ;;
    all)
        run_unit
        run_integration
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
