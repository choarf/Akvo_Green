"""Unit tests for history_web.py, the local dashboard over history.db: its
queries (bucketing, alarm episodes, sensor picking) against a database
written by the real HistoryStore, and the HTTP layer end to end on a
throwaway port (read-only access, static whitelist, error mapping)."""

import csv
import io
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, UTC

import pytest

import history_web as hw
from storage import HistoryStore

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
POLL = 20


def sensor(name, **kw):
    return {"name": name, "addr": 0, "count": 1, "type": "float", "scale": 1.0,
            "offset": 0.0, "unit": "C", "min": 0.0, "max": 50.0, **kw}


CONFIG = {
    "gateway": {"gateway_id": "GW1", "city": "America/Mexico_City", "poll_interval": POLL,
                "system_interval": 60, "watchdog_timeout": 120},
    "modbus": {"simulate": True},
    "database": {"enabled": True, "retention_days": 30},
    "web": {"enabled": True, "port": 8080},
    "devices": [{"id": "DEV_1", "slave": 1, "enabled": True, "sensors": [sensor("Temp")]},
                {"id": "DEV_2", "slave": 2, "enabled": True, "sensors": [sensor("Hum", unit="%")]}],
}

# DEV_1.Temp, one sample per poll: normal, two HIGH, normal, LOW, a read error.
TEMP = [(10.0, "OK", "NORMAL"), (60.0, "OK", "HIGH"), (70.0, "OK", "HIGH"),
        (20.0, "OK", "NORMAL"), (-5.0, "OK", "LOW"), (None, "EXCEPTION", None)]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "history.db"
    store = HistoryStore(db_path=path)
    store.sync_config(CONFIG)
    for i, (val, status, alarm) in enumerate(TEMP):
        temp = {"status": status, "alarm": alarm} if val is None else {"val": val, "status": status, "alarm": alarm}
        store.record_devices({"ts": (T0 + timedelta(seconds=POLL * i)).isoformat(timespec="microseconds"),
                              "devices": {"DEV_1": {"Temp": temp},
                                          "DEV_2": {"Hum": {"val": 40.0 + i, "status": "OK", "alarm": "NORMAL"}}}})
    store.record_system({"ts": T0.isoformat(), "gateway": "GW1", "city": "X", "cpu_load_percent": 5.0,
                         "ram_usage_percent": 40.0, "disk_usage_percent": 10.0, "ip_address": "10.0.0.2",
                         "platform_type": "Raspberry Pi", "os": "Linux 6"})
    store.close()
    return path


@pytest.fixture
def conn(db):
    c = hw.open_db(db)
    yield c
    c.close()


def temp(conn):
    return next(s for s in hw.list_sensors(conn) if s["key"] == "DEV_1.Temp")


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

def test_list_sensors_in_config_order_with_limits(conn):
    sensors = hw.list_sensors(conn)
    assert [s["key"] for s in sensors] == ["DEV_1.Temp", "DEV_2.Hum"]
    assert sensors[1]["unit"] == "%" and sensors[0]["max"] == 50.0


def test_latest_reading_is_the_newest_row_even_when_it_is_an_error(conn):
    latest = hw.latest_reading(conn, temp(conn))
    assert latest["status"] == "EXCEPTION" and latest["value"] is None


def test_series_buckets_avg_min_max_and_alarm_count(conn):
    # one bucket spanning all six samples: AVG/MIN/MAX skip the NULL error value
    [b] = hw.sensor_series(conn, temp(conn), T0, T0 + timedelta(hours=1), step=3600)
    _, avg, lo, hi, n_alarm = b
    assert (lo, hi) == (-5.0, 70.0)
    assert avg == pytest.approx((10 + 60 + 70 + 20 - 5) / 5)
    assert n_alarm == 3


def test_series_respects_the_time_range(conn):
    pts = hw.sensor_series(conn, temp(conn), T0 + timedelta(seconds=POLL), T0 + timedelta(seconds=3 * POLL), step=POLL)
    assert [p[1] for p in pts] == [60.0, 70.0]  # end is exclusive


def test_alarm_episodes_merge_consecutive_samples_and_split_on_type(conn):
    eps = hw.alarm_episodes(conn, T0, T0 + timedelta(hours=1), gap_s=2.5 * POLL)
    by_type = {e["alarm"]: e for e in eps}
    assert len(eps) == 2
    assert by_type["HIGH"]["samples"] == 2 and by_type["HIGH"]["peak"] == 70.0
    assert by_type["HIGH"]["duration_s"] == POLL
    assert by_type["LOW"]["samples"] == 1 and by_type["LOW"]["peak"] == -5.0
    assert eps[0]["alarm"] == "LOW"  # newest first


def test_alarm_episodes_split_when_samples_are_too_far_apart(conn):
    eps = hw.alarm_episodes(conn, T0, T0 + timedelta(hours=1), gap_s=POLL / 2)
    assert sum(e["alarm"] == "HIGH" for e in eps) == 2


def test_overview_has_latest_and_sparkline_for_every_sensor(conn):
    rows = {s["key"]: s for s in hw.overview(conn, T0, step=60)}
    assert rows["DEV_2.Hum"]["latest"]["value"] == 45.0
    assert rows["DEV_2.Hum"]["spark"]


def test_pick_sensors_rejects_unknown_and_malformed_keys(conn):
    assert [s["key"] for s in hw.pick_sensors(conn, "DEV_2.Hum")] == ["DEV_2.Hum"]
    assert len(hw.pick_sensors(conn, None)) == 2
    for bad in ("DEV_9.Nope", "x'; DROP TABLE readings;--", "DEV_1"):
        with pytest.raises(ValueError):
            hw.pick_sensors(conn, bad)


def test_time_range_validates_order_and_length():
    q = {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}
    start, end = hw.time_range(q, timedelta(hours=24), hw.MAX_RANGE)
    assert end - start == timedelta(days=1)
    assert hw.parse_time("1791374400000", None) == datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        hw.time_range({"start": q["end"], "end": q["start"]}, timedelta(hours=1), hw.MAX_RANGE)
    with pytest.raises(ValueError):
        hw.time_range({"start": "2026-01-01", "end": "2026-10-01"}, timedelta(hours=1), hw.MAX_RANGE)


def test_bounds_compare_correctly_with_stored_timestamps():
    # stored ts carry microseconds; a bound on the whole second must sort before them
    assert hw.iso(T0) < (T0 + timedelta(microseconds=1)).isoformat()


def export(conn, sensors):
    lines = hw.export_rows(conn, sensors, T0, T0 + timedelta(hours=1), hw.ZoneInfo("America/Mexico_City"))
    return list(csv.reader(io.StringIO("".join(lines))))


def test_export_has_one_row_per_cycle_and_one_column_per_selected_sensor(conn):
    # the reported bug: several sensors selected must come out side by side,
    # not one sensor's rows after another's
    rows = export(conn, hw.list_sensors(conn))
    assert rows[0] == ["ts_utc", "hora_local", "Temp DEV_1 (C)", "Hum DEV_2 (%)", "alarmas"]
    assert len(rows) == 1 + len(TEMP)
    assert rows[1][1:] == ["2026-10-07 06:00:00", "10.0", "40.0", ""]
    assert rows[2][2:] == ["60.0", "41.0", "Temp: alto"]
    assert rows[5][4] == "Temp: bajo"
    assert rows[6][2:] == ["", "45.0", "Temp: error"]  # read error: blank value, flagged


def test_export_only_includes_the_selected_sensors_in_their_order(conn):
    hum = next(s for s in hw.list_sensors(conn) if s["key"] == "DEV_2.Hum")
    rows = export(conn, [hum])
    assert rows[0] == ["ts_utc", "hora_local", "Hum DEV_2 (%)", "alarmas"]
    assert all(r[-1] == "" for r in rows[1:])  # Temp's alarms aren't reported for Hum alone


def test_db_is_opened_read_only(conn):
    with pytest.raises(Exception):
        conn.execute("DELETE FROM readings")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def server(db):
    srv = hw.ThreadingHTTPServer(("127.0.0.1", 0), hw.make_handler(hw.App(CONFIG, db)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def test_http_serves_the_page_and_info(server):
    status, headers, body = get(server + "/")
    assert status == 200 and b"Historial local" in body
    status, _, body = get(server + "/api/info")
    info = json.loads(body)
    assert info["gateway_id"] == "GW1" and info["simulated"] is True
    assert info["db"]["readings"] == 2 * len(TEMP)


def test_http_history_and_errors(server):
    start, end = int(T0.timestamp() * 1000), int((T0 + timedelta(hours=1)).timestamp() * 1000)
    status, _, body = get(f"{server}/api/history?sensors=DEV_1.Temp&start={start}&end={end}")
    assert status == 200 and json.loads(body)["series"][0]["key"] == "DEV_1.Temp"
    assert get(f"{server}/api/history?sensors=nope.x")[0] == 400
    assert get(f"{server}/api/nope")[0] == 404
    assert get(f"{server}/../config_data/config.json")[0] == 404


def test_http_csv_export(server):
    start, end = int(T0.timestamp() * 1000), int((T0 + timedelta(hours=1)).timestamp() * 1000)
    status, headers, body = get(f"{server}/api/export.csv?sensors=DEV_2.Hum&start={start}&end={end}")
    assert status == 200 and headers["Content-Type"].startswith("text/csv")
    assert body.decode("utf-8-sig").count("\n") == 1 + len(TEMP)


def test_http_missing_database_is_503(tmp_path):
    srv = hw.ThreadingHTTPServer(("127.0.0.1", 0), hw.make_handler(hw.App(CONFIG, tmp_path / "none.db")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _, body = get(f"http://127.0.0.1:{srv.server_address[1]}/api/info")
        assert status == 503 and "database_enabled" in json.loads(body)["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_main_exits_quietly_when_disabled(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({**CONFIG, "web": {"enabled": False}}))
    assert hw.main(["--config", str(cfg), "--db", str(tmp_path / "x.db")]) == 0
