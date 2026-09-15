"""
Schema and validation for config.json (and the CSVs config_manager.py
builds it from) - the single source of truth both config_manager.py
(build-time) and edge_node_improved.py's ConfigManager (runtime) validate
against. Before this, a hand-edited config.json that skipped
config_manager.py entirely had zero validation: a bad value only
surfaced later as a scattered exception in whichever thread hit the
missing/malformed field first.

Also provides typed dataclasses (SensorConfig/DeviceConfig) for callers
that want a structured view of a device/sensor instead of a raw dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from domain.sensors import DECODERS

# Derived from the decoder registry, not hardcoded separately - "valid
# sensor type" means "domain/sensors.py has a decoder for it".
VALID_SENSOR_TYPES = set(DECODERS)
MULTI_REGISTER_TYPES = {t for t, d in DECODERS.items() if d.register_count >= 2}

REQUIRED_GATEWAY_KEYS = {"gateway_id", "city", "poll_interval", "system_interval"}
REQUIRED_MODBUS_KEYS = {"port", "baudrate", "timeout", "parity", "stopbits", "bytesize"}
REQUIRED_AWS_KEYS = {"host", "client_id", "ca", "cert", "key", "topic_pub", "topic_system"}


@dataclass
class SensorConfig:
    name: str
    addr: int
    count: int = 1
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    type: str = "int"
    min: float | None = None
    max: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "SensorConfig":
        return cls(
            name=data["name"],
            addr=data["addr"],
            count=data.get("count", 1),
            scale=data.get("scale", 1.0),
            offset=data.get("offset", 0.0),
            unit=data.get("unit", ""),
            type=data.get("type", "int"),
            min=data.get("min"),
            max=data.get("max"),
        )


@dataclass
class DeviceConfig:
    id: str
    slave: int
    enabled: bool = True
    sensors: list[SensorConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfig":
        return cls(
            id=data["id"],
            slave=data["slave"],
            enabled=data.get("enabled", True),
            sensors=[SensorConfig.from_dict(s) for s in data.get("sensors", [])],
        )


def validate(config: dict) -> tuple[list[str], list[str]]:
    """Validate a full config.json-shaped dict.

    Returns (errors, warnings). An empty `errors` list means the config is
    valid enough to run; `warnings` are informational (e.g. two devices
    sharing one slave ID, which is a normal way to model two sensors on
    one physical unit, not a mistake).
    """
    errors: list[str] = []
    warnings: list[str] = []

    errors += _validate_section(config, "gateway", REQUIRED_GATEWAY_KEYS)
    errors += _validate_section(config, "modbus", REQUIRED_MODBUS_KEYS)
    errors += _validate_section(config, "aws", REQUIRED_AWS_KEYS)

    device_errors, device_warnings = _validate_devices(config.get("devices", []))
    errors += device_errors
    warnings += device_warnings

    return errors, warnings


def _validate_section(config: dict, section: str, required_keys: set[str]) -> list[str]:
    data = config.get(section)
    if not isinstance(data, dict):
        return [f"'{section}' section is missing or not an object"]

    missing = required_keys - data.keys()
    if missing:
        return [f"'{section}' section is missing required key(s): {sorted(missing)}"]

    return []


def _validate_devices(devices: list) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    slave_ids: set = set()

    for device in devices:
        dev_id = device.get("id", "<unknown>")
        slave = device.get("slave")

        if not isinstance(slave, int) or not (1 <= slave <= 247):
            errors.append(f"{dev_id}: Invalid slave ID {slave!r}")

        if slave in slave_ids:
            # Two device entries sharing one slave ID is the normal way to
            # model two sensors on one physical unit - informational only.
            warnings.append(f"{dev_id}: Duplicate slave ID {slave!r}")
        slave_ids.add(slave)

        used_registers: set = set()
        sensor_names: set = set()

        for sensor in device.get("sensors", []):
            name = sensor.get("name")
            addr = sensor.get("addr")
            count = sensor.get("count", 1)
            sensor_type = sensor.get("type", "int")

            if name in sensor_names:
                errors.append(f"{dev_id}: Duplicate sensor '{name}'")
            sensor_names.add(name)

            valid_addr = isinstance(addr, int) and 0 <= addr <= 65535
            if not valid_addr:
                errors.append(f"{dev_id}.{name}: Invalid register {addr!r}")

            if not isinstance(count, int) or count <= 0:
                errors.append(f"{dev_id}.{name}: Count must be > 0")
            elif valid_addr:
                regs = set(range(addr, addr + count))
                overlap = used_registers.intersection(regs)
                if overlap:
                    errors.append(f"{dev_id}.{name}: Register overlap {sorted(overlap)}")
                used_registers.update(regs)

            if sensor_type not in VALID_SENSOR_TYPES:
                errors.append(f"{dev_id}.{name}: Unsupported type '{sensor_type}'")
            elif sensor_type in MULTI_REGISTER_TYPES and isinstance(count, int) and count < 2:
                errors.append(
                    f"{dev_id}.{name}: type '{sensor_type}' requires count >= 2 (got {count})"
                )

    return errors, warnings
