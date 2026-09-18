import os
import json
import time
import queue
import random
import socket
import hashlib
import logging
import platform
import subprocess
import threading
from datetime import datetime, UTC
from pathlib import Path
from zoneinfo import ZoneInfo
from logging.handlers import RotatingFileHandler

import psutil
from pymodbus.client import ModbusSerialClient

from awscrt import mqtt, io
from awsiot import mqtt_connection_builder

from config.schema import validate as validate_config
from domain.sensors import decode as decode_sensor

# aws.{ca,cert,key} in config.json are conventionally relative (e.g.
# "./certs/AmazonRootCA1.pem", as config_manager.py's aws.csv -> config.json
# build writes them) and meant to resolve against this file's own directory
# - not whatever the process's cwd happens to be at startup - since that's
# where the certs/ directory actually lives. ConfigManager.load() resolves
# them against this before MQTTManager ever opens them, so a manual run
# from the wrong directory (unlike the systemd service, which sets
# WorkingDirectory=) doesn't fail with a confusing "No such file or
# directory" on a path that looked fine relative to the intended directory.
BASE_DIR = Path(__file__).resolve().parent


# =========================
# LOGGER
# =========================
def build_logger():
    """Level defaults to INFO; set AKVO_LOG_LEVEL (DEBUG/INFO/WARNING/
    ERROR/CRITICAL) before starting the process for more (or less) detail
    - e.g. `AKVO_LOG_LEVEL=DEBUG python3 edge_node_improved.py`. At DEBUG,
    SensorNode.read() logs every register read's raw registers, decoded
    value, and alarm result, and the scheduler logs its queue depth each
    cycle - both silent at INFO."""
    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger("EdgeNode")

    level_name = os.environ.get("AKVO_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO
        level_name = "INFO (invalid AKVO_LOG_LEVEL ignored)"
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )

    fh = RotatingFileHandler("logs/system.log", maxBytes=1_000_000, backupCount=5)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Log level: {level_name} (set AKVO_LOG_LEVEL to change, e.g. AKVO_LOG_LEVEL=DEBUG)")

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
            config = json.load(f)

        # Validates against the same schema config_manager.py's build
        # enforces, so a hand-edited config.json that skips
        # config_manager.py entirely is rejected here - with a clear,
        # complete list of problems - instead of surfacing later as
        # whichever thread hits the first missing/malformed field.
        errors, warnings = validate_config(config)
        for warning in warnings:
            logger.warning(warning)
        if errors:
            raise ValueError("Invalid config: " + "; ".join(errors))

        # See BASE_DIR's comment above. An already-absolute path is left
        # untouched: Path(BASE_DIR) / absolute_path collapses to just
        # absolute_path.
        aws = config.get("aws", {})
        for key in ("ca", "cert", "key"):
            path = aws.get(key)
            if path:
                aws[key] = str((BASE_DIR / path).resolve())

        return config

    def get(self):
        with self.lock:
            return self.config

    def reload(self):
        with self.lock:
            self.config = self.load()
            return self.config


def _retry_delay(attempt, base=5, cap=60, jitter=0.3):
    """Exponential backoff with jitter, capped at `cap` seconds.

    Used by MQTTManager/ModbusManager.connect() so a permanently wrong
    cert/host/port doesn't retry every 5s forever - it escalates up to
    `cap` and stays there, with jitter to avoid a reconnect-storm if many
    gateways fail at once. `attempt` is 0 on the first failure.
    """
    delay = min(base * (2 ** min(attempt, 10)), cap)
    return delay * (1 + random.uniform(-jitter, jitter))


_REBOOT_HISTORY_PATH = "logs/reboot_history.json"
_REBOOT_LOOP_WINDOW = 3600  # seconds
_REBOOT_LOOP_LIMIT = 3  # max reboots _default_reboot_fn will trigger per window
_reboot_history_lock = threading.RLock()


def _load_reboot_history(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def _record_reboot_if_allowed(path, window, limit):
    """Persisted, cross-manager reboot-loop guard for _default_reboot_fn -
    the same idea as systemd's StartLimitBurst/StartLimitIntervalSec.

    A reboot restarts this process from scratch, wiping every in-memory
    counter with it, so "how many times have we rebooted recently" has to
    live on disk (`path`) to survive that. One shared file/lock, not one
    per manager: the thing being limited - the whole machine rebooting -
    is global, whichever of Modbus/MQTT/WiFi triggered it.

    Returns (allowed, recent_count) - recent_count excludes the reboot
    just recorded when allowed is True, and is the full count that hit the
    limit when allowed is False. If allowed is False, `limit` reboots
    already happened within the last `window` seconds and the caller
    should NOT reboot again right now.
    """
    with _reboot_history_lock:
        now = time.time()
        history = [t for t in _load_reboot_history(path) if now - t < window]
        if len(history) >= limit:
            return False, len(history)
        recent_count = len(history)
        history.append(now)
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w") as f:
            json.dump(history, f)
        return True, recent_count


def _default_reboot_fn():
    """Default reboot_fn for _RebootEscalator: reboots the whole host via
    `sudo reboot`, not just this process.

    Field deployments of this gateway run bare - no systemd unit, no
    container restart policy, no supervisor of any kind - so exiting the
    process (the old default, os._exit(1)) would just leave it dead with
    nothing to bring it back. Only rebooting the OS itself actually
    recovers a Pi whose network/serial stack has wedged in a way retries
    alone can't clear.

    Guarded by _record_reboot_if_allowed: if a communication layer is down
    for a reason a reboot can't fix (the WiFi router itself is down, not
    the Pi; a serial cable is unplugged), rebooting every reboot_after
    seconds forever would just boot-loop the device. Past
    _REBOOT_LOOP_LIMIT reboots in _REBOOT_LOOP_WINDOW seconds, this gives
    up on rebooting - connect()'s own retry loop keeps running regardless,
    so the gateway still recovers on its own once the real problem clears,
    it just stops trying to fix it by rebooting.

    Requires the user this process runs as to have passwordless sudo for
    `reboot` (or to run as root outright) - set that up as part of
    deployment, it's not handled here. If the reboot command itself can't
    even be started (missing sudo rule, `reboot` not on PATH, etc.), this
    falls back to os._exit(1) so the process at least stops instead of
    silently spinning forever on a broken reboot path.
    """
    allowed, recent_count = _record_reboot_if_allowed(
        _REBOOT_HISTORY_PATH, _REBOOT_LOOP_WINDOW, _REBOOT_LOOP_LIMIT
    )
    if not allowed:
        logger.critical(
            f"Refusing to reboot: {recent_count} reboots already happened in "
            f"the last {_REBOOT_LOOP_WINDOW}s (limit {_REBOOT_LOOP_LIMIT}) - "
            f"a reboot isn't fixing this, so giving up on rebooting and "
            f"letting connect() keep retrying on its own instead"
        )
        return

    logger.critical(
        f"Rebooting the system now (sudo reboot) - reboot {recent_count + 1}/"
        f"{_REBOOT_LOOP_LIMIT} allowed in the last {_REBOOT_LOOP_WINDOW}s"
    )
    try:
        subprocess.run(["sudo", "reboot"], check=True)
    except Exception as e:
        logger.critical(f"System reboot command failed ({e}) - exiting process as a fallback")
        os._exit(1)


class _RebootEscalator:
    """Shared "give up and reboot" bookkeeping for
    ModbusManager/MQTTManager/WifiManager's connect() retry loops.

    Each manager calls reset() when a connect() attempt begins and check()
    on every failed attempt. Once a single connect() call has been failing
    for longer than `reboot_after` seconds, reboot_fn() fires once (default:
    _default_reboot_fn(), which reboots the whole machine - see its
    docstring for why a plain process exit isn't enough in the field).
    reboot_after=None (the default) disables this entirely, matching
    gateway.watchdog_timeout's "falsy = disabled" convention.
    """

    def __init__(self, reboot_after=None, reboot_fn=None):
        self.reboot_after = reboot_after
        self.reboot_fn = reboot_fn or _default_reboot_fn
        self._started = None
        self._fired = False

    def reset(self):
        self._started = time.time()
        self._fired = False

    def check(self, description):
        if not self.reboot_after or self._fired or self._started is None:
            return
        stalled_for = time.time() - self._started
        if stalled_for > self.reboot_after:
            logger.critical(
                f"{description} has not connected in {stalled_for:.0f}s "
                f"(reboot_after={self.reboot_after}s) - rebooting the system"
            )
            self._fired = True
            self.reboot_fn()


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
        # UDP connect() is just a local route lookup (no handshake), so this
        # rarely blocks - but an explicit timeout is cheap insurance against
        # an unusual routing setup hanging this thread indefinitely, which
        # would otherwise stall host-health reporting itself.
        s.settimeout(2.0)
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
# WIFI MANAGER (NETWORK REACHABILITY)
# =========================
class WifiManager:
    """Monitors host network reachability - not any single WiFi SSID/AP,
    just "is there a route to the internet" - since edge_node_improved.py
    otherwise only discovers a dead network indirectly, as repeated MQTT
    connect failures.

    check_fn is injectable (tests pass a fake instead of touching a real
    socket); it defaults to a real TCP connect to
    gateway.wifi_check_host/wifi_check_port (Google DNS, port 53, by
    default) which - unlike a UDP "connect" (see get_system_status()'s IP
    lookup, which never actually sends a packet) - performs a real
    handshake and fails when there's no route out.
    """

    def __init__(self, cfg, check_fn=None, reboot_fn=None):
        self.check_host = cfg.get("wifi_check_host", "8.8.8.8")
        self.check_port = cfg.get("wifi_check_port", 53)
        self.check_timeout = cfg.get("wifi_check_timeout", 2.0)
        self._check_fn = check_fn or self._default_check
        self.connected = False
        self.lock = threading.RLock()
        self._reboot = _RebootEscalator(cfg.get("wifi_reboot_timeout"), reboot_fn)

    def _default_check(self):
        try:
            socket.create_connection(
                (self.check_host, self.check_port), timeout=self.check_timeout
            ).close()
            return True
        except OSError:
            return False

    def is_reachable(self):
        return self._check_fn()

    def connect(self):
        attempt = 0
        self._reboot.reset()
        while True:
            if self.is_reachable():
                with self.lock:
                    self.connected = True
                logger.info("WiFi connected")
                return

            delay = _retry_delay(attempt)
            logger.error(f"WiFi unreachable - retrying in {delay:.1f}s")
            self._reboot.check("WiFi")
            time.sleep(delay)
            attempt += 1

    def disconnect(self):
        """Marks the link as down. There's no socket to close at this level
        (see class docstring) - this exists for symmetry with
        Modbus/MQTTManager.disconnect() and so EdgeNode.stop() can report a
        clean state instead of a stale 'connected' one."""
        with self.lock:
            self.connected = False
        logger.info("WiFi disconnected")

    def monitor(self, stop_event, interval=5):
        """Daemon-thread loop: notices a drop and blocks in connect() (retry
        + reboot escalation) until reachable again, the same
        detect-on-use-then-self-heal pattern Modbus/MQTTManager already use
        via read_holding_registers()/publish() - WiFi has no such "use" to
        piggyback on, so this polls instead."""
        while not stop_event.is_set():
            try:
                if self.connected and not self.is_reachable():
                    with self.lock:
                        self.connected = False
                    logger.warning("WiFi disconnected")
                    self.connect()
            except Exception as e:
                logger.error(f"WiFi monitor error: {e}")
            time.sleep(interval)


# =========================
# MQTT MANAGER (RECONNECT SAFE)
# =========================
class MQTTManager:
    def __init__(self, cfg, reboot_after=None, reboot_fn=None):
        self.cfg = cfg
        self.connection = None
        # Single lock guards BOTH connect() and publish() so a failed
        # publish on one thread can't race a concurrent reconnect from
        # another thread (e.g. publisher + system_publisher both failing
        # at once).
        self.lock = threading.RLock()
        self._reboot = _RebootEscalator(reboot_after, reboot_fn)
        # Built once and reused for every connect() attempt over this
        # manager's whole lifetime (including retries and update_config()'s
        # reconnects) - a ClientBootstrap/EventLoopGroup is meant to serve
        # many connections, not be rebuilt per attempt. The previous code
        # built a fresh pair inside the connect() retry loop, so a slow
        # AWS endpoint or bad cert leaked one more EventLoopGroup's worth of
        # native I/O threads per retry, indefinitely - those aren't Python
        # daemon threads and don't reliably die with the interpreter, which
        # is a large part of why a killed edge node process could still
        # linger holding the (unrelated, but same-process) serial port open.
        self._bootstrap = io.ClientBootstrap(
            io.EventLoopGroup(1),
            io.DefaultHostResolver(io.EventLoopGroup(1))
        )

    def connect(self):
        attempt = 0
        start = time.time()
        self._reboot.reset()
        while True:
            try:
                logger.info("Connecting MQTT...")

                connection = mqtt_connection_builder.mtls_from_path(
                    endpoint=self.cfg["host"],
                    cert_filepath=self.cfg["cert"],
                    pri_key_filepath=self.cfg["key"],
                    ca_filepath=self.cfg["ca"],
                    client_id=self.cfg["client_id"],
                    client_bootstrap=self._bootstrap,
                    keep_alive_secs=60,
                    clean_session=False
                )

                connection.connect().result()
                with self.lock:
                    self.connection = connection
                elapsed = time.time() - start
                if attempt:
                    logger.info(f"MQTT connected (after {attempt} retries, {elapsed:.1f}s)")
                else:
                    logger.info("MQTT connected")
                return

            except Exception as e:
                delay = _retry_delay(attempt)
                logger.error(f"MQTT connect failed: {e} - retrying in {delay:.1f}s")
                self._reboot.check("MQTT")
                time.sleep(delay)
                attempt += 1

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

    def disconnect(self):
        """Cleanly close the connection. Called from EdgeNode.stop() so AWS
        IoT sees a proper DISCONNECT instead of an unclean session drop."""
        with self.lock:
            connection = self.connection
            self.connection = None
        if connection is not None:
            try:
                connection.disconnect().result()
                logger.info("MQTT disconnected")
            except Exception as e:
                logger.warning(f"MQTT disconnect failed: {e}")


# =========================
# MODBUS MANAGER (RETRY SAFE)
# =========================
class ModbusManager:
    def __init__(self, cfg, reboot_after=None, reboot_fn=None):
        self.cfg = cfg
        self.client = None
        # Serial pymodbus clients are not thread-safe; every read/connect
        # against self.client must go through this lock.
        self.lock = threading.RLock()
        self._reboot = _RebootEscalator(reboot_after, reboot_fn)

    def connect(self):
        attempt = 0
        start = time.time()
        self._reboot.reset()
        while True:
            try:
                # Logged before the attempt (not just on failure/success) so
                # a slow/hung ModbusSerialClient(...).connect() call - e.g.
                # a busy or misbehaving serial port - still leaves a log
                # line marking exactly when this attempt began, instead of
                # a silent gap with nothing to anchor "how long has this
                # been stuck" to.
                logger.info(f"Connecting Modbus ({self.cfg.get('port')})...")
                client = ModbusSerialClient(**self.cfg)
                if client.connect():
                    with self.lock:
                        self.client = client
                    elapsed = time.time() - start
                    if attempt:
                        logger.info(f"Modbus connected (after {attempt} retries, {elapsed:.1f}s)")
                    else:
                        logger.info("Modbus connected")
                    return
                else:
                    raise Exception("Connection failed")

            except Exception as e:
                delay = _retry_delay(attempt)
                logger.error(f"Modbus connect error: {e} - retrying in {delay:.1f}s")
                self._reboot.check("Modbus")
                time.sleep(delay)
                attempt += 1

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

    def disconnect(self):
        """Cleanly close the serial port. Called from EdgeNode.stop()."""
        with self.lock:
            client = self.client
            self.client = None
        if client is not None:
            try:
                client.close()
                logger.info("Modbus disconnected")
            except Exception as e:
                logger.warning(f"Modbus close failed: {e}")


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
        name = self.cfg.get("name")
        addr = self.cfg.get("addr")
        try:
            res = self.modbus_mgr.read_holding_registers(
                address=self.cfg["addr"], count=self.cfg["count"], device_id=self.slave
            )

            if res.isError():
                logger.debug(f"Sensor {name} (slave={self.slave}, addr={addr}): BUS_ERROR")
                return {"status": "BUS_ERROR"}

            sensor_type = self.cfg.get("type", "int")
            val = decode_sensor(
                sensor_type,
                res.registers,
                scale=self.cfg.get("scale", 1),
                offset=self.cfg.get("offset", 0),
            )

            alarm = self._evaluate_alarm(val)
            logger.debug(
                f"Sensor {name} (slave={self.slave}, addr={addr}, type={sensor_type}): "
                f"registers={res.registers} -> val={val} alarm={alarm}"
            )
            return {"val": val, "status": "OK", "alarm": alarm}

        except Exception as e:
            # Silent at INFO - s.read()'s caller (DeviceNode.poll()) already
            # has its own guard for genuinely unexpected errors, so this is
            # the expected path for a bad register/config combo (e.g. a
            # multi-register type whose count didn't survive a hand-edited
            # config.json) - visible on demand rather than by default.
            logger.debug(f"Sensor {name} (slave={self.slave}, addr={addr}): EXCEPTION {e}")
            return {"status": "EXCEPTION", "err": str(e)}


class DeviceNode:
    def __init__(self, cfg, modbus_mgr):
        self.id = cfg["id"]
        self.sensors = [
            SensorNode(s, cfg["slave"], modbus_mgr)
            for s in cfg["sensors"]
        ]
        self.cache = {}
        # Last alarm value seen per sensor name, so _evaluate_rules() can
        # log on transition rather than re-logging every single cycle a
        # value stays out of range.
        self._last_alarms = {}

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
            previous = self._last_alarms.get(name)

            if alarm != previous:
                if alarm in ("HIGH", "LOW"):
                    logger.warning(f"[ALERT] {self.id}.{name} {alarm}")
                elif previous in ("HIGH", "LOW"):
                    status_note = f" (status={data.get('status')})" if alarm is None else ""
                    logger.info(f"[CLEAR] {self.id}.{name} no longer {previous}{status_note}")

            self._last_alarms[name] = alarm

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
        self.wifi = None

        # Track hashes of the sub-sections that require a reconnect
        # (rather than just rebuilding the device list) when changed.
        self._modbus_hash = None
        self._aws_hash = None
        # Last-seen device configs, keyed by id, for partial-reload diffing.
        self._device_cfgs = {}

        # Updated by worker() every loop iteration (whether or not a device
        # was actually polled that tick). watchdog() checks this - a stall
        # here means the worker thread is genuinely stuck (e.g. blocked
        # inside a library call that never returns), not just reporting
        # errors, since per-device failures are already caught and recorded
        # by SensorNode.read()/DeviceNode.poll() without stopping the loop.
        self._last_heartbeat = time.time()
        # Same _RebootEscalator the communication managers use - watchdog()
        # calls reset() whenever _last_heartbeat advances and check() every
        # tick, so _started tracks "time of last heartbeat" and this fires
        # (once, via the same reboot-loop-guarded _default_reboot_fn) only
        # once a stall has actually lasted past watchdog_timeout.
        self._watchdog_reboot = _RebootEscalator()
        self._watchdog_last_seen_heartbeat = self._last_heartbeat

    def init_system(self):
        cfg = self.config_mgr.get()
        gw = cfg.get("gateway", {})

        self.modbus = ModbusManager(cfg["modbus"], reboot_after=gw.get("modbus_reboot_timeout"))
        self.mqtt = MQTTManager(cfg["aws"], reboot_after=gw.get("aws_reboot_timeout"))
        self.wifi = WifiManager(gw)

        # Connect Modbus, WiFi, and MQTT concurrently. Both Modbus's and
        # WiFi's connect() calls retry forever on failure - previously
        # Modbus ran sequentially before MQTT, so a stuck/unreachable
        # Modbus serial port (wrong port, permission denied, device
        # unplugged) blocked MQTT from ever connecting, and nothing
        # reached AWS even though AWS itself was reachable. Now a dead
        # serial bus only blocks sensor data, not system-health reporting
        # or AWS connectivity.
        modbus_thread = threading.Thread(
            target=self.modbus.connect, name="ModbusConnect", daemon=True
        )
        modbus_thread.start()

        wifi_thread = threading.Thread(
            target=self.wifi.connect, name="WifiConnect", daemon=True
        )
        wifi_thread.start()

        # Only block startup on MQTT. Modbus and WiFi keep retrying forever
        # in the background - if the serial bus is down/misconfigured,
        # sensor reads will just come back EXCEPTION/BUS_ERROR until it
        # connects, but AWS reporting (including system_publisher's
        # host-health payload) starts immediately regardless (and MQTT's
        # own connect() retries forever too, so a dead network doesn't
        # block startup here either - it just means this call takes a
        # while).
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
            interval = 5  # fallback if config access below fails
            try:
                cfg = self.config_mgr.get()
                interval = cfg["gateway"]["poll_interval"]

                for d in self.devices.values():
                    try:
                        self.queue.put(d, timeout=1)
                    except queue.Full:
                        logger.warning("Queue full, dropping task")
                logger.debug(
                    f"Scheduler: queued {len(self.devices)} device(s), "
                    f"qsize={self.queue.qsize()}, next in {interval}s"
                )
            except Exception as e:
                # Without this, a bad/missing config key here (e.g. during
                # a hand-edited config.json) kills the scheduler thread
                # permanently and silently - polling just stops forever
                # with nothing in the logs to explain why.
                logger.error(f"Scheduler error: {e}")

            time.sleep(interval)

    def worker(self):
        while not self.stop_event.is_set():
            try:
                device = self.queue.get(timeout=1)
            except queue.Empty:
                self._last_heartbeat = time.time()
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
                self._last_heartbeat = time.time()

    def publisher(self):
        while not self.stop_event.is_set():
            interval = 5  # fallback if config access below fails
            try:
                cfg = self.config_mgr.get()
                interval = cfg["gateway"]["poll_interval"]

                payload = {
                    "ts": datetime.now(UTC).isoformat(),
                    "devices": {
                        k: v.snapshot()
                        for k, v in self.devices.items()
                    }
                }

                self.mqtt.publish(cfg["aws"]["topic_pub"], payload)
            except Exception as e:
                # See scheduler() - same reasoning: don't let one bad cycle
                # permanently kill the thread that reports device data.
                logger.error(f"Publisher error: {e}")

            time.sleep(interval)

    def system_publisher(self):
        while not self.stop_event.is_set():
            interval = 5  # fallback if config access below fails
            try:
                cfg = self.config_mgr.get()
                interval = cfg["gateway"]["system_interval"]

                payload = get_system_status(cfg["gateway"])

                self.mqtt.publish(cfg["aws"]["topic_system"], payload)
            except Exception as e:
                # See scheduler() - same reasoning: don't let one bad cycle
                # permanently kill host-health reporting.
                logger.error(f"System publisher error: {e}")

            time.sleep(interval)

    def config_watcher(self):
        last_mtime = 0

        while not self.stop_event.is_set():
            try:
                mtime = os.path.getmtime(self.config_mgr.path)

                if mtime != last_mtime:
                    reload_start = time.time()
                    logger.info("Reloading config...")
                    cfg = self.config_mgr.reload()

                    # Reconnect Modbus only if its section actually changed.
                    new_modbus_hash = _section_hash(cfg["modbus"])
                    if new_modbus_hash != self._modbus_hash:
                        # update_config() blocks this thread (retries
                        # forever - see ModbusManager.connect()) until the
                        # new settings connect, so no further config
                        # changes are picked up and reload_devices() below
                        # is delayed until it returns. Called out explicitly
                        # here since that stall otherwise looks identical to
                        # a hang, with only ModbusManager's own per-attempt
                        # logs to go on.
                        logger.info(
                            "Modbus config changed, reconnecting "
                            "(config watcher blocks until this succeeds)..."
                        )
                        self.modbus.update_config(cfg["modbus"])
                        self._modbus_hash = new_modbus_hash

                    # Reconnect MQTT only if its section actually changed.
                    new_aws_hash = _section_hash(cfg["aws"])
                    if new_aws_hash != self._aws_hash:
                        logger.info(
                            "AWS/MQTT config changed, reconnecting "
                            "(config watcher blocks until this succeeds)..."
                        )
                        self.mqtt.update_config(cfg["aws"])
                        self._aws_hash = new_aws_hash

                    self.reload_devices(cfg)
                    last_mtime = mtime
                    logger.info(
                        f"Config reload finished in {time.time() - reload_start:.1f}s"
                    )

            except Exception as e:
                logger.error(f"Watcher error: {e}")

            time.sleep(5)

    def watchdog(self):
        """Reboots the system if the worker thread stops making progress.

        gateway.watchdog_timeout was previously loaded from config and
        never used. This is a last resort, not a substitute for the
        per-thread try/except guards elsewhere: those recover from
        expected failures (bad reads, bad config values) without missing
        a beat, whereas this only fires when worker() has stopped
        advancing entirely - e.g. blocked inside a library call that
        never returns.

        Uses the same _RebootEscalator (and by extension the same
        reboot-loop-guarded _default_reboot_fn) as ModbusManager/
        MQTTManager/WifiManager: reset() is called whenever
        _last_heartbeat actually advances, so _watchdog_reboot._started
        tracks "time of last heartbeat" the same way it tracks "time a
        connect() attempt began" for the other three - and check() fires
        (once per stall, not once per 5s tick) only once that has been
        stalled longer than watchdog_timeout. There's no external process
        supervisor in the field to fall back on if this merely exited, so
        this reboots the whole machine, same reasoning as the other
        three - a graceful shutdown could hang too if the process is
        genuinely stuck, which is also why _default_reboot_fn's
        subprocess.run(["sudo", "reboot"]) doesn't try to clean up first.
        """
        while not self.stop_event.is_set():
            try:
                if self._last_heartbeat != self._watchdog_last_seen_heartbeat:
                    self._watchdog_last_seen_heartbeat = self._last_heartbeat
                    self._watchdog_reboot.reset()

                timeout = self.config_mgr.get().get("gateway", {}).get("watchdog_timeout")
                self._watchdog_reboot.reboot_after = timeout
                self._watchdog_reboot.check("Worker")
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

            time.sleep(5)

    # =========================
    # LIFECYCLE
    # =========================
    def start(self):
        logger.info("Starting Edge Node")

        # init_system() is inside this try, not just the idle loop below -
        # it blocks on self.mqtt.connect() (retries forever) before any
        # thread exists, so a Ctrl+C landing during that first connect used
        # to propagate straight out of start() without ever calling stop():
        # self.modbus/self.wifi's already-open connections (init_system()
        # assigns self.modbus/self.mqtt/self.wifi before that blocking call)
        # were simply abandoned instead of disconnected.
        try:
            self.init_system()

            threads = [
                threading.Thread(target=self.scheduler, name="Scheduler"),
                threading.Thread(target=self.worker, name="Worker"),
                threading.Thread(target=self.publisher, name="Publisher"),
                threading.Thread(target=self.system_publisher, name="SystemPublisher"),
                threading.Thread(target=self.config_watcher, name="ConfigWatcher"),
                threading.Thread(target=self.watchdog, name="Watchdog"),
                threading.Thread(
                    target=self.wifi.monitor, args=(self.stop_event,), name="WifiMonitor"
                ),
            ]

            for t in threads:
                t.daemon = True
                t.start()

            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        logger.info("Stopping Edge Node")
        self.stop_event.set()

        # Close both connections cleanly instead of just abandoning them -
        # otherwise AWS IoT sees an unclean session drop rather than a
        # proper disconnect, and the serial port is never released.
        if self.mqtt is not None:
            self.mqtt.disconnect()
        if self.modbus is not None:
            self.modbus.disconnect()
        if self.wifi is not None:
            self.wifi.disconnect()


# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    node = EdgeNode("config_data/config.json")
    node.start()
