"""
Sensor register decoders, keyed by config.json's sensor `type`.

Previously this logic was an if/else chain inline in
edge_node_improved.py::SensorNode.read(). Adding a new sensor type now
means adding one decoder class here and registering it in DECODERS -
not editing a branch buried in the polling code.

Each decoder knows two things about its type: how many holding registers
it needs (`register_count`), and whether scale/offset apply to it
(`scaled`) - `int`/`uint16`/`uint32`/`int32` are read as-is; `float` and
`float32` are physical measurements that get scaled.
"""

from __future__ import annotations

import struct


class SensorDecoder:
    """Base class. Subclasses set register_count/scaled and implement
    decode() to turn raw registers into a number (before scale/offset)."""

    register_count = 1
    scaled = False

    def decode(self, registers: list[int]) -> int | float:
        raise NotImplementedError


class RawRegisterDecoder(SensorDecoder):
    """int / uint16 - registers[0] as-is."""

    register_count = 1
    scaled = False

    def decode(self, registers: list[int]) -> int:
        return registers[0]


class FloatDecoder(SensorDecoder):
    """float - registers[0], scale/offset applied by decode()."""

    register_count = 1
    scaled = True

    def decode(self, registers: list[int]) -> int:
        return registers[0]


def _combine_32bit(registers: list[int], signed: bool = False, as_float: bool = False):
    """Combine two 16-bit registers into one 32-bit value.

    High-register-first ("big-endian word order"), matching pymodbus's own
    default (Endian.BIG for both byte order and word order). Devices that
    use the opposite word order will need this flipped - there's no way to
    detect that automatically from the register values alone.
    """
    raw = struct.pack(">HH", registers[0], registers[1])
    if as_float:
        return struct.unpack(">f", raw)[0]
    return struct.unpack(">i" if signed else ">I", raw)[0]


class Uint32Decoder(SensorDecoder):
    register_count = 2
    scaled = False

    def decode(self, registers: list[int]) -> int:
        return _combine_32bit(registers, signed=False)


class Int32Decoder(SensorDecoder):
    register_count = 2
    scaled = False

    def decode(self, registers: list[int]) -> int:
        return _combine_32bit(registers, signed=True)


class Float32Decoder(SensorDecoder):
    register_count = 2
    scaled = True

    def decode(self, registers: list[int]) -> float:
        return _combine_32bit(registers, as_float=True)


# Single source of truth for "what sensor types exist": config/schema.py
# derives its VALID_SENSOR_TYPES/MULTI_REGISTER_TYPES from this registry,
# rather than keeping a separate hardcoded list that could drift out of
# sync with what can actually be decoded.
DECODERS: dict[str, SensorDecoder] = {
    "int": RawRegisterDecoder(),
    "uint16": RawRegisterDecoder(),
    "float": FloatDecoder(),
    "uint32": Uint32Decoder(),
    "int32": Int32Decoder(),
    "float32": Float32Decoder(),
}


def decode(sensor_type: str, registers: list[int], scale: float = 1.0, offset: float = 0.0):
    """Decode raw holding registers into a sensor value for `sensor_type`.

    Raises ValueError for an unknown type, or if fewer registers were
    supplied than that type's register_count requires -
    config_manager.py's build validation rejects the latter case at build
    time; this is the runtime guard for a hand-edited config.json that
    slips through anyway (fails loudly instead of silently truncating).
    """
    decoder = DECODERS.get(sensor_type)
    if decoder is None:
        raise ValueError(f"Unsupported sensor type '{sensor_type}'")

    if len(registers) < decoder.register_count:
        raise ValueError(
            f"type '{sensor_type}' needs count >= {decoder.register_count}, "
            f"got {len(registers)}"
        )

    val = decoder.decode(registers)
    if decoder.scaled:
        val = val * scale + offset
    return val
