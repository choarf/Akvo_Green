# sites/

One folder per **additional** gateway site (another Raspberry Pi). The code is shared; each site only has its own settings. Site 1 keeps using the repo-root `config_data/` and `gateway/certs/`.

```
sites/<key>/
  config_data/   devices.csv, modbus.csv, system.csv, aws.csv  →  config.json
  certs/         AmazonRootCA1.pem, certificate.pem.crt, private.pem.key  (git-ignored)
```

- **Build a site's config.** Run from `gateway/`: `python3 config_manager.py build --config-dir ../sites/<key>/config_data`. `export` takes the same option.
- **Deploy to its Pi:** `tools/push_site.sh <key> pi@<address> [--certs]`. This copies the code, the site's `config.json` and, with `--certs`, its certificates. It never touches the Pi's own `config_data/`, `data/`, `logs/` or `.venv/`. For site 1, use `root` as the key.
  It also installs the **AKVO Modbus Tool** (desktop app to scan, read and write Modbus registers on site; menu entry "AKVO Modbus Tool" or `akvo-modbus` on the Pi's desktop). It's built into a `.deb` from `../Tools/AkvoModbus` (or `$AKVO_MODBUS_DIR`) by `tools/build_modbus_tool.sh`, and reinstalled only when its source changed. Skip it with `--no-modbus-tool`. Stop the `akvo-green` service while using it on the same RS-485 port.
- **Unique `client_id` per site** (`aws.csv`), plus the site's own topics. Two Pis connecting with the same client id disconnect each other.

The full procedure covers the AWS IoT certificate and policy, the AWS stack and the dashboard. It's in the VenkoDemo README, section **Adding a site**.

| Site | Client id | Topics | Pi | Modbus | Local dashboard |
|---|---|---|---|---|---|
| (root) site 1 | `AKVO_Gateway` | `AKVO/data`, `AKVO/system` | 192.168.68.131 | real sensors | off |
| `akvo` | `AKVO_Akvo` | `VENKO/akvo/data`, `VENKO/akvo/system` | not installed yet | real sensors | off |
| `cmd4` | `AKVO_cmd4` | `VENKO/cmd4/data`, `VENKO/cmd4/system` | 192.168.68.129 | **simulated** (`simulate_enabled=1`) | http://192.168.68.129:8080 |

AWS side of each site:

| Site | Web page (cloud dashboard) | S3 data bucket | S3 web bucket |
|---|---|---|---|
| (root) site 1 | https://ddlxtxblzvu22.cloudfront.net | `venko-demo-884520769610-us-east-1` | `venko-demo-web-884520769610` |
| `akvo` | https://dsm04m3n65m8j.cloudfront.net | `venko-akvo-884520769610-us-east-1` | `venko-akvo-web-884520769610` |
| `cmd4` | https://d29127v2virrqh.cloudfront.net | `venko-cmd4-884520769610-us-east-1` | `venko-cmd4-web-884520769610` |

Everything else about a site (IoT certificate and policy, API, Lambdas, Athena, live status, commands) is in its summary page in VenkoDemo: `docs/sites/README.md` (all sites) and `docs/sites/<key>.md`. Regenerate with `python3 tools/site_summary.py <key> --pi pi@<address>`, and add a row to both tables above for every new site.

## Local dashboard on the Pi

Each Pi can serve its own read-only web page over its local SQLite history (live values, trends, alarm events, CSV export, host health) at `http://<pi-address>:8080`. It works with no internet and no AWS, and has no password - anyone on the Pi's network can view it. All three sites already record the database (`database_enabled=1`); the page is turned on per site:

1. In `sites/<key>/config_data/system.csv` (site 1: `config_data/system.csv`) set `web_enabled=1` and `web_port=8080`, then from `gateway/`: `python3 config_manager.py build --config-dir ../sites/<key>/config_data`.
2. `tools/push_site.sh <key> pi@<address>` - copies code + config and restarts the dashboard.
3. **First time on each Pi only:** `ssh pi@<address> 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl restart akvo-history-web'` installs the `akvo-history-web` service. `push_site.sh` prints a reminder when it's missing.
4. **Optional - open it on the Pi's own screen at every boot** (HDMI/touch display, or the VNC desktop): `ssh pi@<address> 'cd Akvo_Green && tools/setup_kiosk.sh'`. Chromium then starts full screen on the dashboard home (*Actual*) after each boot. `--window` gives a normal window instead, `--off` removes it. Alt+F4 closes the kiosk until the next boot.

Check it: `ssh pi@<address> 'systemctl status akvo-history-web --no-pager'`, or open the page. cmd4 has the boot kiosk turned on. Details: `docs/edge_node_config/CONFIGURATION.md`, "Local web dashboard".
