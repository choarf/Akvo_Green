"""Unit tests for config/schema.py's validate()."""

import copy

import pytest

from config.schema import validate

VALID_CONFIG = {
    "gateway": {
        "gateway_id": "GW1",
        "city": "America/Mexico_City",
        "poll_interval": 15,
        "system_interval": 30,
    },
    "modbus": {
        "port": "/dev/ttyUSB0",
        "baudrate": 9600,
        "timeout": 1.0,
        "parity": "N",
        "stopbits": 1,
        "bytesize": 8,
    },
    "aws": {
        "host": "example.iot.us-east-1.amazonaws.com",
        "client_id": "GW1",
        "ca": "./certs/ca.pem",
        "cert": "./certs/cert.pem",
        "key": "./certs/key.pem",
        "topic_pub": "AKVO/data",
        "topic_system": "AKVO/system",
    },
    "devices": [
        {
            "id": "DEV_1",
            "slave": 1,
            "enabled": True,
            "sensors": [
                {"name": "Temp", "addr": 0, "count": 1, "type": "float", "scale": 0.1, "min": -10, "max": 50},
            ],
        },
    ],
}


def test_valid_config_has_no_errors_or_warnings():
    errors, warnings = validate(copy.deepcopy(VALID_CONFIG))
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize("section", ["gateway", "modbus", "aws"])
def test_missing_section_is_an_error(section):
    config = copy.deepcopy(VALID_CONFIG)
    del config[section]
    errors, _ = validate(config)
    assert any(section in e for e in errors)


@pytest.mark.parametrize(
    "section,key",
    [
        ("gateway", "poll_interval"),
        ("modbus", "port"),
        ("aws", "client_id"),
    ],
)
def test_missing_required_key_is_an_error(section, key):
    config = copy.deepcopy(VALID_CONFIG)
    del config[section][key]
    errors, _ = validate(config)
    assert any(section in e and key in e for e in errors)


def test_devices_defaults_to_empty_list_without_error():
    config = copy.deepcopy(VALID_CONFIG)
    del config["devices"]
    errors, _ = validate(config)
    assert errors == []


def test_invalid_slave_id_is_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    config["devices"][0]["slave"] = 999
    errors, _ = validate(config)
    assert any("Invalid slave ID" in e for e in errors)


def test_duplicate_sensor_name_is_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    config["devices"][0]["sensors"].append(dict(config["devices"][0]["sensors"][0]))
    errors, _ = validate(config)
    assert any("Duplicate sensor" in e for e in errors)


def test_register_overlap_is_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    config["devices"][0]["sensors"][0]["count"] = 2
    config["devices"][0]["sensors"].append(
        {"name": "Hum", "addr": 1, "count": 1, "type": "int"}
    )
    errors, _ = validate(config)
    assert any("Register overlap" in e for e in errors)


def test_unsupported_type_is_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    config["devices"][0]["sensors"][0]["type"] = "bogus"
    errors, _ = validate(config)
    assert any("Unsupported type 'bogus'" in e for e in errors)


def test_32bit_type_with_undersized_count_is_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    config["devices"][0]["sensors"][0]["type"] = "float32"
    config["devices"][0]["sensors"][0]["count"] = 1
    errors, _ = validate(config)
    assert any("count >= 2" in e for e in errors)


def test_duplicate_slave_across_devices_is_a_warning_not_an_error():
    config = copy.deepcopy(VALID_CONFIG)
    second = copy.deepcopy(config["devices"][0])
    second["id"] = "DEV_2"
    second["sensors"][0]["name"] = "Temp2"
    config["devices"].append(second)  # same slave (1) as DEV_1

    errors, warnings = validate(config)
    assert errors == []
    assert any("Duplicate slave ID" in w for w in warnings)
