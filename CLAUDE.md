# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Akvo Green (aka Venko Green) is an industrial IoT edge gateway: it polls Modbus RTU sensors over RS-485 and publishes device data + host telemetry to AWS IoT Core over MQTT (mTLS), running unattended on something like a Raspberry Pi. See `docs/README.md` for the full project README (features, installation, usage) and `docs/edge_node_config/CONFIGURATION.md` for a field-by-field reference of every CSV column and `config.json` key.

## Commands

Repo dependencies (`pymodbus`, `pyserial`, `psutil`, `awsiotsdk`) are on the system `python3`/`pip3` in this dev environment, not in a project-local venv — use `python3`/`pip3` directly rather than assuming a venv is active.

**Build/export the runtime config** (from `src/Venko_Green/`):
```bash
python3 config_manager.py build            # devices.csv/modbus.csv/system.csv/aws.csv -> config.json
python3 config_manager.py build --dry-run  # validate + print, don't write
python3 config_manager.py export           # config.json -> the four CSVs
```

**Run the gateway** (from `src/Venko_Green/`): `python3 edge_node_improved.py`. Editing the CSVs (via `build`) or `config.json` directly triggers a live reload — no restart needed.

**Testing** — four independent suites, no single top-level test command:

- `tests/test_edge_node/` — fast unit tests of `edge_node_improved.py`'s own logic (alarm evaluation, register combining, reload diffing, retry backoff), no hardware/network needed:
  ```bash
  cd tests/test_edge_node && python3 -m pytest -q          # all tests
  python3 -m pytest test_sensor_node.py::test_read_uint32 -v  # a single test
  ```
- `tests/edge_node_mock/` — integration harness: runs the real, unmodified `EdgeNode` against a multi-slave Modbus mock and/or a fake MQTT connection (each independently toggleable via `harness_config.json`'s `fake_modbus`/`fake_mqtt`, or `--no-fake-modbus`/`--no-fake-mqtt`):
  ```bash
  cd tests/edge_node_mock && python3 run_edge_node_test.py --duration 30
  ```
- `tests/akvo_modbus_mock/` and `tests/test_modbus_client/akvo_modbus_api_test_environment/` — exercise the standalone Modbus client (see Known gaps below) via a virtual-serial mock slave (`socat` + `mock_slave.py`); the latter also has a pytest suite (`./run_tests.sh`) and a real-hardware test plan.

No lint/format tooling (flake8/black/ruff/mypy) is configured in this repo.

## Architecture

**`edge_node_improved.py`** (in both `src/Venko_Green/` and duplicated at `src/` — see Known gaps) is the engine, built from small composable classes:

- `ConfigManager` — thread-safe holder for the loaded `config.json`.
- `ModbusManager` — one `ModbusSerialClient`, RLock-guarded (pymodbus serial clients aren't thread-safe); `connect()` retries forever with exponential backoff (5s→60s cap, jittered).
- `MQTTManager` — one AWS IoT Core mTLS connection, same RLock-guarded/backoff-retry pattern.
- `SensorNode`/`DeviceNode` — one sensor/device each; `DeviceNode` tracks alarm state per sensor so `[ALERT]`/`[CLEAR]` log lines fire only on transition, not every poll cycle.
- `EdgeNode` — owns everything above and runs six daemon threads: `scheduler` (queues devices for polling on `poll_interval`), `worker` (single consumer draining the queue — single-threaded because the serial bus is inherently sequential anyway; also the sole heartbeat source for `watchdog`), `publisher` (device snapshots → `aws.topic_pub`), `system_publisher` (CPU/RAM/disk/IP → `aws.topic_system`), `config_watcher` (polls `config.json`'s mtime every 5s and live-reloads), and `watchdog` (calls `os._exit(1)` if `worker` stalls past `gateway.watchdog_timeout` — requires an external process supervisor to actually restart the process).

**Config pipeline**: `config_manager.py` builds `config.json` from four CSVs — `devices.csv` (rows grouped by `device_id` into one device with multiple sensors; validates duplicate names, register overlap, invalid `type`, and that `uint32`/`int32`/`float32` sensors have `count >= 2`), `modbus.csv`, `system.csv`, `aws.csv`. `config.json` can also be hand-edited directly. Live-reload in `edge_node_improved.py` is selective: it only reconnects Modbus/MQTT if that specific config section's hash actually changed, and only rebuilds devices whose config differs (unchanged devices keep their `DeviceNode` instance, and thus their cache/alarm history).

**Sensor types**: only `float`/`float32` apply `scale`/`offset`; `int`/`uint16`/`uint32`/`int32` are read raw. The 32-bit types combine two consecutive holding registers in big-endian word order (`registers[0]` = high 16 bits) — an assumption, not auto-detected; a device using the opposite word order needs `_combine_32bit()` flipped.

## Known gaps in this repo (as of this writing)

- `docs/README.md` is the actual README (moved here from the repo root); its links to `docs/Modbus_client/...` are broken — that directory is currently at `docs/ppModbus_client/` on disk.
- `src/main_modbus.py`, `src/modbus_client.py`, and `src/utils/logger.py` — the standalone Modbus client library `docs/ppModbus_client/` documents — do not currently exist anywhere in this repo (only separate, simpler mock-test copies live under `tests/akvo_modbus_mock/` and `tests/test_modbus_client/.../`). Check git history (pre-dates commit `4562ed9`) if they need restoring.
- `edge_node_improved.py` and `config_manager.py` currently exist as two manually-synced copies (`src/Venko_Green/` and `src/`, duplicated by an earlier external reorg) — there is no build step linking them, so a source change must be applied to both, or the copies will silently diverge.
- AWS IoT certs live under `src/certs/` and `src/Venko_Green/certs/` (gitignored via `*.pem`/`*.key`/`*.crt` — never commit these, and never open the real ones for a task that doesn't need them).
