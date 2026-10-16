"""Unit tests for MQTTManager/ModbusManager.disconnect(), the
publish()-reconnects-when-disconnected behavior, and the _retry_delay
backoff helper they both use."""

import statistics
import threading

import pytest

import edge_node_improved as en
from helpers import FakeMqttConnection, FakeSerialClient


def make_mqtt_mgr(connection=None):
    mgr = en.MQTTManager.__new__(en.MQTTManager)
    mgr.cfg = {}
    mgr.lock = threading.RLock()
    mgr.connection = connection
    mgr.connected = connection is not None
    mgr.last_change = mgr.last_publish = mgr.last_error = None
    return mgr


def make_modbus_mgr(client=None):
    mgr = en.ModbusManager.__new__(en.ModbusManager)
    mgr.cfg = {}
    mgr.lock = threading.RLock()
    mgr.client = client
    return mgr


# ---------------------------------------------------------------------------
# MQTTManager
# ---------------------------------------------------------------------------

def test_mqtt_disconnect_calls_connection_and_clears_it():
    conn = FakeMqttConnection()
    mgr = make_mqtt_mgr(conn)
    mgr.disconnect()
    assert conn.disconnected is True
    assert mgr.connection is None


def test_mqtt_disconnect_is_a_safe_noop_when_not_connected():
    mgr = make_mqtt_mgr(None)
    mgr.disconnect()  # must not raise
    assert mgr.connection is None


def test_publish_without_a_connection_connects_in_the_background_and_returns(monkeypatch):
    mgr = make_mqtt_mgr(None)
    release, started = threading.Event(), []

    def slow_connect():   # an AWS that can't be reached: connect() never returns on its own
        started.append(1)
        release.wait(5)

    monkeypatch.setattr(mgr, "connect", slow_connect)
    mgr.publish("AKVO/data", {"a": 1})          # returns at once
    mgr.publish("AKVO/data", {"a": 2})          # ...and doesn't start a second connect
    mgr._connect_thread.join(0.5)
    assert started == [1] and mgr._connect_thread.is_alive()
    release.set()
    mgr._connect_thread.join(1)
    assert not mgr._connect_thread.is_alive()


def test_publish_sends_payload_when_connected():
    conn = FakeMqttConnection()
    mgr = make_mqtt_mgr(conn)
    mgr.publish("AKVO/data", {"a": 1})
    assert conn.published == [("AKVO/data", '{"a": 1}')]


def test_publish_reconnects_in_the_background_when_send_raises(monkeypatch):
    class RaisingConnection(FakeMqttConnection):
        def publish(self, topic, payload, qos=None):
            raise RuntimeError("boom")

    mgr = make_mqtt_mgr(RaisingConnection())
    reconnect_calls = []
    monkeypatch.setattr(mgr, "connect", lambda: reconnect_calls.append(1))
    mgr.publish("AKVO/data", {"a": 1})
    mgr._connect_thread.join(1)
    assert reconnect_calls == [1] and mgr.connected is False and mgr.last_error == "boom"


def test_update_config_returns_at_once_and_replaces_the_old_connection(monkeypatch):
    old, new = FakeMqttConnection(), FakeMqttConnection()
    mgr = make_mqtt_mgr(old)

    def connect():
        assert mgr.cfg == {"client_id": "AKVO_new"}   # the new settings are used
        mgr.connection = new

    monkeypatch.setattr(mgr, "connect", connect)
    mgr.update_config({"client_id": "AKVO_new"})
    mgr._connect_thread.join(1)
    assert mgr.connection is new and old.disconnected is True and new.disconnected is False


# ---------------------------------------------------------------------------
# ModbusManager
# ---------------------------------------------------------------------------

def test_modbus_disconnect_closes_client_and_clears_it():
    client = FakeSerialClient()
    mgr = make_modbus_mgr(client)
    mgr.disconnect()
    assert client.closed is True
    assert mgr.client is None


def test_modbus_disconnect_is_a_safe_noop_when_not_connected():
    mgr = make_modbus_mgr(None)
    mgr.disconnect()  # must not raise


def test_read_holding_registers_raises_when_not_connected():
    mgr = make_modbus_mgr(None)
    with pytest.raises(ConnectionError):
        mgr.read_holding_registers(address=0, count=1, device_id=1)


# ---------------------------------------------------------------------------
# _retry_delay
# ---------------------------------------------------------------------------

def test_retry_delay_escalates_then_saturates_at_cap():
    means = [statistics.mean(en._retry_delay(a) for _ in range(500)) for a in range(6)]
    assert means[0] < means[1] < means[2] < means[3]
    assert means[4] == pytest.approx(60, rel=0.15)
    assert means[5] == pytest.approx(60, rel=0.15)


def test_retry_delay_is_always_positive():
    for attempt in range(10):
        assert en._retry_delay(attempt) > 0


# ---------------------------------------------------------------------------
# Connection state for the status file
# ---------------------------------------------------------------------------

def test_mqtt_interrupted_and_resumed_callbacks_track_the_link():
    mgr = make_mqtt_mgr(FakeMqttConnection())
    mgr._on_interrupted(None, RuntimeError("socket closed"))
    st = mgr.status()
    assert st["connected"] is False and "socket closed" in st["last_error"] and st["since"]
    mgr._on_resumed(None, 0, True)
    assert mgr.status()["connected"] is True


def test_mqtt_publish_records_last_publish_only_while_connected():
    mgr = make_mqtt_mgr(FakeMqttConnection())
    mgr.publish("t", {})
    first = mgr.last_publish
    assert first is not None
    mgr._on_interrupted(None, "down")
    mgr.publish("t", {})  # queued by the library while down - not a successful send
    assert mgr.last_publish == first


def test_mqtt_disconnect_marks_it_disconnected():
    mgr = make_mqtt_mgr(FakeMqttConnection())
    mgr.disconnect()
    assert mgr.status()["connected"] is False


def test_modbus_status_reports_open_port():
    mgr = make_modbus_mgr(FakeSerialClient())
    mgr.cfg = {"port": "/dev/ttySC0", "baudrate": 9600}
    assert mgr.status() == {"connected": True, "port": "/dev/ttySC0", "baudrate": 9600, "simulated": False}
    assert make_modbus_mgr(None).status()["connected"] is False


def test_mqtt_connect_registers_the_link_callbacks(monkeypatch):
    seen = {}

    def fake_mtls_from_path(**kwargs):
        seen.update(kwargs)
        return FakeMqttConnection()

    monkeypatch.setattr(en.mqtt_connection_builder, "mtls_from_path", fake_mtls_from_path)
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    mgr.connect()
    assert seen["on_connection_interrupted"] == mgr._on_interrupted
    assert seen["on_connection_resumed"] == mgr._on_resumed
    assert mgr.connected is True


def test_serial_settings_drop_keys_pymodbus_rejects():
    cfg = {"port": "/dev/ttySC0", "baudrate": 9600, "timeout": 1.0, "parity": "N", "stopbits": 1,
           "bytesize": 8, "simulate": False}
    settings = en.serial_settings(cfg)
    assert "simulate" not in settings and settings["port"] == "/dev/ttySC0"
    en.ModbusSerialClient(**settings)   # the real pymodbus class accepts it (no TypeError)
