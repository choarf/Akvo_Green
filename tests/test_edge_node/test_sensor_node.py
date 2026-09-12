"""Unit tests for SensorNode (_evaluate_alarm, read) and _combine_32bit."""

import struct

import pytest

import edge_node_improved as en
from helpers import FakeModbusMgr, FakeModbusResult


def make_sensor(cfg, mgr=None):
    return en.SensorNode(cfg, slave=1, modbus_mgr=mgr or FakeModbusMgr())


def regs_for(fmt, value):
    raw = struct.pack(fmt, value)
    return list(struct.unpack(">HH", raw))


# ---------------------------------------------------------------------------
# _evaluate_alarm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "val,min_,max_,expected",
    [
        (5, 0, 10, "NORMAL"),
        (-1, 0, 10, "LOW"),
        (11, 0, 10, "HIGH"),
        (0, 0, 10, "NORMAL"),        # boundary: equal to min is NOT low
        (10, 0, 10, "NORMAL"),       # boundary: equal to max is NOT high
        (5, None, None, "NORMAL"),   # no bounds configured -> never alarms
        (-100, None, 10, "NORMAL"),  # only max set -> can't go LOW
        (1000, 0, None, "NORMAL"),   # only min set -> can't go HIGH
    ],
)
def test_evaluate_alarm_numeric(val, min_, max_, expected):
    sensor = make_sensor({"min": min_, "max": max_})
    assert sensor._evaluate_alarm(val) == expected


def test_evaluate_alarm_non_numeric_returns_none():
    sensor = make_sensor({"min": 0, "max": 10})
    assert sensor._evaluate_alarm("not a number") is None
    assert sensor._evaluate_alarm(None) is None


# ---------------------------------------------------------------------------
# read() - single-register types
# ---------------------------------------------------------------------------

def test_read_int_type_is_raw_no_scaling():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[1234]))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "int", "scale": 10, "offset": 5}, mgr)
    assert sensor.read() == {"val": 1234, "status": "OK", "alarm": "NORMAL"}


def test_read_uint16_type_is_raw_no_scaling():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[65535]))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "uint16"}, mgr)
    assert sensor.read()["val"] == 65535


def test_read_float_applies_scale_and_offset():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[250]))
    sensor = make_sensor(
        {"addr": 0, "count": 1, "type": "float", "scale": 0.1, "offset": 5, "min": 0, "max": 100},
        mgr,
    )
    result = sensor.read()
    assert result["val"] == pytest.approx(30.0)  # 250 * 0.1 + 5
    assert result["status"] == "OK"
    assert result["alarm"] == "NORMAL"


def test_read_float_out_of_range_is_high_alarm():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[600]))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "float", "scale": 0.1, "min": -10, "max": 50}, mgr)
    result = sensor.read()
    assert result["val"] == pytest.approx(60.0)
    assert result["alarm"] == "HIGH"


# ---------------------------------------------------------------------------
# read() - 32-bit types
# ---------------------------------------------------------------------------

def test_read_uint32():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=regs_for(">I", 4_000_000_000)))
    sensor = make_sensor({"addr": 0, "count": 2, "type": "uint32"}, mgr)
    assert sensor.read()["val"] == 4_000_000_000


def test_read_int32_negative():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=regs_for(">i", -500_000)))
    sensor = make_sensor({"addr": 0, "count": 2, "type": "int32"}, mgr)
    assert sensor.read()["val"] == -500_000


def test_read_float32_applies_scale():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=regs_for(">f", 314.159)))
    sensor = make_sensor(
        {"addr": 0, "count": 2, "type": "float32", "scale": 0.01, "min": 0, "max": 100}, mgr
    )
    result = sensor.read()
    assert result["val"] == pytest.approx(3.14159, abs=1e-3)
    assert result["status"] == "OK"


def test_read_32bit_type_with_only_one_register_is_exception_not_truncation():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[12345]))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "float32"}, mgr)
    result = sensor.read()
    assert result["status"] == "EXCEPTION"
    assert "count >= 2" in result["err"]


# ---------------------------------------------------------------------------
# read() - error paths
# ---------------------------------------------------------------------------

def test_read_bus_error_on_modbus_exception_response():
    mgr = FakeModbusMgr(result=FakeModbusResult(registers=[], error=True))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "int"}, mgr)
    assert sensor.read() == {"status": "BUS_ERROR"}


def test_read_exception_when_modbus_mgr_raises():
    mgr = FakeModbusMgr(exception=ConnectionError("Modbus client not yet connected"))
    sensor = make_sensor({"addr": 0, "count": 1, "type": "int"}, mgr)
    result = sensor.read()
    assert result["status"] == "EXCEPTION"
    assert "not yet connected" in result["err"]


# ---------------------------------------------------------------------------
# _combine_32bit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 100000, 4294967295])
def test_combine_32bit_unsigned_roundtrip(value):
    assert en._combine_32bit(regs_for(">I", value), signed=False) == value


@pytest.mark.parametrize("value", [-100000, -1, 0, 2147483647, -2147483648])
def test_combine_32bit_signed_roundtrip(value):
    assert en._combine_32bit(regs_for(">i", value), signed=True) == value


@pytest.mark.parametrize("value", [3.14, -1.5, 0.0, 123456.75])
def test_combine_32bit_float_roundtrip(value):
    got = en._combine_32bit(regs_for(">f", value), as_float=True)
    assert got == pytest.approx(value, abs=1e-3)
