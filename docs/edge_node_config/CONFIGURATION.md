# Edge Node Configuration Reference

`edge_node_improved.py` runs entirely off `src/Venko_Green/config_data/config.json`. You
normally don't hand-edit that file — you edit four CSVs and run
`config_manager.py build` to regenerate it:

```text
devices.csv   modbus.csv   system.csv   aws.csv
        \          |           |          /
         `-------  config_manager.py build  -------'
                          |
                          v
                    config.json   <-- can also be edited directly
                          |
                          v
              edge_node_improved.py (live-reloads on any change)
```

`config_manager.py export` does the reverse: it regenerates the four CSVs
from the current `config.json`.

This document lists every column/field each file accepts, what the edge
node actually does with it, and a few non-obvious behaviors worth knowing
before you edit one.

## devices.csv

One row per **sensor**. Multiple rows sharing the same `device_id` become
one device with several sensors (e.g. `DEV_3`/`Temp` and `DEV_6`/`Hum` below
both live on Modbus slave 3, at different registers).

| Column | Required | Type | Description |
|---|---|---|---|
| `device_id` | yes | string | Groups sensor rows into one device. Free-form, but must be unique per device (not per row). |
| `slave` | yes | int (1-247) | Modbus RTU slave/unit ID. All rows for the same `device_id` must use the same `slave` — a build fails otherwise. Two different `device_id`s *can* share a `slave` (two sensors on one physical unit), which is expected and only logged, not an error. |
| `sensor_name` | yes | string | Sensor name, unique within its device. Becomes the key in the device's snapshot, e.g. `{"Temp": {...}}`. |
| `addr` | yes | int (0-65535) | Holding-register start address (FC03), zero-based. |
| `count` | yes | int (>0) | Register count to read starting at `addr`. Only `registers[0]` is actually used by the runtime today (see [Sensor types](#sensor-types-and-scaling) below) — `count` mainly matters for detecting register overlaps. |
| `scale` | yes (may be blank) | float | Multiplier applied to the raw register value. Blank defaults to `1`. |
| `offset` | yes (may be blank) | float | Added after scaling. Blank defaults to `0`. |
| `unit` | yes | string | Free-text unit label (`C`, `Mpa`, `%`, ...). Stored in `config.json` but not currently read anywhere at runtime — documentation only for now. |
| `type` | yes (may be blank) | one of `int`, `float`, `uint16`, `uint32`, `int32`, `float32` | Blank defaults to `int`. `float`/`float32`/`uint32`/`int32` are read from registers and combined as described in [Sensor types and scaling](#sensor-types-and-scaling) below; `int`/`uint16` are read raw with no scaling. `uint32`/`int32`/`float32` need `count >= 2` — `config_manager.py build` rejects a smaller `count` for these types. |
| `min` / `max` | no | float | Alarm thresholds against the *scaled* value. A read below `min` logs `LOW`, above `max` logs `HIGH`. Leave blank to disable that bound. Registers are unsigned 16-bit, so a negative `min` can only ever be exceeded if `offset` makes that reachable — see the [edge-node mock harness README](../../tests/edge_node_mock/README.md#known-limitations-mirrors-the-real-app-not-a-mock-bug) for the same caveat in the test mock. |
| `enabled` | no | `1/0`, `true/false`, `yes/no`, `y/n`, `on/off` | Defaults to enabled if the column is absent, or if a row leaves it blank. A disabled device is **dropped entirely** when building `config.json` — see [Known behaviors](#known-behaviors) below. |

Validation performed by `config_manager.py build` (aborts the build on
failure, logging every problem found):

- duplicate `sensor_name` within one device
- register overlap between two sensors of the same device (`addr`..`addr+count`)
- `addr` outside `0..65535`, or `count <= 0`
- `type` not one of the six valid values
- `type` is `uint32`/`int32`/`float32` with `count < 2`
- inconsistent `slave` across rows sharing one `device_id`

Duplicate `slave` across *different* `device_id`s is allowed and only
logged as a warning (that's the normal way to put two sensors on one
physical unit).

Example (two sensors on slave 3):

```csv
device_id,slave,sensor_name,addr,count,scale,offset,unit,type,min,max,enabled
DEV_3,3,Temp,0,1,0.1,0,C,float,-10,50,1
DEV_6,3,Hum,1,1,0.1,0,%,float,0,100,1
```

## modbus.csv

One header row + one data row — the whole serial bus configuration.

| Column | Type | Description |
|---|---|---|
| `port` | string | Serial device path (`/dev/ttyUSB0`, `COM3`, ...). |
| `baudrate` | int | Serial baud rate. |
| `timeout` | float | Per-request timeout, in seconds. |
| `parity` | string | Passed straight to `ModbusSerialClient` (typically `N`/`E`/`O`). |
| `stopbits` | int | `1` or `2`. |
| `bytesize` | int | `5`-`8`. |

`config/schema.py` (see **Validation** below) checks that all six keys are
*present*, but not that `parity`/`stopbits`/`bytesize` hold a *valid*
value — a bad value there only surfaces later, as a `ModbusManager`
connect failure at runtime.

```csv
port,baudrate,timeout,parity,stopbits,bytesize
/dev/ttyUSB0,9600,1.0,N,1,8
```

## system.csv

One header row + one data row — gateway identity and timing.

| Column | Type | Description |
|---|---|---|
| `gateway_id` | string | Identifier for this gateway, included in every `AKVO/system` telemetry payload. |
| `city` | string | **Actually an IANA/Olson timezone name** (e.g. `America/Mexico_City`), not a display label — it's passed straight to `zoneinfo.ZoneInfo()` to compute `city_time` in the system payload. An invalid value doesn't fail the build; at runtime `city_time()` catches the lookup error and falls back to a UTC timestamp suffixed with `Error`. |
| `poll_interval` | int (seconds) | Used **twice**: it's both how often the scheduler queues every device for a Modbus poll, and how often the publisher thread sends the `AKVO/data` payload. |
| `system_interval` | int (seconds) | How often the `AKVO/system` host-telemetry payload (CPU/RAM/disk/IP) is published. |
| `watchdog_timeout` | int (seconds) | If the worker thread (the one doing Modbus reads) stalls for longer than this — genuinely stuck, e.g. blocked inside a library call that never returns, not just returning read errors — a dedicated watchdog thread logs a `CRITICAL` line and exits the process via `os._exit(1)`. Leave blank/`0` to disable. **Requires an external supervisor** (systemd `Restart=always`, a container restart policy, etc.) to actually bring the process back up — without one, the gateway just stays down after the watchdog fires. |

Unlike `devices.csv`, a missing column here isn't caught with a friendly
error — it surfaces as a raw `KeyError` during `build`.

```csv
gateway_id,city,poll_interval,system_interval,watchdog_timeout
AKVO_GW_101,America/Mexico_City,15,30,120
```

## aws.csv

One header row + one data row — AWS IoT Core connection details.
`config_manager.py` loads this one **generically**: any column you add
round-trips through `build`/`export` untouched. The columns below are the
ones `edge_node_improved.py` actually reads.

| Column | Description |
|---|---|
| `host` | AWS IoT Core custom (ATS) endpoint, e.g. from `aws iot describe-endpoint`. |
| `client_id` | MQTT client ID. **AWS IoT Core allows only one active connection per `client_id`** — using an ID that's already connected in production (from a test run, or this repo's [edge-node mock harness](../../tests/edge_node_mock/README.md) with `fake_mqtt: false`) will disconnect it. |
| `ca` | Path to the Amazon Root CA certificate. |
| `cert` | Path to this device's certificate. |
| `key` | Path to this device's private key. |
| `topic_pub` | MQTT topic device/sensor data is published to. |
| `topic_system` | MQTT topic host telemetry is published to. |

`ca`/`cert`/`key` are conventionally relative paths (`./certs/...`).
`ConfigManager.load()` resolves them against `edge_node_improved.py`'s own
directory (`src/Venko_Green/`) — not the process's working directory, and
not `config.json`'s own location (`config_data/`) — so they work
regardless of where the process is launched from. An already-absolute
path is left untouched.

```csv
host,client_id,ca,cert,key,topic_pub,topic_system
a2zzu55sjawy4x-ats.iot.us-east-1.amazonaws.com,AKVO_Gateway,./certs/AmazonRootCA1.pem,./certs/certificate.pem.crt,./certs/private.pem.key,AKVO/data,AKVO/system
```

## config.json (generated)

```json
{
    "meta": {
        "version": "2.0",
        "generated_at": "2026-09-05T01:04:33.346619+00:00",
        "hash": "e789a58cdaf803a272763d87ed0c6d77"
    },
    "gateway": {
        "gateway_id": "AKVO_GW_101",
        "city": "America/Mexico_City",
        "poll_interval": 15,
        "system_interval": 30,
        "watchdog_timeout": 120
    },
    "modbus": {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "timeout": 1.0,
        "parity": "N",
        "stopbits": 1,
        "bytesize": 8
    },
    "aws": {
        "host": "a2zzu55sjawy4x-ats.iot.us-east-1.amazonaws.com",
        "client_id": "AKVO_Gateway",
        "ca": "./certs/AmazonRootCA1.pem",
        "cert": "./certs/certificate.pem.crt",
        "key": "./certs/private.pem.key",
        "topic_pub": "AKVO/data",
        "topic_system": "AKVO/system"
    },
    "devices": [
        {
            "id": "DEV_3",
            "slave": 3,
            "enabled": true,
            "sensors": [
                {
                    "name": "Temp",
                    "addr": 0,
                    "count": 1,
                    "scale": 0.1,
                    "offset": 0.0,
                    "unit": "C",
                    "type": "float",
                    "min": -10.0,
                    "max": 50.0
                }
            ]
        }
    ]
}
```

- **`meta`** is informational, written by `config_manager.py build` — not
  read by `edge_node_improved.py`. `hash` is an MD5 of the four source
  CSVs' bytes, computed by `ConfigManager.calculate_hash()`. There's also a
  `ConfigManager.has_changed()` built on top of it for detecting CSV edits,
  but nothing in `config_manager.py`'s CLI calls it today — it's unused.
  This `meta.hash` is a *different* hash from the one
  `edge_node_improved.py`'s own `config_watcher` computes internally over
  just the `modbus` and `aws` sections of `config.json`, to decide whether
  a live-reload needs to reconnect Modbus/MQTT rather than just rebuild the
  device list.
- **`gateway`**, **`modbus`**, **`aws`** are 1:1 with `system.csv`,
  `modbus.csv`, and `aws.csv` respectively (plus any extra columns you add
  to `aws.csv`).
- **`devices`** is `devices.csv` regrouped by `device_id`: one entry per
  device, each with a `sensors` array. Field names mostly match the CSV
  columns 1:1 (`sensor_name` becomes `name`).

You can edit `config.json` directly instead of going through the CSVs —
`edge_node_improved.py`'s `config_watcher` thread polls its mtime every 5s
and live-reloads: it only reconnects Modbus/MQTT if the `modbus`/`aws`
section actually changed, and only rebuilds devices that are new, removed,
or changed (`reload_devices()`), leaving unaffected devices running.

## Validation (`config/schema.py`)

Both `config_manager.py build` and `edge_node_improved.py`'s runtime
`ConfigManager` validate the fully-assembled config against the same
`config/schema.py` — so a hand-edited `config.json` that skips
`config_manager.py` entirely is held to the same rules a bad CSV build
would be, instead of only surfacing later as a scattered error in
whichever thread hits the missing/malformed field first:

- `gateway`/`modbus`/`aws` must each be present with all of their required
  keys (see the tables above) — a missing section or key is rejected
  immediately, at `EdgeNode` construction or at the next config reload,
  with a clear message naming what's missing.
- Every device-level rule described under **devices.csv** above (duplicate
  sensor names, register overlap, invalid/under-sized `type`, invalid
  `slave`) applies here too — `config/schema.py` is the single place both
  the CSV builder and the runtime loader check against.
- Two devices sharing one `slave` ID is a *warning*, not an error (the
  normal way to model two sensors on one physical unit).
- A `config.json` edit that fails validation while the gateway is already
  running is rejected by `config_watcher` (logged, old config kept in
  place) rather than crashing or partially applying.

## Sensor types and scaling

| `type` | Registers used | Runtime behavior |
|---|---|---|
| `int`, `uint16` | 1 (`registers[0]`) | `val = registers[0]` — raw, `scale`/`offset` ignored |
| `float` | 1 (`registers[0]`) | `val = registers[0] * scale + offset` |
| `uint32` | 2 | `val = <32-bit unsigned combine>` — raw, `scale`/`offset` ignored |
| `int32` | 2 | `val = <32-bit signed combine>` — raw, `scale`/`offset` ignored |
| `float32` | 2 | `val = <32-bit IEEE-754 combine> * scale + offset` |

Decoding lives in `domain/sensors.py`'s decoder registry (`DECODERS`), not
inline in `SensorNode.read()` — adding a 7th type means adding one decoder
class there, not editing an if/else in the polling code.

The 32-bit types (`uint32`/`int32`/`float32`) combine `registers[0]` and
`registers[1]` as one big-endian 32-bit word (`registers[0]` = high 16 bits),
matching pymodbus's own default word order. **A device using the opposite
word order will read wrong values with no error** — there's no way to detect
that automatically from the register values alone; swap `registers[0]`/`[1]`
in `_combine_32bit()` (`domain/sensors.py`) if so. `count` must be `>= 2`
for these three types — both `config_manager.py build` and the runtime
`config/schema.py` check reject a smaller `count` (see **Validation**
below), and a hand-edited `config.json` that slips through anyway gets an
`"EXCEPTION"` status at read time rather than a silently truncated value.

## Known behaviors

- **Disabled devices don't round-trip.** `config_manager.py build` drops
  any device with `enabled` false *before* it's written into
  `config.json` — it's not just filtered at runtime. Running
  `config_manager.py export` afterward regenerates `devices.csv` from
  `config.json`, so a device that was disabled in your original
  `devices.csv` will be **missing entirely** from the exported CSV, not
  present-but-disabled.
- **Single-register types are unsigned.** `int`/`uint16`/`float`'s raw
  register is always `0..65535`; a `min` below what `offset`/`scale` can
  reach from that range can never actually trigger a `LOW` alarm. This is
  exercised (not worked around) by the
  [edge-node mock harness](../../tests/edge_node_mock/README.md). This does
  **not** apply to `int32`/`float32`, which properly represent negative
  values via the signed 32-bit combine described above.
