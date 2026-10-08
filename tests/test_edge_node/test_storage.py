"""Unit tests for storage.py's HistoryStore (readings/telemetry recording,
retention) and its one-way config.json -> devices/sensors sync - plus how
EdgeNode wires it in (failure isolation from the MQTT publish path)."""

import copy
import sqlite3
import threading
from datetime import datetime, timedelta, UTC

import pytest

import edge_node_improved as en
from config.schema import validate
from storage import HistoryStore
from test_config_schema import VALID_CONFIG


def sensor(name, addr=0, **kw):
    return {"name": name, "addr": addr, "count": 1, "type": "float",
            "scale": 1.0, "offset": 0.0, "unit": "C", "min": 0.0, "max": 50.0, **kw}


def cfg_with(*devices):
    return {"devices": list(devices)}


def device(dev_id, slave=1, sensors=None, enabled=True):
    return {"id": dev_id, "slave": slave, "enabled": enabled,
            "sensors": sensors if sensors is not None else [sensor("Temp")]}


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(db_path=tmp_path / "history.db", retention_days=30)
    yield s
    s.close()


def rows(store, sql, *params):
    return [dict(r) for r in store._connect().execute(sql, params)]


# ---------------------------------------------------------------------------
# schema / migration
# ---------------------------------------------------------------------------

def test_schema_creation_is_idempotent(tmp_path):
    HistoryStore(db_path=tmp_path / "h.db").close()
    HistoryStore(db_path=tmp_path / "h.db").close()


def test_existing_db_without_sensor_id_is_migrated_in_place(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "device_id TEXT NOT NULL, sensor_name TEXT NOT NULL, value REAL, "
        "status TEXT NOT NULL, alarm TEXT)"
    )
    conn.execute("INSERT INTO readings (ts, device_id, sensor_name, value, status) "
                 "VALUES ('2026-01-01T00:00:00+00:00', 'D', 'S', 1.5, 'OK')")
    conn.commit()
    conn.close()

    s = HistoryStore(db_path=path)
    try:
        cols = {r["name"] for r in s._connect().execute("PRAGMA table_info(readings)")}
        assert "sensor_id" in cols
        assert rows(s, "SELECT value, sensor_id FROM readings") == [{"value": 1.5, "sensor_id": None}]
    finally:
        s.close()


# ---------------------------------------------------------------------------
# record_devices / record_system / queries
# ---------------------------------------------------------------------------

PAYLOAD = {
    "ts": "2026-09-25T12:00:00+00:00",
    "devices": {
        "DEV_1": {"Temp": {"val": 21.5, "status": "OK", "alarm": "NORMAL"}},
        "DEV_2": {"Temp": {"status": "BUS_ERROR"}, "Hum": {"status": "EXCEPTION", "err": "x"}},
    },
}


def test_record_devices_flattens_and_stores_null_value_for_errors(store):
    store.record_devices(PAYLOAD)
    got = {(r["device_id"], r["sensor_name"]): r for r in store.query_history()}
    assert got[("DEV_1", "Temp")]["value"] == 21.5
    assert got[("DEV_1", "Temp")]["alarm"] == "NORMAL"
    assert got[("DEV_2", "Temp")]["value"] is None
    assert got[("DEV_2", "Temp")]["status"] == "BUS_ERROR"
    assert len(got) == 3


def test_record_devices_links_sensor_id_once_config_is_synced(store):
    store.record_devices(PAYLOAD)  # before any sync: no sensor rows yet
    assert all(r["sensor_id"] is None for r in store.query_history())

    store.sync_config(cfg_with(device("DEV_1")))
    store.record_devices({"ts": "2026-09-25T12:01:00+00:00",
                          "devices": {"DEV_1": {"Temp": {"val": 22.0, "status": "OK", "alarm": "NORMAL"}}}})
    latest = store.query_history(device_id="DEV_1", limit=1)[0]
    assert latest["sensor_id"] == rows(store, "SELECT id FROM sensors")[0]["id"]


def test_query_history_filters_and_limit(store):
    store.record_devices(PAYLOAD)
    assert len(store.query_history(device_id="DEV_2")) == 2
    assert len(store.query_history(device_id="DEV_2", sensor_name="Hum")) == 1
    assert len(store.query_history(limit=1)) == 1
    assert store.query_history(start="2026-09-26T00:00:00+00:00") == []


def test_record_system_roundtrip(store):
    status = {"ts": "2026-09-25T12:00:00+00:00", "gateway": "GW1", "city": "X",
              "cpu_load_percent": 5.0, "ram_usage_percent": 40.0, "disk_usage_percent": 10.0,
              "ip_address": "10.0.0.2", "platform_type": "Linux", "os": "Linux 6"}
    store.record_system(status)
    got = store.query_system()[0]
    assert got["gateway"] == "GW1" and got["cpu_load_percent"] == 5.0


# ---------------------------------------------------------------------------
# sync_config: config.json -> devices/sensors, one-way
# ---------------------------------------------------------------------------

def test_first_sync_adds_everything_and_records_history(store):
    diff = store.sync_config(cfg_with(device("A"), device("B", slave=2)))
    assert diff["added"] == ["A", "B"] and diff["removed"] == [] and diff["updated"] == []
    assert {r["device_id"] for r in rows(store, "SELECT * FROM devices WHERE active = 1")} == {"A", "B"}
    assert len(rows(store, "SELECT * FROM sensors WHERE active = 1")) == 2
    history = store.query_config_history()
    assert len(history) == 1 and history[0]["added"] == ["A", "B"]


def test_sync_with_unchanged_config_is_a_noop(store):
    cfg = cfg_with(device("A"))
    store.sync_config(cfg)
    assert store.sync_config(copy.deepcopy(cfg)) == {}
    assert len(store.query_config_history()) == 1  # no second history row


def test_sync_sensor_added_to_existing_device(store):
    store.sync_config(cfg_with(device("A")))
    diff = store.sync_config(cfg_with(device("A", sensors=[sensor("Temp"), sensor("Hum", addr=1)])))
    assert diff["added"] == ["A.Hum"] and diff["removed"] == []


def test_sync_sensor_calibration_change_records_old_and_new(store):
    store.sync_config(cfg_with(device("A", sensors=[sensor("Temp", scale=1.0, max=50.0)])))
    diff = store.sync_config(cfg_with(device("A", sensors=[sensor("Temp", scale=0.1, max=80.0)])))
    assert diff["updated"] == [{
        "id": "A.Temp",
        "old": {"scale": 1.0, "max": 50.0},
        "new": {"scale": 0.1, "max": 80.0},
    }]
    assert rows(store, "SELECT scale, max FROM sensors")[0] == {"scale": 0.1, "max": 80.0}
    assert store.query_config_history()[0]["updated"][0]["old"] == {"scale": 1.0, "max": 50.0}


def test_sync_device_slave_change_is_an_update(store):
    store.sync_config(cfg_with(device("A", slave=1)))
    diff = store.sync_config(cfg_with(device("A", slave=5)))
    assert diff["updated"][0]["id"] == "A"
    assert diff["updated"][0]["new"]["slave"] == 5


def test_removed_sensor_and_device_are_deactivated_not_deleted_and_history_survives(store):
    store.sync_config(cfg_with(device("A", sensors=[sensor("Temp"), sensor("Hum", addr=1)]), device("B")))
    store.record_devices({"ts": "2026-09-25T12:00:00+00:00", "devices": {
        "A": {"Hum": {"val": 40.0, "status": "OK", "alarm": "NORMAL"}},
        "B": {"Temp": {"val": 9.0, "status": "OK", "alarm": "NORMAL"}},
    }})

    diff = store.sync_config(cfg_with(device("A", sensors=[sensor("Temp")])))
    assert sorted(diff["removed"]) == ["A.Hum", "B"]

    assert rows(store, "SELECT active FROM devices WHERE device_id = 'B'") == [{"active": 0}]
    assert rows(store, "SELECT active FROM sensors WHERE device_id = 'B'") == [{"active": 0}]
    assert rows(store, "SELECT active FROM sensors WHERE name = 'Hum'") == [{"active": 0}]
    assert len(store.query_history(device_id="B")) == 1     # readings kept
    assert len(store.query_history(sensor_name="Hum")) == 1


def test_readded_device_is_reactivated_not_duplicated(store):
    store.sync_config(cfg_with(device("A"), device("B")))
    store.sync_config(cfg_with(device("A")))
    diff = store.sync_config(cfg_with(device("A"), device("B")))
    assert diff["added"] == ["B"]
    assert len(rows(store, "SELECT * FROM devices WHERE device_id = 'B'")) == 1
    assert rows(store, "SELECT active FROM devices WHERE device_id = 'B'") == [{"active": 1}]
    assert rows(store, "SELECT active FROM sensors WHERE device_id = 'B'") == [{"active": 1}]


def test_sync_failure_rolls_back_everything(store):
    store.sync_config(cfg_with(device("A")))
    bad = cfg_with(device("A", sensors=[sensor("Temp"), sensor("New", addr=1)]), {"slave": 3, "sensors": []})
    with pytest.raises(KeyError):
        store.sync_config(bad)  # second device has no "id"
    assert len(rows(store, "SELECT * FROM sensors")) == 1  # "New" was not left half-applied
    assert len(store.query_config_history()) == 1


def test_sync_only_mirrors_devices_never_secrets(store):
    store.sync_config({**cfg_with(device("A")), "aws": {"cert": "SECRET_CERT_PATH", "client_id": "SECRET_ID"}})
    dump = "".join(store._connect().iterdump())
    assert "SECRET" not in dump


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------

def test_prune_removes_only_rows_past_retention_and_keeps_config_mirror(tmp_path):
    s = HistoryStore(db_path=tmp_path / "h.db", retention_days=30)
    try:
        now = datetime(2026, 9, 25, tzinfo=UTC)
        old = (now - timedelta(days=31)).isoformat()
        new = (now - timedelta(days=1)).isoformat()
        s.sync_config(cfg_with(device("A")))
        for ts in (old, new):
            s.record_devices({"ts": ts, "devices": {"A": {"Temp": {"val": 1.0, "status": "OK", "alarm": "NORMAL"}}}})
            s.record_system({"ts": ts})

        assert s.prune(now=now) == (1, 1)
        assert [r["ts"] for r in s.query_history()] == [new]
        assert len(rows(s, "SELECT * FROM devices")) == 1
        assert len(s.query_config_history()) == 1
    finally:
        s.close()


def test_maybe_prune_respects_interval(tmp_path):
    s = HistoryStore(db_path=tmp_path / "h.db", prune_interval_seconds=100)
    try:
        calls = []
        s.prune = lambda now=None: calls.append(1)
        s.maybe_prune(now=1000.0)
        s.maybe_prune(now=1050.0)   # inside the interval
        s.maybe_prune(now=1200.0)
        assert len(calls) == 2
    finally:
        s.close()


def test_store_is_usable_from_multiple_threads(store):
    errors = []

    def work():
        try:
            store.record_devices(PAYLOAD)
            store.query_history()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(store.query_history(limit=100)) == 12


# ---------------------------------------------------------------------------
# config schema: optional "database" section
# ---------------------------------------------------------------------------

def test_database_section_is_optional_and_validated():
    assert validate(VALID_CONFIG)[0] == []

    ok = copy.deepcopy(VALID_CONFIG)
    ok["database"] = {"enabled": True, "retention_days": 7}
    assert validate(ok)[0] == []

    for bad in ({"enabled": "yes"}, {"retention_days": 0}, {"retention_days": True}, {"retention_days": "7"}):
        cfg = copy.deepcopy(VALID_CONFIG)
        cfg["database"] = bad
        assert validate(cfg)[0], bad

    cfg = copy.deepcopy(VALID_CONFIG)
    cfg["database"] = "on"
    assert validate(cfg)[0]


def test_modbus_simulate_flag_is_optional_and_must_be_a_bool():
    assert validate(VALID_CONFIG)[0] == []  # absent: fine, real hardware is used

    ok = copy.deepcopy(VALID_CONFIG)
    ok["modbus"]["simulate"] = True
    assert validate(ok)[0] == []

    bad = copy.deepcopy(VALID_CONFIG)
    bad["modbus"]["simulate"] = "yes"
    assert validate(bad)[0]


# ---------------------------------------------------------------------------
# EdgeNode wiring: the DB must never be able to hurt the MQTT publish path
# ---------------------------------------------------------------------------

class FakeConfigMgr:
    def __init__(self, config):
        self._config = config

    def get(self):
        return self._config


class RecordingMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append(topic)


class ExplodingHistory:
    def record_devices(self, payload):
        raise sqlite3.OperationalError("database is locked")

    def record_system(self, payload):
        raise sqlite3.OperationalError("disk I/O error")

    def maybe_prune(self):
        raise AssertionError("must not be reached after record_system fails")

    def sync_config(self, cfg):
        raise sqlite3.OperationalError("database is locked")


def make_publishing_node(history):
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node.devices = {}
    node.mqtt = RecordingMqtt()
    node.history = history
    node.simulate_modbus = False
    node.config_mgr = FakeConfigMgr({
        "gateway": {"poll_interval": 1, "system_interval": 1},
        "aws": {"topic_pub": "pub", "topic_system": "sys"},
    })
    return node


def run_two_cycles(monkeypatch, node, thread_fn):
    ticks = {"n": 0}

    def fake_sleep(seconds):
        ticks["n"] += 1
        if ticks["n"] >= 2:
            node.stop_event.set()

    monkeypatch.setattr(en.time, "sleep", fake_sleep)
    thread_fn()
    return ticks["n"]


def test_publisher_keeps_publishing_when_history_write_fails(monkeypatch):
    node = make_publishing_node(ExplodingHistory())
    cycles = run_two_cycles(monkeypatch, node, node.publisher)
    assert cycles == 2                       # loop survived the first failure
    assert node.mqtt.published == ["pub", "pub"]


def test_system_publisher_keeps_publishing_when_history_write_fails(monkeypatch):
    node = make_publishing_node(ExplodingHistory())
    monkeypatch.setattr(en, "get_system_status", lambda gw: {"ts": "t"})
    cycles = run_two_cycles(monkeypatch, node, node.system_publisher)
    assert cycles == 2
    assert node.mqtt.published == ["sys", "sys"]


def test_history_sync_helper_never_raises():
    node = make_publishing_node(ExplodingHistory())
    node._sync_history_config({"devices": []})   # must swallow the error


def test_history_sync_helper_is_a_noop_when_database_disabled():
    node = make_publishing_node(None)
    node._sync_history_config({"devices": []})


def test_existing_database_gets_the_network_columns(tmp_path):
    path = tmp_path / "history.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE system_telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, gateway TEXT, "
        "city TEXT, cpu_load_percent REAL, ram_usage_percent REAL, disk_usage_percent REAL, ip_address TEXT, "
        "platform_type TEXT, os TEXT);"
        "INSERT INTO system_telemetry (ts, gateway) VALUES ('2026-01-01T00:00:00+00:00', 'old');")
    conn.commit()
    conn.close()

    s = HistoryStore(db_path=path)
    s.record_system({"ts": "2026-01-02T00:00:00+00:00", "gateway": "new", "net_bytes_recv": 5, "net_bytes_sent": 7})
    got = rows(s, "SELECT gateway, net_bytes_recv, net_bytes_sent FROM system_telemetry ORDER BY id")
    s.close()
    assert got == [{"gateway": "old", "net_bytes_recv": None, "net_bytes_sent": None},
                   {"gateway": "new", "net_bytes_recv": 5, "net_bytes_sent": 7}]
