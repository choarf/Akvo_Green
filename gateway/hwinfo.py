"""Which physical machine this is: hostname, board model and serial number.

The serial (from the device tree, the same one as `cat /proc/device-tree/serial-number`)
never changes with the SD card, the IP or the hostname, so it ties a site
to its Pi: tools/push_site.sh checks it against sites/<key>/pi.csv before
pushing, and the gateway reports it so the dashboards can show it.
"""
from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

DEVICE_TREE = Path("/proc/device-tree")


def _read(path: Path) -> str | None:
    try:
        value = path.read_bytes().rstrip(b"\0").decode("ascii", "replace").strip()
    except OSError:
        return None
    return value or None


def hardware_info(device_tree: Path = DEVICE_TREE) -> dict:
    """{"hostname", "hw_model", "hw_serial"}; model/serial are None off a Pi."""
    return {
        "hostname": socket.gethostname(),
        "hw_model": _read(device_tree / "model"),
        "hw_serial": _read(device_tree / "serial-number"),
    }


def _nmcli(args: list[str], run) -> list[list[str]]:
    """nmcli -t rows split on unescaped ':' (an SSID may contain '\\:')."""
    out = run(["nmcli", "-t", *args], capture_output=True, text=True, timeout=5, check=False).stdout
    return [[f.replace("\\:", ":") for f in re.split(r"(?<!\\):", line)] for line in out.splitlines() if line]


def wifi_info(run=subprocess.run) -> dict | None:
    """{"ssid", "profile", "signal"} of the active WiFi link (NetworkManager), None without one.

    profile is the NetworkManager connection name - akvo-wifi1 / akvo-wifi2 when
    push_site.sh installed the site's networks; signal is 0-100 %."""
    try:
        active = [r for r in _nmcli(["-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi", "list", "--rescan", "no"], run)
                  if len(r) >= 3 and r[0] == "yes"]
        profiles = [r[0] for r in _nmcli(["-f", "NAME,TYPE", "connection", "show", "--active"], run)
                    if len(r) >= 2 and r[1] == "802-11-wireless"]
    except (OSError, subprocess.SubprocessError):
        return None
    if not active:
        return None
    ssid, signal = active[0][1], active[0][2]
    return {"ssid": ssid or None, "profile": profiles[0] if profiles else None,
            "signal": int(signal) if signal.isdigit() else None}
