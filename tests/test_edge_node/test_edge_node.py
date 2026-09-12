"""Unit tests for EdgeNode: device reload diffing, config-section hashing,
and the watchdog - constructed via __new__() to skip ConfigManager's real
file I/O, since none of this logic needs an actual config.json on disk."""

import threading
import time

import edge_node_improved as en
from helpers import FakeModbusMgr


def make_node():
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.modbus = FakeModbusMgr()
    node.devices = {}
    node._device_cfgs = {}
    return node


def device_cfg(dev_id, slave=1, sensors=None):
    return {"id": dev_id, "slave": slave, "sensors": sensors or []}


class FakeConfigMgr:
    def __init__(self, config):
        self._config = config

    def get(self):
        return self._config


# ---------------------------------------------------------------------------
# build_devices / reload_devices
# ---------------------------------------------------------------------------

def test_build_devices_creates_one_devicenode_per_entry():
    node = make_node()
    node.build_devices({"devices": [device_cfg("A"), device_cfg("B")]})
    assert set(node.devices) == {"A", "B"}


def test_reload_devices_adds_updates_and_leaves_unchanged_devices_alone():
    node = make_node()
    node.build_devices({"devices": [device_cfg("A"), device_cfg("B")]})
    original_a = node.devices["A"]
    original_b = node.devices["B"]

    node.reload_devices({"devices": [
        device_cfg("A"),          # unchanged
        device_cfg("B", slave=2),  # changed
        device_cfg("C"),          # added
    ]})

    assert set(node.devices) == {"A", "B", "C"}
    assert node.devices["A"] is original_a       # untouched: same cache/alarm history
    assert node.devices["B"] is not original_b   # rebuilt because its config changed


def test_reload_devices_removes_dropped_device():
    node = make_node()
    node.build_devices({"devices": [device_cfg("A"), device_cfg("B")]})
    node.reload_devices({"devices": [device_cfg("A")]})
    assert set(node.devices) == {"A"}


def test_reload_devices_is_a_noop_when_nothing_changed():
    node = make_node()
    node.build_devices({"devices": [device_cfg("A")]})
    instance = node.devices["A"]
    node.reload_devices({"devices": [device_cfg("A")]})
    assert node.devices["A"] is instance


# ---------------------------------------------------------------------------
# _section_hash
# ---------------------------------------------------------------------------

def test_section_hash_stable_regardless_of_key_order():
    assert en._section_hash({"x": 1, "y": 2}) == en._section_hash({"y": 2, "x": 1})


def test_section_hash_differs_for_different_content():
    assert en._section_hash({"x": 1}) != en._section_hash({"x": 2})


# ---------------------------------------------------------------------------
# watchdog
# ---------------------------------------------------------------------------

def test_watchdog_fires_when_heartbeat_is_stale(monkeypatch):
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node._last_heartbeat = time.time() - 999
    node.config_mgr = FakeConfigMgr({"gateway": {"watchdog_timeout": 1}})

    exit_calls = []

    def fake_exit(code):
        exit_calls.append(code)
        node.stop_event.set()  # let the loop terminate instead of really exiting

    monkeypatch.setattr(en.os, "_exit", fake_exit)
    monkeypatch.setattr(en.time, "sleep", lambda s: None)  # skip the real 5s poll interval

    node.watchdog()

    assert exit_calls == [1]


def test_watchdog_stays_inert_when_timeout_unset(monkeypatch):
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node._last_heartbeat = time.time() - 999  # very stale, but disabled below
    node.config_mgr = FakeConfigMgr({"gateway": {}})  # no watchdog_timeout key

    exit_calls = []
    monkeypatch.setattr(en.os, "_exit", lambda code: exit_calls.append(code))
    # Let the loop run exactly one iteration, then stop it via the sleep call.
    monkeypatch.setattr(en.time, "sleep", lambda s: node.stop_event.set())

    node.watchdog()

    assert exit_calls == []
