#!/usr/bin/env python3
"""Print a root shell script that installs a site's WiFi networks into
NetworkManager on the Pi. Used by tools/push_site.sh, which pipes it to
`ssh <pi> sudo bash -s`.

  python3 tools/wifi_profiles.py <config.json> <wifi.csv>

config.json's "wifi" section lists the SSIDs, preferred first
({"networks": ["Planta", "Oficina"]}). The passwords come from the site's
git-ignored wifi.csv (columns ssid,password; an empty password = open
network). Each network becomes a keyfile profile akvo-wifi1 / akvo-wifi2
(mode 600, root only), so a password never appears on a command line or in
config.json. A slot with no network removes its profile. Profiles made by
Raspberry Pi Imager or by hand are never touched.

Exit status: 0 = script printed, 2 = config has no "wifi" section (leave the
Pi's WiFi alone), 1 = error (message on stderr, nothing printed).
"""
import csv
import json
import sys
from pathlib import Path

PROFILE_DIR = "/etc/NetworkManager/system-connections"
PRIORITIES = (20, 10)  # akvo-wifi1 is preferred over akvo-wifi2


def _keyfile_value(value: str) -> str:
    """Escape a value for NetworkManager's keyfile (GKeyFile) format."""
    out = value.replace("\\", "\\\\")
    if out.startswith(" "):
        out = "\\s" + out[1:]
    return out


def keyfile(slot: int, ssid: str, password: str) -> str:
    lines = [
        "[connection]",
        f"id=akvo-wifi{slot}",
        "type=wifi",
        "autoconnect=true",
        f"autoconnect-priority={PRIORITIES[slot - 1]}",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={_keyfile_value(ssid)}",
        "",
    ]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk", f"psk={_keyfile_value(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def load_passwords(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {(row.get("ssid") or "").strip(): row.get("password") or ""
                for row in csv.DictReader(f) if (row.get("ssid") or "").strip()}


def build_script(config: dict, passwords: dict[str, str], wifi_csv: str) -> str:
    networks = config["wifi"].get("networks", [])
    lines = ["set -e", "umask 077"]
    for slot in (1, 2):
        path = f"{PROFILE_DIR}/akvo-wifi{slot}.nmconnection"
        if slot > len(networks):
            lines.append(f"rm -f '{path}'")
            continue
        ssid = networks[slot - 1]
        if ssid not in passwords:
            raise ValueError(f"no password for WiFi {ssid!r} in {wifi_csv} "
                             "(add a row: ssid,password - empty password for an open network)")
        password = passwords[ssid]
        if password.startswith("CHANGE_ME"):
            raise ValueError(f"WiFi {ssid!r}: still the CHANGE_ME placeholder in {wifi_csv} - put the real password")
        if password and not 8 <= len(password) <= 63:
            raise ValueError(f"WiFi {ssid!r}: a WPA password must be 8-63 characters")
        if "\n" in password or "\r" in password:
            raise ValueError(f"WiFi {ssid!r}: the password can't contain a line break")
        lines += [f"cat > '{path}' <<'AKVO_EOF'", keyfile(slot, ssid, password) + "AKVO_EOF",
                  f"chmod 600 '{path}'"]
    lines.append("nmcli connection reload")
    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: tools/wifi_profiles.py <config.json> <wifi.csv>", file=sys.stderr)
        return 1
    config = json.loads(Path(sys.argv[1]).read_text())
    if "wifi" not in config:
        return 2
    try:
        script = build_script(config, load_passwords(Path(sys.argv[2])), sys.argv[2])
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
