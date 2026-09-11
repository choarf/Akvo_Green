"""
Fake AWS IoT Core MQTT connection for exercising edge_node_improved.py
without real AWS credentials, certificates, or network access.

edge_node_improved.py's MQTTManager calls
awsiot.mqtt_connection_builder.mtls_from_path(...) to get a connection
object, then only ever calls .connect(), .publish(), and .disconnect() on
it (each returning something with a .result() method, matching awscrt's
API). FakeMqttConnection reproduces that surface and prints instead of
publishing to AWS IoT Core.

Usage (see run_edge_node_test.py for the full picture):

    import edge_node_improved as en
    from fake_mqtt import make_fake_mtls_from_path

    en.mqtt_connection_builder.mtls_from_path = make_fake_mtls_from_path()
"""

from __future__ import annotations

import json
from typing import Any, Callable


def round_floats(value: Any, digits: int = 2) -> Any:
    """Recursively round floats for display (e.g. 109.10000000000001 -> 109.1).

    SensorNode.read() computes val = raw * scale + offset in plain float
    arithmetic, which routinely produces these artifacts. Only affects how
    values are printed here - never touches what edge_node_improved.py itself
    computes, stores, or publishes.
    """
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: round_floats(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [round_floats(v, digits) for v in value]
    return value


def _has_alarm(sensors: Any) -> bool:
    return isinstance(sensors, dict) and any(
        isinstance(data, dict) and data.get("alarm") in ("HIGH", "LOW")
        for data in sensors.values()
    )


class _ImmediateFuture:
    """Stand-in for the futures awscrt returns from connect/disconnect."""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def result(self, timeout: float | None = None) -> Any:
        return self._value


class FakeMqttConnection:
    """Drop-in replacement for awscrt.mqtt.Connection."""

    def __init__(
        self,
        client_id: str,
        on_publish: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.client_id = client_id
        self._on_publish = on_publish or self._default_on_publish
        self.connected = False

    @staticmethod
    def _default_on_publish(topic: str, payload: Any) -> None:
        payload = round_floats(payload)

        # Device data: {"ts": ..., "devices": {"DEV_1": {"Temp": {...}}, ...}}
        if isinstance(payload, dict) and isinstance(payload.get("devices"), dict):
            print(f"[FakeMQTT] {topic} @ {payload.get('ts', '?')}")
            for device_id, sensors in payload["devices"].items():
                marker = "!" if _has_alarm(sensors) else " "
                print(f"  {marker} {device_id}: {sensors}")
            return

        # Host telemetry: {"ts": ..., "gateway": ..., "cpu_load_percent": ...}
        if isinstance(payload, dict) and "cpu_load_percent" in payload:
            print(f"[FakeMQTT] {topic} @ {payload.get('ts', '?')}")
            print(
                f"    Gateway:  {payload.get('gateway', '?')} "
                f"({payload.get('city', '?')}, local time {payload.get('city_time', '?')})"
            )
            print(f"    Platform: {payload.get('platform_type', '?')}")
            print(
                f"    CPU: {payload.get('cpu_load_percent', '?')}%   "
                f"RAM: {payload.get('ram_usage_percent', '?')}%   "
                f"Disk: {payload.get('disk_usage_percent', '?')}%"
            )
            print(f"    IP: {payload.get('ip_address', '?')}   OS: {payload.get('os', '?')}")
            return

        print(f"[FakeMQTT] {topic}: {json.dumps(payload)}")

    def connect(self) -> _ImmediateFuture:
        self.connected = True
        print(f"[FakeMQTT] connected as {self.client_id}")
        return _ImmediateFuture()

    def disconnect(self) -> _ImmediateFuture:
        self.connected = False
        print(f"[FakeMQTT] disconnected {self.client_id}")
        return _ImmediateFuture()

    def publish(
        self, topic: str, payload: str, qos: Any = None
    ) -> tuple[_ImmediateFuture, int]:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            data = payload
        self._on_publish(topic, data)
        return _ImmediateFuture(), 0


def make_fake_mtls_from_path(
    on_publish: Callable[[str, Any], None] | None = None,
) -> Callable[..., FakeMqttConnection]:
    """Returns a drop-in replacement for mqtt_connection_builder.mtls_from_path.

    Accepts (and ignores) the same keyword arguments as the real
    mtls_from_path (endpoint, cert_filepath, pri_key_filepath, ca_filepath,
    client_id, client_bootstrap, keep_alive_secs, clean_session) so it can be
    swapped in without touching MQTTManager.connect().
    """

    def fake_mtls_from_path(**kwargs: Any) -> FakeMqttConnection:
        return FakeMqttConnection(kwargs.get("client_id", "unknown"), on_publish)

    return fake_mtls_from_path
