"""Connect/disconnect/retry/reboot-escalation tests for the three
communication layers edge_node_improved.py manages independently:
WifiManager (network reachability), MQTTManager (AWS IoT Core), and
ModbusManager (the RS-485 sensor bus).

All three share the same shape (see _RebootEscalator in
edge_node_improved.py): connect() retries forever with backoff on failure,
disconnect() tears down cleanly, and - if a single connect() call has been
failing longer than a configurable `reboot_after` seconds - a reboot_fn()
fires once, asking an external process supervisor for a restart (the same
pattern EdgeNode.watchdog() already uses for a stalled worker thread).

No real serial port, AWS credentials, or network access is used: Modbus and
MQTT are exercised via the same dependency-injection points
tests/test_edge_node/test_managers.py already relies on (monkeypatching
en.ModbusSerialClient / en.mqtt_connection_builder.mtls_from_path), and
WifiManager takes its reachability check as a constructor argument for
exactly this reason. time.sleep is monkeypatched to a no-op and time.time()
to a deterministic fake clock (see helpers.make_fake_clock) so retry/backoff
and reboot-after-N-seconds behavior can be tested without waiting in real
time or racing the wall clock.
"""

import threading

import pytest

import edge_node_improved as en
from helpers import (
    FakeMqttConnection,
    FlakyMqttConnection,
    make_fake_clock,
    make_flaky_check_fn,
    make_flaky_serial_client_factory,
    make_mtls_from_path_returning,
)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """None of these tests should ever actually wait out a real backoff
    delay (up to 60s) or watch a real wall clock for reboot-after timing."""
    monkeypatch.setattr(en.time, "sleep", lambda seconds: None)


@pytest.fixture
def fake_clock(monkeypatch):
    clock = make_fake_clock(start=1000.0, step=1.0)
    monkeypatch.setattr(en.time, "time", clock)
    return clock


class _StopLoop(Exception):
    """Test-only signal to unwind a manager's infinite connect() retry loop
    once reboot_fn has fired. The real reboot_fn (os._exit) never returns,
    so a real connect() loop never needs this - but a test double running
    connect() on a background thread does, or it spins forever (time.sleep
    is mocked to a no-op above) even after the assertion it's testing for
    has already happened."""


def make_recording_reboot_fn():
    """reboot_fn for the "reboots once when stalled" tests below: records
    the call, then raises _StopLoop so the manager's connect() loop (all
    three place the reboot check right after the retry log line, not
    inside another try/except) unwinds and the background thread it's
    running on actually terminates."""
    calls = []

    def reboot_fn():
        calls.append(1)
        raise _StopLoop()

    reboot_fn.calls = calls
    return reboot_fn


@pytest.fixture(autouse=True)
def quiet_stop_loop_traceback(monkeypatch):
    """_StopLoop is expected/deliberate - don't let threading's default
    excepthook print a traceback for it on every reboot-escalation test."""
    original_hook = threading.excepthook

    def hook(args):
        if args.exc_type is _StopLoop:
            return
        original_hook(args)

    monkeypatch.setattr(threading, "excepthook", hook)


@pytest.fixture(autouse=True)
def no_real_awscrt_io(monkeypatch):
    """MQTTManager.connect() builds a real io.ClientBootstrap/EventLoopGroup
    (each backed by an OS thread pool) on every attempt - harmless once, but
    wasteful and slow across the multi-attempt retry loops these tests
    drive. Only the network handshake itself (mtls_from_path's connection)
    needs to be faked for these tests; these constructors just need to not
    do real work."""
    monkeypatch.setattr(en.io, "EventLoopGroup", lambda *a, **kw: None)
    monkeypatch.setattr(en.io, "ClientBootstrap", lambda *a, **kw: None)
    monkeypatch.setattr(en.io, "DefaultHostResolver", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# ModbusManager
# ---------------------------------------------------------------------------

def test_modbus_connect_succeeds_immediately():
    factory = make_flaky_serial_client_factory([True])
    en_client = en.ModbusSerialClient
    try:
        en.ModbusSerialClient = factory
        mgr = en.ModbusManager({"port": "/dev/ttyUSB0"})
        mgr.connect()
        assert mgr.client is not None
        assert factory.attempts == 1
    finally:
        en.ModbusSerialClient = en_client


def test_modbus_connect_retries_then_succeeds():
    factory = make_flaky_serial_client_factory(
        [Exception("port busy"), Exception("port busy"), True]
    )
    en_client = en.ModbusSerialClient
    try:
        en.ModbusSerialClient = factory
        mgr = en.ModbusManager({"port": "/dev/ttyUSB0"})
        mgr.connect()
        assert mgr.client is not None
        assert factory.attempts == 3
    finally:
        en.ModbusSerialClient = en_client


def test_modbus_disconnect_closes_and_clears_client():
    factory = make_flaky_serial_client_factory([True])
    en_client = en.ModbusSerialClient
    try:
        en.ModbusSerialClient = factory
        mgr = en.ModbusManager({"port": "/dev/ttyUSB0"})
        mgr.connect()
        client = mgr.client
        mgr.disconnect()
        assert client.closed is True
        assert mgr.client is None
    finally:
        en.ModbusSerialClient = en_client


def test_modbus_reboots_once_when_stalled_past_reboot_after(fake_clock):
    factory = make_flaky_serial_client_factory([Exception("port busy")])
    reboot_fn = make_recording_reboot_fn()
    en_client = en.ModbusSerialClient
    try:
        en.ModbusSerialClient = factory
        mgr = en.ModbusManager(
            {"port": "/dev/ttyUSB0"}, reboot_after=5, reboot_fn=reboot_fn
        )
        # connect() would otherwise never return (permanently failing) -
        # run it on a thread; reboot_fn raises _StopLoop once it fires,
        # which unwinds connect() and ends the thread.
        t = threading.Thread(target=mgr.connect, daemon=True)
        t.start()
        t.join(timeout=2)
        assert not t.is_alive()
        assert reboot_fn.calls == [1]
    finally:
        en.ModbusSerialClient = en_client


# ---------------------------------------------------------------------------
# MQTTManager (AWS IoT Core)
# ---------------------------------------------------------------------------

def test_mqtt_connect_succeeds_immediately(monkeypatch):
    conn = FakeMqttConnection()
    monkeypatch.setattr(
        en.mqtt_connection_builder, "mtls_from_path", make_mtls_from_path_returning(conn)
    )
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    mgr.connect()
    assert mgr.connection is conn


def test_mqtt_connect_retries_then_succeeds(monkeypatch):
    conn = FlakyMqttConnection(fail_times=2)
    monkeypatch.setattr(
        en.mqtt_connection_builder, "mtls_from_path", make_mtls_from_path_returning(conn)
    )
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    mgr.connect()
    assert mgr.connection is conn
    assert conn.connect_attempts == 3


def test_mqtt_disconnect_calls_connection_and_clears_it(monkeypatch):
    conn = FakeMqttConnection()
    monkeypatch.setattr(
        en.mqtt_connection_builder, "mtls_from_path", make_mtls_from_path_returning(conn)
    )
    mgr = en.MQTTManager({"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"})
    mgr.connect()
    mgr.disconnect()
    assert conn.disconnected is True
    assert mgr.connection is None


def test_mqtt_reboots_once_when_stalled_past_reboot_after(monkeypatch, fake_clock):
    conn = FlakyMqttConnection(fail_times=10_000)  # never succeeds
    monkeypatch.setattr(
        en.mqtt_connection_builder, "mtls_from_path", make_mtls_from_path_returning(conn)
    )
    reboot_fn = make_recording_reboot_fn()
    mgr = en.MQTTManager(
        {"host": "h", "cert": "c", "key": "k", "ca": "ca", "client_id": "id"},
        reboot_after=5,
        reboot_fn=reboot_fn,
    )
    t = threading.Thread(target=mgr.connect, daemon=True)
    t.start()
    t.join(timeout=2)
    assert not t.is_alive()
    assert reboot_fn.calls == [1]


# ---------------------------------------------------------------------------
# WifiManager (network reachability)
# ---------------------------------------------------------------------------

def test_wifi_connect_succeeds_immediately():
    check_fn = make_flaky_check_fn([True])
    mgr = en.WifiManager({}, check_fn=check_fn)
    mgr.connect()
    assert mgr.connected is True
    assert check_fn.attempts == 1


def test_wifi_connect_retries_then_succeeds():
    check_fn = make_flaky_check_fn([False, False, True])
    mgr = en.WifiManager({}, check_fn=check_fn)
    mgr.connect()
    assert mgr.connected is True
    assert check_fn.attempts == 3


def test_wifi_disconnect_marks_not_connected():
    check_fn = make_flaky_check_fn([True])
    mgr = en.WifiManager({}, check_fn=check_fn)
    mgr.connect()
    mgr.disconnect()
    assert mgr.connected is False


def test_wifi_monitor_detects_drop_and_reconnects():
    reconnected = threading.Event()
    state = {"n": 0}

    def check_fn():
        state["n"] += 1
        n = state["n"]
        if n == 1:
            return True  # initial connect() succeeds
        if n in (2, 3):
            return False  # monitor notices the drop; connect() retries
        reconnected.set()  # n >= 4: reachable again
        return True

    mgr = en.WifiManager({}, check_fn=check_fn)
    mgr.connect()  # attempt 1
    assert mgr.connected is True

    stop_event = threading.Event()
    t = threading.Thread(target=mgr.monitor, args=(stop_event, 0), daemon=True)
    t.start()

    assert reconnected.wait(timeout=2), "monitor() never reconnected after the simulated drop"
    stop_event.set()
    t.join(timeout=2)

    assert mgr.connected is True
    assert state["n"] >= 4


def test_wifi_reboots_once_when_stalled_past_reboot_after(fake_clock):
    check_fn = make_flaky_check_fn([False])  # never reachable
    reboot_fn = make_recording_reboot_fn()
    mgr = en.WifiManager(
        {"wifi_reboot_timeout": 5}, check_fn=check_fn, reboot_fn=reboot_fn
    )
    t = threading.Thread(target=mgr.connect, daemon=True)
    t.start()
    t.join(timeout=2)
    assert not t.is_alive()
    assert reboot_fn.calls == [1]


# ---------------------------------------------------------------------------
# _RebootEscalator (shared by all three managers above)
# ---------------------------------------------------------------------------

def test_reboot_escalator_disabled_by_default(fake_clock):
    calls = []
    esc = en._RebootEscalator(reboot_after=None, reboot_fn=lambda: calls.append(1))
    esc.reset()
    for _ in range(1000):
        esc.check("Test")
    assert calls == []


def test_reboot_escalator_fires_only_once_per_reset(fake_clock):
    calls = []
    esc = en._RebootEscalator(reboot_after=3, reboot_fn=lambda: calls.append(1))
    esc.reset()
    for _ in range(20):
        esc.check("Test")
    assert calls == [1]


def test_reboot_escalator_can_fire_again_after_reset(fake_clock):
    calls = []
    esc = en._RebootEscalator(reboot_after=3, reboot_fn=lambda: calls.append(1))
    esc.reset()
    for _ in range(20):
        esc.check("Test")
    esc.reset()
    for _ in range(20):
        esc.check("Test")
    assert calls == [1, 1]


# ---------------------------------------------------------------------------
# _default_reboot_fn (the actual default reboot_fn all three managers use
# when a caller doesn't inject its own - reboots the whole host, since a
# field deployment has no process supervisor to restart a merely-exited
# process). Every test here monkeypatches en._REBOOT_HISTORY_PATH to a
# tmp_path file - otherwise _default_reboot_fn would write to the real
# logs/reboot_history.json relative to wherever pytest runs from.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_reboot_history(tmp_path, monkeypatch):
    monkeypatch.setattr(en, "_REBOOT_HISTORY_PATH", str(tmp_path / "reboot_history.json"))


def test_default_reboot_fn_reboots_the_host(monkeypatch):
    calls = []
    monkeypatch.setattr(en.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    en._default_reboot_fn()
    assert calls == [((["sudo", "reboot"],), {"check": True})]


def test_default_reboot_fn_falls_back_to_process_exit_if_reboot_command_fails(monkeypatch):
    def raising_run(*a, **kw):
        raise FileNotFoundError("reboot: command not found")

    exit_calls = []
    monkeypatch.setattr(en.subprocess, "run", raising_run)
    monkeypatch.setattr(en.os, "_exit", lambda code: exit_calls.append(code))
    en._default_reboot_fn()
    assert exit_calls == [1]


def test_default_reboot_fn_stops_rebooting_past_the_loop_limit(monkeypatch):
    reboot_calls = []
    monkeypatch.setattr(en.subprocess, "run", lambda *a, **kw: reboot_calls.append(1))
    monkeypatch.setattr(en, "_REBOOT_LOOP_LIMIT", 3)

    for _ in range(3):
        en._default_reboot_fn()
    assert reboot_calls == [1, 1, 1]

    en._default_reboot_fn()  # 4th call within the window - should be refused
    assert reboot_calls == [1, 1, 1]  # no 4th reboot


# ---------------------------------------------------------------------------
# _record_reboot_if_allowed (the reboot-loop guard itself)
# ---------------------------------------------------------------------------

def test_reboot_guard_allows_up_to_the_limit(tmp_path):
    path = str(tmp_path / "history.json")
    for i in range(3):
        allowed, recent_count = en._record_reboot_if_allowed(path, window=3600, limit=3)
        assert allowed is True
        assert recent_count == i


def test_reboot_guard_refuses_past_the_limit(tmp_path):
    path = str(tmp_path / "history.json")
    for _ in range(3):
        en._record_reboot_if_allowed(path, window=3600, limit=3)

    allowed, recent_count = en._record_reboot_if_allowed(path, window=3600, limit=3)
    assert allowed is False
    assert recent_count == 3


def test_reboot_guard_persists_across_separate_calls(tmp_path):
    """Simulates surviving a real reboot: nothing but the file on disk
    carries state from one call to the next - there's no shared object."""
    path = str(tmp_path / "history.json")
    en._record_reboot_if_allowed(path, window=3600, limit=3)
    en._record_reboot_if_allowed(path, window=3600, limit=3)

    allowed, recent_count = en._record_reboot_if_allowed(path, window=3600, limit=3)
    assert allowed is True
    assert recent_count == 2


def test_reboot_guard_forgets_reboots_older_than_the_window(fake_clock, tmp_path):
    path = str(tmp_path / "history.json")
    for _ in range(3):
        en._record_reboot_if_allowed(path, window=10, limit=3)
    allowed, _ = en._record_reboot_if_allowed(path, window=10, limit=3)
    assert allowed is False  # at the limit

    for _ in range(15):  # advance the fake clock well past the 10s window
        en.time.time()

    allowed, recent_count = en._record_reboot_if_allowed(path, window=10, limit=3)
    assert allowed is True
    assert recent_count == 0  # the earlier 3 all aged out


def test_reboot_guard_missing_history_file_starts_from_zero(tmp_path):
    path = str(tmp_path / "does_not_exist_yet.json")
    allowed, recent_count = en._record_reboot_if_allowed(path, window=3600, limit=3)
    assert allowed is True
    assert recent_count == 0
