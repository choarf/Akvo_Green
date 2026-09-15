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
        v
AWS IoT Core (MQTT, mTLS)
```

## Features

- **Modbus RTU client (V3)** — thread-safe wrapper around PyModbus supporting FC01/02/03/04/05/06/15/16, connection/reconnection helpers, input validation, and per-call statistics (success rate, response time). See [`docs/Modbus_client/AKVO_Modbus_Client_API_V3.md`](docs/Modbus_client/AKVO_Modbus_Client_API_V3.md) for the full API reference.
- **Edge Node engine** (`src/Venko_Green/edge_node_improved.py`) — polls configured devices/sensors on a schedule, evaluates min/max alarms, and publishes device data and host telemetry (CPU/RAM/disk/IP) to AWS IoT via MQTT. Modbus and MQTT connections retry independently and forever, so a dead serial bus doesn't block cloud reporting.
- **Live config reload** — a background watcher detects changes to `config.json` and reconnects Modbus/MQTT or rebuilds only the affected devices, without restarting the process.
- **CSV-driven configuration** (`src/Venko_Green/config_manager.py`) — builds `config.json` from `devices.csv`, `modbus.csv`, `system.csv`, and `aws.csv`, with validation (duplicate slave IDs, overlapping registers, invalid sensor types) and a matching `export` command to go back from JSON to CSV.
- **Hardware-free testing** — a mock Modbus slave plus a virtual serial link (`socat`) let the client be exercised end-to-end without physical RS‑485 equipment.

## Repository layout

```text
Akvo_Green/
├── src/
│   ├── main_modbus.py            # Minimal example: connect + read one register
│   ├── modbus_client.py          # ModbusClient V3 (standalone Modbus API)
│   ├── utils/logger.py           # Shared logger used by the Modbus client
│   └── Venko_Green/               # Edge gateway application
│       ├── edge_node_improved.py # Main engine: scheduler/worker/publisher threads
│       ├── config_manager.py     # CSV <-> config.json build/export tool
│       ├── config_data/
│       │   ├── config.json       # Generated runtime configuration
│       │   └── devices.csv / modbus.csv / system.csv / aws.csv
│       └── certs/                # AWS IoT Core certificates (mTLS)
├── tests/
│   ├── akvo_modbus_mock/         # Virtual-serial mock Modbus slave + client tests
│   ├── test_modbus_client/       # Additional pytest suite + real-hardware test plan
│   ├── edge_node_mock/           # Full edge-node test harness (multi-slave mock + fake MQTT)
│   └── test_edge_node/           # Unit tests for edge_node_improved.py's own logic
└── docs/Modbus_client/           # Modbus client API documentation
```

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
cd src
python3 main_modbus.py
```

### Running the edge gateway

1. Build `config.json` from the CSV files:

   ```bash
   cd src/Venko_Green
   python3 config_manager.py build
   ```

2. Place AWS IoT Core certificates under `src/Venko_Green/certs/` (paths are referenced from `config.json`'s `aws` section).
3. Start the edge node:

   ```bash
   python3 edge_node_improved.py
   ```

   Editing `devices.csv`, `modbus.csv`, `system.csv`, or `aws.csv` and re-running `config_manager.py build` (or writing `config.json` directly) triggers a live reload — no restart required.

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

The edge node's own logic (alarm evaluation, 32-bit register combining, device-reload diffing, retry backoff) has a fast, hardware-free unit test suite under `tests/test_edge_node/`, using fake collaborators instead of real Modbus/MQTT connections:

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
- [Edge Node Configuration Reference](docs/edge_node_config/CONFIGURATION.md) — every column in `devices.csv`/`modbus.csv`/`system.csv`/`aws.csv` and every field in the generated `config.json`, plus non-obvious behaviors (disabled devices, sensor-type scaling, live-reload semantics).
- [Testing Guide](docs/testing/TESTING.md) — the three test levels (unit/mocked/real-hardware) across all four test suites, a decision table for which to run, and setup for each.

## License

See [LICENSE](LICENSE).
