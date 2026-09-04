#!/usr/bin/env python3
"""Human-readable integration test against the virtual Modbus RTU slave."""

import argparse
from modbus_client import ModbusClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/tmp/akvo_modbus_master")
    parser.add_argument("--slave", type=int, default=1)
    args = parser.parse_args()

    client = ModbusClient()

    print("=" * 64)
    print("AKVO Modbus Client V3 - API INTEGRATION TEST")
    print("=" * 64)

    if not client.connect(
        port=args.port,
        baudrate=9600,
        parity="N",
        stopbits=1,
        bytesize=8,
        timeout=1.0,
    ):
        print("CONNECT: FAIL")
        print("ERROR:", client.get_last_error())
        return 1

    print("CONNECT: PASS")

    tests = [
        ("FC03", lambda: client.read_holding_registers(args.slave, 0, 4)),
        ("FC04", lambda: client.read_input_registers(args.slave, 0, 3)),
        ("FC01", lambda: client.read_coils(args.slave, 0, 4)),
        ("FC02", lambda: client.read_discrete_inputs(args.slave, 0, 4)),
        ("FC06", lambda: client.write_register(args.slave, 20, 4321)),
        ("FC16", lambda: client.write_registers(args.slave, 21, [11, 22, 33])),
        ("FC05", lambda: client.write_coil(args.slave, 4, True)),
        ("FC15", lambda: client.write_coils(args.slave, 5, [True, False, True])),
    ]

    passed = 0
    failed = 0

    for name, operation in tests:
        try:
            result = operation()
            ok = bool(result)
            print(f"{name}: {'PASS' if ok else 'FAIL'} -> {result}")
            passed += ok
            failed += not ok
        except Exception as exc:
            print(f"{name}: FAIL -> {exc}")
            failed += 1

    checks = [
        ("FC06 read-back", client.read_holding_registers(args.slave, 20, 1), [4321]),
        ("FC16 read-back", client.read_holding_registers(args.slave, 21, 3), [11, 22, 33]),
    ]

    for name, actual, expected in checks:
        ok = actual == expected
        print(f"{name}: {'PASS' if ok else 'FAIL'} -> {actual}")
        passed += ok
        failed += not ok

    print()
    print("STATISTICS:", client.get_statistics())
    print("LAST ERROR:", client.get_last_error() or "<none>")

    client.disconnect()

    print()
    print(f"RESULT: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
