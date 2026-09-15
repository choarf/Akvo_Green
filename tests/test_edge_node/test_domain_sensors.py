"""Unit tests for domain/sensors.py: the decoder registry and decode()."""

import struct

import pytest

from domain.sensors import DECODERS, decode


def regs_for(fmt, value):
    raw = struct.pack(fmt, value)
    return list(struct.unpack(">HH", raw))


# ---------------------------------------------------------------------------
# DECODERS registry shape
# ---------------------------------------------------------------------------

def test_all_expected_types_are_registered():
    assert set(DECODERS) == {"int", "uint16", "float", "uint32", "int32", "float32"}


@pytest.mark.parametrize("sensor_type", ["int", "uint16", "float"])
def test_single_register_types_need_one_register(sensor_type):
    assert DECODERS[sensor_type].register_count == 1


@pytest.mark.parametrize("sensor_type", ["uint32", "int32", "float32"])
def test_32bit_types_need_two_registers(sensor_type):
    assert DECODERS[sensor_type].register_count == 2


@pytest.mark.parametrize("sensor_type", ["int", "uint16", "uint32", "int32"])
def test_integer_types_are_not_scaled(sensor_type):
    assert DECODERS[sensor_type].scaled is False


@pytest.mark.parametrize("sensor_type", ["float", "float32"])
def test_float_types_are_scaled(sensor_type):
    assert DECODERS[sensor_type].scaled is True


# ---------------------------------------------------------------------------
# decode() - single-register types
# ---------------------------------------------------------------------------

def test_decode_int_is_raw():
    assert decode("int", [1234], scale=10, offset=5) == 1234  # scale/offset ignored


def test_decode_uint16_is_raw():
    assert decode("uint16", [65535]) == 65535


def test_decode_float_applies_scale_and_offset():
    assert decode("float", [250], scale=0.1, offset=5) == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# decode() - 32-bit types, round-tripped through struct pack/unpack
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 100000, 4294967295])
def test_decode_uint32_roundtrip(value):
    assert decode("uint32", regs_for(">I", value)) == value


@pytest.mark.parametrize("value", [-100000, -1, 0, 2147483647, -2147483648])
def test_decode_int32_roundtrip(value):
    assert decode("int32", regs_for(">i", value)) == value


@pytest.mark.parametrize("value", [3.14, -1.5, 0.0, 123456.75])
def test_decode_float32_roundtrip(value):
    got = decode("float32", regs_for(">f", value))
    assert got == pytest.approx(value, abs=1e-3)


def test_decode_float32_applies_scale_and_offset():
    got = decode("float32", regs_for(">f", 314.159), scale=0.01, offset=1.0)
    assert got == pytest.approx(4.14159, abs=1e-3)  # 314.159 * 0.01 + 1.0


# ---------------------------------------------------------------------------
# decode() - error paths
# ---------------------------------------------------------------------------

def test_decode_unknown_type_raises():
    with pytest.raises(ValueError, match="Unsupported sensor type 'bogus'"):
        decode("bogus", [1])


def test_decode_32bit_type_with_one_register_raises_not_truncates():
    with pytest.raises(ValueError, match="count >= 2"):
        decode("float32", [12345])
