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


# ---------------------------------------------------------------------------
# write_status: the status file history_web.py's Sistema page reads
# ---------------------------------------------------------------------------

class _Mgr:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


def test_write_status_counts_errors_alarms_and_copies_readings(tmp_path):
    import json
    node = make_node()
    node.wifi = _Mgr({"connected": True, "check": "8.8.8.8:53"})
    node.mqtt = _Mgr({"connected": False, "last_error": "x"})
    node.modbus = _Mgr({"connected": True, "port": "/dev/ttyUSB0", "simulated": False})
    payload = {"ts": "2026-10-07T12:00:00+00:00", "devices": {
        "DEV_1": {"T": {"val": 1, "status": "OK", "alarm": "NORMAL"},
                  "H": {"val": 99, "status": "OK", "alarm": "HIGH"}},
        "DEV_2": {"P": {"status": "EXCEPTION", "err": "timeout"}}}}
    cfg = {"gateway": {"gateway_id": "GW1", "poll_interval": 20}}
    path = tmp_path / "sub" / "status.json"

    node.write_status(cfg, payload, path=path)

    st = json.loads(path.read_text())
    assert st["ts"] == payload["ts"] and st["gateway_id"] == "GW1"
    assert st["mqtt"]["connected"] is False and st["network"]["connected"] is True
    assert st["modbus"] == {"connected": True, "port": "/dev/ttyUSB0", "simulated": False,
                            "sensors": 3, "read_errors": 1, "alarms": 1}
    assert st["readings"] == {
        "DEV_1": {"T": {"val": 1, "status": "OK", "alarm": "NORMAL"},
                  "H": {"val": 99, "status": "OK", "alarm": "HIGH"}},
        "DEV_2": {"P": {"status": "EXCEPTION"}}}  # error text left out
    assert not (tmp_path / "sub" / "status.json.tmp").exists()


def test_publisher_status_file_failure_does_not_stop_publishing(monkeypatch):
    node = make_node()
    node.stop_event = threading.Event()
    node.simulate_modbus = False
    node.history = None
    node.config_mgr = FakeConfigMgr({"gateway": {"poll_interval": 0}, "aws": {"topic_pub": "t"}})
    sent = []

    class Mqtt:
        def publish(self, topic, payload):
            sent.append(topic)
            node.stop_event.set()

    node.mqtt = Mqtt()

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(node, "write_status", boom)
    node.publisher()
    assert sent == ["t"]


def test_system_status_counts_network_bytes_on_every_interface_but_loopback(monkeypatch):
    from types import SimpleNamespace as NS
    monkeypatch.setattr(en.psutil, "net_io_counters", lambda pernic: {
        "lo": NS(bytes_recv=10**9, bytes_sent=10**9),
        "wlan0": NS(bytes_recv=1000, bytes_sent=200),
        "eth0": NS(bytes_recv=30, bytes_sent=4),
    })
    monkeypatch.setattr(en.psutil, "cpu_percent", lambda interval: 1.0)
    st = en.get_system_status({"gateway_id": "GW1"})
    assert (st["net_bytes_recv"], st["net_bytes_sent"]) == (1030, 204)


# ---------------------------------------------------------------------------
# AWS down: the Pi itself (sensors, history, OLED status file) never waits on it
# ---------------------------------------------------------------------------

def test_init_system_does_not_wait_for_aws(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(en.MQTTManager, "connect", lambda self: release.wait(5))   # AWS unreachable
    monkeypatch.setattr(en.WifiManager, "connect", lambda self: None)
    node = make_node()
    node.history = None
    node.config_mgr = FakeConfigMgr({
        "gateway": {"gateway_id": "GW1"}, "modbus": {"simulate": True}, "devices": [],
        "aws": {"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "GW1"}})
    t = time.monotonic()
    node.init_system()
    try:
        assert time.monotonic() - t < 2
        assert node.mqtt.connected is False and node.mqtt._connect_thread.is_alive()
        assert node.mqtt.status()["since"] is not None   # "down since start-up" for the OLED
    finally:
        release.set()


def test_publisher_order_history_aws_status_and_aws_errors_skip_nothing(monkeypatch):
    node = make_node()
    node.stop_event = threading.Event()
    node.simulate_modbus = False
    node.config_mgr = FakeConfigMgr({"gateway": {"poll_interval": 0}, "aws": {"topic_pub": "t"}})
    order = []

    class History:
        def record_devices(self, payload):
            order.append("history")

    class Mqtt:
        def publish(self, topic, payload):
            order.append("aws")
            raise RuntimeError("unexpected")   # must not skip the status file

    def status(cfg, payload):
        order.append("status")
        node.stop_event.set()

    node.history, node.mqtt = History(), Mqtt()
    monkeypatch.setattr(node, "write_status", status)
    node.publisher()
    assert order == ["history", "aws", "status"]   # status after AWS: fresh last_publish


def test_system_publisher_records_history_before_aws(monkeypatch):
    node = make_node()
    node.stop_event = threading.Event()
    node.config_mgr = FakeConfigMgr({"gateway": {"system_interval": 0}, "aws": {"topic_system": "s"}})
    order = []

    class History:
        def record_system(self, payload):
            order.append("history")

        def maybe_prune(self):
            pass

    class Mqtt:
        def publish(self, topic, payload):
            order.append("aws")
            node.stop_event.set()

    node.history, node.mqtt = History(), Mqtt()
    monkeypatch.setattr(en, "get_system_status", lambda gw: {})
    node.system_publisher()
    assert order == ["history", "aws"]


# ---------------------------------------------------------------------------
# Startup: first poll waits for the Modbus port, first publish for readings
# ---------------------------------------------------------------------------

def test_scheduler_first_poll_waits_for_modbus_to_connect(monkeypatch):
    node = make_node()
    node.stop_event = threading.Event()
    node.queue = en.queue.Queue()
    node.config_mgr = FakeConfigMgr({"gateway": {"poll_interval": 0}})
    node.modbus.connected = threading.Event()
    node.build_devices({"devices": [device_cfg("A")]})
    waited = []

    def wait(timeout):
        waited.append(timeout)
        assert node.queue.empty()  # nothing polled before the port is open
        return True

    node.modbus.connected.wait = wait
    monkeypatch.setattr(en.time, "sleep", lambda s: node.stop_event.set())
    node.scheduler()
    assert waited == [en.STARTUP_MODBUS_WAIT]
    assert node.queue.qsize() == 1


def test_publisher_first_publish_waits_for_the_first_poll_round(monkeypatch):
    node = make_node()
    node.stop_event = threading.Event()
    node.simulate_modbus = False
    node.history = None
    node.config_mgr = FakeConfigMgr({"gateway": {"poll_interval": 20}, "aws": {"topic_pub": "t"}})
    node.build_devices({"devices": [device_cfg("A")]})
    sent = []

    class Mqtt:
        def publish(self, topic, payload):
            sent.append(payload["devices"])
            node.stop_event.set()

    node.mqtt = Mqtt()
    monkeypatch.setattr(node, "write_status", lambda *a, **k: None)

    def fake_sleep(s):  # the worker reads device A during the startup wait
        node.devices["A"].cache = {"T": {"val": 21.0, "status": "OK", "alarm": "NORMAL"}}

    monkeypatch.setattr(en.time, "sleep", fake_sleep)
    node.publisher()
    assert sent == [{"A": {"T": {"val": 21.0, "status": "OK", "alarm": "NORMAL"}}}]


def test_simulated_modbus_reports_connected():
    from domain.simulation import SimulatedModbusManager
    m = SimulatedModbusManager([])
    assert not m.connected.is_set()
    m.connect()
    assert m.connected.is_set()


# ---------------------------------------------------------------------------
# System telemetry: uptime and connection state for the cloud Sistema page
# ---------------------------------------------------------------------------

def test_system_status_reports_uptime(monkeypatch):
    monkeypatch.setattr(en.psutil, "cpu_percent", lambda interval: 1.0)
    monkeypatch.setattr(en.psutil, "boot_time", lambda: time.time() - 3600)
    assert 3599 <= en.get_system_status({"gateway_id": "GW1"})["uptime_s"] <= 3601


def test_link_status_modbus_mqtt_wifi_and_oled(monkeypatch):
    from domain.simulation import SimulatedModbusManager
    node = make_node()
    node.modbus = SimulatedModbusManager([])
    node.mqtt = type("M", (), {"connected": True, "last_change": 1792780556.0})()
    monkeypatch.setattr(en, "wifi_info", lambda: {"ssid": "Vocho", "profile": "akvo-wifi1", "signal": 72})
    monkeypatch.setattr(en, "network_info", lambda: {"iface": "wlan0", "type": "wifi"})
    st = node.link_status({"display": {"enabled": True}})
    assert st == {"modbus": {"connected": True, "port": None, "baudrate": None, "simulated": True},
                  "mqtt_since": 1792780556.0, "oled_enabled": True, "network": {"iface": "wlan0", "type": "wifi"},
                  "wifi": {"ssid": "Vocho", "profile": "akvo-wifi1", "signal": 72}}


def test_link_status_never_raises(monkeypatch):
    node = make_node()
    node.modbus = object()                      # no status()
    node.mqtt = type("M", (), {"connected": False, "last_change": 1.0})()
    monkeypatch.setattr(en, "wifi_info", lambda: (_ for _ in ()).throw(OSError("nmcli")))
    monkeypatch.setattr(en, "network_info", lambda: (_ for _ in ()).throw(OSError("proc")))
    st = node.link_status({})
    assert st == {"modbus": None, "mqtt_since": None, "network": None, "wifi": None, "oled_enabled": False}
