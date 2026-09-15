# Testing Akvo Green

Akvo Green has four independent test suites, spanning three levels — unit, mocked/virtual integration, and real hardware. There is no single top-level test command; which suite(s) to run depends on what you changed. This document explains the levels, maps them to the suites, and gives the setup for each. Each suite also has its own, more detailed doc — linked below — for day-to-day use.

## The three levels

```text
UNIT                    MOCKED / VIRTUAL INTEGRATION            REAL HARDWARE
(pure logic,            (real production code, fake             (physical RS-485 device,
 no I/O)                 serial port + fake/real AWS)             real AWS IoT Core)
     |                            |                                      |
milliseconds              seconds - minutes                       minutes - hours
no hardware               socat virtual serial pair               USB-RS485 adapter +
no network                needed; AWS optional                    a real Modbus device
```

- **Unit** — tests one function/class's logic in isolation, with fake collaborators standing in for `ModbusManager`/`MQTTManager`/serial connections. Fastest, runs anywhere, no setup beyond Python packages.
- **Mocked/virtual integration** — runs the *real* production code (the actual `EdgeNode` or the actual `ModbusClient`) against a simulated Modbus bus, using `socat` to create a virtual serial port pair and a small Python script pretending to be a Modbus slave on the other end. Exercises real serial framing, real thread/connection logic, real config parsing — just not real hardware.
- **Real hardware** — the actual thing: a USB-RS485 adapter wired to a physical Modbus device, and (optionally) a real AWS IoT Core connection. The only level that validates wiring, real register maps, and physical failure/recovery behavior.

## Which suite do I run?

| I changed... | Run this |
|---|---|
| Alarm evaluation, register combining, config-reload diffing, retry backoff, or any other logic inside `edge_node_improved.py` | **§1** `tests/test_edge_node/` (unit) |
| Anything in the edge node and want to see the whole gateway actually run — multiple simulated slaves, alarms, an offline device, MQTT publishes | **§2** `tests/edge_node_mock/` (mocked integration) |
| `config_manager.py` (CSV ↔ `config.json`) | **§2** `tests/edge_node_mock/` — it builds/loads real `config.json` |
| The standalone Modbus client library (`modbus_client.py` — see the note in [Known repo state](#known-repo-state) below) | **§3** `tests/akvo_modbus_mock/` or **§4** `tests/test_modbus_client/.../` (mocked integration) |
| Anything, before deploying to a real gateway/sensor | **§4** `real_hardware_tests/` (real hardware) |
| Nothing, but you want a long-run reliability check before production sign-off | `run_soak_test.py` under `real_hardware_tests/` (real hardware, hours) |

## 1. `tests/test_edge_node/` — unit tests of the edge node

**Level:** Unit. **Needs:** nothing but Python packages — no `socat`, no serial port, no AWS.

Tests `SensorNode`, `DeviceNode`, and `EdgeNode`'s own logic (alarm thresholds, partial config-reload diffing, the `_retry_delay` backoff curve, the watchdog), plus `domain/sensors.py`'s decoder registry (register decoding for every sensor type) and `config/schema.py`'s `validate()` (the same schema both the CSV build and the runtime config loader check against) — using fake `ModbusManager`/MQTT collaborators instead of real connections. 87 tests, <1s.

```bash
cd tests/test_edge_node
pip install -r requirements.txt   # only if these packages aren't already installed
python3 -m pytest -q                                          # all tests
python3 -m pytest test_sensor_node.py::test_read_uint32 -v    # a single test
python3 -m pytest --cov=edge_node_improved --cov-report=term-missing -q  # coverage
```

## 2. `tests/edge_node_mock/` — edge node integration harness

**Level:** Mocked integration (Modbus and MQTT can each be independently switched to real). **Needs:** `socat`; a real AWS IoT thing only if you disable `fake_mqtt`.

Runs the real, unmodified `EdgeNode` against a mock server that simulates every device/slave from your `config.json` on one virtual serial bus (with realistic drifting sensor values and alarm excursions), and a fake MQTT connection that prints instead of publishing to AWS. Both fakes are controlled by `harness_config.json`'s `fake_modbus`/`fake_mqtt` flags, or by CLI override.

```bash
cd tests/edge_node_mock
python3 run_edge_node_test.py --duration 30                    # fully mocked, default
python3 run_edge_node_test.py --duration 60 --offline 3 9      # simulate two dead slaves
python3 run_edge_node_test.py --no-fake-mqtt                   # mock bus, real AWS IoT
python3 run_edge_node_test.py --no-fake-modbus                 # real serial port, fake MQTT
```

Full configuration reference, the production-AWS caution for `fake_mqtt: false`, and a manual (multi-terminal) walkthrough: [`tests/edge_node_mock/README.md`](../../tests/edge_node_mock/README.md).

## 3. `tests/akvo_modbus_mock/` — Modbus client quick mock

**Level:** Mocked integration. **Needs:** `socat`.

A minimal, manual, single-slave virtual-serial setup for poking at the Modbus client API interactively — five commands across three terminals, no pytest involved.

```bash
sudo apt install socat && python3 -m pip install pyserial pymodbus
# Terminal 1
./start_virtual_serial.sh
# Terminal 2
python3 mock_slave.py --port /tmp/akvo_modbus_slave --slave 1
# Terminal 3
python3 test_modbus_client.py --port /tmp/akvo_modbus_master
# when done
./stop_virtual_serial.sh
```

Use `--slave 2` on the client test to exercise a no-response/timeout condition against the slave-1 mock. See [`tests/akvo_modbus_mock/README.md`](../../tests/akvo_modbus_mock/README.md). A ready-made `init.sh` also runs the full sequence (serial + slave + client test) in one command with automatic cleanup.

## 4. `tests/test_modbus_client/...` — Modbus client pytest suite + real hardware

**Level:** Unit + mocked integration + real hardware + soak (this one directory spans all of them). **Needs:** `socat` for the virtual tests; a USB-RS485 adapter and a physical Modbus device for the real ones.

The most complete of the four suites, with its own exhaustive walkthrough — [`HowToTest.md`](../../tests/test_modbus_client/akvo_modbus_api_test_environment/HowToTest.md) — covering everything summarized below.

**Setup:**
```bash
cd tests/test_modbus_client/akvo_modbus_api_test_environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install socat
```

**Unit tests** (no serial port needed):
```bash
python -m pytest tests/test_api_unit.py -v
```

**Virtual integration** (start the mock slave first, in another terminal — `./start_virtual_serial.sh` then `python3 mock_slave.py --port /tmp/akvo_modbus_slave --slave 1`):
```bash
python -m pytest tests/test_api_integration.py -v
python3 test_modbus_client.py              # human-readable run against the mock
python -m pytest -v                        # everything
python -m pytest --cov=modbus_client --cov-report=term-missing
./stop_virtual_serial.sh                   # when done
```

**Real hardware** (wire a USB-RS485 adapter to a real Modbus device; add your user to the `dialout` group if `/dev/ttyUSB0` isn't accessible; edit `real_hardware_tests/config.json` with the device's real port/serial settings/register map — keep `write_tests_enabled: false` until you've confirmed which registers are safe to write):
```bash
python3 real_hardware_tests/test_real_hardware.py
# or
./real_hardware_tests/run_real_tests.sh
```
Results are saved under `real_hardware_tests/results/`. Full setup (wiring, permissions, write-test safety) and the failure-recovery test procedures (cable disconnect, device power-cycle, wrong slave ID): [`real_hardware_tests/README.md`](../../tests/test_modbus_client/akvo_modbus_api_test_environment/real_hardware_tests/README.md) and [`REAL_TEST_PLAN.md`](../../tests/test_modbus_client/akvo_modbus_api_test_environment/real_hardware_tests/REAL_TEST_PLAN.md).

**Soak / long-duration** (real hardware, hours):
```bash
python3 real_hardware_tests/run_soak_test.py         # default: 1 hour
python3 real_hardware_tests/run_soak_test.py 600      # 10 minutes
python3 real_hardware_tests/run_soak_test.py 600 2    # 10 minutes, 2s poll interval
```
Recommended before production sign-off: 1 hour, then 8 hours, then a 24-hour acceptance run.

## Known repo state

- `tests/akvo_modbus_mock/modbus_client.py` and `tests/test_modbus_client/.../modbus_client.py` are each a standalone copy of the Modbus client used only by their own test suite — neither imports from `src/`.
- The actual `src/modbus_client.py`/`src/main_modbus.py` that `docs/ppModbus_client/` documents do not currently exist in this repo (see `CLAUDE.md`'s "Known gaps" section) — the two test copies above are the only surviving code, and are what suites 3 and 4 actually exercise.
- `HowToTest.md` (§32) notes a known issue in `modbus_client.py`'s statistics: success is recorded before checking whether the response is a Modbus exception, so an exception response can be double-counted as both a success and an error. Don't rely on `success_rate` as a production acceptance metric until that's fixed.
