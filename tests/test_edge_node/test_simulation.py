"""Unit tests for domain/simulation.py (SimSensor/SimulatedModbusManager) -
the in-process "no real serial bus at all" Modbus stand-in used when
config.json's modbus.simulate is on. The core correctness property is that
SimSensor.registers() round-trips through the REAL domain.sensors.decode()
for every supported type - not a parallel reimplementation of decoding."""

import random
import threading

import pytest

import edge_node_improved as en
from domain.sensors import decode as decode_sensor
from domain.simulation import SimSensor, SimulatedModbusManager


# ---------------------------------------------------------------------------
# SimSensor: slow thermal-like dynamics
# ---------------------------------------------------------------------------

def run(sensor, seconds, poll=20, start=0):
    return [sensor.step(t) for t in range(start, start + seconds, poll)]


def test_normal_running_stays_inside_min_max():
    s = SimSensor("float32", min_v=0.0, max_v=100.0, rng=random.Random(1))
    values = run(s, 3 * 86400)
    assert 0.0 < min(values) and max(values) < 100.0  # no excursion -> no alarm


def test_reading_changes_slowly_between_polls():
    s = SimSensor("float32", min_v=0.0, max_v=100.0, rng=random.Random(2))
    s.start_excursion(0, high=True, hold_s=1800)
    values = run(s, 4 * 3600)
    assert max(abs(b - a) for a, b in zip(values, values[1:])) < 1.5  # % of span per 20 s


def test_excursion_goes_into_alarm_and_out_slowly():
    s = SimSensor("float32", min_v=0.0, max_v=100.0, rng=random.Random(3))
    s.process = s.drift = 60.0
    s.start_excursion(0, high=True, hold_s=1800)
    trace = list(zip(range(0, 5 * 3600, 20), run(s, 5 * 3600)))
    first = lambda pred: next(t for t, v in trace if pred(t, v))
    up70 = first(lambda t, v: v >= 70)
    up100 = first(lambda t, v: v > 100)
    down100 = first(lambda t, v: t > up100 and v <= 100)
    down70 = first(lambda t, v: t > down100 and v < 70)
    assert up100 - up70 >= 10 * 60      # into the alarm: tens of minutes, not one poll
    assert down70 - down100 >= 10 * 60  # and out of it
    # One alarm episode: once back under the limit it stays there.
    assert all(v <= 100 for t, v in trace if t >= down100)


def test_low_excursion_only_where_the_register_can_encode_it():
    assert SimSensor("float", scale=1.0, offset=0.0, min_v=0.0, max_v=50.0).can_go_low() is False
    assert SimSensor("uint16", min_v=0, max_v=50).can_go_low() is False
    assert SimSensor("int32", min_v=0, max_v=50).can_go_low() is True
    assert SimSensor("float", scale=1.0, offset=0.0, min_v=10.0, max_v=50.0).can_go_low() is True


def test_unsigned_float_is_centred_on_its_encodable_range():
    # ORP-style -1500..1500 on a one-register float can't encode < 0, so
    # normal running sits in 0..1500 instead of reading a clamped 0.
    s = SimSensor("float", scale=1.0, offset=0.0, min_v=-1500.0, max_v=1500.0, rng=random.Random(4))
    values = run(s, 86400)
    assert min(values) > 0.0
    assert sum(v < 1.0 for v in values) == 0


def test_defaults_when_min_max_are_both_absent():
    s = SimSensor("int")
    assert s.value == 50.0  # (0 + 100) / 2, the documented fallback span


# ---------------------------------------------------------------------------
# SimSensor.registers(): must round-trip through the REAL decoder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sensor_type,scale,offset,min_v,max_v", [
    ("int", 1.0, 0.0, 0, 1000),
    ("uint16", 1.0, 0.0, 0, 65000),
    ("float", 0.1, 5.0, -10.0, 50.0),
    ("uint32", 1.0, 0.0, 0, 4_000_000_000),
    ("int32", 1.0, 0.0, -2_000_000_000, 2_000_000_000),
    ("float32", 0.01, -3.0, -500.0, 500.0),
])
def test_registers_round_trip_through_real_decode(sensor_type, scale, offset, min_v, max_v):
    s = SimSensor(sensor_type, scale=scale, offset=offset, min_v=min_v, max_v=max_v)
    s.value = (min_v + max_v) / 2.0  # deterministic midpoint, no random walk involved

    regs = s.registers()
    decoded = decode_sensor(sensor_type, regs, scale=scale, offset=offset)

    # float32 loses precision to IEEE-754 single; everything else should be
    # exact modulo integer rounding.
    tolerance = abs(s.value) * 1e-5 + 0.01 if sensor_type == "float32" else 0.6
    assert decoded == pytest.approx(s.value, abs=tolerance)


def test_registers_use_two_words_only_for_multi_register_types():
    assert len(SimSensor("int", min_v=0, max_v=10).registers()) == 1
    assert len(SimSensor("float", min_v=0, max_v=10).registers()) == 1
    assert len(SimSensor("uint32", min_v=0, max_v=10).registers()) == 2
    assert len(SimSensor("int32", min_v=-10, max_v=10).registers()) == 2
    assert len(SimSensor("float32", min_v=0, max_v=10).registers()) == 2


def test_registers_raises_for_an_unsupported_type():
    with pytest.raises(ValueError, match="unsupported sensor type"):
        SimSensor("bogus", min_v=0, max_v=10).registers()


# ---------------------------------------------------------------------------
# SimulatedModbusManager: same interface ModbusManager exposes
# ---------------------------------------------------------------------------

def device(dev_id, slave, sensors):
    return {"id": dev_id, "slave": slave, "sensors": sensors}


def sensor(name, addr, **kw):
    return {"name": name, "addr": addr, "count": kw.pop("count", 1), **kw}


def test_read_holding_registers_returns_a_decodable_result():
    devices = [device("A", 1, [sensor("Temp", 0, type="float", scale=0.1, min=0, max=50)])]
    mgr = SimulatedModbusManager(devices)

    res = mgr.read_holding_registers(address=0, count=1, device_id=1)

    assert res.isError() is False
    assert len(res.registers) == 1
    val = decode_sensor("float", res.registers, scale=0.1, offset=0.0)
    assert 0 <= val <= 50


def test_unknown_slave_or_address_is_an_error_result():
    devices = [device("A", 1, [sensor("Temp", 0, type="int", min=0, max=50)])]
    mgr = SimulatedModbusManager(devices)

    assert mgr.read_holding_registers(address=0, count=1, device_id=99).isError() is True
    assert mgr.read_holding_registers(address=5, count=1, device_id=1).isError() is True


def test_multi_register_sensor_advances_each_call():
    devices = [device("A", 1, [sensor("Cond", 0, type="uint32", count=2, min=0, max=1000)])]
    mgr = SimulatedModbusManager(devices)

    first = mgr.read_holding_registers(address=0, count=2, device_id=1).registers
    second = mgr.read_holding_registers(address=0, count=2, device_id=1).registers
    assert len(first) == 2 and len(second) == 2  # never truncated to the 1-register case


def test_connect_update_config_disconnect_never_raise():
    mgr = SimulatedModbusManager([])
    mgr.connect()
    mgr.update_config({"port": "/dev/ttyUSB0"})
    mgr.disconnect()


def test_same_slave_different_devices_each_keep_their_own_sensor():
    # Two Akvo-style sensors sharing one physical slave (duplicate slave id
    # is a normal, warned-not-errored config shape) must not collide on
    # (slave, addr) unless they really do share the same register.
    devices = [
        device("A", 3, [sensor("Hum", 1, type="int", min=0, max=100)]),
        device("B", 3, [sensor("Temp", 0, type="int", min=0, max=50)]),
    ]
    mgr = SimulatedModbusManager(devices)
    assert mgr.read_holding_registers(address=1, count=1, device_id=3).isError() is False
    assert mgr.read_holding_registers(address=0, count=1, device_id=3).isError() is False


# ---------------------------------------------------------------------------
# Integration: a real SensorNode reading through a real SimulatedModbusManager
# ---------------------------------------------------------------------------

def test_sensor_node_read_through_simulated_manager_end_to_end():
    devices = [device("A", 1, [sensor("Ph", 0, type="float32", count=2, scale=1.0, offset=0.0, min=0, max=14)])]
    mgr = SimulatedModbusManager(devices)
    node = en.SensorNode({"name": "Ph", "addr": 0, "count": 2, "type": "float32",
                           "scale": 1.0, "offset": 0.0, "min": 0, "max": 14}, slave=1, modbus_mgr=mgr)

    result = node.read()

    assert result["status"] == "OK"
    assert 0 <= result["val"] <= 14
    assert result["alarm"] == "NORMAL"


# ---------------------------------------------------------------------------
# EdgeNode: simulate_modbus flag tags the publisher() payload
# ---------------------------------------------------------------------------

class RecordingMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append(payload)


class FakeConfigMgrForPublisher:
    def __init__(self, config):
        self._config = config

    def get(self):
        return self._config


def test_publisher_tags_payload_as_simulated_when_mode_is_on(monkeypatch):
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node.devices = {}
    node.mqtt = RecordingMqtt()
    node.history = None
    node.simulate_modbus = True
    node.config_mgr = FakeConfigMgrForPublisher({
        "gateway": {"poll_interval": 1},
        "aws": {"topic_pub": "pub"},
    })
    monkeypatch.setattr(en.time, "sleep", lambda s: node.stop_event.set())

    node.publisher()

    assert node.mqtt.published[0]["simulated"] is True


def test_publisher_omits_simulated_key_when_mode_is_off(monkeypatch):
    node = en.EdgeNode.__new__(en.EdgeNode)
    node.stop_event = threading.Event()
    node.devices = {}
    node.mqtt = RecordingMqtt()
    node.history = None
    node.simulate_modbus = False
    node.config_mgr = FakeConfigMgrForPublisher({
        "gateway": {"poll_interval": 1},
        "aws": {"topic_pub": "pub"},
    })
    monkeypatch.setattr(en.time, "sleep", lambda s: node.stop_event.set())

    node.publisher()

    assert "simulated" not in node.mqtt.published[0]


# ---------------------------------------------------------------------------
# SimulatedModbusManager: alarm excursions, ALARMS_PER_DAY site-wide
# ---------------------------------------------------------------------------

def count_episodes(devices, days, poll=60, seed=0, alarms_per_day=10):
    t = [0.0]
    mgr = SimulatedModbusManager(devices, alarms_per_day=alarms_per_day,
                                 clock=lambda: t[0], rng=random.Random(seed))
    sensors = [(d["slave"], s) for d in devices for s in d["sensors"]]
    inside = {}
    episodes = 0
    for i in range(int(days * 86400 / poll)):
        t[0] = i * poll
        for slave, s in sensors:
            regs = mgr.read_holding_registers(s["addr"], s["count"], slave).registers
            v = decode_sensor(s["type"], regs, scale=s.get("scale", 1.0), offset=s.get("offset", 0.0))
            out = v > s["max"] or v < s["min"]
            if out and inside.get(s["name"], True):
                episodes += 1
            inside[s["name"]] = not out
    return episodes


def five_sensors():
    return [device(f"D{i}", i, [sensor(f"S{i}", 0, type="float32", count=2, min=0, max=100)])
            for i in range(1, 6)]


def test_about_ten_alarms_per_day_across_the_site():
    episodes = count_episodes(five_sensors(), days=20, seed=11)
    assert 150 <= episodes <= 250  # Poisson mean 200 over 20 days


def test_no_alarms_when_rate_is_zero():
    assert count_episodes(five_sensors(), days=3, alarms_per_day=0) == 0
