# Akvo Green

Industrial IoT edge gateway that reads sensor data from Modbus RTU devices (RS‑485) and publishes it to AWS IoT Core over MQTT. Designed to run unattended on a Raspberry Pi or similar edge device, with hot-reloadable configuration and automatic reconnection for both the serial bus and the cloud connection.

```text
Modbus RTU sensors (RS‑485)
        |
        v
ModbusClient / ModbusManager  --  config.json (devices, polling, AWS)
        |
        v
Edge Node (scheduler, worker, publisher threads)
        |
        +--> data/history.db  (optional local SQLite history, mirrors config.json)
        |
        v
AWS IoT Core (MQTT, mTLS)
```

## Features

- **Modbus RTU client (V3)** — thread-safe wrapper around PyModbus supporting FC01/02/03/04/05/06/15/16, connection/reconnection helpers, input validation, and per-call statistics (success rate, response time). See [`docs/Modbus_client/AKVO_Modbus_Client_API_V3.md`](docs/Modbus_client/AKVO_Modbus_Client_API_V3.md) for the full API reference.
- **Edge Node engine** (`gateway/edge_node_improved.py`) — polls configured devices/sensors on a schedule, evaluates min/max alarms, and publishes device data and host telemetry (CPU/RAM/disk/IP) to AWS IoT via MQTT. Modbus and MQTT connections retry independently and forever, so a dead serial bus doesn't block cloud reporting.
- **Live config reload** — a background watcher detects changes to `config.json` and reconnects Modbus/MQTT or rebuilds only the affected devices, without restarting the process.
- **Local SQLite history (optional)** (`gateway/storage.py`) — records every published sensor reading and host-telemetry sample to `data/history.db` for on-site inspection with plain SQL, and keeps a one-way mirror of `config.json`'s devices/sensors (with a change log) so old readings stay interpretable after the config changes. Off by default; see [Local history database](#local-history-database-optional).
- **CSV-driven configuration** (`gateway/config_manager.py`) — builds `config.json` from `devices.csv`, `modbus.csv`, `system.csv`, and `aws.csv`, with validation (duplicate slave IDs, overlapping registers, invalid sensor types) and a matching `export` command to go back from JSON to CSV.
- **Hardware-free testing** — a mock Modbus slave plus a virtual serial link (`socat`) let the client be exercised end-to-end without physical RS‑485 equipment.

## Repository layout

```text
Akvo_Green/
├── config_data/                  # Centralized runtime config - same files for
│   ├── config.json               # the real gateway AND every test suite
│   └── devices.csv / modbus.csv / system.csv / aws.csv
├── modbus_client/                 # Standalone Modbus client library
│   ├── main_modbus.py            # Minimal example: connect + read one register
│   ├── modbus_client.py          # ModbusClient V3 (standalone Modbus API)
│   └── utils/logger.py           # Shared logger used by the Modbus client
├── gateway/                       # Edge gateway application
│   ├── edge_node_improved.py     # Main engine: scheduler/worker/publisher threads
│   ├── config_manager.py         # CSV <-> config.json build/export tool
│   ├── config/schema.py          # config.json validation
│   ├── domain/sensors.py         # Sensor-type decoder registry
│   ├── storage.py                # Optional SQLite history store + config.json mirror
│   └── certs/                    # AWS IoT Core certificates (mTLS)
├── data/                          # Created at runtime (gitignored): history.db
├── tests/
│   ├── akvo_modbus_mock/         # Virtual-serial mock Modbus slave + client tests
│   ├── test_modbus_client/       # Additional pytest suite + real-hardware test plan
│   ├── edge_node_mock/           # Full edge-node test harness (multi-slave mock + fake MQTT)
│   └── test_edge_node/           # Unit tests for edge_node_improved.py's own logic
└── docs/Modbus_client/           # Modbus client API documentation
```

`config_data/` lives at the repo root (not nested under `gateway/`) specifically so the real gateway and every test suite (`tests/edge_node_mock/run_edge_node_test.py`/`stress_test.py`/`mock_devices_slave.py`) read and build from the exact same `config.json`/CSVs - see `config_manager.py`'s `REPO_ROOT`/`CONFIG_DIR`.

## Requirements

- Python 3.10+ (3.12/3.14 tested)
- `socat` for the virtual-serial mock environment (Linux/macOS)
- An AWS IoT Core thing with certificates if running the full edge gateway

## Installation

```bash
git clone git@github.com:choarf/Akvo_Green.git
cd Akvo_Green
python3 -m venv .venv
source .venv/bin/activate
pip install pymodbus pyserial psutil awsiotsdk
```

## Usage

### Quick Modbus test

Connects to a device on `/dev/ttyUSB0` and reads one holding register:

```bash
cd modbus_client
python3 main_modbus.py
```

### Running the edge gateway

1. Build `config.json` from the CSV files:

   ```bash
   cd gateway
   python3 config_manager.py build
   ```

2. Place AWS IoT Core certificates under `gateway/certs/` (paths are referenced from `config.json`'s `aws` section).
3. Start the edge node:

   ```bash
   python3 edge_node_improved.py
   ```

   Editing `devices.csv`, `modbus.csv`, `system.csv`, or `aws.csv` and re-running `config_manager.py build` (or writing `config.json` directly) triggers a live reload — no restart required.

### Local history database (optional)

By default the gateway keeps only the *latest* reading per sensor in memory and forwards it to AWS. Turning on the local database also writes every published reading and host-telemetry sample to a SQLite file, so you can look at what a gateway has been doing without going through the cloud.

**Enable it** with two columns in `config_data/system.csv`, then rebuild the config and restart the gateway:

```csv
gateway_id,city,poll_interval,system_interval,watchdog_timeout,database_enabled,database_retention_days
AKVO_GW_101,America/Mexico_City,20,60,120,1,30
```

```bash
cd gateway && python3 config_manager.py build
```

This produces a `database` section in `config.json` (`"database": { "enabled": true, "retention_days": 30 }`). The columns are optional — a `system.csv` without them leaves the database off.

`retention_days` (default `30`) bounds disk use: older readings and telemetry are pruned hourly. The file is `data/history.db` at the repo root. Leaving the columns out, or `database_enabled` at `0`, keeps everything exactly as before. `config_manager.py export` writes the settings back to `system.csv`. The startup log confirms it worked:

```text
History DB synced from config.json -> added:['DEV_1', 'DEV_2', ...] removed:[] updated:[]
```

**How it relates to `config.json`.** `config.json` stays the single source of truth; the database follows it, never the other way round. At startup and on every live reload, the `devices` and `sensors` tables are updated to match `config.json`'s device list (only that list — never the `aws`/`modbus`/`gateway` sections):

| You change in `config.json` (or the CSVs) | The database |
|---|---|
| Add a device or sensor | New row; readings start linking to it |
| Change `scale`, `offset`, `min`, `max`, `unit`, `addr`, ... | Row updated in place; the old and new values are logged in `config_history` |
| Remove a device or sensor | Row marked `active = 0` — **not** deleted; its readings remain queryable |
| Add it back later | Same row reactivated |
| Save an invalid `config.json` | Rejected by validation before it reaches the database; nothing changes |

**Tables:** `readings` (one row per sensor per publish cycle: `ts`, `device_id`, `sensor_name`, `value`, `status`, `alarm`, `sensor_id`), `system_telemetry` (CPU/RAM/disk/IP per system interval), `devices` and `sensors` (the config mirror), and `config_history` (what changed, and when).

**Querying it.** The gateway can keep running while you read: the database uses WAL mode, so open it read-only with the `sqlite3` CLI (`sudo apt install sqlite3`) or any SQLite viewer:

```bash
sqlite3 -readonly data/history.db
```

Timestamps are UTC ISO-8601 strings, so plain string comparison sorts and filters them correctly. `value` is `NULL` when a read failed (`status` is then `BUS_ERROR` or `EXCEPTION`).

#### Use cases

**What is every sensor reading right now?** (e.g. checking a unit on-site)

```sql
SELECT r.device_id, r.sensor_name, r.value, s.unit, r.alarm, MAX(r.ts) AS ts
FROM readings r JOIN sensors s ON s.id = r.sensor_id
WHERE s.active = 1
GROUP BY r.sensor_id;
```

**Which sensors went out of range in the last day?** (`alarm` is `HIGH`/`LOW` outside the sensor's `min`/`max`; the log only records transitions, the database keeps every sample)

```sql
SELECT ts, device_id, sensor_name, value, alarm FROM readings
WHERE alarm IN ('HIGH','LOW') AND ts > strftime('%Y-%m-%dT%H:%M:%S','now','-1 day')
ORDER BY ts DESC;
```

**How has a sensor trended?** (hourly average — e.g. spotting drift or fouling before it alarms)

```sql
SELECT substr(ts,1,13) AS hour, ROUND(AVG(value),2) AS avg_value, COUNT(*) AS samples
FROM readings
WHERE device_id = 'DEV_4' AND sensor_name = 'Presion1' AND status = 'OK'
GROUP BY hour ORDER BY hour;
```

**Which sensors or slaves are unreliable?** (a cable, termination or address problem shows up as repeated failures on one device)

```sql
SELECT device_id, sensor_name, COUNT(*) AS failures FROM readings
WHERE status != 'OK' AND ts > strftime('%Y-%m-%dT%H:%M:%S','now','-7 day')
GROUP BY device_id, sensor_name ORDER BY failures DESC;
```

**What changed in the configuration, and when?** (and which readings were taken under the old calibration)

```sql
SELECT ts, added, removed, updated FROM config_history ORDER BY id DESC LIMIT 10;
```

`updated` holds the old and new values of each changed setting, so a jump in a trend that lines up with a `config_history` row is a calibration change, not a real process change.

**What devices used to exist?** (removed devices keep their history)

```sql
SELECT device_id, last_changed FROM devices WHERE active = 0;
```

**Is the gateway host healthy?**

```sql
SELECT ts, cpu_load_percent, ram_usage_percent, disk_usage_percent
FROM system_telemetry ORDER BY ts DESC LIMIT 20;
```

To take a copy while the gateway is running, use `sqlite3 data/history.db ".backup copy.db"` rather than copying the file — recent writes live in the `-wal` file next to it.

#### Limitations

- **Independent of AWS — never synced or replayed to it.** The database only records; nothing reads it back to send to AWS, and AWS never writes to it. If the connection drops after the gateway has started, polling and recording carry on (every cycle is still written locally), while the AWS library holds the unsent publishes in memory and delivers them when the connection resumes. Those held messages live only in memory: if the gateway restarts or the Pi reboots during the outage they are lost from AWS, and the database is **not** used to backfill them — the readings stay only in `data/history.db`.
- **Nothing is polled or recorded until AWS has connected once.** At startup the gateway waits for its first MQTT connection before starting any polling threads, so a boot with no network produces no readings, in AWS or in the database, until the connection comes up.
- **Restart required** to turn it on/off or change `retention_days`. Only the `devices` mirror follows live `config.json` edits.
- **A database problem never stops the gateway.** If `data/` is unwritable or the disk is full, the failure is logged (`History record_devices error: ...`) and Modbus polling and MQTT publishing carry on unaffected.
- **No built-in viewer.** It is a plain SQLite file — there is no web dashboard in the gateway.

### Debug logging

Logging defaults to `INFO`. Set `AKVO_LOG_LEVEL` before starting the process for more (or less) detail — it's read once at startup, so it needs a (re)start to take effect, not a live-reloadable `config.json` setting:

```bash
# Running the real gateway directly (from gateway/)
AKVO_LOG_LEVEL=DEBUG python3 edge_node_improved.py

# Running the mocked integration harness (from tests/edge_node_mock/) -
# useful to reproduce/debug without touching real hardware
AKVO_LOG_LEVEL=DEBUG python3 run_edge_node_test.py --duration 30

# Via the repo-root test runner
AKVO_LOG_LEVEL=DEBUG ./run_tests.sh integration --duration 30
```

At `DEBUG`, every sensor read logs its raw registers, decoded value, and alarm result (`Sensor Temp (slave=1, addr=0, type=float): registers=[560] -> val=56.0 alarm=HIGH`), and the scheduler logs its queue depth every cycle — both silent at `INFO`. Accepts any standard level name (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`); an invalid value falls back to `INFO` with a note in the startup log line.

DEBUG is verbose (one line per sensor per poll cycle) — filter it live, or after the fact from `logs/system.log`:

```bash
AKVO_LOG_LEVEL=DEBUG python3 edge_node_improved.py 2>&1 | grep "DEV_3"
grep "Sensor Temp" logs/system.log
```

### Exporting config back to CSV

```bash
python3 config_manager.py export
```

## Testing

Without physical hardware, the Modbus client can be tested via a virtual serial link and a mock slave:

```bash
cd tests/akvo_modbus_mock
sudo apt install socat
./start_virtual_serial.sh
python3 mock_slave.py --port /tmp/akvo_modbus_slave --slave 1   # terminal 2
python3 test_modbus_client.py --port /tmp/akvo_modbus_master    # terminal 3
./stop_virtual_serial.sh
```

A pytest-based suite (including a real-hardware test plan) lives under `tests/test_modbus_client/akvo_modbus_api_test_environment/`:

```bash
cd tests/test_modbus_client/akvo_modbus_api_test_environment
pip install -r requirements.txt
./run_tests.sh
```

The full edge node (`edge_node_improved.py`) can be tested end-to-end — multiple simulated slaves on one virtual bus, alarm evaluation, offline-slave handling — without hardware or AWS credentials:

```bash
cd tests/edge_node_mock
python3 run_edge_node_test.py --duration 30
```

Modbus and MQTT can each independently be faked or left real, via `tests/edge_node_mock/harness_config.json`:

```json
{
    "fake_modbus": true,
    "fake_mqtt": true
}
```

```bash
# Simulate two dead slaves, run for 60s
python3 run_edge_node_test.py --duration 60 --offline 3 9

# Mock Modbus bus, but publish to a real AWS IoT Core endpoint
python3 run_edge_node_test.py --no-fake-mqtt

# Real Modbus hardware, MQTT faked
python3 run_edge_node_test.py --no-fake-modbus
```

See [`tests/edge_node_mock/README.md`](tests/edge_node_mock/README.md) for the full configuration reference, a caution about `fake_mqtt: false` against production AWS IoT things, and a manual (multi-terminal) walkthrough.

The edge node's own logic (alarm evaluation, 32-bit register combining, device-reload diffing, retry backoff, the SQLite history store and its `config.json` sync) has a fast, hardware-free unit test suite under `tests/test_edge_node/`, using fake collaborators instead of real Modbus/MQTT connections:

```bash
cd tests/test_edge_node
pip install -r requirements.txt   # only needed if these aren't already installed
./run_tests.sh                    # all tests

# a single file or test
python3 -m pytest test_sensor_node.py -v
python3 -m pytest test_sensor_node.py::test_read_uint32 -v

# with coverage
python3 -m pytest --cov=edge_node_improved --cov-report=term-missing -q
```

## Documentation

- [Modbus Client API V3](docs/Modbus_client/AKVO_Modbus_Client_API_V3.md) — full reference for connection handling, supported function codes, error handling, statistics, and thread safety.
- [Edge Node Configuration Reference](docs/edge_node_config/CONFIGURATION.md) — every column in `devices.csv`/`modbus.csv`/`system.csv`/`aws.csv` and every field in the generated `config.json`, plus the optional `database` section and non-obvious behaviors (disabled devices, sensor-type scaling, live-reload semantics).
- [Testing Guide](docs/testing/TESTING.md) — the three test levels (unit/mocked/real-hardware) across all four test suites, a decision table for which to run, and setup for each.

## License

See [LICENSE](LICENSE).
