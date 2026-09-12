"""Unit tests for DeviceNode: per-sensor isolation and edge-triggered alarms."""

import edge_node_improved as en
from helpers import FakeModbusMgr, FakeModbusResult


def make_device(sensor_cfgs, mgr=None):
    cfg = {"id": "DEV_TEST", "slave": 1, "sensors": sensor_cfgs}
    return en.DeviceNode(cfg, mgr or FakeModbusMgr(result=FakeModbusResult(registers=[42])))


def test_poll_one_bad_sensor_does_not_stop_the_rest():
    device = make_device([
        {"addr": 0, "count": 1, "type": "int"},  # missing "name" -> KeyError inside poll()
        {"name": "Good", "addr": 0, "count": 1, "type": "int"},
    ])
    device.poll()
    assert device.cache == {"Good": {"val": 42, "status": "OK", "alarm": "NORMAL"}}


def test_poll_populates_cache_for_every_sensor():
    device = make_device([
        {"name": "A", "addr": 0, "count": 1, "type": "int"},
        {"name": "B", "addr": 0, "count": 1, "type": "int"},
    ])
    device.poll()
    assert set(device.cache) == {"A", "B"}


def test_evaluate_rules_logs_only_on_alarm_transition(caplog):
    device = make_device([{"name": "Temp", "addr": 0, "count": 1, "type": "int", "min": 0, "max": 10}])

    def set_cache(alarm, status="OK"):
        device.cache = {"Temp": {"status": status, "alarm": alarm}}
        device._evaluate_rules()

    with caplog.at_level("INFO", logger="EdgeNode"):
        set_cache("NORMAL")                  # 1: no log
        set_cache("HIGH")                    # 2: first alarm -> ALERT
        set_cache("HIGH")                    # 3: still HIGH -> no re-log
        set_cache("HIGH")                    # 4: still HIGH -> no re-log
        set_cache("NORMAL")                  # 5: recovered -> CLEAR
        set_cache("NORMAL")                  # 6: still normal -> no log
        set_cache("LOW")                     # 7: new alarm -> ALERT
        set_cache(None, status="EXCEPTION")  # 8: went offline while alarming -> CLEAR w/ status

    messages = [r.message for r in caplog.records]
    assert messages == [
        "[ALERT] DEV_TEST.Temp HIGH",
        "[CLEAR] DEV_TEST.Temp no longer HIGH",
        "[ALERT] DEV_TEST.Temp LOW",
        "[CLEAR] DEV_TEST.Temp no longer LOW (status=EXCEPTION)",
    ]


def test_snapshot_returns_the_cache():
    device = make_device([{"name": "A", "addr": 0, "count": 1, "type": "int"}])
    device.poll()
    assert device.snapshot() is device.cache
