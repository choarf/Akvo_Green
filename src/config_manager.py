#!/usr/bin/env python3
"""
AKVO Configuration Manager V2

Builds config.json from CSV files and exports CSV files from config.json.

Usage:
    python config_manager.py build
    python config_manager.py build --dry-run
    python config_manager.py export
"""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import logging
import sys
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List

# ------------------------------------------------------------------
# Project Paths
# ------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

CONFIG_JSON = BASE_DIR / "config.json"

DEVICES_CSV = BASE_DIR / "devices.csv"
MODBUS_CSV = BASE_DIR / "modbus.csv"
SYSTEM_CSV = BASE_DIR / "system.csv"
AWS_CSV = BASE_DIR / "aws.csv"

# Columns that MUST be present in devices.csv. "enabled" is optional -
# if the column is missing every device defaults to enabled.
DEVICE_COLUMNS = {
    "device_id",
    "slave",
    "sensor_name",
    "addr",
    "count",
    "scale",
    "offset",
    "unit",
    "type",
    "min",
    "max",
}

VALID_SENSOR_TYPES = {"int", "float", "uint16", "uint32", "int32", "float32"}

# 32-bit types are read from two consecutive holding registers by
# SensorNode.read() (edge_node_improved.py) - count must cover both.
MULTI_REGISTER_TYPES = {"uint32", "int32", "float32"}

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("ConfigManager")

# ------------------------------------------------------------------
# Base config skeleton
# ------------------------------------------------------------------

BASE_CONFIG: Dict[str, Any] = {
    "meta": {
        "version": "2.0",
        "generated_at": "",
        "hash": "",
    },
    "gateway": {},
    "modbus": {},
    "aws": {},
    "devices": [],
}


def _parse_bool(value: str | None, default: bool = True) -> bool:
    """Parse common truthy/falsy CSV string values."""
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class ConfigManager:
    """Builds config.json from CSVs, and can export CSVs back from it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.config: Dict[str, Any] = copy.deepcopy(BASE_CONFIG)
        self.last_hash: str | None = None

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        with self.lock:
            self.config = self.build_config()
            return self.config

    def reload(self) -> Dict[str, Any]:
        logger.info("Reloading configuration")
        return self.load()

    def has_changed(self) -> bool:
        """True if the source CSVs differ from the last time this was
        called. The first call establishes a baseline and returns False."""
        with self.lock:
            new_hash = self.calculate_hash()
            if self.last_hash is None:
                self.last_hash = new_hash
                return False
            if new_hash != self.last_hash:
                logger.info("Configuration changed.")
                self.last_hash = new_hash
                return True
            return False

    # ------------------------------------------------------------------
    # Hashing / generic CSV helpers
    # ------------------------------------------------------------------

    def calculate_hash(self) -> str:
        md5 = hashlib.md5(usedforsecurity=False)
        for file in (DEVICES_CSV, MODBUS_CSV, SYSTEM_CSV, AWS_CSV):
            if not file.exists():
                continue
            md5.update(file.read_bytes())
        return md5.hexdigest()

    def load_single_row_csv(self, filename: Path) -> Dict[str, str]:
        """Load a CSV that has a header row and exactly one data row,
        e.g. modbus.csv, system.csv, aws.csv. Returns the row with
        whitespace stripped from every value, preserving column order."""
        if not filename.exists():
            raise FileNotFoundError(filename)

        with open(filename, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader, None)
            if row is None:
                raise ValueError(f"{filename.name} contains no data rows.")
            return {k: (v.strip() if v is not None else v) for k, v in row.items()}

    def _write_single_row_csv(self, filename: Path, row: Dict[str, Any]) -> None:
        """Write a header + one data row, using whatever keys/order the
        dict has. This keeps export symmetric with load_single_row_csv,
        so unrecognized/extra columns round-trip instead of being dropped."""
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(list(row.keys()))
            writer.writerow(list(row.values()))

    # ------------------------------------------------------------------
    # Device Validation
    # ------------------------------------------------------------------

    def validate_devices(self, devices: List[Dict[str, Any]]) -> None:
        logger.info("Validating devices...")
        errors: List[str] = []
        slave_ids: set = set()

        for device in devices:
            dev_id = device["id"]
            slave = device["slave"]

            if not (1 <= slave <= 247):
                errors.append(f"{dev_id}: Invalid slave ID {slave}")

            if slave in slave_ids:
                logger.warning(f"{dev_id}: Duplicate slave ID {slave}")
            slave_ids.add(slave)

            used_registers: set = set()
            sensor_names: set = set()

            for sensor in device["sensors"]:
                name = sensor["name"]
                addr = sensor["addr"]
                count = sensor["count"]

                if name in sensor_names:
                    errors.append(f"{dev_id}: Duplicate sensor '{name}'")
                sensor_names.add(name)

                if not (0 <= addr <= 65535):
                    errors.append(f"{dev_id}.{name}: Invalid register {addr}")

                if count <= 0:
                    errors.append(f"{dev_id}.{name}: Count must be > 0")

                regs = set(range(addr, addr + count))
                overlap = used_registers.intersection(regs)
                if overlap:
                    errors.append(
                        f"{dev_id}.{name}: Register overlap {sorted(overlap)}"
                    )
                used_registers.update(regs)

                if sensor["type"] not in VALID_SENSOR_TYPES:
                    errors.append(
                        f"{dev_id}.{name}: Unsupported type '{sensor['type']}'"
                    )
                elif sensor["type"] in MULTI_REGISTER_TYPES and count < 2:
                    errors.append(
                        f"{dev_id}.{name}: type '{sensor['type']}' requires "
                        f"count >= 2 (got {count})"
                    )

        if errors:
            logger.error("Configuration validation failed")
            for e in errors:
                logger.error(e)
            raise ValueError("\n".join(errors))

        logger.info("Validation successful.")

    # ------------------------------------------------------------------
    # Load devices.csv
    # ------------------------------------------------------------------

    def load_devices(self) -> List[Dict[str, Any]]:
        logger.info("Loading devices.csv")

        if not DEVICES_CSV.exists():
            raise FileNotFoundError(DEVICES_CSV)

        devices = defaultdict(
            lambda: {"slave": None, "enabled": True, "sensors": []}
        )

        with open(DEVICES_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("devices.csv has no header.")

            missing = DEVICE_COLUMNS - set(reader.fieldnames)
            if missing:
                raise ValueError(f"Missing columns: {sorted(missing)}")

            has_enabled_column = "enabled" in reader.fieldnames

            for line, row in enumerate(reader, start=2):
                try:
                    dev_id = row["device_id"].strip()
                    if not dev_id:
                        raise ValueError("Empty device_id")

                    slave = int(row["slave"])
                    enabled = (
                        _parse_bool(row.get("enabled"))
                        if has_enabled_column
                        else True
                    )

                    sensor = {
                        "name": row["sensor_name"].strip(),
                        "addr": int(row["addr"]),
                        "count": int(row["count"]),
                        "scale": float(row["scale"] or 1),
                        "offset": float(row["offset"] or 0),
                        "unit": row["unit"],
                        "type": row["type"] or "int",
                        "min": float(row["min"]) if row["min"] else None,
                        "max": float(row["max"]) if row["max"] else None,
                    }

                    if devices[dev_id]["slave"] is None:
                        # First row seen for this device_id: sets slave + enabled.
                        devices[dev_id]["slave"] = slave
                        devices[dev_id]["enabled"] = enabled
                    elif devices[dev_id]["slave"] != slave:
                        raise ValueError("Inconsistent slave ID")

                    devices[dev_id]["sensors"].append(sensor)

                except Exception as ex:
                    raise ValueError(f"devices.csv line {line}: {ex}")

        skipped = [name for name, data in devices.items() if not data["enabled"]]
        if skipped:
            logger.info("Skipping disabled devices: %s", ", ".join(skipped))

        result = [
            {
                "id": name,
                "slave": data["slave"],
                "enabled": data["enabled"],
                "sensors": data["sensors"],
            }
            for name, data in devices.items()
            if data["enabled"]
        ]

        self.validate_devices(result)
        logger.info("Loaded %d devices", len(result))
        return result

    # ------------------------------------------------------------------
    # Build Configuration
    # ------------------------------------------------------------------

    def build_config(self, dry_run: bool = False) -> Dict[str, Any]:
        logger.info("Building configuration...")
        config = copy.deepcopy(BASE_CONFIG)

        config["devices"] = self.load_devices()

        m = self.load_single_row_csv(MODBUS_CSV)
        config["modbus"] = {
            "port": m["port"],
            "baudrate": int(m["baudrate"]),
            "timeout": float(m["timeout"]),
            "parity": m["parity"],
            "stopbits": int(m["stopbits"]),
            "bytesize": int(m["bytesize"]),
        }

        s = self.load_single_row_csv(SYSTEM_CSV)
        config["gateway"] = {
            "gateway_id": s["gateway_id"],
            "city": s["city"],
            "poll_interval": int(s["poll_interval"]),
            "system_interval": int(s["system_interval"]),
            "watchdog_timeout": int(s["watchdog_timeout"]),
        }

        # Loaded generically (not restricted to a fixed set of keys) so any
        # column present in aws.csv - now or in the future - survives the
        # build/export round trip.
        config["aws"] = self.load_single_row_csv(AWS_CSV)

        config["meta"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        config["meta"]["hash"] = self.calculate_hash()

        if dry_run:
            print(json.dumps(config, indent=4))
            return config

        self.save_json(config)
        logger.info("config.json generated.")
        self.config = config
        return config

    # ------------------------------------------------------------------
    # Atomic JSON Save / Load
    # ------------------------------------------------------------------

    def save_json(self, config: Dict[str, Any]) -> None:
        CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)

        with NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=CONFIG_JSON.parent,
            encoding="utf-8",
        ) as tmp:
            json.dump(config, tmp, indent=4)
            temp_name = tmp.name

        Path(temp_name).replace(CONFIG_JSON)

    def load_json(self) -> Dict[str, Any]:
        if not CONFIG_JSON.exists():
            raise FileNotFoundError(CONFIG_JSON)

        with open(CONFIG_JSON, encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Export CSVs from config.json
    # ------------------------------------------------------------------

    def export_devices(self, config: Dict[str, Any]) -> None:
        logger.info("Exporting devices.csv")

        with open(DEVICES_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "device_id",
                    "slave",
                    "sensor_name",
                    "addr",
                    "count",
                    "scale",
                    "offset",
                    "unit",
                    "type",
                    "min",
                    "max",
                    "enabled",
                ]
            )

            for device in config["devices"]:
                for sensor in device["sensors"]:
                    writer.writerow(
                        [
                            device["id"],
                            device["slave"],
                            sensor["name"],
                            sensor["addr"],
                            sensor["count"],
                            sensor.get("scale", 1),
                            sensor.get("offset", 0),
                            sensor.get("unit", ""),
                            sensor.get("type", "int"),
                            sensor.get("min", ""),
                            sensor.get("max", ""),
                            int(device.get("enabled", True)),
                        ]
                    )

    def export_modbus(self, config: Dict[str, Any]) -> None:
        logger.info("Exporting modbus.csv")
        self._write_single_row_csv(MODBUS_CSV, config["modbus"])

    def export_system(self, config: Dict[str, Any]) -> None:
        logger.info("Exporting system.csv")
        self._write_single_row_csv(SYSTEM_CSV, config["gateway"])

    def export_aws(self, config: Dict[str, Any]) -> None:
        logger.info("Exporting aws.csv")
        self._write_single_row_csv(AWS_CSV, config["aws"])

    def export_config(self) -> None:
        logger.info("Exporting configuration")
        config = self.load_json()
        self.export_devices(config)
        self.export_modbus(config)
        self.export_system(config)
        self.export_aws(config)
        logger.info("Export complete.")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> None:
    cm = ConfigManager()

    if len(sys.argv) < 2:
        print()
        print("Usage:")
        print()
        print(" build       Build config.json")
        print(" export      Export CSV")
        print(" build --dry-run")
        print()
        sys.exit(1)

    command = sys.argv[1].lower()

    try:
        if command == "build":
            cm.build_config(dry_run="--dry-run" in sys.argv)
        elif command == "export":
            cm.export_config()
        else:
            raise ValueError(f"Unknown command '{command}'")
    except Exception as ex:
        logger.exception(ex)
        sys.exit(1)


if __name__ == "__main__":
    main()
