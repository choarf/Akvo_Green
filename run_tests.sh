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
#   ./run_tests.sh plan        [--allow-real-mqtt]
#
# integration/all refuse to run when tests/edge_node_mock/harness_config.json
# has fake_mqtt=false, since that makes a REAL connection to AWS IoT Core and
# publishes real MQTT messages - pass --allow-real-mqtt to do that on purpose.
#
# Every run writes a dated Markdown report to reports/ (see write_report()
# below) - a suite failing does NOT abort a multi-suite run (all/plan);
# each suite's pass/fail is recorded independently so the report reflects
# everything that ran, and the script's own exit code is 0 only if every
# recorded suite passed.

set -uo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./run_tests.sh unit                     # tests/test_edge_node (fast, no hardware/network)
  ./run_tests.sh communication             # just the Wifi/AWS/Modbus connect-retry-reboot tests
  ./run_tests.sh integration [--duration N] [--allow-real-mqtt]
  ./run_tests.sh stress      [--duration N] [--devices N] [--reboot-after N]
  ./run_tests.sh all         [--duration N] [--allow-real-mqtt]
  ./run_tests.sh plan        [--allow-real-mqtt]

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

  plan         Runs the automated steps of docs/testing/TEST_PLAN.md §7's
               "Recommended pre-release checklist", in order, with that
               checklist's own fixed durations (not --duration): unit,
               then integration for 60s, then stress for 600s (10 min -
               this step alone takes a while). Ignores --duration/
               --devices/--reboot-after. The checklist's remaining two
               steps - the real-hardware suite, and a long background
               soak before a unit's first field deployment - need
               physical hardware or a multi-hour run and aren't
               automated here; a reminder about them prints at the end.
               Every suite runs even if an earlier one fails, so the
               report always reflects all three.

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

Every invocation writes a dated report to reports/<mode>_<timestamp>.md:
who/where/when it ran, each suite's type and pass/fail with a one-line
detail pulled from its own output, an overall conclusion, and
recommendations for what to run next. reports/ is gitignored.
EOF
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HERE/tests/test_edge_node"
MOCK_DIR="$HERE/tests/edge_node_mock"
REPORT_DIR="$HERE/reports"
mkdir -p "$REPORT_DIR"

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

# ---------------------------------------------------------------------------
# Suite runners
# ---------------------------------------------------------------------------

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
            return 1
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

# ---------------------------------------------------------------------------
# Result recording + report generation
# ---------------------------------------------------------------------------

RESULT_NAMES=()
RESULT_TYPES=()
RESULT_STATUSES=()
RESULT_DURATIONS=()
RESULT_DETAILS=()
RESULT_LOGS=()

# Pulls one representative line out of a suite's captured output for the
# report's Detail column: pytest's own summary line, or - for the
# harness/stress scripts, which don't print one - a couple of their own
# key lines, or the first Traceback if something crashed outright.
summarize_log() {
    local log="$1"
    if grep -q "Traceback (most recent call last)" "$log"; then
        grep -m1 "^[A-Za-z.]*Error\|^[A-Za-z.]*Exception" "$log" | tail -1 | sed 's/^/crashed: /'
        return
    fi
    local pytest_line
    pytest_line="$(grep -oE '[0-9]+ (passed|failed|error|skipped)[a-z0-9, .]*' "$log" | tail -1)"
    if [[ -n "$pytest_line" ]]; then
        echo "$pytest_line"
        return
    fi
    if grep -q "Reboot escalation:" "$log"; then
        grep -E "Threads:|RSS MB:|Reboot escalation:" "$log" | paste -sd';' -
        return
    fi
    if grep -q "Final device snapshots" "$log"; then
        echo "gateway ran and shut down cleanly, $(grep -c '\[ALERT\]' "$log") alert(s) logged"
        return
    fi
    tail -1 "$log"
}

# Runs `fn` (one of the run_* functions above), tees its output live to the
# terminal while also capturing it to reports/.<name>.log, and records a
# PASS/FAIL row - without aborting the script even under `set -e`-style
# strictness, so a failure partway through all/plan doesn't hide the
# suites that would have run after it.
record() {
    local name="$1" type="$2" fn="$3"
    local log_file="$REPORT_DIR/.${fn}.log"
    echo ""
    local start_ts=$(date +%s)
    "$fn" 2>&1 | tee "$log_file"
    local status=${PIPESTATUS[0]}
    local end_ts=$(date +%s)

    local pf="PASS"
    [[ $status -ne 0 ]] && pf="FAIL"

    RESULT_NAMES+=("$name")
    RESULT_TYPES+=("$type")
    RESULT_STATUSES+=("$pf")
    RESULT_DURATIONS+=("$((end_ts - start_ts))s")
    RESULT_DETAILS+=("$(summarize_log "$log_file")")
    RESULT_LOGS+=("$log_file")
}

recommendations_for() {
    if [[ "$OVERALL_STATUS" == "FAIL" ]]; then
        echo "- Investigate the FAILED suite(s) above (see their logs under reports/) before merging or deploying anything."
        return
    fi
    case "$MODE" in
        unit|communication)
            echo "- Logic-level tests passed. Run \`./run_tests.sh integration\` before merging a change to the edge node."
            ;;
        integration)
            echo "- The gateway ran cleanly under mocked integration. Run \`./run_tests.sh stress\` before a release."
            ;;
        stress)
            echo "- Check this run's Threads/RSS-growth/reboot-escalation numbers above against docs/testing/TEST_PLAN.md §5's exit criteria - a clean exit code alone doesn't confirm bounded memory/thread growth."
            echo "- If this is pre-field-deployment, also run a longer soak (\`--duration 3600\` or more) - this run's duration doesn't rule out a slow leak."
            ;;
        all)
            echo "- unit + integration passed. Run \`./run_tests.sh stress\` before a release, and the real-hardware suite before deploying to new/changed hardware."
            ;;
        plan)
            echo "- All automated pre-release checklist steps passed (docs/testing/TEST_PLAN.md §7, items 1-3)."
            echo "- Before first field deployment, still run items 4-5 of that checklist: the real-hardware suite, and a long background soak."
            ;;
    esac
}

write_report() {
    local n=${#RESULT_NAMES[@]}
    [[ $n -eq 0 ]] && return 0

    OVERALL_STATUS="PASS"
    for s in "${RESULT_STATUSES[@]}"; do
        [[ "$s" == "FAIL" ]] && OVERALL_STATUS="FAIL"
    done

    local ts_file ts_display
    ts_file="$(date '+%Y%m%d_%H%M%S')"
    ts_display="$(date '+%Y-%m-%d %H:%M:%S %Z')"
    REPORT_FILE="$REPORT_DIR/${MODE}_${ts_file}.md"

    {
        echo "# Akvo Green test report"
        echo ""
        echo "| | |"
        echo "|---|---|"
        echo "| **Date/time** | $ts_display |"
        echo "| **Host** | $(hostname) |"
        echo "| **Run by** | $(whoami) |"
        echo "| **Mode** | \`$MODE\` |"
        echo "| **Overall** | **$OVERALL_STATUS** |"
        echo ""
        echo "## Summary"
        echo ""
        echo "| Suite | Type | Status | Duration | Detail |"
        echo "|---|---|---|---|---|"
        for i in "${!RESULT_NAMES[@]}"; do
            echo "| ${RESULT_NAMES[$i]} | ${RESULT_TYPES[$i]} | ${RESULT_STATUSES[$i]} | ${RESULT_DURATIONS[$i]} | ${RESULT_DETAILS[$i]} |"
        done
        echo ""
        echo "## Conclusion"
        echo ""
        if [[ "$OVERALL_STATUS" == "PASS" ]]; then
            echo "All $n suite(s) run in this pass (\`$MODE\`) completed successfully."
        else
            local fail_count=0
            for s in "${RESULT_STATUSES[@]}"; do [[ "$s" == "FAIL" ]] && fail_count=$((fail_count + 1)); done
            echo "$fail_count of $n suite(s) FAILED. Do not treat this run as a pass."
        fi
        echo ""
        echo "## Recommendations"
        echo ""
        recommendations_for
        echo ""
        echo "## Full logs"
        echo ""
        for i in "${!RESULT_NAMES[@]}"; do
            echo "- ${RESULT_NAMES[$i]}: \`${RESULT_LOGS[$i]#$HERE/}\`"
        done
    } > "$REPORT_FILE"

    echo ""
    echo "=== Report written: ${REPORT_FILE#$HERE/} (overall: $OVERALL_STATUS) ==="
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "$MODE" in
    unit)
        record "Unit" "unit" run_unit
        ;;
    communication)
        record "Communication" "unit" run_communication
        ;;
    integration)
        record "Integration" "integration" run_integration
        ;;
    stress)
        record "Stress" "stress" run_stress
        ;;
    all)
        record "Unit" "unit" run_unit
        record "Integration" "integration" run_integration
        ;;
    plan)
        record "Unit" "unit" run_unit
        DURATION=60
        record "Integration (60s)" "integration" run_integration
        DURATION=600
        record "Stress (600s)" "stress" run_stress
        echo ""
        echo "Not run here (see docs/testing/TEST_PLAN.md §7, items 4-5): the real-hardware"
        echo "suite, and a long background soak before a unit's first field deployment."
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

write_report
[[ "${OVERALL_STATUS:-PASS}" == "PASS" ]]
