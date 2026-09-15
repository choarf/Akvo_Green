# Test Plan — Akvo Green Edge Gateway

The concrete suites, cases, and pass/fail criteria implementing
[`TEST_STRATEGY.md`](TEST_STRATEGY.md). For quick "which command do I
run" lookup without the rationale, see [`TESTING.md`](TESTING.md).

## 1. Traceability — component → suite

| Component (`edge_node_improved.py` unless noted) | Test file(s) | Level |
|---|---|---|
| `ConfigManager` (load/reload, validation integration) | `test_config_schema.py` (via `config/schema.py`), stress test's config-chaos | Unit + Stress |
| `config/schema.py::validate()` | `test_config_schema.py` (14 tests) | Unit |
| `domain/sensors.py` decoder registry | `test_domain_sensors.py` (31 tests) | Unit |
| `ModbusManager` | `test_managers.py`, `test_communication_managers.py` | Unit |
| `MQTTManager` | `test_managers.py`, `test_communication_managers.py` | Unit |
| `WifiManager` | `test_communication_managers.py` | Unit |
| `_RebootEscalator` / `_default_reboot_fn` / `_record_reboot_if_allowed` | `test_communication_managers.py` | Unit |
| `SensorNode` (register read, alarm eval) | `test_sensor_node.py` (20 tests) | Unit |
| `DeviceNode` (poll, alarm transition logging) | `test_device_node.py` (4 tests) | Unit |
| `EdgeNode` (device reload diffing, section hashing, watchdog) | `test_edge_node.py` (9 tests) | Unit |
| Whole gateway, real threads, real config pipeline | `run_edge_node_test.py` | Mocked integration |
| Whole gateway under load + concurrent multi-layer failure | `stress_test.py` | Stress/chaos |

Total unit suite: **112 tests**, run via `python3 -m pytest -q` from
`tests/test_edge_node/` (or `./run_tests.sh unit`), completing in well
under a second.

## 2. Unit suite — `tests/test_edge_node/`

**Setup:** none beyond the packages already on the system Python
(`pymodbus`, `pyserial`, `psutil`, `awsiotsdk`, `pytest`). No hardware,
no network.

```bash
./run_tests.sh unit
# or, for one file / one test:
cd tests/test_edge_node && python3 -m pytest test_sensor_node.py::test_read_uint32 -v
```

| File | Tests | Covers |
|---|---|---|
| `test_config_schema.py` | 14 | Required section/key checks, invalid slave IDs, duplicate sensor names, register overlap, unsupported types, undersized 32-bit register counts, duplicate-slave-across-devices as a *warning* not an error |
| `test_domain_sensors.py` | 31 | Decoder registry completeness, per-type register-count requirements, unscaled int/uint16/uint32/int32 vs. scaled float/float32, roundtrip decode at boundary values (`2147483647`, `-2147483648`, etc.), unknown-type and undersized-register error paths |
| `test_device_node.py` | 4 | One bad sensor doesn't stop the rest of a device's poll; cache populated per sensor; `[ALERT]`/`[CLEAR]` logs only on transition; `snapshot()` returns the cache |
| `test_edge_node.py` | 9 | Device add/update/remove/no-op reload diffing; config-section hashing (order-independent, content-sensitive); watchdog reboots on a stale heartbeat, stays inert when `watchdog_timeout` unset, and doesn't fire while the heartbeat keeps advancing |
| `test_managers.py` | 10 | MQTT/Modbus disconnect safety, publish-triggers-reconnect (cold and on send failure), `_retry_delay` backoff escalation/cap, read-before-connect guard |
| `test_sensor_node.py` | 20 | Alarm threshold table (numeric + non-numeric no-op), raw vs. scaled reads, out-of-range → alarm, 32-bit roundtrips, bus-error/exception paths, unsupported-type exception |
| `test_communication_managers.py` | 24 | See §3 below |

**Exit criteria:** 112/112 pass, 0 skips, runtime <1s (a slower run
signals something's accidentally touching real I/O — check for a
missing mock/monkeypatch).

## 3. Communication-manager suite — `test_communication_managers.py`

The 24 tests specifically covering WiFi/AWS/Modbus's shared
connect/disconnect/retry/reboot-escalation contract (`./run_tests.sh
communication` runs just this file):

- **Per manager** (Modbus, MQTT, WiFi — same shape for all three):
  connect succeeds immediately; connect retries then succeeds (flaky
  factory failing N times); disconnect closes/clears cleanly; reboots
  exactly once when stalled past `reboot_after` (via a background
  thread + a `reboot_fn` that raises a marker exception to unwind the
  otherwise-infinite retry loop, since the real default never returns).
- **WiFi-specific**: `monitor()` detects a drop after a prior successful
  connect and self-heals via `connect()` again.
- **`_RebootEscalator`**: disabled when `reboot_after=None`; fires
  exactly once per `reset()`; can fire again after a fresh `reset()`.
- **`_default_reboot_fn`**: calls `sudo reboot`; falls back to
  `os._exit(1)` if that command can't even start.
- **`_record_reboot_if_allowed` (the loop guard)**: allows up to the
  limit; refuses past it; persists across separate calls (simulating
  surviving a real reboot, since nothing but the file on disk carries
  state); forgets reboots older than the window; starts from zero when
  the history file doesn't exist yet.

No real sockets, serial ports, or AWS connections — `time.sleep` is
mocked to a no-op and `time.time()` to a deterministic fake clock so
backoff/reboot-after timing is tested without waiting in real time.

## 4. Mocked/virtual integration — `run_edge_node_test.py`

**Setup:** `socat` on PATH.

```bash
./run_tests.sh integration                              # 30s, fully mocked
./run_tests.sh integration --duration 60                # longer
cd tests/edge_node_mock && python3 run_edge_node_test.py --offline 3 9   # simulate dead slaves
```

**Scenarios covered by this suite as-is:**
1. Default run — every configured device polled, alarms evaluated,
   `AKVO/data`/`AKVO/system` published, clean shutdown.
2. `--offline <slave-ids>` — one or more slaves never respond; those
   devices' sensors should read `EXCEPTION`/`BUS_ERROR`, other devices
   unaffected.
3. `--no-fake-mqtt` — real AWS IoT connection, mocked Modbus (requires
   valid certs; see the production-AWS caution in
   `tests/edge_node_mock/README.md` before running this one).
4. `--no-fake-modbus` — real serial hardware, fake MQTT.

**Exit criteria:** see `TEST_STRATEGY.md` §6. In practice: check the
run's final "device snapshots" output has every device present with
`status: OK` (or an expected `EXCEPTION`/`BUS_ERROR` for a deliberately
offline slave), and the log shows both connects and both disconnects
with no unhandled traceback in between.

## 5. Stress/chaos — `stress_test.py`

**Setup:** `socat`, `psutil` (already a project dependency).

```bash
./run_tests.sh stress                                    # 240s, 40 devices, 8s reboot timeouts
./run_tests.sh stress --duration 600 --devices 100
./run_tests.sh stress --reboot-after 5
```

Runs the real `EdgeNode` with all four axes active simultaneously:

| Axis | Mechanism |
|---|---|
| High load | `--devices` synthetic devices (default 40), mixed sensor types, 1s poll interval |
| WiFi chaos | Reachability flips randomly every 2-5s (~35% chance per tick) |
| MQTT chaos | Fails whenever WiFi is down, *plus* an independent 10% random failure even when WiFi is up |
| Modbus chaos | ~15% of real reads swapped for an injected fault: dropped bus, `BUS_ERROR`, or truncated register count |
| Config chaos | Every 15-25s, writes an invalid `config.json` (missing key / bad sensor type / out-of-range slave), then restores it |
| Reboot escalation | All four `*_reboot_timeout`s set short (`--reboot-after`, default 8s) — the *real* `_RebootEscalator`/`_default_reboot_fn`/loop-guard code runs; only `subprocess.run`/`os._exit` are intercepted, so nothing ever touches the machine running the test |

**What it reports:** thread-count min/max/end, RSS memory start/end/max/
growth, log records tallied by level, MQTT publish success/failure
counts, Modbus chaos-injection counts by type, config-chaos write count,
and reboot-escalation attempts (plus how many the loop guard refused).

**Exit criteria** (see `TEST_STRATEGY.md` §6 for the general form):
- 0 tracebacks / unhandled exceptions in the log.
- Thread count doesn't grow monotonically over the run (sampled every 2s
  throughout).
- RSS growth stays in the low single-digit MB range for a multi-minute
  run — a baseline reference run (40 devices, 240s, 8s reboot timeouts)
  measured **+1.5MB** over 4 minutes with 0 thread growth (max 12,
  returned to 7 at teardown).
- If reboot timeouts are configured short enough to fire: at least one
  `CRITICAL ... rebooting the system` log line, and — if chaos persists
  past 3 reboots within the hour — at least one `CRITICAL ... Refusing
  to reboot` line afterward, with no further `sudo reboot` attempts
  logged. The same baseline run hit exactly this: 3 allowed reboots
  (shared correctly across the MQTT- and WiFi-triggered escalations,
  confirming the loop guard's cross-manager state sharing), then 2
  further attempts correctly refused.

**Known limitation** (see `TEST_STRATEGY.md` §9): this suite's Modbus
chaos never triggers `ModbusManager`'s *own* `reboot_after`, since it
injects bad data on an already-open connection, not a dead port at
startup — that path is unit-tested directly instead (§3 above).

## 6. Suites out of scope for this plan

Covered by their own, separate documentation — not duplicated here:

- **`tests/akvo_modbus_mock/`** and **`tests/test_modbus_client/...`** —
  the standalone Modbus client library's unit/mocked/real-hardware/soak
  suites. See `TESTING.md` §3-4 and
  `tests/test_modbus_client/akvo_modbus_api_test_environment/HowToTest.md`.
- **Real hardware acceptance** — see
  `real_hardware_tests/REAL_TEST_PLAN.md` and `README.md` under
  `tests/test_modbus_client/akvo_modbus_api_test_environment/`.

## 7. Recommended pre-release checklist

1. `./run_tests.sh unit` — must be 112/112.
2. `./run_tests.sh integration --duration 60` — clean start, poll,
   publish, stop.
3. `./run_tests.sh stress --duration 600` (10 min) or longer — check the
   summary against §5's exit criteria.
4. If deploying to new/changed hardware: the real-hardware suite (§6).
5. Before first field deployment of a unit: a background soak
   (`./run_tests.sh stress --duration 3600` or longer) — see
   `TEST_STRATEGY.md` §9, this hasn't been done yet as of this writing.
