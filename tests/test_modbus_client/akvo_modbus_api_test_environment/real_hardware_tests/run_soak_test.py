#!/usr/bin/env python3
"""AKVO Modbus API real-device polling/soak test.

Run manually, for example:
    python3 real_hardware_tests/run_soak_test.py
    python3 real_hardware_tests/run_soak_test.py --duration 600 --interval 2
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modbus_client import ModbusClient

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def parse_args():
    parser = argparse.ArgumentParser(description="AKVO Modbus API real-device soak test")
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Test duration in seconds (default: 3600)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds (default: 1.0)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.duration <= 0:
        raise SystemExit("--duration must be greater than 0")
    if args.interval < 0:
        raise SystemExit("--interval must be 0 or greater")

    client = ModbusClient()

    connected = client.connect(
        port=CFG["port"],
        baudrate=CFG["baudrate"],
        parity=CFG["parity"],
        stopbits=CFG["stopbits"],
        bytesize=CFG["bytesize"],
        timeout=CFG["timeout"],
    )

    if not connected:
        print("ERROR: Could not connect to the Modbus device.")
        print("Last error:", client.get_last_error())
        return 1

    start = time.time()
    cycles = 0

    print("AKVO Modbus API - REAL DEVICE SOAK TEST")
    print("=" * 55)
    print(f"Port       : {CFG['port']}")
    print(f"Slave ID   : {CFG['slave_id']}")
    print(f"Duration   : {args.duration} seconds")
    print(f"Interval   : {args.interval} seconds")
    print()

    try:
        while time.time() - start < args.duration:
            cycles += 1

            values = client.read_holding_registers(
                CFG["slave_id"],
                CFG["holding_address"],
                CFG["holding_count"],
            )

            if cycles % 60 == 0:
                print(
                    datetime.now().isoformat(),
                    "cycles=", cycles,
                    "values=", values,
                    "stats=", client.get_statistics(),
                    "last_error=", client.get_last_error(),
                )

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nSoak test interrupted by user.")

    finally:
        client.close()

        report = {
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": time.time() - start,
            "cycles": cycles,
            "statistics": client.get_statistics(),
            "last_error": client.get_last_error(),
        }

        output = HERE / "results" / (
            f"soak_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print()
        print("Soak test complete.")
        print(f"Cycles : {cycles}")
        print(f"Stats  : {json.dumps(report['statistics'], indent=2)}")
        print(f"Report : {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
