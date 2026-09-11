import os
import json
import time
import queue
import socket
import hashlib
import logging
import platform
import threading
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler

import psutil
from pymodbus.client import ModbusSerialClient

from awscrt import mqtt, io
from awsiot import mqtt_connection_builder


# =========================
# LOGGER
# =========================
def build_logger():
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger("EdgeNode")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )

    fh = RotatingFileHandler("logs/system.log", maxBytes=1_000_000, backupCount=5)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


logger = build_logger()


# =========================
# CONFIG MANAGER
# =========================
class ConfigManager:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.config = self.load()

    def load(self):
        with open(self.path) as f:
            return json.load(f)

    def get(self):
        with self.lock:
            return self.config

    def reload(self):
        with self.lock:
            self.config = self.load()
            return self.config


def _section_hash(section: dict) -> str:
    """Stable hash of a config sub-section, used to detect changes that
    require reconnecting Modbus/MQTT rather than just rebuilding devices."""
    return hashlib.sha256(json.dumps(section, sort_keys=True).encode()).hexdigest()


def city_time(city: str) -> str:
    """Local time string for the configured gateway city/timezone."""
    try:
        return datetime.now(ZoneInfo(city)).strftime("%Y-%m-%d_%H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d_%H:%M:%S") + "Error"


def is_raspberry_pi() -> bool:
    try:
        with open("/proc/device-tree/model", "r") as f:
            return "raspberry pi" in f.read().lower()
    except Exception:
        return False


def get_system_status(gateway_cfg: dict) -> dict:
    """Host + gateway telemetry: CPU/RAM/disk, IP, OS/platform, local time."""
    cpu_load = psutil.cpu_percent(interval=1)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
    except Exception:
        ip_address = "Unavailable"

    system = platform.system()
    os_info = f"{system} {platform.release()}"
    if system == "Linux":
        platform_type = "Raspberry Pi" if is_raspberry_pi() else "Linux"
    else:
        platform_type = system

    disk_path = "/" if system != "Windows" else "C:\\"

    city = gateway_cfg.get("city")

    return {
        "ts": datetime.now(UTC).isoformat(),
        "gateway": gateway_cfg.get("gateway_id"),
        "city": city,
        "city_time": city_time(city) if city else None,
        "platform_type": platform_type,
        "cpu_load_percent": cpu_load,
        "ram_usage_percent": psutil.virtual_memory().percent,
        "disk_usage_percent": psutil.disk_usage(disk_path).percent,
        "ip_address": ip_address,
        "os": os_info,
    }


# =========================
# MQTT MANAGER (RECONNECT SAFE)
# =========================
class MQTTManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.connection = None
        # Single lock guards BOTH connect() and publish() so a failed
        # publish on one thread can't race a concurrent reconnect from
        # another thread (e.g. publisher + system_publisher both failing
        # at once).
        self.lock = threading.RLock()

    def connect(self):
        while True:
            try:
                logger.info("Connecting MQTT...")

                bootstrap = io.ClientBootstrap(
                    io.EventLoopGroup(1),
                    io.DefaultHostResolver(io.EventLoopGroup(1))
                )

                connection = mqtt_connection_builder.mtls_from_path(
                    endpoint=self.cfg["host"],
                    cert_filepath=self.cfg["cert"],
                    pri_key_filepath=self.cfg["key"],
                    ca_filepath=self.cfg["ca"],
                    client_id=self.cfg["client_id"],
                    client_bootstrap=bootstrap,
                    keep_alive_secs=60,
                    clean_session=False
                )

                connection.connect().result()
                with self.lock:
                    self.connection = connection
                logger.info("MQTT connected")
                return

            except Exception as e:
                logger.error(f"MQTT connect failed: {e}")
                time.sleep(5)

    def update_config(self, cfg):
        """Called by config_watcher when the 'aws' section changes.
        Reconnects using the new settings."""
        old_connection = self.connection
        self.cfg = cfg
        self.connect()  # blocks (retries forever) but no longer holds the lock while doing so
        try:
            if old_connection is not None:
                old_connection.disconnect().result()
        except Exception as e:
            logger.warning(f"MQTT disconnect of old connection failed: {e}")

    def publish(self, topic, payload):
        with self.lock:
            connection = self.connection
        if connection is None:
            logger.error("MQTT publish skipped: not yet connected")
            self.connect()
            return
        try:
            connection.publish(
                topic=topic,
                payload=json.dumps(payload),
                qos=mqtt.QoS.AT_LEAST_ONCE
            )
        except Exception as e:
            logger.error(f"MQTT publish failed: {e}")
            self.connect()


# =========================
# MODBUS MANAGER (RETRY SAFE)
# =========================
class ModbusManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        # Serial pymodbus clients are not thread-safe; every read/connect
        # against self.client must go through this lock.
        self.lock = threading.RLock()

    def connect(self):
        while True:
            try:
                client = ModbusSerialClient(**self.cfg)
                if client.connect():
                    with self.lock:
                        self.client = client
                    logger.info("Modbus connected")
                    return
                else:
                    raise Exception("Connection failed")

            except Exception as e:
                logger.error(f"Modbus connect error: {e}")
                time.sleep(5)

    def update_config(self, cfg):
        """Called by config_watcher when the 'modbus' section changes."""
        old_client = self.client
        self.cfg = cfg
        self.connect()  # blocks (retries forever) but no longer holds the lock while doing so
        try:
            if old_client is not None:
                old_client.close()
        except Exception as e:
            logger.warning(f"Modbus close of old client failed: {e}")

    def read_holding_registers(self, address, count, device_id):
        """Thread-safe wrapper other code should call instead of touching
        self.client directly."""
        with self.lock:
            client = self.client
        if client is None:
            raise ConnectionError("Modbus client not yet connected")
        return client.read_holding_registers(
            address=address, count=count, device_id=device_id
        )


# =========================
# SENSOR + DEVICE
# =========================
class SensorNode:
    def __init__(self, cfg, slave, modbus_mgr):
        self.cfg = cfg
        self.slave = slave
        self.modbus_mgr = modbus_mgr

    def _evaluate_alarm(self, val):
        if not isinstance(val, (int, float)):
            return None
        if self.cfg.get("min") is not None and val < self.cfg["min"]:
            return "LOW"
        if self.cfg.get("max") is not None and val > self.cfg["max"]:
            return "HIGH"
        return "NORMAL"

    def read(self):
        try:
            res = self.modbus_mgr.read_holding_registers(
                address=self.cfg["addr"], count=self.cfg["count"], device_id=self.slave
            )

            if res.isError():
                return {"status": "BUS_ERROR"}

            val = res.registers[0]

            if self.cfg.get("type") == "float":
                val = val * self.cfg.get("scale", 1) + self.cfg.get("offset", 0)

            return {"val": val, "status": "OK", "alarm": self._evaluate_alarm(val)}

        except Exception as e:
            return {"status": "EXCEPTION", "err": str(e)}


class DeviceNode:
    def __init__(self, cfg, modbus_mgr):
        self.id = cfg["id"]
        self.sensors = [
            SensorNode(s, cfg["slave"], modbus_mgr)
            for s in cfg["sensors"]
        ]
        self.cache = {}

    def poll(self):
        for s in self.sensors:
            try:
                self.cache[s.cfg["name"]] = s.read()
            except Exception as e:
                # s.read() already catches its own errors and returns a
                # status dict - this is a last-resort guard in case a
                # sensor's config is malformed (e.g. missing "name") so
                # one bad sensor can't stop the rest of this device (or
                # the whole worker thread) from being polled.
                logger.error(f"Unexpected error polling sensor on device: {e}")
        self._evaluate_rules()

    def _evaluate_rules(self):
        for name, data in self.cache.items():
            alarm = data.get("alarm")
            if alarm in ("HIGH", "LOW"):
                logger.warning(f"[ALERT] {self.id}.{name} {alarm}")

    def snapshot(self):
        return self.cache


# =========================
# EDGE NODE (MAIN ENGINE)
# =========================
class EdgeNode:
    def __init__(self, config_path):
        self.config_mgr = ConfigManager(config_path)
        self.stop_event = threading.Event()

        self.devices = {}
        self.queue = queue.Queue(maxsize=1000)

        self.mqtt = None
        self.modbus = None

        # Track hashes of the sub-sections that require a reconnect
        # (rather than just rebuilding the device list) when changed.
        self._modbus_hash = None
        self._aws_hash = None
        # Last-seen device configs, keyed by id, for partial-reload diffing.
        self._device_cfgs = {}

    def init_system(self):
        cfg = self.config_mgr.get()

        self.modbus = ModbusManager(cfg["modbus"])
        self.mqtt = MQTTManager(cfg["aws"])

        # Connect Modbus and MQTT concurrently. Both connect() calls
        # retry forever on failure - previously they ran sequentially,
        # so a stuck/unreachable Modbus serial port (wrong port,
        # permission denied, device unplugged) blocked MQTT from ever
        # connecting, and nothing reached AWS even though AWS itself
        # was reachable. Now a dead serial bus only blocks sensor
        # data, not system-health reporting or AWS connectivity.
        modbus_thread = threading.Thread(
            target=self.modbus.connect, name="ModbusConnect", daemon=True
        )
        modbus_thread.start()

        # Only block startup on MQTT. Modbus keeps retrying forever in
        # the background - if the serial bus is down/misconfigured,
        # sensor reads will just come back EXCEPTION/BUS_ERROR until it
        # connects, but AWS reporting (including system_publisher's
        # host-health payload) starts immediately regardless.
        self.mqtt.connect()

        self._modbus_hash = _section_hash(cfg["modbus"])
        self._aws_hash = _section_hash(cfg["aws"])

        self.build_devices(cfg)

    def build_devices(self, cfg):
        """Initial build: construct every device from scratch."""
        self.devices = {
            d["id"]: DeviceNode(d, self.modbus)
            for d in cfg["devices"]
        }
        self._device_cfgs = {d["id"]: d for d in cfg["devices"]}

    def reload_devices(self, cfg):
        """Partial reload: only rebuild devices whose config actually
        changed, add new ones, and drop removed ones - instead of
        rebuilding the entire device dict on every config change."""
        new_cfgs = {d["id"]: d for d in cfg["devices"]}

        added = new_cfgs.keys() - self._device_cfgs.keys()
        removed = self._device_cfgs.keys() - new_cfgs.keys()
        updated = {
            k for k in new_cfgs.keys() & self._device_cfgs.keys()
            if new_cfgs[k] != self._device_cfgs[k]
        }

        if not (added or removed or updated):
            return

        logger.info(f"Device reload -> added:{added} removed:{removed} updated:{updated}")

        # Build a new dict and swap it in atomically (a single reference
        # assignment) rather than mutating self.devices in place, since
        # scheduler() iterates self.devices.values() concurrently on
        # another thread and an in-place pop()/assignment could raise
        # "dictionary changed size during iteration" there.
        next_devices = dict(self.devices)

        for device_id in removed:
            next_devices.pop(device_id, None)

        for device_id in updated | added:
            next_devices[device_id] = DeviceNode(new_cfgs[device_id], self.modbus)

        self.devices = next_devices
        self._device_cfgs = new_cfgs

    # =========================
    # THREADS
    # =========================
    def scheduler(self):
        while not self.stop_event.is_set():
            cfg = self.config_mgr.get()
            interval = cfg["gateway"]["poll_interval"]

            for d in self.devices.values():
                try:
                    self.queue.put(d, timeout=1)
                except queue.Full:
                    logger.warning("Queue full, dropping task")

            time.sleep(interval)

    def worker(self):
        while not self.stop_event.is_set():
            try:
                device = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                device.poll()
            except Exception as e:
                # Without this, an unexpected exception here kills the
                # worker thread permanently and silently - every device's
                # cache freezes at whatever it last held (possibly empty)
                # with no indication in the logs that polling stopped.
                logger.error(f"Unexpected error polling device {device.id}: {e}")
            finally:
                self.queue.task_done()

    def publisher(self):
        while not self.stop_event.is_set():
            cfg = self.config_mgr.get()

            payload = {
                "ts": datetime.now(UTC).isoformat(),
                "devices": {
                    k: v.snapshot()
                    for k, v in self.devices.items()
                }
            }

            self.mqtt.publish(cfg["aws"]["topic_pub"], payload)

            time.sleep(cfg["gateway"]["poll_interval"])

    def system_publisher(self):
        while not self.stop_event.is_set():
            cfg = self.config_mgr.get()

            payload = get_system_status(cfg["gateway"])

            self.mqtt.publish(cfg["aws"]["topic_system"], payload)

            time.sleep(cfg["gateway"]["system_interval"])

    def config_watcher(self):
        last_mtime = 0

        while not self.stop_event.is_set():
            try:
                mtime = os.path.getmtime(self.config_mgr.path)

                if mtime != last_mtime:
                    logger.info("Reloading config...")
                    cfg = self.config_mgr.reload()

                    # Reconnect Modbus only if its section actually changed.
                    new_modbus_hash = _section_hash(cfg["modbus"])
                    if new_modbus_hash != self._modbus_hash:
                        logger.info("Modbus config changed, reconnecting...")
                        self.modbus.update_config(cfg["modbus"])
                        self._modbus_hash = new_modbus_hash

                    # Reconnect MQTT only if its section actually changed.
                    new_aws_hash = _section_hash(cfg["aws"])
                    if new_aws_hash != self._aws_hash:
                        logger.info("AWS/MQTT config changed, reconnecting...")
                        self.mqtt.update_config(cfg["aws"])
                        self._aws_hash = new_aws_hash

                    self.reload_devices(cfg)
                    last_mtime = mtime

            except Exception as e:
                logger.error(f"Watcher error: {e}")

            time.sleep(5)

    # =========================
    # LIFECYCLE
    # =========================
    def start(self):
        logger.info("Starting Edge Node")

        self.init_system()

        threads = [
            threading.Thread(target=self.scheduler, name="Scheduler"),
            threading.Thread(target=self.worker, name="Worker"),
            threading.Thread(target=self.publisher, name="Publisher"),
            threading.Thread(target=self.system_publisher, name="SystemPublisher"),
            threading.Thread(target=self.config_watcher, name="ConfigWatcher"),
        ]

        for t in threads:
            t.daemon = True
            t.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        logger.info("Stopping Edge Node")
        self.stop_event.set()


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    node = EdgeNode("config.json")
    node.start()
