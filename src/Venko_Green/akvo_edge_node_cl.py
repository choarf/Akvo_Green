import os
import json
import time
import queue
import hashlib
import logging
import threading
from datetime import datetime, UTC
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
        with self.lock:
            while True:
                try:
                    logger.info("Connecting MQTT...")

                    bootstrap = io.ClientBootstrap(
                        io.EventLoopGroup(1),
                        io.DefaultHostResolver(io.EventLoopGroup(1))
                    )

                    self.connection = mqtt_connection_builder.mtls_from_path(
                        endpoint=self.cfg["host"],
                        cert_filepath=self.cfg["cert"],
                        pri_key_filepath=self.cfg["key"],
                        ca_filepath=self.cfg["ca"],
                        client_id=self.cfg["client_id"],
                        client_bootstrap=bootstrap,
                        keep_alive_secs=60,
                        clean_session=False
                    )

                    self.connection.connect().result()
                    logger.info("MQTT connected")
                    return

                except Exception as e:
                    logger.error(f"MQTT connect failed: {e}")
                    time.sleep(5)

    def update_config(self, cfg):
        """Called by config_watcher when the 'aws' section changes.
        Reconnects using the new settings."""
        with self.lock:
            self.cfg = cfg
            try:
                if self.connection is not None:
                    self.connection.disconnect().result()
            except Exception as e:
                logger.warning(f"MQTT disconnect before reconnect failed: {e}")
        self.connect()

    def publish(self, topic, payload):
        with self.lock:
            try:
                self.connection.publish(
                    topic=topic,
                    payload=json.dumps(payload),
                    qos=mqtt.QoS.AT_LEAST_ONCE
                )
                return
            except Exception as e:
                logger.error(f"MQTT publish failed: {e}")
            # Reconnect while still holding the lock, so a second thread
            # calling publish() blocks here instead of independently
            # kicking off its own reconnect.
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
        with self.lock:
            while True:
                try:
                    self.client = ModbusSerialClient(**self.cfg)
                    if self.client.connect():
                        logger.info("Modbus connected")
                        return
                    else:
                        raise Exception("Connection failed")

                except Exception as e:
                    logger.error(f"Modbus connect error: {e}")
                    time.sleep(5)

    def update_config(self, cfg):
        """Called by config_watcher when the 'modbus' section changes."""
        with self.lock:
            try:
                if self.client is not None:
                    self.client.close()
            except Exception as e:
                logger.warning(f"Modbus close before reconnect failed: {e}")
            self.cfg = cfg
        self.connect()

    def read_holding_registers(self, address, count, device_id):
        """Thread-safe wrapper other code should call instead of touching
        self.client directly."""
        with self.lock:
            return self.client.read_holding_registers(
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

    def read(self):
        try:
            res = self.modbus_mgr.read_holding_registers(
                address=self.cfg["addr"], count=self.cfg["count"], device_id=self.slave
            )

            if res.isError():
                return {"status": "BUS_ERROR"}

            val = res.registers[0]

            if self.cfg.get("type") == "float":
                val = val * self.cfg.get("scale", 1)

            return {"val": val, "status": "OK"}

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
            self.cache[s.cfg["name"]] = s.read()

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

    def init_system(self):
        cfg = self.config_mgr.get()

        self.modbus = ModbusManager(cfg["modbus"])
        self.modbus.connect()
        self._modbus_hash = _section_hash(cfg["modbus"])

        self.mqtt = MQTTManager(cfg["aws"])
        self.mqtt.connect()
        self._aws_hash = _section_hash(cfg["aws"])

        self.build_devices(cfg)

    def build_devices(self, cfg):
        self.devices = {
            d["id"]: DeviceNode(d, self.modbus)
            for d in cfg["devices"]
        }

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
                device.poll()
                self.queue.task_done()
            except queue.Empty:
                continue

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

            payload = {
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
                "ts": datetime.now(UTC).isoformat(),
            }

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

                    self.build_devices(cfg)
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
