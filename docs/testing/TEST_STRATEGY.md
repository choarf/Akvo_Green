# Test Strategy — Akvo Green Edge Gateway

This is the strategy document: why we test the way we do, what matters
most, and what "done" means. For the concrete list of suites/cases and
how to run them, see [`TEST_PLAN.md`](TEST_PLAN.md). For day-to-day
"which command do I run" reference, see [`TESTING.md`](TESTING.md) —
this document explains the reasoning behind that one.

Scope: `edge_node_improved.py` and its supporting modules (`config_manager.py`,
`config/schema.py`, `domain/sensors.py`) — the unattended edge gateway
that polls Modbus sensors and publishes to AWS IoT Core. The standalone
Modbus client library under `tests/akvo_modbus_mock/` and
`tests/test_modbus_client/` has its own, separate test plan
(`real_hardware_tests/REAL_TEST_PLAN.md`) and isn't duplicated here.

## 1. Why this system needs a specific strategy, not just "write tests"

Three properties of this system shape everything below:

1. **It runs unattended, in the field, with no process supervisor.**
   There's no systemd unit, no container restart policy, nothing to
   notice if the process dies. This is *why* `_RebootEscalator` reboots
   the whole host instead of just exiting (see `CLAUDE.md`'s
   Architecture section) — and it's why testing that mechanism correctly,
   including its failure modes, is not optional polish. A test suite that
   only checks the happy path would miss the exact thing that makes this
   system safe to leave alone.
2. **A bad reboot policy can brick the device worse than the problem it
   was meant to fix.** If a communication layer stays down for a reason
   a reboot can't fix (the router itself down, not the Pi), an
   unguarded reboot-on-timeout would boot-loop the device forever. This
   is the single highest-risk behavior in the system, and it's why
   `_record_reboot_if_allowed`'s loop guard exists and why it's tested
   both in isolation (unit) and under real concurrent chaos (stress).
3. **Silent data corruption is worse than a crash.** A gateway that
   crashes gets noticed. A gateway that silently decodes a `uint32`
   register pair backwards, or fails to fire a HIGH alarm, doesn't — it
   just reports wrong numbers to AWS IoT Core indefinitely. This is why
   `domain/sensors.py`'s decoder registry and `SensorNode`'s alarm
   evaluation have the densest unit coverage of any module (51 tests
   between `test_domain_sensors.py` and `test_sensor_node.py`).

## 2. Objectives

- **Correctness**: every sensor type decodes to the right value; alarm
  thresholds evaluate correctly; config validation rejects exactly the
  configs that would misbehave at runtime, and no others.
- **Resilience**: every communication layer (Modbus/AWS/WiFi) recovers on
  its own from a transient failure, without operator intervention.
- **Safety under sustained failure**: when a layer *can't* recover on its
  own, the reboot-escalation path fires — but never in an unbounded loop.
- **Stability under load and adversarial input**: the process doesn't
  leak threads or memory, and doesn't crash on malformed data, over a
  sustained run with all three communication layers failing concurrently.
- **Config safety**: a hand-edited or malformed `config.json` is rejected
  with a clear error, never partially applied, never crashes the
  `config_watcher` thread.

## 3. Test levels

Four levels, from fastest/narrowest to slowest/broadest. See
`TESTING.md`'s diagram for the original three (unit / mocked integration
/ real hardware) — **stress/chaos is a fourth level added for this
gateway specifically**, sitting between mocked integration and real
hardware: still no real hardware or AWS, but combining load, concurrency,
and fault injection in ways a single-scenario integration run doesn't.

| Level | Tests | Speed | Needs |
|---|---|---|---|
| **Unit** | One function/class's logic, fake collaborators | ms | Python only |
| **Mocked/virtual integration** | Real `EdgeNode` against a mock Modbus bus + fake/real MQTT | seconds-minutes | `socat` |
| **Stress/chaos** | Real `EdgeNode`, high device count, concurrent multi-layer fault injection, sustained duration | minutes | `socat`, `psutil` |
| **Real hardware** | Physical RS-485 device, real AWS IoT Core | minutes-hours | USB-RS485 adapter, a real device |

## 4. Risk-based prioritization

Ranked by (impact if wrong) × (how easy the bug would be to miss without
a targeted test):

1. **Reboot-loop guard failing open** — would boot-loop a field device
   indefinitely. Mitigated by: unit tests on `_record_reboot_if_allowed`
   directly (limit enforcement, window expiry, persistence-across-calls
   simulating surviving a real reboot) *and* live exercise under the
   stress test's concurrent multi-layer chaos (§`stress_test.py`).
2. **A communication layer failing to self-heal** — the gateway looks
   "up" but silently stops reporting data or polling sensors forever.
   Mitigated by: connect/retry/reboot tests per manager
   (`test_communication_managers.py`) plus the stress test's sustained
   WiFi/MQTT chaos.
3. **Decoder/alarm logic errors** — wrong values published, or a real
   HIGH/LOW condition never alarms. Mitigated by: the densest unit
   coverage in the repo (register combining, scale/offset, boundary
   values, alarm transition logic).
4. **Config validation gaps** — a bad config either crashes the process
   or gets silently accepted and misbehaves later. Mitigated by:
   `test_config_schema.py`'s error/warning coverage, plus the stress
   test's live malformed-config injection against a *running* gateway
   (not just a cold `validate()` call).
5. **Resource exhaustion over time** — thread or memory leaks that only
   show up after hours/days unattended. Partially mitigated (see §6
   Known gaps) — the stress test's 2-8 minute runs show no growth, but
   this doesn't rule out a slow leak; a longer soak is the actual test
   for that risk and hasn't been run yet.

## 5. Test environment & tooling

Everything below the real-hardware level runs against `socat` virtual
serial pairs and either a fake MQTT connection or a chaos-injecting fake
— never real AWS credentials, except when a suite is deliberately run
with `fake_mqtt: false` (see `tests/edge_node_mock/README.md`'s
production-AWS caution). One entry point wraps all of it:

```bash
./run_tests.sh unit           # tests/test_edge_node, <1s
./run_tests.sh communication  # just the 3-layer connect/retry/reboot tests
./run_tests.sh integration    # real EdgeNode, mocked bus, --duration N
./run_tests.sh stress         # load + chaos + reboot escalation, --duration N
./run_tests.sh all            # unit + integration
```

## 6. Entry/exit criteria

**Entry** (before running a level): the level below it passes. Don't
chase an integration failure while unit tests are red — fix those first.

**Exit criteria per level:**

- **Unit**: 100% pass, 0 skips. Any new manager/decoder/validation rule
  ships with tests in the same change, not after.
- **Mocked/virtual integration**: gateway starts, connects all three
  layers, polls every configured device at least twice, publishes at
  least one `AKVO/data` and one `AKVO/system` payload, shuts down cleanly
  (both connections explicitly disconnected in the log) — no unhandled
  exception anywhere in the run's log output.
- **Stress/chaos**: 0 unhandled tracebacks; thread count returns to
  baseline after stop (no monotonic growth during the run); RSS memory
  growth stays small relative to duration (a few MB over minutes, not
  tens); if reboot timeouts are configured, escalation fires and the
  loop guard visibly refuses once the limit is hit — never uncapped.
- **Real hardware**: see the Modbus client's own
  `real_hardware_tests/REAL_TEST_PLAN.md` — out of scope here.

## 7. Cadence — when to run what

| When | Run |
|---|---|
| Every change to `edge_node_improved.py`/`config_manager.py`/`config/schema.py`/`domain/sensors.py` | `./run_tests.sh unit` |
| Touched a communication manager or `_RebootEscalator` specifically | `./run_tests.sh communication` |
| Before merging any change to the edge node | `./run_tests.sh integration` (30-60s) |
| Before a release, or after any change to reboot-escalation/config-reload/retry logic | `./run_tests.sh stress` (240s+) |
| Before a field deployment | Real-hardware suite (separate plan) + a long stress/soak run |

## 8. Out of scope (here)

- Physical wiring, real register maps, real AWS IoT Core connectivity —
  covered by the Modbus client's own real-hardware suite.
- Security/penetration testing of the AWS IoT/mTLS setup.
- The `Web/` dashboard front-ends.
- Load beyond what a single mocked Modbus bus + synthetic device count
  can represent — this strategy tests the *gateway's* behavior under
  load, not a real RS-485 bus's physical throughput limits.

## 9. Known gaps (living list — update as these close)

- **No multi-hour/day soak run has been performed.** The stress test
  (§`TEST_PLAN.md`) runs minutes, not hours; a slow leak wouldn't show up
  in that window. Recommended before field deployment: a background run
  of `./run_tests.sh stress --duration 3600` or longer.
- **`ModbusManager`'s own `reboot_after` isn't exercised by the stress
  test** — it only fires from a stalled *initial* connect, and the
  stress test's Modbus chaos injects bad *data* on an otherwise-connected
  bus, not a dead port at startup. That specific path has unit coverage
  (`test_modbus_reboots_once_when_stalled_past_reboot_after`) but not a
  live/stress exercise.
- **`config_manager.py`'s CSV build/export path has no chaos coverage** —
  only `config/schema.py`'s `validate()` (called by both the CSV builder
  and the runtime loader) is stress-tested live; malformed *CSVs* feeding
  `config_manager.py build` aren't part of the stress test.
- See `CLAUDE.md`'s "Known gaps in this repo" for repo-wide items (the
  exposed WiFi password in `certs/wifi_lib.py`, etc.) that affect
  testability but aren't test gaps per se.
