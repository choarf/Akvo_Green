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


def test_publish_reconnects_when_not_yet_connected(monkeypatch):
    mgr = make_mqtt_mgr(None)
    reconnect_calls = []
    monkeypatch.setattr(mgr, "connect", lambda: reconnect_calls.append(1))
    mgr.publish("AKVO/data", {"a": 1})
    assert reconnect_calls == [1]


def test_publish_sends_payload_when_connected():
    conn = FakeMqttConnection()
    mgr = make_mqtt_mgr(conn)
    mgr.publish("AKVO/data", {"a": 1})
    assert conn.published == [("AKVO/data", '{"a": 1}')]


def test_publish_reconnects_when_send_raises(monkeypatch):
    class RaisingConnection(FakeMqttConnection):
        def publish(self, topic, payload, qos=None):
            raise RuntimeError("boom")

    mgr = make_mqtt_mgr(RaisingConnection())
    reconnect_calls = []
    monkeypatch.setattr(mgr, "connect", lambda: reconnect_calls.append(1))
    mgr.publish("AKVO/data", {"a": 1})
    assert reconnect_calls == [1]


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
