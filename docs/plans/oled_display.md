# Plan: 0.96" I2C OLED status display

Status: **proposed, not started** (written 2026-10-07). Nothing below exists in the code yet.

Goal: a small 0.96" I2C OLED (128×64, monochrome) on the Pi showing gateway status
(WiFi, MQTT, Modbus, local web dashboard, database) and the latest sensor values.

## Screens

The display rotates every ~5 s between a status page and pages of sensor values:

```
┌────────────────────────┐   ┌────────────────────────┐
│AKVO_cmd4      17:42:10 │   │Sensores 1/4            │
│IP 192.168.68.129       │   │Pt100      24.0 °C      │
│WiFi  OK   MQTT OK      │   │Ambient    57.0 °C   ▲  │
│Modbus OK  0 err        │   │Humedad    32.0 %HR     │
│Web   OK   DB 47MB      │   │COD      1650 mg/L   ▲  │
│SIMULADO · 3 alarmas    │   │                        │
└────────────────────────┘   └────────────────────────┘
```

## 1. Hardware

- **Controller chip:** most 0.96" I2C modules use an **SSD1306** at address `0x3C`. Some are
  **SH1106** and need a different driver setting, so check the listing.
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

- Add **`luma.oled`** to the venv. It pulls in Pillow and smbus2 and works on Pi 5/CM5.
- Add a font package: `fonts-dejavu-core` (apt).
- **Commit a real `gateway/requirements.txt`.** Today `install.sh` only creates it when it's
  missing, so Pis that already have it never get new packages. It also isn't in git, so
  `tools/push_site.sh` (rsync `--delete`) removes it from the Pi.

## 4. Gateway: publish its own status (main code change)

The display runs as its **own service** (like `akvo-history-web`), so a display problem can't
stop sensor reading or publishing. It needs state that only the gateway process knows:

- **MQTT online/offline: missing today.** `MQTTManager` builds the connection but registers no
  `on_connection_interrupted` / `on_connection_resumed` callbacks. After the first connect, a
  drop goes unnoticed: the AWS library reconnects silently, and `publish()` (QoS 1) doesn't
  raise. Add both callbacks → a `connected` flag, plus a "last successful publish" time.
- **Modbus:** connected or not (`ModbusManager.client`), plus the number of sensors whose
  status ≠ `OK` in the last cycle (already in the publish payload).
- **WiFi:** `WifiManager.connected` already exists.
- **Status file:** every poll cycle the publisher thread writes `status.json` atomically
  (write temp + rename): the above, the latest values, alarms, the simulate flag, and its own
  timestamp. Put it in **RAM**, at `/run/akvo-green/status.json` via `RuntimeDirectory=akvo-green`
  in the `akvo-green` unit, not on the SD card, to avoid card wear. Wrap it in try/except like
  the history writes: it must never break publishing.

## 5. Display service: `gateway/oled_display.py`, systemd unit `akvo-oled`

- **Reads:**
  - `status.json` for gateway state and sensors;
  - `http://localhost:<web_port>/api/info` for **Web OK** and database size;
  - the system's IP and time.
- **Stale status:** if `status.json` is older than 3 poll intervals, show **"GATEWAY DETENIDO"**.
- **Sensor names:** from `config.json`, shortened to about 10 characters. An alarm shows as
  ▲/▼, a read error as ✕.
- **Burn-in protection:**
  - rotate pages;
  - shift the content by a pixel now and then;
  - dim after a few minutes;
  - optionally, screen off at night.
- Standalone, testable drawing: render pages to a Pillow image, so the layout can be tested
  (and previewed as PNG) without the hardware.

## 6. Config, scripts, docs, tests

- **`system.csv`:** new optional columns `display_enabled`, `display_driver`
  (`ssd1306`/`sh1106`) and `display_address` (default `0x3C`). They become a `display` section
  in `config.json` (`config_manager.py` build/export, `config/schema.py` validation). Off when
  absent, same pattern as `web_*`.
- **`install.sh`:**
  - installs the `akvo-oled` unit (`Nice=10`, `Restart=on-failure`), which exits 0 at start
    when `display_enabled` is off;
  - adds `RuntimeDirectory=akvo-green` to the `akvo-green` unit;
  - installs `fonts-dejavu-core` and `i2c-tools`.
- **`tools/push_site.sh`:** restarts `akvo-oled` on every push when installed, like
  `akvo-history-web`.
- **Tests (`tests/test_edge_node/`):**
  - status-file content and atomic write;
  - the MQTT interrupted/resumed flag;
  - page rendering to an image: status page, sensor pages, stale "GATEWAY DETENIDO";
  - config build/export/validation for `display_*`.
- **Docs:**
  - `docs/README.md`: "RS-485 hardware" neighbour section with wiring and I2C setup;
  - `docs/edge_node_config/CONFIGURATION.md`: `display_*` columns and the `display` section;
  - `sites/README.md`: per-site steps;
  - `CLAUDE.md`: architecture note.

## Effort and order

About the same size as the local web dashboard. Steps 4 and 5 are the real work. Suggested order:

1. Step 4: MQTT callbacks and `status.json`. It is useful on its own: the local web dashboard
   could then show MQTT/Modbus status too.
2. Step 5, with page previews rendered to PNG before the hardware arrives.
3. Steps 3 and 6: install, push, docs.
4. Steps 1 and 2 on a real Pi: enable I2C, `i2cdetect`, and turn on `display_enabled` for
   the site.

## Open question

Which controller does the purchased module use, **SSD1306 or SH1106**? That becomes the
default for `display_driver`.
