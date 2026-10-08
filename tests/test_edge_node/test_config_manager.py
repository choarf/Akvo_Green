"""Unit tests for config_manager.py's CSV -> config.json build and export,
focused on the optional database_* columns in system.csv. Every file path
config_manager.py reads/writes is redirected to tmp_path, so the real
config_data/ is never touched."""

import csv
import json

import pytest

import config_manager as cm

SYSTEM_BASE = {
    "gateway_id": "GW1",
    "city": "America/Mexico_City",
    "poll_interval": "20",
    "system_interval": "60",
    "watchdog_timeout": "120",
}


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name, filename in (
        ("DEVICES_CSV", "devices.csv"),
        ("MODBUS_CSV", "modbus.csv"),
        ("SYSTEM_CSV", "system.csv"),
        ("AWS_CSV", "aws.csv"),
        ("CONFIG_JSON", "config.json"),
    ):
        monkeypatch.setattr(cm, name, tmp_path / filename)

    write_csv(
        tmp_path / "devices.csv",
        ["device_id", "slave", "sensor_name", "addr", "count", "scale", "offset",
         "unit", "type", "min", "max", "enabled"],
        [["DEV_1", "1", "Temp", "0", "1", "1", "0", "C", "float", "0", "50", "1"]],
    )
    write_csv(
        tmp_path / "modbus.csv",
        ["port", "baudrate", "timeout", "parity", "stopbits", "bytesize"],
        [["/dev/ttyUSB0", "9600", "1.0", "N", "1", "8"]],
    )
    write_csv(
        tmp_path / "aws.csv",
        ["host", "client_id", "ca", "cert", "key", "topic_pub", "topic_system"],
        [["h.example.com", "GW1", "./ca.pem", "./c.pem", "./k.pem", "t/data", "t/sys"]],
    )
    return tmp_path


def write_system(workspace, **extra):
    row = {**SYSTEM_BASE, **extra}
    write_csv(workspace / "system.csv", list(row), [list(row.values())])


MODBUS_BASE = {
    "port": "/dev/ttyUSB0",
    "baudrate": "9600",
    "timeout": "1.0",
    "parity": "N",
    "stopbits": "1",
    "bytesize": "8",
}


def write_modbus(workspace, **extra):
    row = {**MODBUS_BASE, **extra}
    write_csv(workspace / "modbus.csv", list(row), [list(row.values())])


def build():
    return cm.ConfigManager().build_config()


# ---------------------------------------------------------------------------
# build: system.csv -> config["database"]
# ---------------------------------------------------------------------------

def test_build_maps_database_columns_into_their_own_section(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="30")
    config = build()

    assert config["database"] == {"enabled": True, "retention_days": 30}
    # not folded into "gateway" - storage reads its own section
    assert "database_enabled" not in config["gateway"]
    assert json.loads((workspace / "config.json").read_text())["database"]["enabled"] is True


def test_build_database_disabled(workspace):
    write_system(workspace, database_enabled="0", database_retention_days="7")
    assert build()["database"] == {"enabled": False, "retention_days": 7}


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "1"])
def test_build_accepts_the_usual_truthy_spellings(workspace, value):
    write_system(workspace, database_enabled=value, database_retention_days="30")
    assert build()["database"]["enabled"] is True


def test_build_without_database_columns_leaves_it_off_and_still_works(workspace):
    write_system(workspace)  # an older system.csv, before these columns existed
    config = build()
    assert "database" not in config
    assert config["gateway"]["gateway_id"] == "GW1"


def test_build_blank_retention_omits_it_so_the_runtime_default_applies(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="")
    assert build()["database"] == {"enabled": True}


def test_build_rejects_non_integer_retention_with_a_clear_message(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="thirty")
    with pytest.raises(ValueError, match=r"database_retention_days must be an integer.*'thirty'"):
        build()


def test_build_rejects_zero_retention_via_schema_validation(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="0")
    with pytest.raises(ValueError, match="retention_days"):
        build()


def test_build_no_longer_carries_over_a_hand_added_section_from_old_config_json(workspace):
    # system.csv is now the source of truth for "database"
    (workspace / "config.json").write_text(json.dumps({"database": {"enabled": True}}))
    write_system(workspace)
    assert "database" not in build()


# ---------------------------------------------------------------------------
# export: config.json -> system.csv
# ---------------------------------------------------------------------------

def read_system_row(workspace):
    with open(workspace / "system.csv", newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))


def test_export_writes_database_columns_back_to_system_csv(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="45")
    build()
    write_system(workspace)  # wipe the columns, then restore them from config.json

    cm.ConfigManager().export_config()

    row = read_system_row(workspace)
    assert row["database_enabled"] == "1"
    assert row["database_retention_days"] == "45"
    assert row["gateway_id"] == "GW1"


def test_export_without_database_section_adds_no_columns(workspace):
    write_system(workspace)
    build()
    cm.ConfigManager().export_config()
    row = read_system_row(workspace)
    assert "database_enabled" not in row and "database_retention_days" not in row


def test_build_export_build_roundtrip_is_stable(workspace):
    write_system(workspace, database_enabled="1", database_retention_days="30")
    first = build()
    cm.ConfigManager().export_config()
    second = build()

    for cfg in (first, second):
        cfg.pop("meta")
    assert first == second


# ---------------------------------------------------------------------------
# build/export: modbus.csv's optional simulate_enabled -> config["modbus"]["simulate"]
# ---------------------------------------------------------------------------

def test_build_maps_simulate_enabled_into_modbus_section(workspace):
    write_system(workspace)
    write_modbus(workspace, simulate_enabled="1")

    config = build()

    assert config["modbus"]["simulate"] is True
    assert config["modbus"]["port"] == "/dev/ttyUSB0"  # the rest of the section is untouched


def test_build_without_simulate_enabled_column_leaves_modbus_unchanged(workspace):
    write_system(workspace)
    write_modbus(workspace)  # an older modbus.csv, before this column existed

    config = build()

    assert "simulate" not in config["modbus"]


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("0", False), ("", False)])
def test_build_simulate_enabled_accepts_the_usual_spellings(workspace, value, expected):
    write_system(workspace)
    write_modbus(workspace, simulate_enabled=value)
    assert build()["modbus"]["simulate"] is expected


def test_export_writes_simulate_enabled_back_to_modbus_csv(workspace):
    write_system(workspace)
    write_modbus(workspace, simulate_enabled="1")
    build()
    write_modbus(workspace)  # wipe the column, then restore it from config.json

    cm.ConfigManager().export_config()

    with open(workspace / "modbus.csv", newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["simulate_enabled"] == "1"
    assert row["port"] == "/dev/ttyUSB0"


def test_export_without_simulate_section_adds_no_column(workspace):
    write_system(workspace)
    write_modbus(workspace)
    build()
    cm.ConfigManager().export_config()
    with open(workspace / "modbus.csv", newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert "simulate_enabled" not in row


# ---------------------------------------------------------------------------
# --config-dir: build another site's config directory
# ---------------------------------------------------------------------------

def test_config_dir_option_builds_that_directory_only(workspace, tmp_path, monkeypatch):
    write_system(workspace)
    for name in ("CONFIG_DIR", "CONFIG_JSON", "DEVICES_CSV", "MODBUS_CSV", "SYSTEM_CSV", "AWS_CSV"):
        monkeypatch.setattr(cm, name, getattr(cm, name))  # restore after the test
    monkeypatch.setattr(cm.sys, "argv", ["config_manager.py", "build", "--config-dir", str(workspace)])

    cm.main()

    assert cm.CONFIG_JSON == workspace.resolve() / "config.json"
    config = json.loads((workspace / "config.json").read_text())
    assert config["gateway"]["gateway_id"] == "GW1"
    assert config["aws"]["topic_pub"] == "t/data"


def test_config_dir_option_rejects_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(cm.sys, "argv", ["config_manager.py", "build", "--config-dir", str(tmp_path / "nope")])
    with pytest.raises(SystemExit):
        cm.main()


# ---------------------------------------------------------------------------
# build/export: system.csv's optional web_* columns -> config["web"]
# ---------------------------------------------------------------------------

def test_build_maps_web_columns_into_their_own_section(workspace):
    write_system(workspace, database_enabled="1", web_enabled="1", web_port="8080")
    config = build()
    assert config["web"] == {"enabled": True, "port": 8080}
    assert "web_enabled" not in config["gateway"]


def test_build_without_web_columns_leaves_it_off(workspace):
    write_system(workspace)
    assert "web" not in build()


def test_build_blank_web_port_omits_it_so_the_default_applies(workspace):
    write_system(workspace, web_enabled="1", web_port="")
    assert build()["web"] == {"enabled": True}


def test_build_rejects_non_integer_web_port(workspace):
    write_system(workspace, web_enabled="1", web_port="eighty")
    with pytest.raises(ValueError, match="web_port"):
        build()


def test_export_writes_web_columns_back_to_system_csv(workspace):
    write_system(workspace, web_enabled="1", web_port="8081")
    build()
    write_system(workspace)
    cm.ConfigManager().export_config()
    row = read_system_row(workspace)
    assert row["web_enabled"] == "1" and row["web_port"] == "8081"
