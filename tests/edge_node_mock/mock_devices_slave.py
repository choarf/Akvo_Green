#!/usr/bin/env python3
"""
Multi-slave Modbus RTU mock server driven by an edge-node config.json.

edge_node_improved.py polls several devices (slave IDs) over a single shared
RS-485 bus. This script mirrors that: it reads the "devices" section of a
Venko-Green-style config.json (the edge gateway's own config shape) and
serves every enabled device's slave ID and sensor registers on ONE virtual
serial port, the way real slaves would share one bus. Sensor values drift randomly within (and occasionally outside) each
sensor's configured min/max so alarm evaluation (HIGH/LOW) and error handling
(BUS_ERROR/EXCEPTION for an unreachable slave) can be exercised without
physical hardware.

Only FC03 (Read Holding Registers) is required by edge_node_improved.py's
ModbusManager. FC06/FC16 (single/multiple register writes) are also
implemented so the same mock can double as a generic register sandbox.

Usage:
    python3 mock_devices_slave.py --port /tmp/akvo_edge_node_slave \\
        --config ../../config_data/config.json

Pair with a virtual serial link (see tests/akvo_modbus_mock/start_virtual_serial.sh
or run_edge_node_test.py, which sets one up automatically), and point the
edge node's config at the *other* end of that link.
"""

from __future__ import annotations

import argparse
import json
import random
import struct
import threading
import time
from pathlib import Path

import serial


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes) -> bytes:
    return frame + struct.pack("<H", crc16(frame))


def frame_is_valid(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


class SimSensor:
    """Tracks one simulated sensor's physical value and its raw register."""

    def __init__(
        self,
        addr: int,
        count: int,
        scale: float,
        offset: float,
        min_v: float | None,
        max_v: float | None,
    ) -> None:
        self.addr = addr
        self.count = max(count, 1)
        self.scale = scale or 1.0
        self.offset = offset or 0.0
        self.min_v = min_v
        self.max_v = max_v

        lo = min_v if min_v is not None else 0.0
        hi = max_v if max_v is not None else lo + 100.0
        self.value = (lo + hi) / 2.0

    def step(self) -> int:
        """Advance the simulated value one tick and return its raw register."""

        lo = self.min_v if self.min_v is not None else self.value - 50
        hi = self.max_v if self.max_v is not None else self.value + 50
        span = max(hi - lo, 1.0)

        self.value += random.uniform(-span * 0.03, span * 0.03)

        # Occasionally spike out of range to exercise HIGH/LOW alarm evaluation.
        if random.random() < 0.03:
            self.value = hi + span * 0.1 if random.random() < 0.5 else lo - span * 0.1

        # Keep the walk bounded so it doesn't run away over a long test run.
        self.value = max(lo - span * 0.2, min(hi + span * 0.2, self.value))

        raw = round((self.value - self.offset) / self.scale)
        # Registers are unsigned 16-bit; a negative physical value (e.g. a
        # sub-zero "min" on a Temp sensor) can't be represented this way.
        # This mirrors the real SensorNode.read(), which also treats
        # registers as plain unsigned ints with no sign extension.
        return max(0, min(65535, raw))


class MockDeviceSlave:
    """One Modbus RTU slave ID, holding registers for all of its sensors."""

    def __init__(self, slave_id: int) -> None:
        self.slave_id = slave_id
        self.sensors: list[SimSensor] = []
        self.holding = [0] * 16  # grown on demand as sensors are added

    def add_sensor(self, sensor: SimSensor) -> None:
        needed = sensor.addr + sensor.count
        if needed > len(self.holding):
            self.holding.extend([0] * (needed - len(self.holding)))
        self.sensors.append(sensor)

    def tick(self) -> None:
        for sensor in self.sensors:
            self.holding[sensor.addr] = sensor.step()

    def exception(self, function: int, code: int) -> bytes:
        return with_crc(bytes([self.slave_id, function | 0x80, code]))

    def handle(self, frame: bytes) -> bytes | None:
        function = frame[1]
        address = int.from_bytes(frame[2:4], "big")
        quantity_or_value = int.from_bytes(frame[4:6], "big")

        if function == 3:  # Read Holding Registers
            if (
                quantity_or_value <= 0
                or quantity_or_value > 125
                or address + quantity_or_value > len(self.holding)
            ):
                return self.exception(function, 2)
            values = self.holding[address : address + quantity_or_value]
            payload = b"".join(int(v).to_bytes(2, "big") for v in values)
            return with_crc(bytes([self.slave_id, function, len(payload)]) + payload)

        if function == 6:  # Write Single Register
            if address >= len(self.holding):
                return self.exception(function, 2)
            self.holding[address] = quantity_or_value
            return with_crc(frame[:6])

        if function == 16:  # Write Multiple Registers
            quantity = quantity_or_value
            byte_count = frame[6]
            payload = frame[7 : 7 + byte_count]
            if address + quantity > len(self.holding):
                return self.exception(function, 2)
            for i in range(quantity):
                offset = i * 2
                self.holding[address + i] = int.from_bytes(
                    payload[offset : offset + 2], "big"
                )
            return with_crc(frame[:6])

        return self.exception(function, 1)


def load_slaves(config_path: Path, offline: set[int]) -> dict[int, MockDeviceSlave]:
    config = json.loads(config_path.read_text())
    slaves: dict[int, MockDeviceSlave] = {}

    for device in config.get("devices", []):
        if not device.get("enabled", True):
            continue

        slave_id = device["slave"]
        slave = slaves.setdefault(slave_id, MockDeviceSlave(slave_id))

        for sensor_cfg in device.get("sensors", []):
            slave.add_sensor(
                SimSensor(
                    addr=sensor_cfg["addr"],
                    count=sensor_cfg.get("count", 1),
                    scale=sensor_cfg.get("scale", 1.0),
                    offset=sensor_cfg.get("offset", 0.0),
                    min_v=sensor_cfg.get("min"),
                    max_v=sensor_cfg.get("max"),
                )
            )

    for slave_id in offline:
        if slaves.pop(slave_id, None) is not None:
            print(f"Slave {slave_id} configured OFFLINE (no response)")

    return slaves


def expected_length(buffer: bytearray) -> int | None:
    if len(buffer) < 2:
        return None
    function = buffer[1]
    if function == 16:
        return 9 + buffer[6] if len(buffer) >= 7 else None
    return 8


def simulate_forever(
    slaves: dict[int, MockDeviceSlave], interval: float, stop: threading.Event
) -> None:
    while not stop.is_set():
        for slave in slaves.values():
            slave.tick()
        time.sleep(interval)


def run(port: str, config_path: Path, interval: float, offline: set[int]) -> None:
    slaves = load_slaves(config_path, offline)
    if not slaves:
        raise SystemExit(f"No enabled devices found in {config_path}")

    print(
        f"Serving {len(slaves)} slave(s) from {config_path}: "
        f"{sorted(slaves)} on {port}"
    )

    stop = threading.Event()
    sim_thread = threading.Thread(
        target=simulate_forever, args=(slaves, interval, stop), daemon=True
    )
    sim_thread.start()

    ser = serial.Serial(
        port=port, baudrate=9600, bytesize=8, parity="N", stopbits=1, timeout=0.05
    )
    buffer = bytearray()

    try:
        while True:
            data = ser.read(256)
            if not data:
                continue

            buffer.extend(data)
            time.sleep(0.003)

            while True:
                needed = expected_length(buffer)
                if needed is None or len(buffer) < needed:
                    break

                frame = bytes(buffer[:needed])
                del buffer[:needed]

                if not frame_is_valid(frame):
                    continue

                slave = slaves.get(frame[0])
                if slave is None:
                    continue  # unknown/offline slave: stay silent, master times out

                response = slave.handle(frame)
                if response:
                    ser.write(response)
                    ser.flush()
    finally:
        stop.set()
        ser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", required=True, help="Virtual serial port to listen on"
    )
    parser.add_argument(
        "--config",
        default=str(
            Path(__file__).resolve().parents[2] / "config_data" / "config.json"
        ),
        help="Edge-node config.json to source devices/slaves/sensors from",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between simulated sensor updates",
    )
    parser.add_argument(
        "--offline",
        type=int,
        nargs="*",
        default=[],
        help="Slave IDs to simulate as offline (no response)",
    )
    args = parser.parse_args()

    run(args.port, Path(args.config), args.interval, set(args.offline))


if __name__ == "__main__":
    main()
