#!/usr/bin/env python3
"""
End-to-end test harness for src/Venko_Green/edge_node_improved.py.

Runs the real EdgeNode engine (scheduler/worker/publisher/config_watcher
threads, alarm evaluation, reconnect logic - unmodified) against Modbus and
MQTT, each of which can independently be faked or left real. Which is which
is controlled by harness_config.json (see --settings):

    {"fake_modbus": true, "fake_mqtt": true}

  - fake_modbus: true  -> a mock multi-slave Modbus RTU server
    (mock_devices_slave.py), reachable over a virtual serial port pair (via
    socat), simulating every device/sensor defined in config.json.
    false -> connects to whatever real serial port config.json's
    modbus.port already points at.
  - fake_mqtt: true  -> a FakeMqttConnection (fake_mqtt.py) stands in for
    AWS IoT Core, so no AWS credentials or network access are needed.
    false -> uses the real AWS IoT mtls_from_path connection, which needs
    valid certs/network reachability in config.json's aws section.

CLI flags override the settings file for one-off runs without editing it.

Requires `socat` on PATH when fake_modbus is true (Linux/macOS):
`sudo apt install socat`

Usage:
    python3 run_edge_node_test.py
    python3 run_edge_node_test.py --config ../../src/Venko_Green/config_data/config.json
    python3 run_edge_node_test.py --duration 60 --offline 3 9
    python3 run_edge_node_test.py --duration 0            # run until Ctrl+C
    python3 run_edge_node_test.py --no-fake-mqtt           # real AWS, mock Modbus
    python3 run_edge_node_test.py --no-fake-modbus         # real hardware, fake MQTT
    python3 run_edge_node_test.py --settings my_settings.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENKO_GREEN_DIR = HERE.parents[1] / "src" / "Venko_Green"
DEFAULT_CONFIG = VENKO_GREEN_DIR / "config_data" / "config.json"
DEFAULT_SETTINGS = HERE / "harness_config.json"

MASTER_PORT = "/tmp/akvo_edge_node_master"
SLAVE_PORT = "/tmp/akvo_edge_node_slave"


def load_settings(path: Path) -> dict:
    """Read {"fake_modbus": bool, "fake_mqtt": bool} from a JSON file.

    Missing keys default to true (the fully-mocked, hardware/AWS-free mode).
    """
    settings = json.loads(path.read_text()) if path.exists() else {}
    return {
        "fake_modbus": settings.get("fake_modbus", True),
        "fake_mqtt": settings.get("fake_mqtt", True),
    }


def start_virtual_serial() -> subprocess.Popen:
    for path in (MASTER_PORT, SLAVE_PORT):
        Path(path).unlink(missing_ok=True)

    proc = subprocess.Popen(
        [
            "socat",
            "-d",
            "-d",
            f"PTY,link={MASTER_PORT},raw,echo=0,mode=666",
            f"PTY,link={SLAVE_PORT},raw,echo=0,mode=666",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(50):
        if Path(MASTER_PORT).exists() and Path(SLAVE_PORT).exists():
            return proc
        time.sleep(0.1)

    proc.terminate()
    raise RuntimeError("socat did not create the virtual serial link in time")


def start_mock_slave(
    config_path: Path, interval: float, offline: list[int]
) -> subprocess.Popen:
    cmd = [
        sys.executable,
        str(HERE / "mock_devices_slave.py"),
        "--port",
        SLAVE_PORT,
        "--config",
        str(config_path),
        "--interval",
        str(interval),
    ]
    if offline:
        cmd += ["--offline", *map(str, offline)]
    return subprocess.Popen(cmd)


def build_test_config(source: Path, dest: Path, fake_modbus: bool, cert_base: Path) -> None:
    """Copy config.json, pointing modbus.port at the mock's virtual port
    when fake_modbus is enabled. Left untouched (real hardware port) otherwise.

    AWS cert paths are always resolved to absolute paths against cert_base
    (normally VENKO_GREEN_DIR, not source's own directory - config.json now
    lives in a config_data/ subfolder, but the "./certs/..." relative paths
    it stores are still meant to resolve from src/Venko_Green/, since that's
    where edge_node_improved.py is normally run from): the harness chdir's
    into a scratch work dir before starting EdgeNode, which would otherwise
    break those relative paths (only matters when fake_mqtt is disabled and
    the real builder opens them)."""
    config = json.loads(source.read_text())
    if fake_modbus:
        config["modbus"]["port"] = MASTER_PORT

    aws = config.get("aws", {})
    for key in ("cert", "key", "ca"):
        path = aws.get(key)
        if path:
            aws[key] = str((cert_base / path).resolve())

    dest.write_text(json.dumps(config, indent=4))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS,
        help="JSON file selecting fake_modbus/fake_mqtt (default: harness_config.json)",
    )
    parser.add_argument(
        "--fake-modbus",
        dest="fake_modbus",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the settings file's fake_modbus flag",
    )
    parser.add_argument(
        "--fake-mqtt",
        dest="fake_mqtt",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override the settings file's fake_mqtt flag",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Seconds to run before stopping (0 = run until Ctrl+C)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Mock sensor refresh interval, in seconds (fake_modbus only)",
    )
    parser.add_argument(
        "--offline",
        type=int,
        nargs="*",
        default=[],
        help="Slave IDs to simulate as offline, no response (fake_modbus only)",
    )
    args = parser.parse_args()

    if not args.config.exists():
        raise SystemExit(f"Config file not found: {args.config}")

    settings = load_settings(args.settings)
    fake_modbus = settings["fake_modbus"] if args.fake_modbus is None else args.fake_modbus
    fake_mqtt = settings["fake_mqtt"] if args.fake_mqtt is None else args.fake_mqtt

    if not fake_modbus and args.offline:
        print("Note: --offline is ignored when fake_modbus is disabled.")

    print(f"fake_modbus={fake_modbus}  fake_mqtt={fake_mqtt}  (settings: {args.settings})")

    work_dir = Path(tempfile.mkdtemp(prefix="akvo_edge_node_test_"))
    test_config = work_dir / "config.json"
    build_test_config(args.config, test_config, fake_modbus, cert_base=VENKO_GREEN_DIR)

    print(f"Test config: {test_config}")
    print(f"Working dir (logs go here): {work_dir}")

    socat_proc = None
    mock_proc = None

    if fake_modbus:
        socat_proc = start_virtual_serial()
        print(f"Virtual serial link: {MASTER_PORT} <-> {SLAVE_PORT}")

        mock_proc = start_mock_slave(args.config, args.interval, args.offline)
        time.sleep(0.5)  # let the mock slave open its side of the serial port
    else:
        real_port = json.loads(args.config.read_text())["modbus"]["port"]
        print(f"Connecting to real Modbus hardware at {real_port}")

    sys.path.insert(0, str(VENKO_GREEN_DIR))
    import edge_node_improved as en  # noqa: E402  (path must be set first)
    from fake_mqtt import make_fake_mtls_from_path, round_floats  # noqa: E402

    if fake_mqtt:
        en.mqtt_connection_builder.mtls_from_path = make_fake_mtls_from_path()
    else:
        print(
            "Using the real AWS IoT MQTT connection "
            "(requires valid certs/network in config.json's aws section)."
        )

    previous_cwd = Path.cwd()
    os.chdir(work_dir)

    node = en.EdgeNode(str(test_config))
    node_thread = threading.Thread(target=node.start, daemon=True)

    try:
        node_thread.start()

        if args.duration > 0:
            time.sleep(args.duration)
        else:
            print("Running until Ctrl+C ...")
            while node_thread.is_alive():
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()

        print("\n=== Final device snapshots ===")
        for device_id, device in node.devices.items():
            print(f"{device_id}: {round_floats(device.snapshot())}")

        os.chdir(previous_cwd)

        if mock_proc is not None:
            mock_proc.terminate()
            mock_proc.wait(timeout=5)
        if socat_proc is not None:
            socat_proc.terminate()
            socat_proc.wait(timeout=5)


if __name__ == "__main__":
    main()
