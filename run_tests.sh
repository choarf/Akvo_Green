#!/usr/bin/env bash
# Run the edge-node test suites. See CLAUDE.md's Testing section for what
# each suite actually covers.
#
# Usage:
#   ./run_tests.sh unit                     # tests/test_edge_node (fast, no hardware/network)
#   ./run_tests.sh communication             # just the Wifi/AWS/Modbus connect-retry-reboot tests
#   ./run_tests.sh integration [--duration N] [--allow-real-mqtt]
#   ./run_tests.sh stress      [--duration N] [--devices N] [--reboot-after N]
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
  ./run_tests.sh stress      [--duration N] [--devices N] [--reboot-after N]
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

  stress       Runs tests/edge_node_mock/stress_test.py: the real EdgeNode
               under combined load (many synthetic devices, 1s polling),
               WiFi/MQTT/Modbus chaos injection, malformed live config
               edits, and reboot-escalation under the loop guard - all
               against the mocked harness (never real hardware/AWS), with
               subprocess.run/os._exit intercepted so a triggered reboot
               never touches this machine. Reports thread/memory growth
               and a full activity summary at the end. --duration here
               overrides the default 240s; --devices overrides 40;
               --reboot-after overrides the 8s reboot/watchdog timeout.

  all          Runs unit, then integration.

  --duration N       Seconds the integration/stress run stays up (default:
                      30 for integration, 240 for stress).
  --devices N        Stress only: synthetic device count (default: 40).
  --reboot-after N   Stress only: reboot/watchdog timeout in seconds
                      (default: 8).
  --allow-real-mqtt  Required to run integration/all when
                      tests/edge_node_mock/harness_config.json has
                      fake_mqtt=false - that mode makes a REAL connection to
                      AWS IoT Core and publishes real MQTT messages, not a
                      virtual one. Without this flag, such a run is refused.
                      (stress never uses harness_config.json - it always
                      fakes MQTT itself, so this flag doesn't apply to it.)

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

DURATION=
ALLOW_REAL_MQTT=0
DEVICES=
REBOOT_AFTER=

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
        --devices)
            DEVICES="$2"
            shift 2
            ;;
        --reboot-after)
            REBOOT_AFTER="$2"
            shift 2
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

    (cd "$MOCK_DIR" && python3 run_edge_node_test.py --duration "${DURATION:-30}")
}

run_stress() {
    echo "=== STRESS: tests/edge_node_mock/stress_test.py ==="
    local args=(--duration "${DURATION:-240}")
    [[ -n "$DEVICES" ]] && args+=(--devices "$DEVICES")
    [[ -n "$REBOOT_AFTER" ]] && args+=(--reboot-after "$REBOOT_AFTER")
    (cd "$MOCK_DIR" && python3 stress_test.py "${args[@]}")
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
    stress)
        run_stress
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
