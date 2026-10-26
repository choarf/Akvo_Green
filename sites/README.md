# sites/

One folder per **additional** gateway site (another Raspberry Pi). The code is shared; each site only has its own settings. Site 1 keeps using the repo-root `config_data/` and `gateway/certs/`.

```
sites/<key>/
  config_data/   devices.csv, modbus.csv, system.csv, aws.csv, display.csv (OLED)  →  config.json
  certs/         AmazonRootCA1.pem, certificate.pem.crt, private.pem.key  (git-ignored)
  wifi.csv       WiFi passwords: ssid,password  (git-ignored, optional)
```

- **Build a site's config.** Run from `gateway/`: `python3 config_manager.py build --config-dir ../sites/<key>/config_data`. `export` takes the same option.
- **Deploy to its Pi:** `tools/push_site.sh <key> pi@<address> [--certs]`. This copies the code, the site's `config.json` and, with `--certs`, its certificates. It never touches the Pi's own `config_data/`, `data/`, `logs/` or `.venv/`. For site 1, use `root` as the key.
- **Which Pi:** `sites/<key>/pi.csv` (`hostname,user,serial,mac`) ties the site to one physical Pi by its serial number (`/proc/device-tree/serial-number`), which doesn't change with the SD card, IP or hostname. The first push with an address writes it (commit it) and sets the Pi's hostname to the site key, so afterwards `tools/push_site.sh <key>` needs no address: it finds the Pi as `<hostname>.local`, or else at the IP the gateway last reported to AWS. Every push checks the serial and stops on another site's Pi; if a site really moved to new hardware, push once with `--new-hw`. The local page is then at `http://<key>.local:8080`.
- **Overlay file system:** if the Pi runs the read-only overlay (`raspi-config` → Performance → Overlay File System; `/` is then an overlay in RAM), everything pushed is lost at its next reboot, so the push stops. Turn it off first (`sudo raspi-config nonint disable_overlayfs && sudo reboot`), push, and re-enable it afterwards if wanted; `--overlay-ok` pushes anyway. With the overlay on, the local history (`data/`) also goes back to its frozen copy at every reboot; AWS keeps the full record.
  It also installs the **AKVO Modbus Tool** (desktop app to scan, read and write Modbus registers on site; menu entry "AKVO Modbus Tool" or `akvo-modbus` on the Pi's desktop). It's built into a `.deb` from `../Tools/AkvoModbus` (or `$AKVO_MODBUS_DIR`) by `tools/build_modbus_tool.sh`, and reinstalled only when its source changed. Skip it with `--no-modbus-tool`. Stop the `akvo-green` service while using it on the same RS-485 port.

  On a Pi with a desktop it also sets up the **kiosk**: `tools/setup_kiosk.sh` makes Chromium open the local dashboard full screen at every boot, and the push opens it (or reopens it, to show the new page) on the Pi's screen right away when someone is logged in on the desktop. Skip it with `--no-kiosk`; turn it off on the Pi with `tools/setup_kiosk.sh --off`.
- **WiFi:** put the networks' names in `system.csv` (`wifi1_ssid` preferred, `wifi2_ssid` fallback) and their passwords in `sites/<key>/wifi.csv` (`ssid,password`), rebuild, then `push_site.sh`. It saves them on the Pi as NetworkManager profiles `akvo-wifi1`/`akvo-wifi2`. Details: `docs/edge_node_config/CONFIGURATION.md`, "WiFi networks".
- **Every CSV lists all its columns.** A blank cell means "use the default"; a missing column means the same, but then it can't be seen or edited. `system.csv` columns (in this order): `gateway_id, client, city, poll_interval, system_interval, watchdog_timeout, database_enabled, database_retention_days, database_max_mb, database_interval, web_enabled, web_port, wifi1_ssid, wifi2_ssid`. `display.csv`: `enabled, driver, address, column_offset, font, sensor_repeats, sensor_list, per_page, screen_seconds, scroll_speed, logo_every, contrast, burn_in_shift`. Meanings and defaults: `docs/edge_node_config/CONFIGURATION.md`. Plain LF line endings (a file saved with Windows CRLF breaks a column added at the end of a line).
- **A new site** starts as a copy of another site's CSVs (`cp sites/cmd4/config_data/*.csv sites/<key>/config_data/`), then gets its own `gateway_id`/`client_id`, topics, buckets and dashboard. Never copy over a site that already exists: it takes the other site's identity, and AWS then refuses it.
- **Unique `client_id` per site** (`aws.csv`), plus the site's own topics. Two Pis connecting with the same client id disconnect each other.

The full procedure covers the AWS IoT certificate and policy, the AWS stack and the dashboard. It's in the VenkoDemo README, section **Adding a site**.

| Site | Client id | Topics | Pi | Modbus | OLED (`display.csv`) | Local history | Local dashboard |
|---|---|---|---|---|---|---|---|
| `cmd4` | `AKVO_cmd4` | `VENKO/cmd4/*` | `pi@192.168.68.124` (CM5) | **simulated** | none (off) | 7 days | http://192.168.68.124:8080 |
| `akvo01005` | `AKVO_akvo01005` | `VENKO/akvo01005/*` | `akvo@192.168.68.107` (Pi 4) | **simulated** | sh1106, offset 3, pages | 30 days / 500 MB, every 60 s | http://192.168.68.107:8080 |
| `akvo01002` | `AKVO_akvo01002` | `VENKO/akvo01002/*` | `akvo@192.168.68.130` (Pi 4) | **real sensors** (`/dev/ttySC0`, 9600 8N1) | off | 30 days / 500 MB, every 60 s | http://192.168.68.130:8080 |

AWS side of each site:

| Site | Web page (cloud dashboard) | S3 data bucket | S3 web bucket |
|---|---|---|---|
| `cmd4` | https://d29127v2virrqh.cloudfront.net | `venko-cmd4-884520769610-us-east-1` | `venko-cmd4-web-884520769610` |
| `akvo01005` | https://d3quloi3nf2hd4.cloudfront.net | `venko-akvo01005-884520769610-us-east-1` | `venko-akvo01005-web-884520769610` |
| `akvo01002` | https://dcjm9hzmbcx07.cloudfront.net | `venko-akvo01002-884520769610-us-east-1` | `venko-akvo01002-web-884520769610` |

Everything else about a site (IoT certificate and policy, API, Lambdas, Athena, live status, commands) is in its summary page in VenkoDemo: `docs/sites/README.md` (all sites) and `docs/sites/<key>.md`. Regenerate with `python3 tools/site_summary.py <key> --pi pi@<address>`, and add a row to both tables above for every new site.

## Local dashboard on the Pi

Each Pi can serve its own read-only web page over its local SQLite history (live values, trends, alarm events, CSV export, host health) at `http://<pi-address>:8080`. It works with no internet and no AWS, and has no password - anyone on the Pi's network can view it. All three sites already record the database (`database_enabled=1`); the page is turned on per site:

1. In `sites/<key>/config_data/system.csv` (site 1: `config_data/system.csv`) set `web_enabled=1` and `web_port=8080`, then from `gateway/`: `python3 config_manager.py build --config-dir ../sites/<key>/config_data`.
2. `tools/push_site.sh <key> pi@<address>` - copies code + config and restarts the dashboard.
3. **First time on each Pi only:** `ssh pi@<address> 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl restart akvo-history-web'` installs the `akvo-history-web` service. `push_site.sh` prints a reminder when it's missing.
4. **Optional - open it on the Pi's own screen at every boot** (HDMI/touch display, or the VNC desktop): `ssh pi@<address> 'cd Akvo_Green && tools/setup_kiosk.sh'`. Chromium then starts full screen on the dashboard home (*Actual*) after each boot. `--window` gives a normal window instead, `--off` removes it. Alt+F4 closes the kiosk until the next boot.

Check it: `ssh pi@<address> 'systemctl status akvo-history-web --no-pager'`, or open the page. cmd4 has the boot kiosk turned on. Details: `docs/edge_node_config/CONFIGURATION.md`, "Local web dashboard".
