import os
import pytest
from modbus_client import ModbusClient

PORT = os.environ.get("AKVO_TEST_PORT", "/tmp/akvo_modbus_master")
SLAVE = int(os.environ.get("AKVO_TEST_SLAVE", "1"))

def connected_client():
    client = ModbusClient()
    if not client.connect(PORT, 9600, "N", 1, 8, 1.0):
        pytest.skip(f"Mock slave unavailable: {client.get_last_error()}")
    return client


def test_fc03():
    c = connected_client()
    try:
        assert c.read_holding_registers(SLAVE, 0, 4) == [1234, 250, 1000, 65535]
    finally:
        c.disconnect()


def test_fc04():
    c = connected_client()
    try:
        assert c.read_input_registers(SLAVE, 0, 3) == [500, 750, 1250]
    finally:
        c.disconnect()


def test_fc01():
    c = connected_client()
    try:
        assert c.read_coils(SLAVE, 0, 4) == [True, False, True, False]
    finally:
        c.disconnect()


def test_fc02():
    c = connected_client()
    try:
        assert c.read_discrete_inputs(SLAVE, 0, 4) == [True, True, False, True]
    finally:
        c.disconnect()


def test_fc06_readback():
    c = connected_client()
    try:
        assert c.write_register(SLAVE, 30, 5432)
        assert c.read_holding_registers(SLAVE, 30, 1) == [5432]
    finally:
        c.disconnect()


def test_fc16_readback():
    c = connected_client()
    try:
        assert c.write_registers(SLAVE, 31, [101, 202, 303])
        assert c.read_holding_registers(SLAVE, 31, 3) == [101, 202, 303]
    finally:
        c.disconnect()


def test_fc05():
    c = connected_client()
    try:
        assert c.write_coil(SLAVE, 20, True)
        assert c.read_coils(SLAVE, 20, 1) == [True]
    finally:
        c.disconnect()


def test_fc15():
    c = connected_client()
    try:
        assert c.write_coils(SLAVE, 21, [True, False, True])
        assert c.read_coils(SLAVE, 21, 3) == [True, False, True]
    finally:
        c.disconnect()


def test_statistics():
    c = connected_client()
    try:
        c.reset_statistics(log=False)
        assert c.read_holding_registers(SLAVE, 0, 1) == [1234]
        stats = c.get_statistics()
        assert stats["requests"] == 1
        assert stats["successes"] == 1
        assert stats["errors"] == 0
        assert stats["success_rate"] == 100.0
        assert stats["last_response_ms"] >= 0
    finally:
        c.disconnect()


def test_wrong_slave_generates_error():
    c = connected_client()
    try:
        result = c.read_holding_registers(2 if SLAVE == 1 else 1, 0, 1)
        assert result == []
        assert c.get_statistics()["errors"] >= 1
        assert c.get_last_error()
    finally:
        c.disconnect()
