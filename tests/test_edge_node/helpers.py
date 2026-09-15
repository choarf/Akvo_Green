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

    def connect(self):
        return _ImmediateFuture()

    def disconnect(self):
        self.disconnected = True
        return _ImmediateFuture()

    def publish(self, topic, payload, qos=None):
        self.published.append((topic, payload))
        return _ImmediateFuture(), 0


class FlakyMqttConnection(FakeMqttConnection):
    """Like FakeMqttConnection, but connect() raises on the first
    `fail_times` calls before succeeding - for exercising
    MQTTManager.connect()'s retry loop without a real AWS connection."""

    def __init__(self, fail_times=0, exc=None):
        super().__init__()
        self.fail_times = fail_times
        self.exc = exc or ConnectionError("MQTT unreachable")
        self.connect_attempts = 0

    def connect(self):
        self.connect_attempts += 1
        if self.connect_attempts <= self.fail_times:
            raise self.exc
        return super().connect()


def make_mtls_from_path_returning(connection):
    """Returns a drop-in replacement for mqtt_connection_builder.mtls_from_path
    that always hands back `connection` (ignoring its kwargs) - pair with a
    Flaky/FakeMqttConnection so MQTTManager.connect()'s retry-then-succeed
    behavior lives on the connection object, not on how many times
    mtls_from_path itself gets called."""

    def fake_mtls_from_path(**kwargs):
        return connection

    return fake_mtls_from_path


class FakeSerialClient:
    """Minimal stand-in for pymodbus's ModbusSerialClient."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def make_flaky_serial_client_factory(results):
    """Returns a drop-in replacement for ModbusSerialClient (a callable
    that, like the real class, is constructed with **kwargs and returns an
    object with .connect()/.close()) whose .connect() calls consume
    `results` in order (True/False, or an exception instance to raise),
    repeating the last entry once exhausted - for exercising
    ModbusManager.connect()'s retry loop without a real serial port.

    The returned factory's `.attempts` counts total .connect() calls across
    every instance it created, for assertions on retry count."""

    class _FlakyClient:
        attempts = 0

        def __init__(self, **kwargs):
            self.closed = False

        def connect(self):
            i = min(_FlakyClient.attempts, len(results) - 1)
            _FlakyClient.attempts += 1
            outcome = results[i]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def close(self):
            self.closed = True

    return _FlakyClient


def make_flaky_check_fn(results):
    """Returns a zero-arg callable (for WifiManager(check_fn=...)) that
    consumes `results` (bools) in order, repeating the last entry once
    exhausted - so a test can script "unreachable a few times, then
    reachable" without touching a real socket. The callable's `.attempts`
    counts total calls, for assertions on retry count."""

    calls = {"n": 0}

    def check_fn():
        i = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        check_fn.attempts = calls["n"]
        return results[i]

    check_fn.attempts = 0
    return check_fn


def make_fake_clock(start=1000.0, step=1.0):
    """Returns a zero-arg callable (for monkeypatching en.time.time) that
    advances by `step` seconds on every call - lets a test cross a
    reboot_after threshold deterministically, in real time measured in
    microseconds, instead of racing a real wall clock."""

    state = {"t": start}

    def fake_time():
        state["t"] += step
        return state["t"]

    return fake_time
