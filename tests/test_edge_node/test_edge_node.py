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

def make_watchdog_node(heartbeat_age_seconds, watchdog_timeout):
    """A stale heartbeat means "the worker last made progress this long
    ago, and hasn't since" - which in the new _RebootEscalator-based
    watchdog means _watchdog_reboot._started (set by reset() whenever
    _last_heartbeat actually advances) is itself that old, not just
    _last_heartbeat. Setting it directly here is the test equivalent of
    "reset() was called heartbeat_age_seconds ago and heartbeat hasn't
    moved since" - watchdog_last_seen_heartbeat matching _last_heartbeat
    means the loop won't call reset() again and clobber it."""
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node._last_heartbeat = time.time() - heartbeat_age_seconds
    node._watchdog_last_seen_heartbeat = node._last_heartbeat
    node._watchdog_reboot = en._RebootEscalator()
    node._watchdog_reboot._started = time.time() - heartbeat_age_seconds
    gateway_cfg = {"watchdog_timeout": watchdog_timeout} if watchdog_timeout else {}
    node.config_mgr = FakeConfigMgr({"gateway": gateway_cfg})
    return node


def test_watchdog_reboots_when_heartbeat_is_stale(monkeypatch):
    node = make_watchdog_node(heartbeat_age_seconds=999, watchdog_timeout=1)

    reboot_calls = []

    def fake_reboot():
        reboot_calls.append(1)
        node.stop_event.set()  # let the loop terminate instead of looping forever

    node._watchdog_reboot.reboot_fn = fake_reboot
    monkeypatch.setattr(en.time, "sleep", lambda s: None)  # skip the real 5s poll interval

    node.watchdog()

    assert reboot_calls == [1]


def test_watchdog_stays_inert_when_timeout_unset(monkeypatch):
    node = make_watchdog_node(heartbeat_age_seconds=999, watchdog_timeout=None)  # very stale, but disabled

    reboot_calls = []
    node._watchdog_reboot.reboot_fn = lambda: reboot_calls.append(1)
    # Let the loop run exactly one iteration, then stop it via the sleep call.
    monkeypatch.setattr(en.time, "sleep", lambda s: node.stop_event.set())

    node.watchdog()

    assert reboot_calls == []


def test_watchdog_does_not_reboot_while_heartbeat_keeps_advancing(monkeypatch):
    node = make_watchdog_node(heartbeat_age_seconds=0, watchdog_timeout=1)
    reboot_calls = []
    node._watchdog_reboot.reboot_fn = lambda: reboot_calls.append(1)

    ticks = {"n": 0}

    def fake_sleep(seconds):
        ticks["n"] += 1
        node._last_heartbeat = time.time()  # worker made progress during this tick
        if ticks["n"] >= 5:
            node.stop_event.set()

    monkeypatch.setattr(en.time, "sleep", fake_sleep)

    node.watchdog()

    assert reboot_calls == []
    assert ticks["n"] == 5
