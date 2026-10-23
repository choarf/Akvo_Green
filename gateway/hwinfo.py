"""Which physical machine this is: hostname, board model and serial number.

The serial (from the device tree, the same one as `cat /proc/device-tree/serial-number`)
never changes with the SD card, the IP or the hostname, so it ties a site
to its Pi: tools/push_site.sh checks it against sites/<key>/pi.csv before
pushing, and the gateway reports it so the dashboards can show it.
"""
from __future__ import annotations

import socket
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
