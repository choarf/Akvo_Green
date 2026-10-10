# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Akvo Green (aka Venko Green) is an industrial IoT edge gateway: it polls Modbus RTU sensors over RS-485 and publishes device data + host telemetry to AWS IoT Core over MQTT (mTLS), running unattended on something like a Raspberry Pi. See `docs/README.md` for the full project README (features, installation, usage) and `docs/edge_node_config/CONFIGURATION.md` for a field-by-field reference of every CSV column and `config.json` key.

## Commands

Repo dependencies (`pymodbus`, `pyserial`, `psutil`, `awsiotsdk`) are on the system `python3`/`pip3` in this dev environment, not in a project-local venv — use `python3`/`pip3` directly rather than assuming a venv is active.

**Build/export the runtime config** (from `gateway/`):
```bash
python3 config_manager.py build            # devices.csv/modbus.csv/system.csv/aws.csv -> config.json
python3 config_manager.py build --dry-run  # validate + print, don't write
python3 config_manager.py export           # config.json -> the four CSVs
```

**Run the gateway** (from `gateway/`): `python3 edge_node_improved.py`. Editing the CSVs (via `build`) or `config.json` directly triggers a live reload — no restart needed. Both commands above read/write `config_data/` at the **repo root** by default, regardless of what directory they're run from (see Architecture's Config pipeline note); `--config-dir DIR` points `build`/`export` at another site's folder instead (`sites/<key>/config_data/`, see `sites/README.md`). `tools/push_site.sh <key> <user@pi> [--certs]` deploys the code plus one site's `config.json` (and certs) to a Pi, plus the AKVO Modbus Tool (separate repo `../Tools/AkvoModbus`, packaged to a `.deb` by `tools/build_modbus_tool.sh`, installed to `/opt/akvo-modbus` only when its source hash changed; `--no-modbus-tool` skips it).

**Testing** — four independent suites, no single top-level test command:

- `tests/test_edge_node/` — fast unit tests of `edge_node_improved.py`/`config/schema.py`/`domain/sensors.py`'s own logic (alarm evaluation, register decoding, config validation, reload diffing, retry backoff), no hardware/network needed:
  ```bash
  cd tests/test_edge_node && python3 -m pytest -q          # all tests
  python3 -m pytest test_sensor_node.py::test_read_uint32 -v  # a single test
  ```
- `tests/edge_node_mock/` — integration harness: runs the real, unmodified `EdgeNode` against a multi-slave Modbus mock and/or a fake MQTT connection (each independently toggleable via `harness_config.json`'s `fake_modbus`/`fake_mqtt`, or `--no-fake-modbus`/`--no-fake-mqtt`):
  ```bash
  cd tests/edge_node_mock && python3 run_edge_node_test.py --duration 30
  ```
- `tests/akvo_modbus_mock/` and `tests/test_modbus_client/akvo_modbus_api_test_environment/` — exercise the standalone Modbus client (see Known gaps below) via a virtual-serial mock slave (`socat` + `mock_slave.py`); the latter also has a pytest suite (`./run_tests.sh`) and a real-hardware test plan.

See `docs/testing/TESTING.md` for the full unit/mocked/stress/real-hardware breakdown and a decision table for which suite to run for a given change, `docs/testing/TEST_STRATEGY.md` for the risk-based reasoning behind it, and `docs/testing/TEST_PLAN.md` for the concrete case-by-case coverage (including `tests/edge_node_mock/stress_test.py`, run via `./run_tests.sh stress`).

No lint/format tooling (flake8/black/ruff/mypy) is configured in this repo.

## Architecture

**`edge_node_improved.py`** (in `gateway/`) is the engine, built from small composable classes:

- `ConfigManager` — thread-safe holder for the loaded `config.json`; `load()`/`reload()` validate against `config/schema.py` and raise on failure, so a bad hand-edited `config.json` is rejected immediately (at construction, or at the next `config_watcher` tick, keeping the last-good config) instead of surfacing later as a scattered error in whichever thread hits the missing field first.
- `ModbusManager` — one `ModbusSerialClient`, RLock-guarded (pymodbus serial clients aren't thread-safe); `connect()` retries forever with exponential backoff (5s→60s cap, jittered).
- `MQTTManager` — one AWS IoT Core mTLS connection, same RLock-guarded/backoff-retry pattern.
- `WifiManager` — the third, independent communication layer: monitors host network reachability (a real TCP connect to `gateway.wifi_check_host`/`wifi_check_port`, not any single SSID — see its docstring) via a dedicated `monitor()` thread, since unlike Modbus/MQTT there's no "read"/"publish" call site to piggyback a self-heal check on. Same `connect()`/`disconnect()` shape as the other two.
- `_RebootEscalator` — shared by all three communication managers' `connect()` loops *and* `EdgeNode.watchdog()` (see below): if a connect() attempt (or, for `watchdog`, the worker heartbeat) has been stalled longer than `reboot_after` seconds (`gateway.modbus_reboot_timeout`/`aws_reboot_timeout`/`wifi_reboot_timeout`/`watchdog_timeout` — all optional, unset = disabled), calls `reboot_fn` once. Default `reboot_fn` (`_default_reboot_fn`) reboots the whole host via `sudo reboot` — not just this process — because field units run with no process supervisor of any kind. Requires passwordless `sudo reboot` for whatever user runs the gateway; falls back to `os._exit(1)` if the reboot command itself can't be started. Guarded by `_record_reboot_if_allowed`: past `_REBOOT_LOOP_LIMIT` (3) reboots in `_REBOOT_LOOP_WINDOW` (1h), persisted to `logs/reboot_history.json` so it survives the reboots it's counting, it refuses to reboot again and just lets the underlying retry loop keep running — stops a problem a reboot can't fix (e.g. the router itself down, not the Pi) from boot-looping the device.
- `SensorNode`/`DeviceNode` — one sensor/device each; `SensorNode.read()` delegates register decoding to `domain/sensors.py`'s decoder registry (see below); `DeviceNode` tracks alarm state per sensor so `[ALERT]`/`[CLEAR]` log lines fire only on transition, not every poll cycle.
- `EdgeNode` — owns everything above and runs seven daemon threads: `scheduler` (queues devices for polling on `poll_interval`), `worker` (single consumer draining the queue — single-threaded because the serial bus is inherently sequential anyway; also the sole heartbeat source for `watchdog`), `publisher` (device snapshots → `aws.topic_pub`), `system_publisher` (CPU/RAM/disk/IP → `aws.topic_system`), `config_watcher` (polls `config.json`'s mtime every 5s and live-reloads), `watchdog` (via its own `_RebootEscalator` instance, `reset()` on every heartbeat advance and `check()` every 5s tick — reboots, same as the three communication managers, if `worker` stalls past `gateway.watchdog_timeout`), and `WifiMonitor` (runs `WifiManager.monitor()`).

**`config/schema.py`** is the single source of truth for what a valid `config.json` looks like — `validate(config) -> (errors, warnings)`, checked by *both* `config_manager.py`'s CSV build (`ConfigManager.validate()`) and `edge_node_improved.py`'s runtime `ConfigManager.load()`/`reload()`. Before this existed, only the build side validated anything; a hand-edited `config.json` that skipped `config_manager.py` had zero validation.

**`domain/sensors.py`** holds the sensor-type decoder registry (`DECODERS: dict[str, SensorDecoder]`) and `decode(sensor_type, registers, scale, offset)`. Adding a new sensor type means adding one `SensorDecoder` subclass and registering it here, not editing an if/else in `SensorNode.read()`. `config/schema.py`'s `VALID_SENSOR_TYPES`/`MULTI_REGISTER_TYPES` are derived from this registry (`set(DECODERS)` / decoders with `register_count >= 2`), not hardcoded separately.

**Local history dashboard** (`gateway/history_web.py` + static `gateway/web/`): a separate process/systemd service (`akvo-history-web`), not part of `EdgeNode` - it opens `data/history.db` read-only (`mode=ro`) per request, so it can never block the publisher threads that write it. Stdlib only (`ThreadingHTTPServer`); the page draws its own SVG charts (`web/chart.js`) so it works offline. Serves only a fixed whitelist of static files; `/api/*` queries are plain functions over a connection (tested in `tests/test_edge_node/test_history_web.py`). Enabled by `system.csv` `web_enabled`/`web_port` -> `config.json` `web` section; with it off the service exits 0 at start. Read at start only: `tools/push_site.sh` restarts it on every push. Connection status (Sistema page) comes from `EdgeNode.write_status()`, which the publisher thread writes atomically each cycle to `STATUS_PATH` (`/dev/shm/akvo-green-status.json`; history_web.py duplicates the path rather than importing the gateway). `MQTTManager.connected` is driven by the AWS library's `on_connection_interrupted`/`on_connection_resumed` callbacks - without them a dropped link is invisible, since `publish()` (QoS 1, not awaited) doesn't raise during an outage.

**WiFi networks** (optional `wifi` section, `system.csv` `wifi1_ssid`/`wifi2_ssid`): only SSIDs live in `config.json`; passwords stay in the git-ignored `sites/<key>/wifi.csv`. Applied by `tools/push_site.sh` (via `tools/wifi_profiles.py`, tested in `tests/test_edge_node/test_wifi_profiles.py`) as NetworkManager keyfiles `akvo-wifi1`/`akvo-wifi2`, piped to `sudo bash -s` over SSH. The gateway never touches WiFi config; `WifiManager` only checks reachability.

**Config pipeline**: `config_manager.py` builds `config.json` from four CSVs — `devices.csv` (rows grouped by `device_id` into one device with multiple sensors), `modbus.csv`, `system.csv`, `aws.csv` — then validates the fully-assembled config via `config/schema.py` before writing (or before printing, for `--dry-run`). `config.json` and the four CSVs live in `config_data/` at the **repo root** (`config_manager.py`'s `REPO_ROOT`/`CONFIG_DIR`) — not nested under `gateway/` — specifically so the real gateway and every test suite (`tests/edge_node_mock/run_edge_node_test.py`/`stress_test.py`/`mock_devices_slave.py`, which all default to reading it too) read and build from the exact same file. `config.json` can also be hand-edited directly; it's validated the same way either way. `edge_node_improved.py` resolves both this path and `aws.{ca,cert,key}` against stable anchors (`REPO_ROOT`/`BASE_DIR`, both `Path(__file__).resolve().parent`-derived) rather than the process's working directory, so a manual run from the wrong directory doesn't break either. Live-reload in `edge_node_improved.py` is selective: it only reconnects Modbus/MQTT if that specific config section's hash actually changed, and only rebuilds devices whose config differs (unchanged devices keep their `DeviceNode` instance, and thus their cache/alarm history).

**Sensor types**: only `float`/`float32` apply `scale`/`offset`; `int`/`uint16`/`uint32`/`int32` are read raw. The 32-bit types combine two consecutive holding registers in big-endian word order (`registers[0]` = high 16 bits) — an assumption, not auto-detected; a device using the opposite word order needs `domain/sensors.py::_combine_32bit()` flipped.

## Known gaps in this repo (as of this writing)

- `docs/README.md` is the actual README (moved here from the repo root); its links to `docs/Modbus_client/...` are broken — that directory is currently at `docs/ppModbus_client/` on disk.
- `modbus_client/main_modbus.py`, `modbus_client/modbus_client.py`, and `modbus_client/utils/logger.py` — the standalone Modbus client library `docs/ppModbus_client/` documents — have been restored at the repo root (previously missing entirely; only separate, simpler mock-test copies existed under `tests/akvo_modbus_mock/` and `tests/test_modbus_client/.../`).
- AWS IoT certs live under `gateway/certs/` (gitignored via `*.pem`/`*.key`/`*.crt` — never commit these, and never open the real ones for a task that doesn't need them).
- `gateway/certs/wifi_lib.py` and `gateway/certs/mainAkvo.py` are unused legacy scripts (not imported by anything) left over from an earlier reorg; `wifi_lib.py` contains a hardcoded WiFi SSID/password in plaintext, already committed to git history.
- The `src/` → `gateway/` rename (and `modbus_client/` restoration above) is a recent, still-uncommitted change (`git status` shows it staged) — if you hit a `ModuleNotFoundError`/stale-path error anywhere, check whether the tool/doc hitting it still says `src/`; `tests/test_edge_node/conftest.py`, `tests/edge_node_mock/{run_edge_node_test.py,stress_test.py}`, and `install.sh` have all been updated for this already, but a `src/`-era reference could still be lurking elsewhere.
- `config_data/config.json` currently sets `gateway.watchdog_timeout: 120` — `_default_reboot_fn` (real `sudo reboot`) is **live**, not dormant: if the worker thread stalls >120s on whatever host runs this config (including a dev machine, if `install.sh` has set up passwordless `sudo reboot` there), it will actually reboot. The three `*_reboot_timeout` settings (`modbus_reboot_timeout`/`aws_reboot_timeout`/`wifi_reboot_timeout`) remain unset. Think about whether `watchdog_timeout` belongs in the checked-in dev config at all before changing anything else here.
