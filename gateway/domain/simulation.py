"""
In-process Modbus simulation, for running the gateway with no real RS-485
bus at all - a demo site, a bare Pi with no sensors wired yet, a plain VM
or container.

Deliberately NOT the same thing as tests/edge_node_mock/mock_devices_slave.py,
which simulates at the wire level (real Modbus-RTU framing over a virtual
serial port via socat) because its job is to exercise pymodbus's real
serial client path in tests. This module simulates one level higher, at
the exact interface ModbusManager.read_holding_registers() exposes to
SensorNode.read() - so there's no serial port, no socat, nothing to set
up beyond one config flag, and it runs anywhere Python runs.

SimSensor's random-walk-with-occasional-excursion shape mirrors
mock_devices_slave.py's SimSensor on purpose (same demo "feel", bounded
drift plus rare alarm-triggering spikes) - but it encodes directly to/from
physical values via domain.sensors' own decode(), instead of writing into
a simulated holding-register array, so every sensor type decode_sensor()
supports (including the 2-register uint32/int32/float32 types) round-trips
correctly. mock_devices_slave.py's holding-register model only ever fills
one word of a multi-register sensor, which is fine for exercising protocol
handling in tests but would not produce a sensible decoded value for a
float32/uint32/int32 sensor here.
"""

from __future__ import annotations

import logging
import random
import struct

logger = logging.getLogger("EdgeNode")


class SimSensor:
    """Tracks one simulated sensor's physical value and encodes it back
    into the holding-register words domain.sensors.decode() would decode
    it from - the exact inverse of decode_sensor(), so a simulated read is
    indistinguishable, at the SensorNode.read() boundary, from a real one
    for every supported sensor type."""

    def __init__(
        self,
        sensor_type: str,
        scale: float = 1.0,
        offset: float = 0.0,
        min_v: float | None = None,
        max_v: float | None = None,
    ):
        self.type = sensor_type
        self.scale = scale or 1.0
        self.offset = offset or 0.0
        self.min_v = min_v
        self.max_v = max_v

        lo = min_v if min_v is not None else 0.0
        hi = max_v if max_v is not None else lo + 100.0
        self.value = (lo + hi) / 2.0

    def step(self) -> float:
        """Advance the simulated physical value one tick (bounded random
        walk, occasionally spiking out of range to exercise HIGH/LOW
        alarm evaluation) and return it."""
        lo = self.min_v if self.min_v is not None else self.value - 50
        hi = self.max_v if self.max_v is not None else self.value + 50
        span = max(hi - lo, 1.0)

        self.value += random.uniform(-span * 0.03, span * 0.03)

        if random.random() < 0.03:
            self.value = hi + span * 0.1 if random.random() < 0.5 else lo - span * 0.1

        self.value = max(lo - span * 0.2, min(hi + span * 0.2, self.value))
        return self.value

    def registers(self) -> list[int]:
        """Encode the current physical value into the raw holding-register
        word(s) that domain.sensors.decode(self.type, ..., self.scale,
        self.offset) would decode back into (approximately, after rounding)
        that same value - the inverse of each decoder in domain/sensors.py."""
        if self.type in ("int", "uint16"):
            return [max(0, min(0xFFFF, round(self.value)))]

        if self.type == "float":
            raw = round((self.value - self.offset) / self.scale)
            return [max(0, min(0xFFFF, raw))]

        if self.type == "uint32":
            raw = max(0, min(0xFFFFFFFF, round(self.value)))
            packed = struct.pack(">I", raw)
        elif self.type == "int32":
            raw = max(-(2**31), min(2**31 - 1, round(self.value)))
            packed = struct.pack(">i", raw)
        elif self.type == "float32":
            packed = struct.pack(">f", (self.value - self.offset) / self.scale)
        else:
            raise ValueError(f"simulation: unsupported sensor type {self.type!r}")

        hi, lo = struct.unpack(">HH", packed)
        return [hi, lo]


class _SimResult:
    """Stands in for pymodbus's read response object - same shape
    FakeModbusResult (tests/test_edge_node/helpers.py) uses, since that's
    exactly the interface SensorNode.read() depends on: .isError()/.registers."""

    def __init__(self, registers: list[int] | None = None, error: bool = False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self) -> bool:
        return self._error


class SimulatedModbusManager:
    """Drop-in stand-in for ModbusManager - same public shape
    (connect/read_holding_registers/update_config/disconnect) so
    SensorNode/DeviceNode/EdgeNode's worker/scheduler/publisher threads
    need no changes at all to run against this instead of a real serial
    bus. Built once from cfg["devices"] at construction (EdgeNode.
    init_system() picks this class instead of ModbusManager based on
    config.json's modbus.simulate flag - see its own comment)."""

    def __init__(self, devices_cfg: list[dict]):
        self._sensors: dict[tuple[int, int], SimSensor] = {}
        for dev in devices_cfg:
            for s in dev.get("sensors", []):
                self._sensors[(dev["slave"], s["addr"])] = SimSensor(
                    sensor_type=s.get("type", "int"),
                    scale=s.get("scale", 1.0),
                    offset=s.get("offset", 0.0),
                    min_v=s.get("min"),
                    max_v=s.get("max"),
                )

    def connect(self) -> None:
        logger.info("Modbus SIMULATED - no serial port opened, synthetic readings only")

    def status(self) -> dict:
        """For the gateway's status file - no port is open in simulate mode."""
        return {"connected": True, "port": None, "baudrate": None, "simulated": True}

    def update_config(self, cfg: dict) -> None:
        """Called by config_watcher when the 'modbus' section's hash
        changes. Device/sensor changes themselves are handled separately
        by EdgeNode.reload_devices() - this manager holds no modbus.*
        connection settings to react to, so there is nothing to do here."""

    def read_holding_registers(self, address: int, count: int, device_id: int) -> _SimResult:
        sensor = self._sensors.get((device_id, address))
        if sensor is None:
            # Not a configured (slave, addr) pair - mirrors a real bus
            # response for an address nothing is listening on.
            return _SimResult(error=True)
        sensor.step()
        return _SimResult(registers=sensor.registers()[:count])

    def disconnect(self) -> None:
        pass
