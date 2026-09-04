import pytest
from modbus_client import ModbusClient


def test_defaults():
    client = ModbusClient()
    settings = client.get_settings()
    assert settings["baudrate"] == 9600
    assert settings["parity"] == "N"
    assert settings["timeout"] == 1.0
    assert client.is_connected() is False


@pytest.mark.parametrize("slave", [0, 248, -1, "1"])
def test_invalid_slave(slave):
    with pytest.raises(ValueError):
        ModbusClient._validate_slave(slave)


@pytest.mark.parametrize("address", [-1, 65536, "10"])
def test_invalid_address(address):
    with pytest.raises(ValueError):
        ModbusClient._validate_address(address)


@pytest.mark.parametrize("count", [0, -1, "1"])
def test_invalid_count(count):
    with pytest.raises(ValueError):
        ModbusClient._validate_count(count)


def test_invalid_register_values():
    with pytest.raises(ValueError):
        ModbusClient._validate_register_values([])

    with pytest.raises(ValueError):
        ModbusClient._validate_register_values([65536])

    with pytest.raises(ValueError):
        ModbusClient._validate_register_values([-1])


def test_invalid_connection_settings():
    with pytest.raises(ValueError):
        ModbusClient._validate_connection_settings("", 9600, "N", 1, 8, 1)

    with pytest.raises(ValueError):
        ModbusClient._validate_connection_settings("/dev/null", 0, "N", 1, 8, 1)

    with pytest.raises(ValueError):
        ModbusClient._validate_connection_settings("/dev/null", 9600, "X", 1, 8, 1)

    with pytest.raises(ValueError):
        ModbusClient._validate_connection_settings("/dev/null", 9600, "N", 3, 8, 1)

    with pytest.raises(ValueError):
        ModbusClient._validate_connection_settings("/dev/null", 9600, "N", 1, 8, 0)


def test_statistics_start_at_zero():
    stats = ModbusClient().get_statistics()
    assert stats["requests"] == 0
    assert stats["successes"] == 0
    assert stats["errors"] == 0
    assert stats["success_rate"] == 0.0


def test_statistics_reset():
    client = ModbusClient()
    client.stats["requests"] = 10
    client.stats["successes"] = 8
    client.stats["errors"] = 2

    client.reset_statistics(log=False)
    stats = client.get_statistics()

    assert stats["requests"] == 0
    assert stats["successes"] == 0
    assert stats["errors"] == 0
    assert stats["success_rate"] == 0.0


def test_settings_are_copies():
    client = ModbusClient()
    settings = client.get_settings()
    settings["port"] = "changed"
    assert client.get_settings()["port"] != "changed"


def test_connection_info_contains_settings():
    info = ModbusClient().get_connection_info()
    assert "connected" in info
    assert "port" in info
    assert "baudrate" in info
    assert "timeout" in info


def test_disconnected_read_returns_empty_list():
    client = ModbusClient()
    result = client.read_holding_registers(1, 0, 1)
    assert result == []
    assert "not connected" in client.get_last_error().lower()


def test_disconnected_write_returns_false():
    client = ModbusClient()
    result = client.write_register(1, 0, 1)
    assert result is False
    assert "not connected" in client.get_last_error().lower()
