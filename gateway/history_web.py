"""
Local web dashboard over the gateway's SQLite history (data/history.db).

Runs as its own process/systemd service (akvo-history-web), separate from
edge_node_improved.py on purpose: it only ever opens the database read-only,
so a slow query or a crash here can never stall Modbus polling or MQTT
publishing. Standard library only (http.server + sqlite3) - nothing to
install on the Pi, and the page (gateway/web/) draws its own charts, so it
works with no internet at all, which is the point of a local view.

Enabled by system.csv's web_enabled / web_port (config.json "web" section).
Read once at startup like database.*: toggling it needs a service restart.

  python3 history_web.py                       # uses config_data/config.json
  python3 history_web.py --force --db /tmp/history.db --port 8081   # local testing

No authentication: anyone on the Pi's network can view (not change) the data.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, UTC
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
DEFAULT_CONFIG = REPO_ROOT / "config_data" / "config.json"
DEFAULT_DB = REPO_ROOT / "data" / "history.db"
STATIC_DIR = BASE_DIR / "web"
DEFAULT_PORT = 8080

MAX_RANGE = timedelta(days=31)       # retention is 30 days by default
MAX_EXPORT_RANGE = timedelta(days=7)  # raw rows: ~4,300/sensor/day at 20 s
TARGET_POINTS = 300                   # buckets per chart
ALARMS = ("HIGH", "LOW")
SENSOR_KEY = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$")

# Only these files are ever served - no path is built from the request.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/chart.js": ("chart.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}

logger = logging.getLogger("history_web")


# ----------------------------------------------------------------------
# Time helpers - readings.ts is ISO-8601 UTC with microseconds
# ('2026-10-07T23:13:50.015348+00:00'), so bounds are written the same way
# and compare correctly as text (and use the ts indexes).
# ----------------------------------------------------------------------

def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def parse_time(value: str | None, default: datetime) -> datetime:
    """Epoch milliseconds or an ISO date/time (naive = UTC)."""
    if value is None or value == "":
        return default
    if re.fullmatch(r"\d{10,14}", value):
        return datetime.fromtimestamp(int(value) / 1000, UTC)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def time_range(q: dict, default_span: timedelta, max_span: timedelta) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end = parse_time(q.get("end"), now)
    start = parse_time(q.get("start"), end - default_span)
    if start >= end:
        raise ValueError("'start' debe ser anterior a 'end'")
    if end - start > max_span:
        raise ValueError(f"el rango máximo es {max_span.days} días")
    return start, end


def bucket_seconds(start: datetime, end: datetime, minimum: int) -> int:
    return max(minimum, math.ceil((end - start).total_seconds() / TARGET_POINTS))


# ----------------------------------------------------------------------
# Queries - plain functions over a sqlite3 connection, so they are
# testable without the HTTP layer.
# ----------------------------------------------------------------------

def open_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def list_sensors(conn) -> list[dict]:
    """Active sensors, in config.json order (sensors.id is assigned in that
    order by storage.sync_config)."""
    rows = conn.execute(
        "SELECT s.device_id, s.name, s.unit, s.min, s.max "
        "FROM sensors s JOIN devices d ON d.device_id = s.device_id "
        "WHERE s.active = 1 AND d.active = 1 AND d.enabled = 1 ORDER BY s.id"
    ).fetchall()
    return [{"key": f"{r['device_id']}.{r['name']}", "device": r["device_id"], "name": r["name"],
             "unit": r["unit"] or "", "min": r["min"], "max": r["max"]} for r in rows]


def pick_sensors(conn, keys: str | None) -> list[dict]:
    known = {s["key"]: s for s in list_sensors(conn)}
    if not keys:
        return list(known.values())
    picked = []
    for key in keys.split(","):
        if not SENSOR_KEY.match(key) or key not in known:
            raise ValueError(f"sensor desconocido: {key!r}")
        picked.append(known[key])
    return picked


def latest_reading(conn, sensor: dict) -> dict | None:
    r = conn.execute(
        "SELECT ts, value, status, alarm FROM readings WHERE device_id = ? AND sensor_name = ? "
        "ORDER BY ts DESC LIMIT 1", (sensor["device"], sensor["name"])
    ).fetchone()
    return dict(r) if r else None


def sensor_series(conn, sensor: dict, start: datetime, end: datetime, step: int) -> list[list]:
    """[bucket_start_ms, avg, min, max, samples_in_alarm] per bucket; buckets
    with no rows are simply absent (the chart breaks the line there), and a
    bucket of only read errors has avg/min/max None."""
    rows = conn.execute(
        "SELECT CAST(strftime('%s', ts) AS INTEGER) / :step AS b, AVG(value) AS avg, "
        "MIN(value) AS lo, MAX(value) AS hi, SUM(alarm IN ('HIGH', 'LOW')) AS n_alarm "
        "FROM readings WHERE device_id = :dev AND sensor_name = :name AND ts >= :start AND ts < :end "
        "GROUP BY b ORDER BY b",
        {"step": step, "dev": sensor["device"], "name": sensor["name"],
         "start": iso(start), "end": iso(end)},
    ).fetchall()
    return [[r["b"] * step * 1000, r["avg"], r["lo"], r["hi"], r["n_alarm"] or 0] for r in rows]


def overview(conn, since: datetime, step: int) -> list[dict]:
    """Every active sensor with its latest reading and a sparkline since
    `since`, in one grouped query rather than one per sensor."""
    spark: dict[str, list] = {}
    for r in conn.execute(
        "SELECT device_id, sensor_name, CAST(strftime('%s', ts) AS INTEGER) / :step AS b, "
        "AVG(value) AS avg FROM readings WHERE ts >= :since "
        "GROUP BY device_id, sensor_name, b ORDER BY b",
        {"step": step, "since": iso(since)},
    ):
        spark.setdefault(f"{r['device_id']}.{r['sensor_name']}", []).append([r["b"] * step * 1000, r["avg"]])
    return [{**s, "latest": latest_reading(conn, s), "spark": spark.get(s["key"], [])}
            for s in list_sensors(conn)]


def alarm_episodes(conn, start: datetime, end: datetime, gap_s: float, limit: int = 500) -> list[dict]:
    """Consecutive HIGH/LOW samples of one sensor merged into an episode.
    A new episode starts when the alarm type changes or more than `gap_s`
    passes between alarm samples (i.e. at least one normal or missing
    sample in between). Newest first."""
    rows = conn.execute(
        "SELECT ts, device_id, sensor_name, value, alarm FROM readings "
        "WHERE ts >= ? AND ts < ? AND alarm IN ('HIGH', 'LOW') ORDER BY device_id, sensor_name, ts",
        (iso(start), iso(end)),
    )
    episodes: list[dict] = []
    cur = None
    for r in rows:
        key = f"{r['device_id']}.{r['sensor_name']}"
        t = datetime.fromisoformat(r["ts"])
        if (cur and cur["key"] == key and cur["alarm"] == r["alarm"]
                and (t - cur["_last"]).total_seconds() <= gap_s):
            cur["_last"], cur["samples"] = t, cur["samples"] + 1
            if r["value"] is not None and (cur["peak"] is None or
                                           (r["value"] > cur["peak"] if r["alarm"] == "HIGH" else r["value"] < cur["peak"])):
                cur["peak"] = r["value"]
            continue
        cur = {"key": key, "alarm": r["alarm"], "_first": t, "_last": t, "samples": 1, "peak": r["value"]}
        episodes.append(cur)
    for e in episodes:
        e["start"], e["end"] = iso(e.pop("_first")), iso(e.pop("_last"))
        e["duration_s"] = (datetime.fromisoformat(e["end"]) - datetime.fromisoformat(e["start"])).total_seconds()
    episodes.sort(key=lambda e: e["start"], reverse=True)
    return episodes[:limit]


def system_series(conn, start: datetime, end: datetime, step: int) -> dict:
    rows = conn.execute(
        "SELECT CAST(strftime('%s', ts) AS INTEGER) / :step AS b, AVG(cpu_load_percent) AS cpu, "
        "AVG(ram_usage_percent) AS ram, AVG(disk_usage_percent) AS disk FROM system_telemetry "
        "WHERE ts >= :start AND ts < :end GROUP BY b ORDER BY b",
        {"step": step, "start": iso(start), "end": iso(end)},
    ).fetchall()
    last = conn.execute("SELECT * FROM system_telemetry ORDER BY ts DESC LIMIT 1").fetchone()
    return {"points": [[r["b"] * step * 1000, r["cpu"], r["ram"], r["disk"]] for r in rows],
            "latest": dict(last) if last else None}


def db_info(conn, path: Path) -> dict:
    size = sum(p.stat().st_size for p in (path, path.with_name(path.name + "-wal")) if p.exists())
    span = conn.execute("SELECT MIN(ts) AS first, MAX(ts) AS last, COUNT(*) AS n FROM readings").fetchone()
    n_system = conn.execute("SELECT COUNT(*) FROM system_telemetry").fetchone()[0]
    return {"size_bytes": size, "readings": span["n"], "first": span["first"],
            "last": span["last"], "system_rows": n_system}


def export_rows(conn, sensors: list[dict], start: datetime, end: datetime, tz: ZoneInfo):
    """CSV lines (header first), oldest first, one row per publish cycle and
    one value column per selected sensor - the layout a spreadsheet expects
    (every reading of one cycle shares its ts). A blank cell is a read error
    or a sensor not read in that cycle; the last column names the sensors
    that were in alarm or failed to read at that moment."""
    buf = io.StringIO()
    w = csv.writer(buf)

    def line(row):
        buf.seek(0)
        buf.truncate()
        w.writerow(row)
        return buf.getvalue()

    col = {s["key"]: i for i, s in enumerate(sensors)}
    yield line(["ts_utc", "hora_local"]
               + [f"{s['name']} {s['device']}" + (f" ({s['unit']})" if s["unit"] else "") for s in sensors]
               + ["alarmas"])
    if not sensors:
        return
    marks = ",".join("?" * len(sensors))
    rows = conn.execute(
        "SELECT ts, device_id || '.' || sensor_name AS key, value, status, alarm FROM readings "
        f"WHERE ts >= ? AND ts < ? AND device_id || '.' || sensor_name IN ({marks}) ORDER BY ts",
        (iso(start), iso(end), *col),
    )
    cur_ts, values, flags = None, [], []

    def flush():
        local = datetime.fromisoformat(cur_ts).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        return line([cur_ts, local] + values + ["; ".join(flags)])

    for r in rows:
        if r["ts"] != cur_ts:
            if cur_ts is not None:
                yield flush()
            cur_ts, values, flags = r["ts"], [""] * len(sensors), []
        name = sensors[col[r["key"]]]["name"]
        if r["value"] is not None:
            values[col[r["key"]]] = r["value"]
        if r["status"] != "OK":
            flags.append(f"{name}: error")
        elif r["alarm"] in ALARMS:
            flags.append(f"{name}: {'alto' if r['alarm'] == 'HIGH' else 'bajo'}")
    if cur_ts is not None:
        yield flush()


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

class App:
    """What the handler needs: paths and the parts of config.json it shows."""

    def __init__(self, config: dict, db_path: Path):
        gw = config.get("gateway", {})
        self.db_path = db_path
        self.gateway_id = gw.get("gateway_id", "")
        self.city = gw.get("city") or "UTC"
        self.tz = ZoneInfo(self.city)
        self.poll_interval = int(gw.get("poll_interval") or 20)
        self.system_interval = int(gw.get("system_interval") or 60)
        self.simulated = bool(config.get("modbus", {}).get("simulate"))
        self.database = config.get("database", {})

    def info(self, conn) -> dict:
        return {"gateway_id": self.gateway_id, "city": self.city, "poll_interval": self.poll_interval,
                "system_interval": self.system_interval, "simulated": self.simulated,
                "retention_days": self.database.get("retention_days", 30),
                "database_enabled": bool(self.database.get("enabled")),
                "now": iso(datetime.now(UTC)), "db": db_info(conn, self.db_path)}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AkvoHistoryWeb/1.0"

        def log_message(self, fmt, *args):  # route through logging, quietly
            logger.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC_FILES:
                return self._static(*STATIC_FILES[url.path])
            if not url.path.startswith("/api/"):
                return self._json({"error": "no encontrado"}, HTTPStatus.NOT_FOUND)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                conn = open_db(app.db_path)
            except FileNotFoundError:
                return self._json({"error": f"no existe la base de datos {app.db_path} - "
                                            "¿database_enabled=1 en system.csv?"}, HTTPStatus.SERVICE_UNAVAILABLE)
            try:
                return self._api(url.path, q, conn)
            except (ValueError, KeyError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except sqlite3.Error as e:
                logger.warning(f"query failed on {url.path}: {e}")
                return self._json({"error": f"error de base de datos: {e}"}, HTTPStatus.SERVICE_UNAVAILABLE)
            finally:
                conn.close()

        def _api(self, path, q, conn):
            if path == "/api/info":
                return self._json(app.info(conn))
            if path == "/api/overview":
                since = datetime.now(UTC) - timedelta(hours=1)
                return self._json({"sensors": overview(conn, since, max(60, app.poll_interval))})
            if path == "/api/sensors":
                return self._json({"sensors": list_sensors(conn)})
            if path == "/api/history":
                start, end = time_range(q, timedelta(hours=24), MAX_RANGE)
                step = bucket_seconds(start, end, app.poll_interval)
                return self._json({"start": iso(start), "end": iso(end), "step_s": step, "series": [
                    {**s, "points": sensor_series(conn, s, start, end, step)}
                    for s in pick_sensors(conn, q.get("sensors"))]})
            if path == "/api/alarms":
                start, end = time_range(q, timedelta(hours=24), MAX_RANGE)
                eps = alarm_episodes(conn, start, end, gap_s=2.5 * app.poll_interval)
                return self._json({"start": iso(start), "end": iso(end), "episodes": eps})
            if path == "/api/system":
                start, end = time_range(q, timedelta(hours=24), MAX_RANGE)
                step = bucket_seconds(start, end, app.system_interval)
                return self._json({"step_s": step, **system_series(conn, start, end, step)})
            if path == "/api/export.csv":
                start, end = time_range(q, timedelta(hours=24), MAX_EXPORT_RANGE)
                sensors = pick_sensors(conn, q.get("sensors"))
                name = f"{app.gateway_id or 'historial'}_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.csv"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                self.wfile.write("﻿".encode())  # BOM: Excel opens UTF-8 (°C, µS) correctly
                for line in export_rows(conn, sensors, start, end, app.tz):
                    self.wfile.write(line.encode())
                return None
            return self._json({"error": "no encontrado"}, HTTPStatus.NOT_FOUND)

        def _json(self, body, status=HTTPStatus.OK):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _static(self, name, ctype):
            data = (STATIC_DIR / name).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, help="overrides web.port")
    ap.add_argument("--force", action="store_true", help="run even if web.enabled is off (testing)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    config = json.loads(args.config.read_text())
    web = config.get("web", {})
    if not (web.get("enabled") or args.force):
        # Exit 0 so systemd (Restart=on-failure) leaves it stopped.
        logger.info("Local web dashboard is off (system.csv web_enabled) - exiting")
        return 0
    if not config.get("database", {}).get("enabled"):
        logger.warning("database.enabled is off - the dashboard will only show data already in "
                       f"{args.db} (set database_enabled=1 in system.csv)")

    port = args.port or web.get("port", DEFAULT_PORT)
    server = ThreadingHTTPServer((args.host, port), make_handler(App(config, args.db)))
    logger.info(f"Local web dashboard on http://{args.host}:{port} (database {args.db}, read-only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
