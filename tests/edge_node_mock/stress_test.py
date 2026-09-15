#!/usr/bin/env python3
"""
Combined stability/stress test for the real, unmodified EdgeNode.

Combines four axes at once, all against the mocked Modbus/MQTT harness
(never real hardware or AWS):

  - HIGH LOAD: --devices synthetic devices (mixed sensor types), 1s poll
    interval, instead of the default ~9 devices / 20s interval.
  - CHAOS/FAULT INJECTION: WiFi reachability flaps randomly; MQTT fails
    both because of that AND independently at random (simulating AWS-side
    issues even when the network is fine); ~15% of real Modbus reads are
    swapped for injected failures (dropped bus, BUS_ERROR, truncated
    register count) instead of the mock slave's real response.
  - MALFORMED CONFIG: periodically writes an invalid config.json (missing
    key / bad sensor type / out-of-range slave id), then restores it, to
    exercise ConfigManager.reload()'s validate-and-keep-last-good path.
  - REBOOT ESCALATION UNDER LOAD: gateway.{modbus,aws,wifi,watchdog}_reboot
    timeouts are all set short (--reboot-after) so the real
    _RebootEscalator/_default_reboot_fn/_record_reboot_if_allowed
    loop-guard code runs for real - subprocess.run/os._exit are
    intercepted (never actually reboots or exits this machine), and
    _REBOOT_HISTORY_PATH is redirected to a scratch dir so it never
    touches the real repo's logs/.

Note: ModbusManager, unlike MQTTManager, doesn't reactively reconnect on a
read failure (only SensorNode.read() catches it, returning an EXCEPTION/
BUS_ERROR status - by design, since a wired serial bus's read failures are
usually transient, not connection-level). So modbus_reboot_timeout only
ever fires from a stalled *initial* connect(), which the per-read chaos
here doesn't exercise - that path already has dedicated coverage in
tests/test_edge_node/test_communication_managers.py.

Reports thread-count and RSS-memory growth, log records by level, publish
success/failure counts, and reboot-escalation/loop-guard activity at the end.

Usage:
    python3 stress_test.py
    python3 stress_test.py --duration 600
    python3 stress_test.py --devices 100 --reboot-after 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_edge_node_test as ret  # noqa: E402

sys.path.insert(0, str(ret.VENKO_GREEN_DIR))
import edge_node_improved as en  # noqa: E402
import psutil  # noqa: E402

wifi_state = {"reachable": True}
counters = {
    "published": 0,
    "publish_failed": 0,
    "modbus_chaos_dropped_bus": 0,
    "modbus_chaos_bus_error": 0,
    "modbus_chaos_truncated": 0,
    "config_chaos_writes": 0,
    "reboot_attempts": 0,
    "process_exits_intercepted": 0,
}


# ---------------------------------------------------------------------------
# Synthetic high-load config: N devices, mixed sensor types
# ---------------------------------------------------------------------------

SENSOR_TYPES = ["int", "uint16", "float", "uint32", "int32", "float32"]


def make_high_load_config(base_config: dict, n_devices: int) -> dict:
    cfg = json.loads(json.dumps(base_config))  # deep copy
    devices = []
    for i in range(1, n_devices + 1):
        n_sensors = random.randint(1, 3)
        sensors = []
        addr = 0
        for s in range(n_sensors):
            t = random.choice(SENSOR_TYPES)
            count = 2 if t in ("uint32", "int32", "float32") else 1
            sensors.append({
                "name": f"S{s}",
                "addr": addr,
                "count": count,
                "scale": 1.0,
                "offset": 0.0,
                "unit": "u",
                "type": t,
                "min": 0.0,
                "max": 100.0,
            })
            addr += count
        devices.append({"id": f"LOAD_{i}", "slave": i, "enabled": True, "sensors": sensors})
    cfg["devices"] = devices
    return cfg


# ---------------------------------------------------------------------------
# Chaos: MQTT (WiFi-dependent + independent random failures)
# ---------------------------------------------------------------------------

class _ImmediateFuture:
    def result(self, timeout=None):
        return None


MQTT_EXTRA_FAIL_CHANCE = 0.1


class ChaosMqttConnection:
    def __init__(self, client_id):
        self.client_id = client_id

    def _should_fail(self):
        return (not wifi_state["reachable"]) or (random.random() < MQTT_EXTRA_FAIL_CHANCE)

    def connect(self):
        if self._should_fail():
            raise ConnectionError("simulated AWS/network failure")
        return _ImmediateFuture()

    def disconnect(self):
        return _ImmediateFuture()

    def publish(self, topic, payload, qos=None):
        if self._should_fail():
            counters["publish_failed"] += 1
            raise ConnectionError("simulated AWS/network failure")
        counters["published"] += 1
        return _ImmediateFuture(), 0


def fake_mtls_from_path(**kwargs):
    return ChaosMqttConnection(kwargs.get("client_id", "?"))


# ---------------------------------------------------------------------------
# Chaos: Modbus (mostly real mock slave, ~15% injected adversarial outcomes)
# ---------------------------------------------------------------------------

class _FakeBusErrorResult:
    def isError(self):
        return True


def install_modbus_chaos():
    original_read = en.ModbusManager.read_holding_registers

    def chaotic_read(self, address, count, device_id):
        roll = random.random()
        if roll < 0.05:
            counters["modbus_chaos_dropped_bus"] += 1
            raise ConnectionError("simulated dropped Modbus bus")
        if roll < 0.10:
            counters["modbus_chaos_bus_error"] += 1
            return _FakeBusErrorResult()
        if roll < 0.15 and count > 1:
            real = original_read(self, address, count, device_id)
            if not real.isError() and len(real.registers) > 1:
                counters["modbus_chaos_truncated"] += 1

                class _Truncated:
                    registers = real.registers[:1]

                    def isError(self):
                        return False

                return _Truncated()
            return real
        return original_read(self, address, count, device_id)

    en.ModbusManager.read_holding_registers = chaotic_read


# ---------------------------------------------------------------------------
# Chaos controllers (background threads)
# ---------------------------------------------------------------------------

def wifi_chaos(stop_event):
    while not stop_event.is_set():
        time.sleep(random.uniform(2, 5))
        if random.random() < 0.35:
            wifi_state["reachable"] = not wifi_state["reachable"]


def config_chaos(stop_event, test_config_path, good_config_text):
    mutations = ["remove_key", "bad_type", "bad_slave"]
    while not stop_event.is_set():
        time.sleep(random.uniform(15, 25))
        if stop_event.is_set():
            break
        try:
            bad = json.loads(good_config_text)
            mutation = random.choice(mutations)
            if mutation == "remove_key":
                del bad["modbus"]["port"]
            elif mutation == "bad_type":
                bad["devices"][0]["sensors"][0]["type"] = "not_a_real_type"
            elif mutation == "bad_slave":
                bad["devices"][0]["slave"] = 9999
            test_config_path.write_text(json.dumps(bad, indent=2))
            counters["config_chaos_writes"] += 1
            print(f"  [CHAOS] wrote malformed config ({mutation})")
            time.sleep(6)  # let config_watcher's 5s poll notice + reject it
        finally:
            test_config_path.write_text(good_config_text)


# ---------------------------------------------------------------------------
# Log-record tally (attached to the real logger instead of parsing stdout)
# ---------------------------------------------------------------------------

class TallyHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.counts = {}

    def emit(self, record):
        self.counts[record.levelname] = self.counts.get(record.levelname, 0) + 1


# ---------------------------------------------------------------------------
# Resource monitor
# ---------------------------------------------------------------------------

def resource_monitor(stop_event, samples, interval=2.0):
    proc = psutil.Process()
    while not stop_event.is_set():
        samples.append({
            "t": time.time(),
            "threads": threading.active_count(),
            "rss_mb": proc.memory_info().rss / (1024 * 1024),
        })
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration", type=float, default=240.0, help="Seconds to run (default: 240)")
    parser.add_argument("--devices", type=int, default=40, help="Synthetic device count (default: 40)")
    parser.add_argument(
        "--reboot-after", type=float, default=8.0,
        help="gateway.*_reboot_timeout / watchdog_timeout, seconds (default: 8)",
    )
    args = parser.parse_args()

    random.seed()

    socat_proc = ret.start_virtual_serial()

    base_config = json.loads(ret.DEFAULT_CONFIG.read_text())
    high_load = make_high_load_config(base_config, args.devices)

    work_dir = Path(tempfile.mkdtemp(prefix="akvo_stress_"))
    source_config = work_dir / "source_config.json"
    source_config.write_text(json.dumps(high_load, indent=2))

    mock_proc = ret.start_mock_slave(source_config, interval=1.0, offline=[])
    time.sleep(0.5)

    test_config = work_dir / "config.json"
    ret.build_test_config(source_config, test_config, fake_modbus=True, cert_base=ret.VENKO_GREEN_DIR)

    cfg = json.loads(test_config.read_text())
    cfg["gateway"]["poll_interval"] = 1
    cfg["gateway"]["system_interval"] = 5
    cfg["gateway"]["watchdog_timeout"] = args.reboot_after
    cfg["gateway"]["modbus_reboot_timeout"] = args.reboot_after
    cfg["gateway"]["aws_reboot_timeout"] = args.reboot_after
    cfg["gateway"]["wifi_reboot_timeout"] = args.reboot_after
    good_config_text = json.dumps(cfg, indent=2)
    test_config.write_text(good_config_text)

    # --- Wire up chaos ---
    en.mqtt_connection_builder.mtls_from_path = fake_mtls_from_path
    install_modbus_chaos()

    original_wifi_init = en.WifiManager.__init__

    def patched_wifi_init(self, wcfg, check_fn=None, reboot_fn=None):
        original_wifi_init(self, wcfg, check_fn=check_fn or (lambda: wifi_state["reachable"]), reboot_fn=reboot_fn)

    en.WifiManager.__init__ = patched_wifi_init

    # --- Safely intercept the REAL reboot mechanism (still exercises the
    # real loop-guard logic - just never touches this machine) ---
    en._REBOOT_HISTORY_PATH = str(work_dir / "reboot_history.json")

    def fake_subprocess_run(*a, **kw):
        counters["reboot_attempts"] += 1
        print(f"  [CHAOS] intercepted: sudo reboot (attempt #{counters['reboot_attempts']})")

    def fake_os_exit(code):
        counters["process_exits_intercepted"] += 1
        print(f"  [CHAOS] intercepted: os._exit({code})")

    en.subprocess.run = fake_subprocess_run
    en.os._exit = fake_os_exit

    tally = TallyHandler()
    en.logger.addHandler(tally)

    os.chdir(work_dir)
    node = en.EdgeNode(str(test_config))
    node_thread = threading.Thread(target=node.start, daemon=True)

    stop_event = threading.Event()
    samples = []
    chaos_threads = [
        threading.Thread(target=wifi_chaos, args=(stop_event,), daemon=True),
        threading.Thread(target=config_chaos, args=(stop_event, test_config, good_config_text), daemon=True),
        threading.Thread(target=resource_monitor, args=(stop_event, samples), daemon=True),
    ]

    print(f"===== STRESS TEST: {args.devices} devices, poll_interval=1s, duration={args.duration:.0f}s =====")
    print("Axes: high load + WiFi/MQTT/Modbus chaos + malformed config + reboot escalation (intercepted)\n")

    start = time.time()
    node_thread.start()
    for t in chaos_threads:
        t.start()

    try:
        while time.time() - start < args.duration:
            time.sleep(5)
            elapsed = time.time() - start
            print(
                f"  t={elapsed:5.0f}s  threads={threading.active_count():3d}  "
                f"wifi={'UP' if wifi_state['reachable'] else 'DOWN'}  "
                f"published={counters['published']}  failed={counters['publish_failed']}  "
                f"reboot_attempts={counters['reboot_attempts']}"
            )
    finally:
        stop_event.set()
        node.stop()
        time.sleep(1)
        mock_proc.terminate()
        mock_proc.wait(timeout=5)
        socat_proc.terminate()
        socat_proc.wait(timeout=5)

    # --- Report ---
    print("\n===== RESULTS =====\n")
    print(f"Duration: {time.time() - start:.0f}s, {args.devices} devices")
    if samples:
        thread_counts = [s["threads"] for s in samples]
        rss = [s["rss_mb"] for s in samples]
        print(f"Threads:  min={min(thread_counts)} max={max(thread_counts)} end={thread_counts[-1]}")
        print(f"RSS MB:   start={rss[0]:.1f} end={rss[-1]:.1f} max={max(rss):.1f} growth={rss[-1]-rss[0]:+.1f}")
    print(f"Log records by level: {tally.counts}")
    print(f"MQTT: published={counters['published']} publish_failed={counters['publish_failed']}")
    print(
        f"Modbus chaos injected: dropped_bus={counters['modbus_chaos_dropped_bus']} "
        f"bus_error={counters['modbus_chaos_bus_error']} truncated={counters['modbus_chaos_truncated']}"
    )
    print(f"Config chaos writes: {counters['config_chaos_writes']}")
    print(
        f"Reboot escalation: attempts_intercepted={counters['reboot_attempts']} "
        f"process_exits_intercepted={counters['process_exits_intercepted']}"
    )
    print(f"\nFinal device snapshot count: {len(node.devices)}")
    exception_statuses = sum(
        1
        for d in node.devices.values()
        for s in d.snapshot().values()
        if s.get("status") in ("EXCEPTION", "BUS_ERROR")
    )
    print(f"Sensors currently in EXCEPTION/BUS_ERROR status at stop: {exception_statuses}")


if __name__ == "__main__":
    main()
