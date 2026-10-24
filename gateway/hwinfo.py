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


PROC_ROUTE = Path("/proc/net/route")
SYS_NET = Path("/sys/class/net")
# Names Linux / ModemManager give cellular data links (QMI/MBIM wwan, PPP, USB modems in RNDIS/ECM mode).
_CELLULAR_PREFIXES = ("wwan", "wwp", "ppp", "usb")


def default_interface(route_file: Path = PROC_ROUTE) -> str | None:
    """Interface of the default route with the lowest metric (the way out to the internet)."""
    try:
        rows = [line.split() for line in route_file.read_text().splitlines()[1:]]
    except OSError:
        return None
    defaults = [(int(r[6]), r[0]) for r in rows if len(r) > 6 and r[1] == "00000000" and int(r[3], 16) & 1]
    return min(defaults)[1] if defaults else None


def interface_type(iface: str, sys_net: Path = SYS_NET) -> str:
    """'wifi' | 'cellular' | 'ethernet' | 'other'."""
    if (sys_net / iface / "wireless").exists() or (sys_net / iface / "phy80211").exists():
        return "wifi"
    if iface.startswith(_CELLULAR_PREFIXES):
        return "cellular"
    if iface.startswith(("eth", "en")):
        return "ethernet"
    return "other"


def network_info(route_file: Path = PROC_ROUTE, sys_net: Path = SYS_NET) -> dict | None:
    """{"iface", "type"} of the link the gateway uses to reach the internet, None without a default route."""
    iface = default_interface(route_file)
    return {"iface": iface, "type": interface_type(iface, sys_net)} if iface else None
