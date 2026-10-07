"""Unit tests for domain/simulation.py (SimSensor/SimulatedModbusManager) -
the in-process "no real serial bus at all" Modbus stand-in used when
config.json's modbus.simulate is on. The core correctness property is that
SimSensor.registers() round-trips through the REAL domain.sensors.decode()
for every supported type - not a parallel reimplementation of decoding."""

import threading

import pytest

import edge_node_improved as en
from domain.sensors import decode as decode_sensor
from domain.simulation import SimSensor, SimulatedModbusManager


# ---------------------------------------------------------------------------
# SimSensor: bounded walk
# ---------------------------------------------------------------------------

def test_step_stays_within_the_expanded_bound(monkeypatch):
    monkeypatch.setattr("domain.simulation.random.random", lambda: 0.5)  # never spikes
    s = SimSensor("float", scale=1.0, offset=0.0, min_v=0.0, max_v=100.0)
    for _ in range(500):
        v = s.step()
        assert -20.0 <= v <= 120.0  # lo - 0.2*span .. hi + 0.2*span


def test_step_can_spike_out_of_configured_range(monkeypatch):
    monkeypatch.setattr("domain.simulation.random.random", lambda: 0.0)  # always spikes
    monkeypatch.setattr("domain.simulation.random.uniform", lambda a, b: b)
    s = SimSensor("float", scale=1.0, offset=0.0, min_v=0.0, max_v=100.0)
    v = s.step()
    assert v > 100.0 or v < 0.0  # outside min/max -> exercises HIGH/LOW alarms downstream


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
