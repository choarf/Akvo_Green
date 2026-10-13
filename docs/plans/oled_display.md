# Plan: 1.54" I2C OLED status display

Status: **code done, not yet on hardware** (written 2026-10-07, updated 2026-10-13).
`gateway/oled_display.py`, the `akvo-oled` unit, the `display_*` config, install/push support,
tests and docs are in. Still to do: steps 1-2 on a real Pi, the GHOTRIX logo artwork, and the
optional contrast/night-off settings.

Goal: a 1.54" I2C OLED (128×64, monochrome, **SSD1309**) on the Pi showing the gateway status
(the same rows as the local web dashboard's *Sistema* page) and the latest sensor values.

**Visual spec:** `VenkoDemo/docs/OLED/oled_simulator.html` (spec notes in `docs/OLED/init.md`
next to it). It's a browser simulator of this exact screen, pixel for pixel. Open it to see
the screens, timing and failure states. The Python drawing code should copy its layout,
font tables and row formats instead of redesigning them.

## Screens

**Order:**

1. At start-up only, the **GHOTRIX logo** with a boot progress bar (5 s).
2. Then this repeats forever: **Sistema** (5 s) → **Sensores** → **Sensores** → Sistema → …

The sensor list is shown twice per round, because it changes more often than the status does.

**Font:** a 3×5 small-caps bitmap font in a 4×6 cell, giving 32 columns × 10 rows: a 7-px
inverted header bar, then 9 text rows. A 5×7 font (21 × 8) also fits but leaves no room for
the alarm detail line or for 9 sensors on screen. Uppercase only, so accents are dropped
(ENVIO, LIMITES).

```
┌────────────────────────────────┐   ┌────────────────────────────────┐
│█SISTEMA████████████████17:42:10│   │█SENSORES 1-9/15████████17:42:15│
│GATEWAY ●ACTIVO       HACE 16 S │   │PT100           24.0  °C      ■ │
│RED     ●CONECTADO              │   │AMBIENT         57.0  °C      ▲ │
│  IP 192.168.68.129             │   │HUMEDAD         32.0  %HR     ■ │
│AWS     ●CONECTADO              │   │ORP           -356.0  MV      ■ │
│  ENVIO HACE 16 S               │   │PH               7.7  PH      ■ │
│MODBUS  !SIMULADO               │   │TEMPPH          30.4  °C      ■ │
│ALARMAS ▲3 ACTIVAS              │   │DO            1374.4  MG/L    ■ │
│  SENSORES FUERA DE LIMITES     │   │COD           1623.7  MG/L    ▲ │
│                                │   │TEMPCOD         24.0  °C      ✕ │
└────────────────────────────────┘   └────────────────────────────────┘
```

### Sistema

The rows, states and wording are the same as `connectionRows()` in `gateway/web/app.js`:

- **Gateway:** `ACTIVO` + "hace N s" (age of the status file), or `DETENIDO` when the file is
  stale (older than 3 poll intervals, as `history_web.read_status()` decides). A missing
  file shows `SIN ESTADO`.
- **Red:** `CONECTADO`/`SIN CONEXION`. The detail line is the Pi's own IP address, or
  "desde hace …" when the network is down.
- **AWS:** `CONECTADO`/`DESCONECTADO`. The detail line is "envío hace N s" (from
  `mqtt.last_publish`), or "desde hace …" (from `mqtt.since`).
- **Modbus:** one of `SIMULADO`, `PTO CERRADO`, `NO RESPONDE`, `ERRORES` (+ `n/total`), or
  `OK` (+ sensor count).
- **Alarmas:** `N ACTIVAS` + "sensores fuera de límites", or `NINGUNA`.

A fault row (✕) is drawn as an **inverted bar** so it can be seen from across the room.

### Sensores

- **Row:** name (12 chars), value (8, right-aligned), unit (6), then a flag:
  - `■` normal;
  - `▲` high and `▼` low (value over `max` / under `min`), which blink;
  - `✕` read error (`status` ≠ `OK`).
- **Names and units** come from `config.json`:
  - names drop a trailing `Sensor`/`Sensort` (`Pt100Sensor` → `PT100`);
  - `uS/cm` → `µS`, and a `unit` unit on a pH sensor → `PH`.
- **Scrolling:** the list scrolls **one pixel at a time** under the fixed header:
  - it waits 1.5 s at the top, scrolls at about 6 px/s (one row per second), then waits
    1.5 s at the bottom;
  - so the screen lasts as long as the scroll needs: about 8.5 s for cmd4's 15 sensors;
  - a 2-px scrollbar on the right edge shows the position, and the header shows the visible
    range (`1-9/15`);
  - a list that fits on the screen doesn't scroll, and stays up for the normal 5 s.

### Burn-in

- Screens rotate, and the sensor list moves.
- Shift the whole image by 1 px each round.
- Optionally:
  - lower the contrast after a few minutes;
  - turn the screen off at night.

## 1. Hardware

- **Module:** 1.54" 128×64 OLED, **SSD1309** controller, I2C (4-pin GND/VCC/SCL/SDA).
  - Address is usually `0x3C` (some boards are strapped to `0x3D`).
  - Modules with a RES pin need it tied high or driven from a GPIO.
- **Wiring (40-pin header):** VCC → 3.3 V (pin 1), GND → pin 6, SDA → GPIO 2 (pin 3),
  SCL → GPIO 3 (pin 5).
- **With the Waveshare 2-CH RS485 HAT:** no conflict. The HAT uses SPI1 and GPIO 18–27, not
  GPIO 2/3. Wire the display to the HAT's pass-through header.

## 2. Pi setup (once per Pi)

- **Enable I2C.** It is off on cmd4 today (`#dtparam=i2c_arm=on` is commented out in
  `/boot/firmware/config.txt`, and there's no `/dev/i2c-1`). Use `raspi-config` (Interface
  Options > I2C) or uncomment the line, then reboot.
- **Check:** `sudo apt install i2c-tools && i2cdetect -y 1` should show `3c`.

## 3. Python packages

- Add **`luma.oled`** to the venv.
  - It has an `ssd1309` device class, and pulls in Pillow and smbus2.
  - It works on Pi 5/CM5.
- **No font package is needed.** The 3×5 and 5×7 fonts are small bitmap tables in the code,
  copied from the simulator.
- **Commit a real `gateway/requirements.txt`.**
  - Today `install.sh` only creates it when it's missing, so Pis that already have it never
    get new packages.
  - It also isn't in git, so `tools/push_site.sh` (rsync `--delete`) removes it from the Pi.

## 4. Gateway status file (done)

**Already in place** (commit `e69320a`, used by the local web *Sistema* page):

- **MQTT:**
  - `MQTTManager` registers `on_connection_interrupted`/`on_connection_resumed`;
  - `status()` reports `connected`, `since`, `last_publish`, `last_error`.
- **Status file:**
  - every poll cycle, `EdgeNode.write_status()` writes the file atomically (temp + rename);
  - path: `/dev/shm/akvo-green-status.json`, falling back to `data/status.json`;
  - fields: `ts`, `gateway_id`, `poll_interval`, `network`, `mqtt`, and `modbus` (port
    state, `simulated`, sensor / read-error / alarm counts).

- **Per-sensor readings:** `readings` (device → sensor → `val`, `status`, `alarm`; the
  error text is left out). The display therefore doesn't depend on the SQLite history being
  enabled.

## 5. Display service: `gateway/oled_display.py`, systemd unit `akvo-oled`

The display runs as its **own service** (like `akvo-history-web`), so a display problem can't
stop sensor reading or publishing.

- **Reads:**
  - the status file, via `history_web.read_status()` (reuse it, including its stale rule);
  - `config.json` for sensor names, units, `min`/`max` and `poll_interval`;
  - the system's own IP and the clock.
- **Writes:** nothing, and there's no network access.
- **Loop:** about 10 frames/s while scrolling, about 1 frame/s otherwise. Over I2C, a full
  128×64 frame is 1 KB.
- **Drawing:** standalone and testable.
  - Pages render to a 1-bit Pillow image (`Image.new("1", (128, 64))`), so the layout can be
    tested and saved as PNG without the hardware.
  - The simulator's *Exportar PNG* button gives the expected images to compare against.
- **Logo:** a 1-bit bitmap of the GHOTRIX logo (simulator: *Logo propio* → threshold to
  128×60), stored in the repo.

## 6. Config, scripts, docs, tests

- **`system.csv`:** new optional columns, off when absent (same pattern as `web_*`):
  - `display_enabled`;
  - `display_driver` (default `ssd1309`; `ssd1306`/`sh1106` for other modules);
  - `display_address` (default `0x3C`);
  - optionally `display_font` (`3x5`/`5x7`) and `display_sensor_repeats` (default 2).

  They become a `display` section in `config.json` (`config_manager.py` build/export,
  `config/schema.py` validation).
- **`install.sh`:**
  - installs the `akvo-oled` unit (`Nice=10`, `Restart=on-failure`), which exits 0 at start
    when `display_enabled` is off;
  - installs `i2c-tools`.
- **`tools/push_site.sh`:** restarts `akvo-oled` on every push when installed, like
  `akvo-history-web`.
- **Tests (`tests/test_edge_node/`):**
  - per-sensor readings in the status file;
  - page rendering to an image:
    - the Sistema page in each state, including stale `DETENIDO` and a missing file;
    - the sensor rows and flags, and the scroll offsets at the start, middle and end;
    - the logo;
  - config build/export/validation for `display_*`.
- **Docs:**
  - `docs/README.md`: "RS-485 hardware" neighbour section with wiring and I2C setup;
  - `docs/edge_node_config/CONFIGURATION.md`: `display_*` columns and the `display` section;
  - `sites/README.md`: per-site steps;
  - `CLAUDE.md`: architecture note.

## Effort and order

Smaller than the local web dashboard, now that the status file exists. Step 5 is the real
work. Suggested order:

1. Step 5, porting the simulator's layout. Check the PNG previews against the simulator
   before the hardware arrives.
2. Steps 3 and 6: install, push, docs.
3. Steps 1 and 2 on a real Pi: enable I2C, `i2cdetect`, and turn on `display_enabled` for
   the site.
