"""Unit tests for gateway/shadow_sync.py (config through the IoT named shadow)
and its hooks in MQTTManager / config_manager.py / config/schema.py."""

import copy
import csv
import json
import os

import pytest

import config_manager as cm
import edge_node_improved as en
import shadow_sync as ss
from config.schema import validate
from helpers import FakeMqttConnection
from test_config_manager import workspace, write_csv, write_system  # noqa: F401 - workspace is a fixture
from test_config_schema import VALID_CONFIG

THING = "AKVO_test"
BASE = f"$aws/things/{THING}/shadow/name/config"


class _Done:
    def result(self, timeout=None):
        return None


class FakeMqttMgr:
    """Records what ShadowSync publishes and subscribes to."""

    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return _Done()

    def subscribe(self, topic, callback):
        self.subscribed.append(topic)
        return _Done()

    def reports(self):
        return [p["state"]["reported"] for t, p in self.published if t == f"{BASE}/update"]


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    cfg = copy.deepcopy(VALID_CONFIG)
    cfg["meta"] = {"version": "1"}
    path.write_text(json.dumps(cfg))
    os.chmod(path, 0o644)
    return path


@pytest.fixture
def sync(config_file):
    restarts = []
    s = ss.ShadowSync(FakeMqttMgr(), str(config_file), THING, restart_fn=lambda: restarts.append(1))
    s.restarts = restarts
    return s


def read(path):
    return json.loads(path.read_text())


def desired_from(cfg, **changes):
    d = ss.shadow_subset(cfg)
    d.update(changes)
    return d


# ---------------------------------------------------------------------------
# subset / hash / merge
# ---------------------------------------------------------------------------

def test_subset_leaves_out_aws_and_serial_settings():
    cfg = copy.deepcopy(VALID_CONFIG)
    cfg["modbus"]["simulate"] = True
    sub = ss.shadow_subset(cfg)
    assert "aws" not in sub
    assert sub["modbus"] == {"simulate": True}
    assert sub["devices"] == cfg["devices"]


def test_hash_ignores_key_order_and_local_only_sections():
    a = copy.deepcopy(VALID_CONFIG)
    b = json.loads(json.dumps(a, sort_keys=True))
    b["aws"]["host"] = "other.example.com"
    b["modbus"]["port"] = "/dev/ttyS9"
    assert ss.config_hash(a) == ss.config_hash(b)
    b["gateway"]["poll_interval"] = 99
    assert ss.config_hash(a) != ss.config_hash(b)


def test_merge_never_takes_aws_or_serial_settings():
    current = copy.deepcopy(VALID_CONFIG)
    desired = {"aws": {"host": "evil"}, "modbus": {"port": "/dev/null", "simulate": True},
               "gateway": {**current["gateway"], "poll_interval": 5}}
    new = ss.merge_desired(current, desired)
    assert new["aws"] == current["aws"]
    assert new["modbus"]["port"] == current["modbus"]["port"]
    assert new["modbus"]["simulate"] is True
    assert new["gateway"]["poll_interval"] == 5
    assert ss.ignored_keys(desired) == ["aws", "modbus.port"]


def test_restart_needed_for_simulate_and_database_only():
    old = copy.deepcopy(VALID_CONFIG)
    new = copy.deepcopy(old)
    new["gateway"]["poll_interval"] = 5
    assert ss.restart_needed(old, new) == []
    new["modbus"]["simulate"] = True
    new["database"] = {"enabled": True}
    assert ss.restart_needed(old, new) == ["modbus.simulate", "database"]


def test_atomic_write_keeps_the_file_mode_and_leaves_no_temp_files(config_file):
    ss.write_json_atomic(str(config_file), {"a": 1})
    assert read(config_file) == {"a": 1}
    assert oct(os.stat(config_file).st_mode & 0o777) == "0o644"
    assert [p.name for p in config_file.parent.iterdir()] == ["config.json"]


# ---------------------------------------------------------------------------
# ShadowSync
# ---------------------------------------------------------------------------

def test_connected_subscribes_reports_and_asks_for_the_document(sync, config_file):
    sync.handle("connected", None)
    assert sync.mqtt.subscribed == [f"{BASE}/update/delta", f"{BASE}/get/accepted", f"{BASE}/get/rejected"]
    assert sync.mqtt.reports()[0]["config_hash"] == ss.config_hash(read(config_file))
    assert sync.mqtt.published[-1] == (f"{BASE}/get", {})


def test_delta_asks_for_the_whole_document(sync):
    sync.handle("delta", json.dumps({"state": {"gateway": {}}}))
    assert sync.mqtt.published == [(f"{BASE}/get", {})]


def test_document_applies_a_change_and_reports_its_hash(sync, config_file):
    cfg = read(config_file)
    cfg["gateway"]["poll_interval"] = 7
    cfg["meta"]["git_rev"] = "abc1234"
    desired = desired_from(cfg, config_hash=ss.config_hash(cfg))
    sync.handle("document", json.dumps({"state": {"desired": desired}}))

    on_disk = read(config_file)
    assert on_disk["gateway"]["poll_interval"] == 7
    assert on_disk["aws"] == VALID_CONFIG["aws"]       # relative cert paths untouched
    report = sync.mqtt.reports()[-1]
    assert report["config_hash"] == desired["config_hash"]
    assert report["git_rev"] == "abc1234"
    assert report["error"] is None
    assert report["restart_for"] is None
    assert sync.restarts == []


def test_same_config_is_not_rewritten(sync, config_file):
    before = os.stat(config_file).st_mtime_ns
    assert sync.apply(desired_from(read(config_file))) is False
    assert os.stat(config_file).st_mtime_ns == before
    assert sync.mqtt.published == []


def test_invalid_config_is_refused_and_reported(sync, config_file):
    cfg = read(config_file)
    cfg["devices"][0]["sensors"][0]["type"] = "bogus"
    assert sync.apply(desired_from(cfg)) is False
    assert read(config_file)["devices"][0]["sensors"][0]["type"] == "float"
    report = sync.mqtt.reports()[-1]
    assert report["error"]
    assert report["config_hash"] == ss.config_hash(read(config_file))


def test_error_clears_once_desired_matches_the_running_config(sync, config_file):
    bad = read(config_file)
    del bad["gateway"]["gateway_id"]
    sync.apply(desired_from(bad))
    assert sync.last_error
    sync.apply(desired_from(read(config_file)))
    assert sync.last_error is None
    assert sync.mqtt.reports()[-1]["error"] is None


def test_simulate_change_is_applied_then_restarts(sync, config_file):
    cfg = read(config_file)
    cfg["modbus"]["simulate"] = True
    assert sync.apply(desired_from(cfg)) is True
    assert read(config_file)["modbus"]["simulate"] is True
    report = sync.mqtt.reports()[-1]
    assert report["simulate"] is True
    assert report["restart_for"] == ["modbus.simulate"]
    assert sync.restarts == [1]


def test_web_change_reports_a_pending_service_restart(sync, config_file):
    cfg = read(config_file)
    cfg["web"] = {"enabled": True, "port": 8080}
    sync.apply(desired_from(cfg))
    report = sync.mqtt.reports()[-1]
    assert report["pending_service_restart"] == ["web"]
    assert sync.restarts == []


def test_missing_shadow_keeps_the_local_config(sync, config_file):
    before = read(config_file)
    sync.handle("rejected", json.dumps({"code": 404, "message": "No shadow exists"}))
    assert read(config_file) == before


def test_document_without_desired_changes_nothing(sync, config_file):
    before = read(config_file)
    sync.handle("document", json.dumps({"state": {"reported": {"config_hash": "x"}}}))
    assert read(config_file) == before
    assert sync.mqtt.published == []


# ---------------------------------------------------------------------------
# MQTTManager hooks
# ---------------------------------------------------------------------------

class SubscribingConnection(FakeMqttConnection):
    def __init__(self):
        super().__init__()
        self.subscriptions = []

    def subscribe(self, topic, qos=None, callback=None):
        self.subscriptions.append(topic)
        return _Done(), 1


def test_mqtt_calls_on_connect_after_connect_and_resume(monkeypatch):
    conn = SubscribingConnection()
    monkeypatch.setattr(en.mqtt_connection_builder, "mtls_from_path", lambda **kw: conn)
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    calls = []
    mgr.on_connect.append(lambda: calls.append("x"))
    mgr.connect()
    mgr._on_resumed(conn, 0, False)
    assert calls == ["x", "x"]
    assert mgr.subscribe("a/b", lambda *a, **k: None) is not None
    assert conn.subscriptions == ["a/b"]


def test_mqtt_subscribe_without_connection_returns_none():
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    assert mgr.subscribe("a/b", lambda *a, **k: None) is None


def test_mqtt_publish_returns_the_future(monkeypatch):
    conn = FakeMqttConnection()
    monkeypatch.setattr(en.mqtt_connection_builder, "mtls_from_path", lambda **kw: conn)
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    mgr.connect()
    assert mgr.publish("t", {"a": 1}) is not None


# ---------------------------------------------------------------------------
# aws.shadow_enabled: schema + aws.csv
# ---------------------------------------------------------------------------

def test_schema_accepts_boolean_shadow_enabled_only():
    cfg = copy.deepcopy(VALID_CONFIG)
    cfg["aws"]["shadow_enabled"] = True
    assert validate(cfg)[0] == []
    cfg["aws"]["shadow_enabled"] = "1"
    assert any("shadow_enabled" in e for e in validate(cfg)[0])


def test_build_and_export_shadow_enabled_column(workspace):
    write_system(workspace)
    write_csv(
        workspace / "aws.csv",
        ["host", "client_id", "ca", "cert", "key", "topic_pub", "topic_system", "shadow_enabled"],
        [["h.example.com", "GW1", "./ca.pem", "./c.pem", "./k.pem", "t/data", "t/sys", "1"]],
    )
    config = cm.ConfigManager().build_config()
    assert config["aws"]["shadow_enabled"] is True

    cm.ConfigManager().export_aws(config)
    with open(workspace / "aws.csv", newline="") as f:
        assert next(csv.DictReader(f))["shadow_enabled"] == "1"
