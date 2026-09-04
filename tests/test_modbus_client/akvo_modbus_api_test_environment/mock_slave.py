#!/usr/bin/env python3
"""Minimal deterministic Modbus RTU mock slave for API integration testing."""

import argparse
import struct
import time
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


def valid(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


class MockSlave:
    def __init__(self, port, slave_id=1):
        self.slave_id = slave_id
        self.holding = [0] * 200
        self.inputs = [0] * 200
        self.coils = [False] * 200
        self.discrete = [False] * 200

        self.holding[0:4] = [1234, 250, 1000, 65535]
        self.holding[10] = 3141
        self.inputs[0:3] = [500, 750, 1250]
        self.inputs[10] = 4200
        self.coils[0:4] = [True, False, True, False]
        self.discrete[0:4] = [True, True, False, True]

        self.serial = serial.Serial(
            port=port, baudrate=9600, bytesize=8,
            parity="N", stopbits=1, timeout=0.05
        )

    def exception(self, function, code):
        return with_crc(bytes([self.slave_id, function | 0x80, code]))

    def read(self, function, address, quantity):
        if function == 1:
            data = self.coils
            max_q = 2000
        elif function == 2:
            data = self.discrete
            max_q = 2000
        elif function == 3:
            data = self.holding
            max_q = 125
        else:
            data = self.inputs
            max_q = 125

        if quantity <= 0 or quantity > max_q or address + quantity > len(data):
            return self.exception(function, 2)

        selected = data[address:address + quantity]

        if function in (1, 2):
            payload = bytearray((quantity + 7) // 8)
            for i, value in enumerate(selected):
                if value:
                    payload[i // 8] |= 1 << (i % 8)
        else:
            payload = b"".join(int(v).to_bytes(2, "big") for v in selected)

        return with_crc(bytes([
            self.slave_id, function, len(payload)
        ]) + bytes(payload))

    def handle(self, frame):
        if not valid(frame) or frame[0] != self.slave_id:
            return None

        function = frame[1]
        address = int.from_bytes(frame[2:4], "big")
        quantity_or_value = int.from_bytes(frame[4:6], "big")

        if function in (1, 2, 3, 4):
            return self.read(function, address, quantity_or_value)

        if function == 5:
            self.coils[address] = quantity_or_value == 0xFF00
            return with_crc(frame[:6])

        if function == 6:
            self.holding[address] = quantity_or_value
            return with_crc(frame[:6])

        if function in (15, 16):
            quantity = quantity_or_value
            byte_count = frame[6]
            payload = frame[7:7 + byte_count]

            if function == 15:
                for i in range(quantity):
                    self.coils[address + i] = bool(
                        payload[i // 8] & (1 << (i % 8))
                    )
            else:
                for i in range(quantity):
                    offset = i * 2
                    self.holding[address + i] = int.from_bytes(
                        payload[offset:offset + 2], "big"
                    )

            return with_crc(frame[:6])

        return self.exception(function, 1)

    def expected_length(self, buffer):
        if len(buffer) < 2:
            return None
        function = buffer[1]
        if function in (1, 2, 3, 4, 5, 6):
            return 8
        if function in (15, 16):
            return 9 + buffer[6] if len(buffer) >= 7 else None
        return 8

    def run(self):
        print(
            f"Mock Modbus RTU slave {self.slave_id} on {self.serial.port}",
            flush=True
        )
        buffer = bytearray()

        while True:
            data = self.serial.read(256)
            if not data:
                continue

            buffer.extend(data)
            time.sleep(0.003)

            while True:
                expected = self.expected_length(buffer)
                if expected is None or len(buffer) < expected:
                    break

                frame = bytes(buffer[:expected])
                del buffer[:expected]

                response = self.handle(frame)
                if response:
                    self.serial.write(response)
                    self.serial.flush()
                    print(f"RX {frame.hex(' ')}", flush=True)
                    print(f"TX {response.hex(' ')}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--slave", type=int, default=1)
    args = parser.parse_args()

    if not 1 <= args.slave <= 247:
        raise SystemExit("Slave ID must be 1..247")

    MockSlave(args.port, args.slave).run()


if __name__ == "__main__":
    main()
