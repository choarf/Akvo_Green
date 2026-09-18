# Edge Node Mock Test Harness

Runs the real `edge_node_improved.py` engine so its scheduler/worker/
publisher/config-watcher threads, alarm evaluation, and reconnect logic can
be exercised without physical RS-485 hardware or AWS credentials. Modbus and
MQTT can each independently be faked or left real — selected via
[`harness_config.json`](harness_config.json) — so you can, for example, test
against a mock Modbus bus while still publishing to a real AWS IoT Core
endpoint, or against real hardware while faking MQTT.

This directory also has `stress_test.py`, a separate high-load/chaos suite —
see [Stress / chaos testing](#stress--chaos-testing) below. For how this
fits into the project's other three test suites (unit, this one, real
hardware) and which to run for a given change, see
[`docs/testing/TESTING.md`](../../docs/testing/TESTING.md); for this
suite's and the stress suite's exit criteria, see
[`docs/testing/TEST_PLAN.md`](../../docs/testing/TEST_PLAN.md). The
repo-root [`run_tests.sh`](../../run_tests.sh) wraps both
`run_edge_node_test.py` and `stress_test.py` with a dated Markdown report —
see [Running via `run_tests.sh`](#running-via-run_testssh) below.

```text
config.json (devices/slaves/sensors)
        |
        +--------------------------+
        v                          v
mock_devices_slave.py        run_edge_node_test.py
(virtual serial, one slave         |
 per device, simulated              v
 sensor values)              edge_node_improved.EdgeNode
        ^                          |
        |  RS-485 over socat       v
        +------------------  fake_mqtt.FakeMqttConnection
                                   (prints instead of publishing to AWS)
```

## Files

- `harness_config.json` — selects which side(s) get faked: `{"fake_modbus":
  true, "fake_mqtt": true}`. See [Configuration](#configuration).
- `mock_devices_slave.py` — a single-process Modbus RTU slave that reads a
  config.json's `devices` list and serves every enabled device's slave ID and
  sensor registers on one virtual serial port. Sensor values drift randomly
  within (and occasionally outside) each sensor's `min`/`max`, so both
  `NORMAL` reads and `HIGH`/`LOW` alarms get exercised. Only used when
  `fake_modbus` is true.
- `fake_mqtt.py` — a drop-in replacement for the AWS IoT `mtls_from_path`
  connection builder. `connect()`/`publish()`/`disconnect()` behave like the
  real thing but print to stdout instead of talking to AWS. Only used when
  `fake_mqtt` is true.
- `run_edge_node_test.py` — orchestrates everything: reads `harness_config.json`
  (or `--settings`/`--fake-modbus`/`--fake-mqtt`), starts a virtual serial
  link and the mock slave when `fake_modbus` is on, patches
  `edge_node_improved`'s MQTT builder when `fake_mqtt` is on, then runs the
  real `EdgeNode` against a copy of your config.json.
- `start_virtual_serial.sh` — creates the `/tmp/akvo_modbus_master` <->
  `/tmp/akvo_modbus_slave` virtual serial pair via `socat` (identical to
  [`tests/akvo_modbus_mock/start_virtual_serial.sh`](../akvo_modbus_mock/start_virtual_serial.sh),
  kept here too so Option B below doesn't need to `cd` out of this
  directory). `run_edge_node_test.py` calls the equivalent of this
  automatically; only needed for the manual walkthrough. Tear down with
  [`../akvo_modbus_mock/stop_virtual_serial.sh`](../akvo_modbus_mock/stop_virtual_serial.sh)
  — there's no separate copy of the stop script here.
- `stress_test.py` — a separate, higher-intensity suite built on top of
  `run_edge_node_test.py`: many synthetic devices, aggressive polling,
  concurrent WiFi/MQTT/Modbus chaos injection, malformed live config edits,
  and reboot-escalation under load. See
  [Stress / chaos testing](#stress--chaos-testing) below.

## Configuration

`harness_config.json` controls which side(s) of the edge node get faked:

```json
{
    "fake_modbus": true,
    "fake_mqtt": true
}
```

| fake_modbus | fake_mqtt | Behavior |
|---|---|---|
| `true`  | `true`  | Default. Fully mocked — no hardware, no AWS. |
| `true`  | `false` | Mock Modbus bus, but publishes to a **real** AWS IoT Core endpoint using the certs/endpoint in `config.json`'s `aws` section. |
| `false` | `true`  | Connects to the **real** serial port in `config.json`'s `modbus` section, MQTT faked. |
| `false` | `false` | Both real — equivalent to running `edge_node_improved.py` directly. |

CLI flags override the file for one-off runs without editing it:

```bash
python3 run_edge_node_test.py --no-fake-mqtt      # mock Modbus, real AWS
python3 run_edge_node_test.py --no-fake-modbus    # real hardware, fake MQTT
python3 run_edge_node_test.py --settings my_settings.json
```

**Caution with `fake_mqtt: false`**: this opens a real MQTT connection using
whatever `client_id`/certs are in `config.json`'s `aws` section. AWS IoT Core
only allows one active connection per `client_id` — if that ID matches a
gateway that's already connected in production, connecting here will kick it
offline. Only disable `fake_mqtt` against a test/non-production AWS IoT
thing.

## Requirements

```bash
sudo apt install socat
python3 -m pip install pymodbus pyserial psutil awsiotsdk
```

## Usage

### Option A — one command (recommended)

```bash
cd tests/edge_node_mock

# Run for 30s using config_data/config.json
python3 run_edge_node_test.py

# Run for 60s, simulate slaves 3 and 9 as offline (no response)
python3 run_edge_node_test.py --duration 60 --offline 3 9

# Refresh simulated sensors every 0.3s and run until Ctrl+C
python3 run_edge_node_test.py --duration 0 --interval 0.3

# Point at a different config.json
python3 run_edge_node_test.py --config /path/to/other/config.json
```

By default (`fake_modbus: true, fake_mqtt: true` in `harness_config.json`),
real AWS certificate paths in `config.json`'s `aws` section are left
untouched but never opened — the fake MQTT builder ignores them. See
[Configuration](#configuration) to run against real Modbus hardware or a
real AWS IoT endpoint instead.

Each run prints:

- `[FakeMQTT] connected as <client_id>` once MQTT "connects"
- one human-readable line per device on every `AKVO/data` publish, and a
  small labeled block on every `AKVO/system` publish — see below
- a final snapshot of every device's cached sensor readings (value, status,
  alarm) when the run ends

```text
[FakeMQTT] AKVO/data @ 2026-09-11T19:21:02.119151+00:00
    DEV_1: {'Temp': {'val': 20.1, 'status': 'OK', 'alarm': 'NORMAL'}}
  ! DEV_6: {'Hum': {'val': 109.1, 'status': 'OK', 'alarm': 'HIGH'}}
    DEV_9: {'Presion1': {'val': 234.0, 'status': 'OK', 'alarm': 'NORMAL'}}
[FakeMQTT] AKVO/system @ 2026-09-11T19:21:03.120798+00:00
    Gateway:  AKVO_GW_101 (America/Mexico_City, local time 2026-09-11_13:21:03)
    Platform: Linux
    CPU: 2.0%   RAM: 48.0%   Disk: 8.4%
    IP: 192.168.68.145   OS: Linux 7.0.0-31-generic
```

A leading `!` marks a device with a `HIGH`/`LOW` alarm. Floats are rounded
to 2 decimal places for display only (`fake_mqtt.round_floats`) — this never
touches what `edge_node_improved.py` itself computes or would actually
publish, it just cleans up float artifacts like `109.10000000000001` in the
terminal. An offline slave's device shows up with `"status": "EXCEPTION"`
(no response after retries) instead of `"status": "OK"`, letting you confirm
the edge node keeps polling other devices and reporting host telemetry even
when one device is unreachable.

### Option B — manual, one step per terminal

Useful when you want to watch the mock slave and the edge node logs live,
side by side, instead of one orchestrated run. This walkthrough fakes both
Modbus and MQTT; to leave one real, skip its step below (skip the port patch
in step 1 to use the real serial port, or skip the `mtls_from_path`
monkeypatch in step 2 to use real AWS — see the [Configuration](#configuration)
caution about `fake_mqtt: false` first).

**Terminal 1 — virtual serial link** (creates `/tmp/akvo_modbus_master` <->
`/tmp/akvo_modbus_slave`):

```bash
cd Akvo_Green
tests/edge_node_mock/start_virtual_serial.sh
```

**Terminal 2 — mock slave**, serving every device/slave defined in
`config.json` on the slave-side port:

```bash
cd Akvo_Green
python3 -u tests/edge_node_mock/mock_devices_slave.py \
  --port /tmp/akvo_modbus_slave \
  --config config_data/config.json \
  --offline 9   # optional: simulate slave 9 as dead
```

`-u` matters here — without it, prints are buffered and you won't see
anything live.

**Terminal 3 — patch a config copy, then run the real edge node:**

```bash
cd Akvo_Green

# 1. Copy config.json, pointing modbus.port at the virtual "master" end
python3 -c "
import json
cfg = json.load(open('config_data/config.json'))
cfg['modbus']['port'] = '/tmp/akvo_modbus_master'
json.dump(cfg, open('/tmp/test_config.json', 'w'), indent=4)
"

# 2. Launch the unmodified EdgeNode, with MQTT faked (no AWS needed)
python3 -u -c "
import sys
sys.path.insert(0, 'src')
sys.path.insert(0, 'tests/edge_node_mock')
import edge_node_improved as en
from fake_mqtt import make_fake_mtls_from_path
en.mqtt_connection_builder.mtls_from_path = make_fake_mtls_from_path()
en.EdgeNode('/tmp/test_config.json').start()
"
```

Ctrl+C to stop the edge node, then run
`tests/akvo_modbus_mock/stop_virtual_serial.sh` to tear down the serial
link (no separate copy of the stop script lives here — see the `Files`
entry for `start_virtual_serial.sh` above).

Note: step 2's inline script writes `logs/system.log` relative to wherever
you run it from — run it from a scratch directory (or delete `logs/`
afterward) if you don't want that written into `Akvo_Green/`.

### Running the mock slave standalone

`mock_devices_slave.py` can also be pointed at the real
`akvo_modbus_mock`/`test_modbus_client` virtual-serial scripts if you want to
poke it with `main_modbus.py` or the ModbusClient test suite instead of the
full edge node:

```bash
../akvo_modbus_mock/start_virtual_serial.sh
python3 mock_devices_slave.py --port /tmp/akvo_modbus_slave \
    --config ../../config_data/config.json
```

## Stress / chaos testing

`stress_test.py` is a separate, heavier suite in this same directory,
built on top of `run_edge_node_test.py`'s harness. Where the options above
exercise the edge node under normal-ish conditions, this combines four
things at once, all still against the mocked harness (never real hardware
or AWS):

- **High load** — `--devices` synthetic devices (mixed sensor types) polled
  every 1s, instead of the ~9 devices / 20s interval in a typical
  `config.json`.
- **Chaos/fault injection** — WiFi reachability flaps randomly; MQTT fails
  both because of that and independently at random; ~15% of Modbus reads
  are swapped for injected failures (dropped bus, `BUS_ERROR`, truncated
  register count).
- **Malformed config** — periodically writes an invalid `config.json`
  (missing key / bad sensor type / out-of-range slave id), then restores
  it, exercising `ConfigManager.reload()`'s validate-and-keep-last-good
  path.
- **Reboot escalation under load** — `gateway.*_reboot_timeout`/
  `watchdog_timeout` are all set short (`--reboot-after`) so the real
  `_RebootEscalator`/loop-guard code actually fires, with
  `subprocess.run`/`os._exit` intercepted so it never reboots or exits
  this machine.

```bash
cd tests/edge_node_mock
python3 stress_test.py                                # 240s, 40 devices
python3 stress_test.py --duration 30 --devices 10      # a quick smoke run
python3 stress_test.py --devices 100 --reboot-after 5  # heavier load, faster reboot escalation
```

Needs `psutil` (already a repo dependency — see `docs/README.md`) for the
memory/thread reporting below. Sample output from a short run:

```text
===== STRESS TEST: 5 devices, poll_interval=1s, duration=5s =====
Axes: high load + WiFi/MQTT/Modbus chaos + malformed config + reboot escalation (intercepted)

  t=    5s  threads= 12  wifi=UP  published=5  failed=1  reboot_attempts=0

===== RESULTS =====

Duration: 6s, 5 devices
Threads:  min=7 max=12 end=12
RSS MB:   start=37.9 end=38.3 max=38.3 growth=+0.4
Log records by level: {'INFO': 12, 'WARNING': 2, 'ERROR': 1}
MQTT: published=5 publish_failed=1
Modbus chaos injected: dropped_bus=4 bus_error=3 truncated=0
Config chaos writes: 0
Reboot escalation: attempts_intercepted=0 process_exits_intercepted=0

Final device snapshot count: 5
Sensors currently in EXCEPTION/BUS_ERROR status at stop: 1
```

A clean exit code alone doesn't confirm bounded memory/thread growth —
check the `Threads`/`RSS MB` numbers against
[`docs/testing/TEST_PLAN.md`](../../docs/testing/TEST_PLAN.md) §5's exit
criteria, especially on a longer (`--duration 3600`+) pre-release soak.

## Running via `run_tests.sh`

The repo-root [`run_tests.sh`](../../run_tests.sh) wraps both this
directory's harness and the stress suite, adding a duration-gated refusal
to run against real AWS IoT Core by accident, plus a dated Markdown
report under `reports/` (gitignored) summarizing pass/fail, duration, and
a one-line detail per suite:

```bash
cd ../..     # repo root
./run_tests.sh integration                    # same as run_edge_node_test.py --duration 30
./run_tests.sh integration --duration 60
./run_tests.sh stress --duration 120 --devices 60
./run_tests.sh all                            # unit, then integration
./run_tests.sh plan                           # docs/testing/TEST_PLAN.md §7's pre-release checklist
```

`integration`/`all` refuse to run when `harness_config.json` has
`fake_mqtt: false` (see the [Configuration](#configuration) caution
above) unless you pass `--allow-real-mqtt`; `stress` is unaffected since
it always fakes MQTT itself regardless of `harness_config.json`. Run
`./run_tests.sh --help` for the full option list.

## Known limitations (mirrors the real app, not a mock bug)

- Single-register sensor types (`int`, `uint16`, `float`) read a plain
  unsigned 16-bit register. A sensor whose `min` is negative (e.g. `Temp`
  with `min: -10`) can never actually report a `LOW` alarm from one of
  these, since `SensorNode.read()`/`domain/sensors.py` don't sign-extend a
  single register either — the mock reproduces this rather than working
  around it. This does **not** apply to `int32`/`float32`, which combine
  two registers into a proper signed 32-bit value (see
  [`docs/edge_node_config/CONFIGURATION.md`](../../docs/edge_node_config/CONFIGURATION.md#sensor-types-and-scaling)).
- Only FC03 (Read Holding Registers) is simulated with live data, since
  that's the only function `ModbusManager` in `edge_node_improved.py` uses.
  FC06/FC16 are implemented as plain register writes for reuse with other
  tools, but nothing in the edge node calls them.
