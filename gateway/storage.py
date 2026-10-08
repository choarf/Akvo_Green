"""
Local SQLite history store for sensor readings and system telemetry, plus a
one-way mirror of config.json's devices/sensors.

Written to inline from EdgeNode's existing publisher()/system_publisher()
threads (see edge_node_improved.py) rather than a dedicated thread+queue -
these writes are a handful of small rows at poll_interval/system_interval
cadence (typically 5-60s), microseconds to low-single-digit ms even on SD
card storage, negligible next to MQTT's own network round trip and already
absorbed by those threads' own time.sleep(interval). Callers are expected
to wrap every call in their own try/except (as publisher()/
system_publisher() do): an uncaught exception here would otherwise silently
kill whichever thread it's called from - and those threads are MQTT
publishing itself, far worse than the write that failed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "history.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    device_id TEXT NOT NULL,
    sensor_name TEXT NOT NULL,
    value REAL,
    status TEXT NOT NULL,
    alarm TEXT,
    sensor_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_dev_sensor_ts
    ON readings(device_id, sensor_name, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS system_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    gateway TEXT,
    city TEXT,
    cpu_load_percent REAL,
    ram_usage_percent REAL,
    disk_usage_percent REAL,
    ip_address TEXT,
    platform_type TEXT,
    os TEXT,
    -- cumulative bytes on all interfaces but loopback since boot (reset by a reboot)
    net_bytes_recv INTEGER,
    net_bytes_sent INTEGER
);
CREATE INDEX IF NOT EXISTS idx_system_ts ON system_telemetry(ts);

-- Mirror of config.json's devices/sensors, kept in sync one-way by
-- sync_config(): config.json is the source of truth, this is never
-- written back to it. Removed devices/sensors get active=0 rather than
-- being deleted, so their readings history stays queryable.
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    slave INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sensors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    name TEXT NOT NULL,
    addr INTEGER, count INTEGER, type TEXT,
    scale REAL, offset REAL, unit TEXT, min REAL, max REAL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(device_id, name)
);
CREATE TABLE IF NOT EXISTS config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    added TEXT NOT NULL,
    removed TEXT NOT NULL,
    updated TEXT NOT NULL
);
"""


class HistoryStore:
    """Records device readings and system telemetry to a local SQLite
    database and mirrors config.json's devices/sensors into it.

    One sqlite3 connection per calling thread (cached in thread-local
    storage), since sqlite3 connections can't be shared across threads by
    default (check_same_thread=True) and publisher()/system_publisher()/
    config_watcher() each write from their own thread. WAL mode lets
    writers and readers proceed without blocking each other; busy_timeout
    bounds how long a write can ever block under lock contention instead of
    hanging indefinitely.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        retention_days: int = 30,
        prune_interval_seconds: float = 3600,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self.prune_interval_seconds = prune_interval_seconds
        self._local = threading.local()
        self._last_pruned = 0.0

        # Create the schema once up front (not lazily per-connection) so a
        # bad db_path/permission problem surfaces immediately at
        # construction time, in EdgeNode.init_system()'s own try/except,
        # rather than silently on the first record_*() call.
        conn = self._connect()
        conn.executescript(_SCHEMA)
        # A history.db created before config sync existed has a readings
        # table without sensor_id, and CREATE TABLE IF NOT EXISTS won't add
        # it - migrate in place, keeping the existing rows.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(readings)")}
        if "sensor_id" not in cols:
            conn.execute("ALTER TABLE readings ADD COLUMN sensor_id INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts ON readings(sensor_id, ts)"
        )
        # Same for the network byte counters added to system telemetry later.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(system_telemetry)")}
        for col in ("net_bytes_recv", "net_bytes_sent"):
            if col not in cols:
                conn.execute(f"ALTER TABLE system_telemetry ADD COLUMN {col} INTEGER")
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=True)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    # Config mirror (config.json -> devices/sensors, one-way)
    # ------------------------------------------------------------------

    def sync_config(self, cfg: dict, now: datetime | None = None) -> dict[str, list]:
        """Makes the devices/sensors tables match cfg["devices"]. Only the
        devices section is mirrored - never aws/modbus/gateway, which hold
        cert paths and connection details that don't belong in a database.

        Returns {"added": [...], "removed": [...], "updated": [...]} - device
        ids, or "DEVICE.sensor" for a sensor changing on an already-known
        device; "updated" entries are {"id", "old", "new"} with only the
        changed fields. Returns {} when nothing differed (and writes no
        config_history row). Removed devices/sensors are marked active=0,
        not deleted. All-or-nothing: an error rolls the whole sync back
        before propagating."""
        devices_cfg = cfg.get("devices", [])
        ts = (now or datetime.now(UTC)).isoformat()
        conn = self._connect()

        added: list[str] = []
        removed: list[str] = []
        updated: list[dict[str, Any]] = []

        try:
            db_devices = {r["device_id"]: r for r in conn.execute("SELECT * FROM devices")}
            db_sensors = {
                (r["device_id"], r["name"]): r for r in conn.execute("SELECT * FROM sensors")
            }
            seen_devices: set[str] = set()
            seen_sensors: set[tuple[str, str]] = set()
            new_devices: set[str] = set()
            changed_devices: set[str] = set()

            for dev in devices_cfg:
                dev_id = dev["id"]
                seen_devices.add(dev_id)
                slave = dev["slave"]
                enabled = int(bool(dev.get("enabled", True)))
                row = db_devices.get(dev_id)

                if row is None:
                    conn.execute(
                        "INSERT INTO devices (device_id, slave, enabled, active, first_seen, "
                        "last_changed) VALUES (?, ?, ?, 1, ?, ?)",
                        (dev_id, slave, enabled, ts, ts),
                    )
                    added.append(dev_id)
                    new_devices.add(dev_id)
                elif not row["active"]:
                    conn.execute(
                        "UPDATE devices SET slave = ?, enabled = ?, active = 1, "
                        "last_changed = ? WHERE device_id = ?",
                        (slave, enabled, ts, dev_id),
                    )
                    added.append(dev_id)
                    new_devices.add(dev_id)
                elif row["slave"] != slave or row["enabled"] != enabled:
                    conn.execute(
                        "UPDATE devices SET slave = ?, enabled = ?, last_changed = ? "
                        "WHERE device_id = ?",
                        (slave, enabled, ts, dev_id),
                    )
                    updated.append({
                        "id": dev_id,
                        "old": {"slave": row["slave"], "enabled": row["enabled"]},
                        "new": {"slave": slave, "enabled": enabled},
                    })

                for s in dev.get("sensors", []):
                    key = (dev_id, s["name"])
                    seen_sensors.add(key)
                    new = {
                        "addr": s["addr"],
                        "count": s.get("count", 1),
                        "type": s.get("type", "int"),
                        "scale": s.get("scale", 1.0),
                        "offset": s.get("offset", 0.0),
                        "unit": s.get("unit", ""),
                        "min": s.get("min"),
                        "max": s.get("max"),
                    }
                    srow = db_sensors.get(key)

                    if srow is None:
                        conn.execute(
                            "INSERT INTO sensors (device_id, name, addr, count, type, scale, "
                            "offset, unit, min, max, active) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                            (dev_id, s["name"], *new.values()),
                        )
                        if dev_id not in new_devices:
                            added.append(f"{dev_id}.{s['name']}")
                            changed_devices.add(dev_id)
                        continue

                    old = {f: srow[f] for f in new}
                    if not srow["active"] or old != new:
                        conn.execute(
                            "UPDATE sensors SET addr = ?, count = ?, type = ?, scale = ?, "
                            "offset = ?, unit = ?, min = ?, max = ?, active = 1 WHERE id = ?",
                            (*new.values(), srow["id"]),
                        )
                        if dev_id not in new_devices:
                            changed_devices.add(dev_id)
                            if not srow["active"]:
                                added.append(f"{dev_id}.{s['name']}")
                            else:
                                diff = [f for f in new if old[f] != new[f]]
                                updated.append({
                                    "id": f"{dev_id}.{s['name']}",
                                    "old": {f: old[f] for f in diff},
                                    "new": {f: new[f] for f in diff},
                                })

            for dev_id, row in db_devices.items():
                if row["active"] and dev_id not in seen_devices:
                    conn.execute(
                        "UPDATE devices SET active = 0, last_changed = ? WHERE device_id = ?",
                        (ts, dev_id),
                    )
                    conn.execute("UPDATE sensors SET active = 0 WHERE device_id = ?", (dev_id,))
                    removed.append(dev_id)

            for (dev_id, name), srow in db_sensors.items():
                if srow["active"] and dev_id in seen_devices and (dev_id, name) not in seen_sensors:
                    conn.execute("UPDATE sensors SET active = 0 WHERE id = ?", (srow["id"],))
                    removed.append(f"{dev_id}.{name}")
                    changed_devices.add(dev_id)

            for dev_id in changed_devices:
                conn.execute(
                    "UPDATE devices SET last_changed = ? WHERE device_id = ?", (ts, dev_id)
                )

            if not (added or removed or updated):
                conn.rollback()
                return {}

            config_hash = hashlib.sha256(
                json.dumps(devices_cfg, sort_keys=True).encode()
            ).hexdigest()
            conn.execute(
                "INSERT INTO config_history (ts, config_hash, added, removed, updated) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, config_hash, json.dumps(added), json.dumps(removed), json.dumps(updated)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return {"added": added, "removed": removed, "updated": updated}

    def query_config_history(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        cur = conn.execute(
            "SELECT ts, config_hash, added, removed, updated FROM config_history "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        )
        return [
            {
                "ts": r["ts"],
                "config_hash": r["config_hash"],
                "added": json.loads(r["added"]),
                "removed": json.loads(r["removed"]),
                "updated": json.loads(r["updated"]),
            }
            for r in cur.fetchall()
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record_devices(self, payload: dict) -> None:
        """payload is EdgeNode.publisher()'s exact
        {"ts": ..., "devices": {device_id: {sensor_name: {"val":..,
        "status":..,"alarm":..}}}} shape - flattened to one row per
        (device_id, sensor_name). `value` is left NULL for BUS_ERROR/
        EXCEPTION entries, which carry no "val" key."""
        conn = self._connect()
        # NULL sensor_id (sensor not mirrored yet, e.g. a reading landing
        # between a config reload and its sync_config() call) is fine - the
        # device_id/sensor_name columns still identify the reading.
        sensor_ids = {
            (device_id, name): sensor_id
            for sensor_id, device_id, name in conn.execute(
                "SELECT id, device_id, name FROM sensors WHERE active = 1"
            )
        }

        ts = payload.get("ts")
        rows = [
            (
                ts, device_id, sensor_name, data.get("val"), data.get("status", ""),
                data.get("alarm"), sensor_ids.get((device_id, sensor_name)),
            )
            for device_id, sensors in payload.get("devices", {}).items()
            for sensor_name, data in sensors.items()
        ]
        if not rows:
            return

        conn.executemany(
            "INSERT INTO readings "
            "(ts, device_id, sensor_name, value, status, alarm, sensor_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    def record_system(self, status: dict) -> None:
        """status is get_system_status()'s exact return dict."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO system_telemetry "
            "(ts, gateway, city, cpu_load_percent, ram_usage_percent, "
            "disk_usage_percent, ip_address, platform_type, os, net_bytes_recv, net_bytes_sent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                status.get("ts"),
                status.get("gateway"),
                status.get("city"),
                status.get("cpu_load_percent"),
                status.get("ram_usage_percent"),
                status.get("disk_usage_percent"),
                status.get("ip_address"),
                status.get("platform_type"),
                status.get("os"),
                status.get("net_bytes_recv"),
                status.get("net_bytes_sent"),
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def query_history(
        self,
        device_id: str | None = None,
        sensor_name: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        if sensor_name is not None:
            clauses.append("sensor_name = ?")
            params.append(sensor_name)
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start)
        if end is not None:
            clauses.append("ts <= ?")
            params.append(end)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 5000)))

        conn = self._connect()
        cur = conn.execute(
            f"SELECT ts, device_id, sensor_name, value, status, alarm, sensor_id "
            f"FROM readings {where} ORDER BY ts DESC LIMIT ?",
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def query_system(
        self,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start)
        if end is not None:
            clauses.append("ts <= ?")
            params.append(end)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 5000)))

        conn = self._connect()
        cur = conn.execute(
            f"SELECT ts, gateway, city, cpu_load_percent, ram_usage_percent, "
            f"disk_usage_percent, ip_address, platform_type, os "
            f"FROM system_telemetry {where} ORDER BY ts DESC LIMIT ?",
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def prune(self, now: datetime | None = None) -> tuple[int, int]:
        """Deletes readings/telemetry older than retention_days. Returns
        (readings_deleted, system_deleted). Keeps a Pi's SD card from
        filling up unbounded. Never touches devices/sensors/config_history
        - those are tiny and are the record of what old readings meant."""
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        cutoff_iso = cutoff.isoformat()

        conn = self._connect()
        r = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff_iso,))
        s = conn.execute("DELETE FROM system_telemetry WHERE ts < ?", (cutoff_iso,))
        conn.commit()
        return r.rowcount, s.rowcount

    def maybe_prune(self, now: float | None = None) -> None:
        """No-ops unless prune_interval_seconds has elapsed since the last
        prune - called from system_publisher()'s existing cadence so
        retention doesn't need its own thread."""
        current = now if now is not None else time.time()
        if current - self._last_pruned < self.prune_interval_seconds:
            return
        self._last_pruned = current
        self.prune()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
