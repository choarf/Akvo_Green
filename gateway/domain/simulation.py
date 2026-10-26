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

SimSensor behaves like a slow thermal process: a setpoint that drifts over
hours inside the middle of the sensor's min/max, followed through a
first-order lag. Alarm excursions are rare and slow - the manager starts
about ALARMS_PER_DAY of them per day across the whole site, each one
ramping a single sensor's setpoint past its min or max, holding it there
and ramping back, so a reading takes tens of minutes to go into alarm and
as long to come out of it, with one alarm episode per excursion (no
chatter at the limit). Everything runs on elapsed time, not on the number
of reads, so the poll interval doesn't change how the demo looks.

It encodes directly to/from physical values via domain.sensors' own
decode(), instead of writing into a simulated holding-register array
(tests/edge_node_mock/mock_devices_slave.py's model only ever fills one
word of a multi-register sensor), so every sensor type decode_sensor()
supports, including the 2-register uint32/int32/float32 types,
round-trips correctly.
"""

from __future__ import annotations

import logging
import math
import random
import struct
import threading
import time

logger = logging.getLogger("EdgeNode")

# Site-wide alarm excursions per day (Poisson arrivals, one sensor each).
ALARMS_PER_DAY = 10
# First-order lag of the reading behind its setpoint, like a thermal mass.
LAG_TAU_S = 10 * 60
# Normal setpoint drift: mean-reverting, wanders over hours, stays inside
# the middle NORMAL_BAND of the range (so normal running never alarms).
DRIFT_TAU_S = 2 * 3600
NORMAL_BAND = 0.5
# An excursion ramps the setpoint to EXCURSION_DEPTH * span past the limit
# over RAMP_UP_S, holds it there for HOLD_S, then ramps it back over
# RAMP_DOWN_S (cooling down slower than heating up, like a thermal mass).
EXCURSION_DEPTH = 0.08
RAMP_UP_S = 25 * 60
RAMP_DOWN_S = 50 * 60
HOLD_S = (15 * 60, 45 * 60)
# Measurement noise, small enough that a crossing never flickers.
NOISE = 0.0005
# Longest gap one step() integrates over (a stalled poll loop resumes
# smoothly instead of jumping).
MAX_DT_S = 300.0


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
        rng: random.Random | None = None,
    ):
        self.type = sensor_type
        self.scale = scale or 1.0
        self.offset = offset or 0.0
        self.min_v = min_v
        self.max_v = max_v
        self.rng = rng or random.Random()

        self.lo = min_v if min_v is not None else 0.0
        self.hi = max_v if max_v is not None else self.lo + 100.0
        self.span = max(self.hi - self.lo, 1e-9)
        # Normal running is centred on the part of the range the register
        # can encode (a one-register float at -1500..1500 can't go below 0).
        floor = self._encodable_min()
        lo_ok = self.lo if floor is None or floor < self.lo or floor >= self.hi else floor
        self.band = max(self.hi - lo_ok, 1e-9)
        self.mid = (lo_ok + self.hi) / 2.0

        self.drift = self.mid          # normal setpoint
        self.process = self.mid        # the lagged process value
        self.value = self.mid          # what the sensor reports (process + noise)
        self._last_t: float | None = None
        # Active excursion: (start, end of hold, peak setpoint), or None.
        self._excursion: tuple[float, float, float] | None = None

    # -- excursions -------------------------------------------------------

    def in_excursion(self, now: float) -> bool:
        return self._excursion is not None and now < self._excursion[1] + RAMP_DOWN_S

    def can_go_low(self) -> bool:
        """A LOW excursion must be encodable (an unsigned register at
        min=0 can't report a value below 0, so it only ever goes HIGH)."""
        floor = self._encodable_min()
        return floor is None or self.lo - EXCURSION_DEPTH * self.span > floor

    def start_excursion(self, now: float, high: bool, hold_s: float) -> None:
        peak = (self.hi + EXCURSION_DEPTH * self.span if high
                else self.lo - EXCURSION_DEPTH * self.span)
        self._excursion = (now, now + RAMP_UP_S + hold_s, peak)

    def setpoint(self, now: float) -> float:
        """Normal drift, blended towards the excursion peak with a smooth
        (cosine) ramp up, a hold, and a ramp back down."""
        if self._excursion is None:
            return self.drift
        start, hold_end, peak = self._excursion
        if now < start + RAMP_UP_S:
            w = (now - start) / RAMP_UP_S
        elif now < hold_end:
            w = 1.0
        elif now < hold_end + RAMP_DOWN_S:
            w = 1.0 - (now - hold_end) / RAMP_DOWN_S
        else:
            self._excursion = None
            return self.drift
        w = 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, w)))
        return self.drift + (peak - self.drift) * w

    # -- dynamics ---------------------------------------------------------

    def step(self, now: float | None = None) -> float:
        """Advance the simulated value to time `now` (seconds, monotonic)
        and return the reading."""
        now = time.monotonic() if now is None else now
        dt = 0.0 if self._last_t is None else max(0.0, min(MAX_DT_S, now - self._last_t))
        self._last_t = now

        if dt > 0:
            # Ornstein-Uhlenbeck drift around the middle, clamped to the band.
            a = math.exp(-dt / DRIFT_TAU_S)
            sigma = 0.15 * self.band * math.sqrt(1 - a * a)
            self.drift = self.mid + (self.drift - self.mid) * a + self.rng.gauss(0, sigma)
            half = NORMAL_BAND * self.band / 2
            self.drift = max(self.mid - half, min(self.mid + half, self.drift))
            # Thermal lag towards the setpoint.
            target = self.setpoint(now)
            self.process = target + (self.process - target) * math.exp(-dt / LAG_TAU_S)

        reading = self.process + self.rng.gauss(0, NOISE * self.span)
        # Unsigned register types can't hold a value below this (a float
        # sensor at min=0 would otherwise read a clamped 0) - a real
        # sensor can't report it either.
        floor = self._encodable_min()
        if floor is not None:
            reading = max(floor, reading)
        self.value = reading
        return reading

    def _encodable_min(self) -> float | None:
        """Lowest physical value registers() can encode, None if signed."""
        if self.type in ("int", "uint16", "uint32"):
            return 0.0
        if self.type == "float":
            return self.offset if self.scale > 0 else None
        return None

    def registers(self) -> list[int]:
        """Encode the current physical value into the raw holding-register
        word(s) that domain.sensors.decode(self.type, ..., self.scale,
        self.offset) would decode back into (approximately, after rounding)
        that same value - the inverse of each decoder in domain/sensors.py."""
        value = self.value
        if self.type in ("int", "uint16"):
            return [max(0, min(0xFFFF, round(value)))]

        if self.type == "float":
            raw = round((value - self.offset) / self.scale)
            return [max(0, min(0xFFFF, raw))]

        if self.type == "uint32":
            raw = max(0, min(0xFFFFFFFF, round(value)))
            packed = struct.pack(">I", raw)
        elif self.type == "int32":
            raw = max(-(2**31), min(2**31 - 1, round(value)))
            packed = struct.pack(">i", raw)
        elif self.type == "float32":
            packed = struct.pack(">f", (value - self.offset) / self.scale)
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

    def __init__(
        self,
        devices_cfg: list[dict],
        alarms_per_day: float = ALARMS_PER_DAY,
        clock=time.monotonic,
        rng: random.Random | None = None,
    ):
        self.connected = threading.Event()
        self.clock = clock
        self.rng = rng or random.Random()
        self.alarms_per_day = alarms_per_day
        self._lock = threading.Lock()
        self._sensors: dict[tuple[int, int], SimSensor] = {}
        for dev in devices_cfg:
            for s in dev.get("sensors", []):
                self._sensors[(dev["slave"], s["addr"])] = SimSensor(
                    sensor_type=s.get("type", "int"),
                    scale=s.get("scale", 1.0),
                    offset=s.get("offset", 0.0),
                    min_v=s.get("min"),
                    max_v=s.get("max"),
                    rng=self.rng,
                )
        self._next_alarm = self._draw_next(self.clock())

    def _draw_next(self, now: float) -> float:
        if self.alarms_per_day <= 0:
            return math.inf
        return now + self.rng.expovariate(self.alarms_per_day / 86400.0)

    def _schedule(self, now: float) -> None:
        """Start every excursion whose Poisson arrival time has passed, on
        a random sensor that isn't already in one."""
        while now >= self._next_alarm:
            free = [s for s in self._sensors.values() if not s.in_excursion(self._next_alarm)]
            if free:
                s = self.rng.choice(free)
                high = not s.can_go_low() or self.rng.random() < 0.5
                s.start_excursion(self._next_alarm, high, self.rng.uniform(*HOLD_S))
            self._next_alarm = self._draw_next(self._next_alarm)

    def connect(self) -> None:
        logger.info("Modbus SIMULATED - no serial port opened, synthetic readings only")
        self.connected.set()

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
        with self._lock:
            now = self.clock()
            self._schedule(now)
            sensor.step(now)
            regs = sensor.registers()[:count]
        return _SimResult(registers=regs)

    def disconnect(self) -> None:
        pass
