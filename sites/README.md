# sites/

One folder per **additional** gateway site (another Raspberry Pi). The code is shared; each site only has its own settings. Site 1 keeps using the repo-root `config_data/` and `gateway/certs/`.

```
sites/<key>/
  config_data/   devices.csv, modbus.csv, system.csv, aws.csv  →  config.json
  certs/         AmazonRootCA1.pem, certificate.pem.crt, private.pem.key  (git-ignored)
```

- **Build a site's config.** Run from `gateway/`: `python3 config_manager.py build --config-dir ../sites/<key>/config_data`. `export` takes the same option.
- **Deploy to its Pi:** `tools/push_site.sh <key> pi@<address> [--certs]`. This copies the code, the site's `config.json` and, with `--certs`, its certificates. It never touches the Pi's own `config_data/`, `data/`, `logs/` or `.venv/`. For site 1, use `root` as the key.
- **Unique `client_id` per site** (`aws.csv`), plus the site's own topics. Two Pis connecting with the same client id disconnect each other.

The full procedure covers the AWS IoT certificate and policy, the AWS stack and the dashboard. It's in the VenkoDemo README, section **Adding a site**.

| Site | Client id | Topics | Pi |
|---|---|---|---|
| (root) site 1 | `AKVO_Gateway` | `AKVO/data`, `AKVO/system` | 192.168.68.131 |
| `akvo` | `AKVO_Akvo` | `VENKO/akvo/data`, `VENKO/akvo/system` | not installed yet |
