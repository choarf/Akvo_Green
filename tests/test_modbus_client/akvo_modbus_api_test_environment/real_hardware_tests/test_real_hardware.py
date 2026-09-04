#!/usr/bin/env python3
"""AKVO Modbus API real-hardware smoke/functional test runner."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow execution from the project root or this directory.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modbus_client import ModbusClient


HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
RESULTS_DIR = HERE / "results"


def load_config():
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def record(results, name, passed, detail="", elapsed_ms=None):
    results.append({
        "test": name,
        "passed": bool(passed),
        "elapsed_ms": elapsed_ms,
        "detail": detail,
    })
    status = "PASS" if passed else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{status:4}] {name}{suffix}")


def timed_call(fn):
    start = time.perf_counter()
    try:
        value = fn()
        return value, (time.perf_counter() - start) * 1000.0
    except Exception as exc:
        return exc, (time.perf_counter() - start) * 1000.0


def main():
    cfg = load_config()
    RESULTS_DIR.mkdir(exist_ok=True)

    results = []
    client = ModbusClient()

    print("\nAKVO Modbus API - REAL HARDWARE TEST")
    print("=" * 55)
    print(f"Port       : {cfg['port']}")
    print(f"Slave ID   : {cfg['slave_id']}")
    print(f"Serial     : {cfg['baudrate']} {cfg['bytesize']}{cfg['parity']}{cfg['stopbits']}")
    print(f"Timeout    : {cfg['timeout']} s")
    print(f"Writes     : {cfg['write_tests_enabled']}")
    print()

    # Connection
    value, ms = timed_call(lambda: client.connect(
        port=cfg["port"],
        baudrate=cfg["baudrate"],
        parity=cfg["parity"],
        stopbits=cfg["stopbits"],
        bytesize=cfg["bytesize"],
        timeout=cfg["timeout"],
    ))
    record(results, "RT-001 Connect", value is True, client.get_last_error(), ms)

    if not value:
        print("\nConnection failed. Check USB-RS485 adapter, port, wiring and serial settings.")
        return finish(results, client, "FAILED")

    try:
        # Ping
        value, ms = timed_call(lambda: client.ping(cfg["slave_id"]))
        record(results, "RT-002 Ping", value is True, client.get_last_error(), ms)

        # FC03
        value, ms = timed_call(lambda: client.read_holding_registers(
            cfg["slave_id"], cfg["holding_address"], cfg["holding_count"]))
        record(results, "RT-003 FC03 Holding Registers",
               isinstance(value, list) and len(value) == cfg["holding_count"],
               client.get_last_error(), ms)

        # FC04
        value, ms = timed_call(lambda: client.read_input_registers(
            cfg["slave_id"], cfg["input_address"], cfg["input_count"]))
        record(results, "RT-004 FC04 Input Registers",
               isinstance(value, list) and len(value) == cfg["input_count"],
               client.get_last_error(), ms)

        # FC01
        value, ms = timed_call(lambda: client.read_coils(
            cfg["slave_id"], cfg["coil_address"], cfg["coil_count"]))
        record(results, "RT-005 FC01 Coils",
               isinstance(value, list) and len(value) == cfg["coil_count"],
               client.get_last_error(), ms)

        # FC02
        value, ms = timed_call(lambda: client.read_discrete_inputs(
            cfg["slave_id"], cfg["discrete_address"], cfg["discrete_count"]))
        record(results, "RT-006 FC02 Discrete Inputs",
               isinstance(value, list) and len(value) == cfg["discrete_count"],
               client.get_last_error(), ms)

        # Statistics
        stats = client.get_statistics()
        record(results, "RT-007 Statistics",
               isinstance(stats, dict) and stats.get("requests", 0) >= 5,
               json.dumps(stats), None)

        # Optional writes
        if cfg["write_tests_enabled"]:
            print("\nWRITE TESTS ENABLED — verify the configured addresses are safe.\n")

            value, ms = timed_call(lambda: client.write_register(
                cfg["slave_id"],
                cfg["safe_write_register"],
                cfg["safe_write_value"]))
            record(results, "RT-008 FC06 Write Register", value is True,
                   client.get_last_error(), ms)

            value, ms = timed_call(lambda: client.write_registers(
                cfg["slave_id"],
                cfg["safe_write_register"],
                cfg["safe_write_registers"]))
            record(results, "RT-009 FC16 Write Registers", value is True,
                   client.get_last_error(), ms)

            value, ms = timed_call(lambda: client.write_coil(
                cfg["slave_id"],
                cfg["safe_write_coil"],
                cfg["safe_write_coil_value"]))
            record(results, "RT-010 FC05 Write Coil", value is True,
                   client.get_last_error(), ms)

            value, ms = timed_call(lambda: client.write_coils(
                cfg["slave_id"],
                cfg["safe_write_coil"],
                cfg["safe_write_coils"]))
            record(results, "RT-011 FC15 Write Coils", value is True,
                   client.get_last_error(), ms)
        else:
            print("\nWrite tests skipped (write_tests_enabled=false).")

        # Clean disconnect
        value, ms = timed_call(lambda: client.disconnect())
        record(results, "RT-012 Disconnect", True, "", ms)

    finally:
        try:
            client.close()
        except Exception:
            pass

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    status = "PASSED" if passed == total else "FAILED"
    return finish(results, client, status)


def finish(results, client, status):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = RESULTS_DIR / f"real_test_{stamp}.json"

    report = {
        "timestamp": datetime.now().isoformat(),
        "status": status,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "results": results,
        "statistics": client.get_statistics(),
    }

    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 55)
    print(f"RESULT: {status}")
    print(f"Passed: {report['passed']}/{report['total']}")
    print(f"Report: {output}")
    print("=" * 55)

    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
