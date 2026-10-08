# Edge Node Configuration Reference

`edge_node_improved.py` runs entirely off `config_data/config.json`, centralized
at the repo root (not nested under `gateway/`) so the real gateway
and every test suite read/build the same file. You normally don't
hand-edit that file — you edit four CSVs and run `config_manager.py build`
to regenerate it:

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
| `port` | string | Serial device path: `/dev/ttyUSB0` (USB-RS485 adapter), `/dev/ttySC0`/`/dev/ttySC1` (Waveshare 2-CH RS485 HAT channel 1/2 - see the README's "RS-485 hardware"), `COM3`, ... A change is live-reloaded (Modbus reconnects on the new port). |
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

### Virtual Modbus (optional `simulate_enabled` column)

An optional seventh column turns on synthetic sensor data instead of
reading `port` at all — useful for demoing the full pipeline (gateway →
AWS IoT → dashboard) with no RS-485 hardware, no `socat`, nothing to wire
up:

```csv
port,baudrate,timeout,parity,stopbits,bytesize,simulate_enabled
/dev/ttyUSB0,9600,1.0,N,1,8,1
```

| Column | Type | Description |
|---|---|---|
| `simulate_enabled` | bool (optional) | `1`/`true`/`yes`/`on` runs against `gateway/domain/simulation.py`'s in-process simulator instead of the real serial port; `0`/blank/absent uses real hardware (the default — absent means every existing `modbus.csv` builds and behaves exactly as before). Becomes `modbus.simulate` in `config.json`. |

Every sensor in `devices.csv` gets its own simulated value: a bounded
random walk within that sensor's `min`/`max`, with an occasional
excursion outside them to exercise `HIGH`/`LOW` alarms — generated from
*this gateway's own config*, not canned data, so changing `devices.csv`
changes what the demo shows with no other changes needed. Every published
reading is also tagged `"simulated": true` in the MQTT payload (and so in
S3/Athena too), so synthetic and real data can never be confused with each
other downstream, even after the fact.

**Read once at startup, like `database.*`** — toggling `simulate_enabled`
on a running gateway needs a restart, not just a config reload (`sam deploy`
isn't involved at all; this is purely a Akvo_Green-side setting). The
startup log makes it unmistakable either way: `Modbus SIMULATE mode is ON
- every reading is synthetic, not real sensor data`.

This is also a supported *deployment* option, not just a dev convenience
— a site with no Pi (or no sensors wired) yet can still stand up a fully
working demo: build that site's `config.json` with `simulate_enabled=1`
and run `edge_node_improved.py` anywhere Python runs (a Pi, a plain VM, a
container) — no RS-485 adapter required at all.

## system.csv

One header row + one data row — gateway identity and timing.

| Column | Type | Description |
|---|---|---|
| `gateway_id` | string | Identifier for this gateway, included in every `AKVO/system` telemetry payload. |
| `city` | string | **Actually an IANA/Olson timezone name** (e.g. `America/Mexico_City`), not a display label — it's passed straight to `zoneinfo.ZoneInfo()` to compute `city_time` in the system payload. An invalid value doesn't fail the build; at runtime `city_time()` catches the lookup error and falls back to a UTC timestamp suffixed with `Error`. |
| `poll_interval` | int (seconds) | Used **twice**: it's both how often the scheduler queues every device for a Modbus poll, and how often the publisher thread sends the `AKVO/data` payload. |
| `system_interval` | int (seconds) | How often the `AKVO/system` host-telemetry payload (CPU/RAM/disk/IP) is published. |
| `watchdog_timeout` | int (seconds) | If the worker thread (the one doing Modbus reads) stalls for longer than this — genuinely stuck, e.g. blocked inside a library call that never returns, not just returning read errors — a dedicated watchdog thread logs a `CRITICAL` line and reboots the whole host (`sudo reboot`, via the same `_RebootEscalator`/`_default_reboot_fn` the three communication managers use — see `CLAUDE.md`'s Architecture section). Leave blank/`0` to disable. Guarded by the same reboot-loop limit (max 3 reboots/hour) so a problem a reboot can't fix doesn't boot-loop the device. |
| `database_enabled` | bool (optional) | `1`/`true`/`yes`/`on` turns on the local SQLite history store; `0`/blank/anything else leaves it off. Becomes `database.enabled` in `config.json` — **not** a `gateway` key. See [Local history database](#local-history-database-optional-database-section). |
| `database_retention_days` | int (optional) | Days of readings/telemetry to keep (default `30` when blank). Becomes `database.retention_days`. Must be a positive integer — a non-integer fails `build` with a clear message. |
| `web_enabled` | bool (optional) | `1` turns on the local web dashboard over that database (`gateway/history_web.py`, service `akvo-history-web`); `0`/blank/absent leaves it off. Becomes `web.enabled`. See [Local web dashboard](#local-web-dashboard-optional-web-section). |
| `web_port` | int (optional) | Port of that dashboard (default `8080` when blank). Becomes `web.port`; must be 1-65535. |

The `database_*` and `web_*` columns are optional: a `system.csv` without them builds
exactly as before, with the database off. The other columns are still required —
a missing one isn't caught with a friendly error, it surfaces as a raw
`KeyError` during `build`.

```csv
gateway_id,city,poll_interval,system_interval,watchdog_timeout,database_enabled,database_retention_days,web_enabled,web_port
AKVO_GW_101,America/Mexico_City,15,30,120,1,30,1,8080
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
| `dashboard_url` | *(optional)* The site's cloud dashboard (`https://...cloudfront.net`). Shown as a link on the local dashboard's *Sistema* page; not used by the gateway. |
| `data_bucket` | *(optional)* The site's S3 data bucket name - linked to the S3 console on *Sistema*. |
| `web_bucket` | *(optional)* The site's S3 web bucket name - same. |

`ca`/`cert`/`key` are conventionally relative paths (`./certs/...`).
`ConfigManager.load()` resolves them against `edge_node_improved.py`'s own
directory (`gateway/`) — not the process's working directory, and
not `config.json`'s own location (the repo root's `config_data/`) — so
they work regardless of where the process is launched from. An already-absolute
path is left untouched.

```csv
host,client_id,ca,cert,key,topic_pub,topic_system,dashboard_url,data_bucket,web_bucket
a2zzu55sjawy4x-ats.iot.us-east-1.amazonaws.com,AKVO_Gateway,./certs/AmazonRootCA1.pem,./certs/certificate.pem.crt,./certs/private.pem.key,AKVO/data,AKVO/system,https://ddlxtxblzvu22.cloudfront.net,venko-demo-884520769610-us-east-1,venko-demo-web-884520769610
```

The three link columns come from VenkoDemo's site summary (`docs/sites/<key>.md`: web page, S3 data bucket, S3 web bucket). Changing them reconnects MQTT once (the `aws` section's hash changes) - harmless.

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

## Local history database (optional `database` section)

An optional top-level `database` object in `config.json` turns on a local
SQLite history store (`gateway/storage.py`, file `data/history.db` at the
repo root). It is configured with the `database_enabled` and
`database_retention_days` columns of **`system.csv`**; `config_manager.py
build` writes them into `config.json` as a separate top-level `database`
section (and `export` writes them back):

```csv
...,watchdog_timeout,database_enabled,database_retention_days
...,120,1,30
```

```json
"database": { "enabled": true, "retention_days": 30 }
```

`system.csv` is the source of truth for this section when you go through
`build`: a `system.csv` without the columns produces a `config.json` with no
`database` section, and a `database` block added to `config.json` by hand is
replaced on the next `build` (edit `system.csv` instead). Editing
`config.json` directly still works if you never re-run `build`.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | off (section absent = off) | Record readings/telemetry and mirror devices/sensors. |
| `retention_days` | `30` | Readings and system telemetry older than this are pruned (hourly). Device/sensor/config records are never pruned. |

Read once at startup — changing `database.*` needs a restart. A database
failure (unwritable `data/`, locked file, full disk) is logged and never
blocks Modbus, MQTT, or config reloads. The database is independent of
AWS: it is never synced to or replayed to it (a reading missed by AWS
during an outage is not backfilled from the database), and no polling or
recording starts until the first MQTT connection succeeds. See the README's [Local history database](../README.md#local-history-database-optional)
section for usage and example queries.

**`config.json` is the source of truth; sync is one-way.** The `devices`
and `sensors` tables mirror `config.json`'s `devices` list, updated at
startup and on every live reload (only when something actually differs).
Nothing is ever written back to `config.json`, and only `devices` is
mirrored — never `aws`/`modbus`/`gateway`.

- A device or sensor removed from `config.json` is marked `active = 0`, not
  deleted, so its readings stay queryable. Re-adding it reactivates the
  same row.
- A changed sensor setting (`scale`, `offset`, `min`, `max`, `unit`, ...)
  updates the row in place; the old and new values are kept in
  `config_history`, so you can tell which calibration produced which
  readings.
- `readings.sensor_id` links each reading to its `sensors` row (NULL only
  for a reading recorded before that sensor was first synced).
- An invalid `config.json` edit is rejected by validation before it ever
  reaches the database.

## Local web dashboard (optional `web` section)

A small web page on the Pi itself that shows the local history database:
open `http://<pi-address>:8080` from any device on the same network. It works
with **no internet** (the page draws its own charts, no CDN) and **without AWS**,
so it is the way to look at a site when the cloud dashboard can't.

```csv
...,database_enabled,database_retention_days,web_enabled,web_port
...,1,30,1,8080
```

```json
"web": { "enabled": true, "port": 8080 }
```

Views (in Spanish):

| View | Shows |
|---|---|
| **Actual** | Latest value of every sensor, status (Normal / ▲ Alto / ▼ Bajo / ✕ Error), limits and a 1-hour sparkline. Refreshes every poll interval. |
| **Históricos** | Pick sensors and a range (1 h – 30 d, or custom): one chart per sensor (average line, min–max band, alarm limits), a table view, and **Exportar CSV** (raw readings of the selected sensors, max 7 days: one row per reading cycle, one column per sensor, plus an `alarmas` column naming the sensors in alarm or with a read error; opens in Excel). |
| **Alarmas** | Alarm *episodes* (consecutive out-of-range samples merged), with start, end, duration and peak. Click one to open its trend. |
| **Sistema** | **Conexiones**: gateway active/stopped, network (Internet), AWS IoT (MQTT: connected, last send, last error), Modbus (port open, read errors, simulated) - refreshed every poll. **Nube AWS**: links to the cloud dashboard, the S3 data and web buckets and the IoT thing in the AWS console (from `aws.csv`). Plus gateway IP/OS, database size and date range, CPU/RAM/disk over time, and **network data used** (MB received/sent per 5 min / hour / 6 h depending on the range, with totals; all interfaces except loopback). |

How it runs:

- **Separate service, read-only.** `gateway/history_web.py` runs as its own
  systemd service, `akvo-history-web` (installed by `install.sh`), and opens
  `data/history.db` read-only. A slow query or a crash there can't affect Modbus
  polling or MQTT. Standard library only, nothing extra to install.
- **Needs `database_enabled=1`** - it shows what the database has recorded.
  With the database off it shows whatever is already in the file, and says so.
- **No password.** Anyone who can reach the Pi on the network can *view* the data
  (nothing can be changed through it). Keep it to trusted networks; turn it off
  with `web_enabled=0`.
- **Restart to apply.** `web.*` is read at service start: after a change,
  `tools/push_site.sh` restarts `akvo-history-web` automatically (or run
  `sudo systemctl restart akvo-history-web` on the Pi). The gateway itself is not
  restarted for this.
- When `web_enabled` is off, the service starts, logs
  `Local web dashboard is off` and exits - so it can stay installed everywhere.
- **Connection status** comes from a small status file the gateway rewrites every
  publish cycle - `/dev/shm/akvo-green-status.json` (RAM, no SD-card writes;
  `data/status.json` where there's no `/dev/shm`). If it's older than 3 poll
  intervals the page shows the gateway as **Detenido** and greys out the rest.
  MQTT state comes from the AWS library's connection interrupted/resumed
  callbacks, so a dropped link shows within the MQTT keep-alive time.
- Times are shown in the gateway's `city` time zone; the CSV has both UTC and
  local time. A blank CSV cell means a read error (or the sensor wasn't read in that cycle).
- Check it on the Pi: `systemctl status akvo-history-web`,
  `journalctl -u akvo-history-web -n 20`, `curl -s localhost:8080/api/info`.
- **Show it on the Pi's own screen at boot:** run `tools/setup_kiosk.sh` on the Pi
  (as `pi`). It adds `~/.config/autostart/akvo-kiosk.desktop`, so at each
  desktop login Chromium opens `http://localhost:<web_port>/#actual` full screen
  (`tools/kiosk_launch.sh`). The launcher waits up to 2 minutes for the
  dashboard service, uses its own browser profile (`~/.config/akvo-kiosk`, so no
  old tabs or "restore pages?" bar after a power cut) and picks Wayland/X11 by
  itself. `--window` = normal maximized window, `--off` = remove. Needs Raspberry
  Pi OS with desktop auto-login (the default; `raspi-config` > System Options >
  Boot / Auto Login). Alt+F4 closes it until the next boot.
- Local testing against a copy of a database:
  `python3 gateway/history_web.py --force --db copy.db --config config.json --host 127.0.0.1 --port 8081`.

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
- `database` is optional; if present it must be an object, `enabled` a
  boolean, and `retention_days` a positive integer.
- `modbus.simulate` is optional; if present it must be a boolean.
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
