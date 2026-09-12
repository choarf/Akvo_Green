"""
Shared test doubles.

Every class in edge_node_improved.py takes its collaborator (a
ModbusManager, an MQTT connection) as a plain constructor/method argument -
so none of these tests need real hardware, AWS, or even the mock-serial
harness under tests/edge_node_mock/. Tests just hand in one of these instead.
"""


class FakeModbusResult:
    """Stands in for pymodbus's read response object."""

    def __init__(self, registers=None, error=False):
        self.registers = registers if registers is not None else []
        self._error = error

    def isError(self):
        return self._error


class FakeModbusMgr:
    """Stands in for ModbusManager.read_holding_registers()."""

    def __init__(self, result=None, exception=None):
        self.result = result
        self.exception = exception
        self.calls = []

    def read_holding_registers(self, address, count, device_id):
        self.calls.append((address, count, device_id))
        if self.exception is not None:
            raise self.exception
        return self.result


class _ImmediateFuture:
    """Stands in for the futures awscrt returns from connect/disconnect."""

    def result(self, timeout=None):
        return None


class FakeMqttConnection:
    """Minimal stand-in for awscrt.mqtt.Connection."""

    def __init__(self):
        self.disconnected = False
        self.published = []

    def disconnect(self):
        self.disconnected = True
        return _ImmediateFuture()

    def publish(self, topic, payload, qos=None):
        self.published.append((topic, payload))
        return _ImmediateFuture(), 0


class FakeSerialClient:
    """Minimal stand-in for pymodbus's ModbusSerialClient."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True
