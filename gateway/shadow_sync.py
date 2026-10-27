"""Config delivery through the AWS IoT named shadow "config".

The cloud side (VenkoDemo tools/push_shadow.py) writes the site's committed
config.json - only the parts in SHADOW_SECTIONS/SHADOW_KEYS - as the
shadow's "desired" state. This module, running inside the gateway, fetches
that desired state on every (re)connect and on every delta, merges it into
the local config.json, validates the result with config/schema.py, and
writes it atomically. The existing EdgeNode.config_watcher then reloads it
like any other config.json change. "reported" carries only the hash of
what is running, never a copy of the config (shadow state documents are
capped at 8 KB).

'aws' and the serial settings in 'modbus' are never taken from the shadow:
a bad endpoint, cert path or serial port pushed remotely would cut the Pi
off with no remote way back. Of 'modbus' only 'simulate' is accepted.

Settings EdgeNode reads only at start-up (RESTART_KEYS) are applied to the
file and then the gateway restarts itself through restart_fn (systemd's
Restart=on-failure brings it back).

The helpers above ShadowSync (shadow_subset, config_hash, ...) import no
AWS library, so push_shadow.py imports them to compute the exact same hash.
"""

import copy
import hashlib
import json
import logging
import os
import queue
import tempfile
import threading
from datetime import datetime, timezone

from config.schema import validate as validate_config

logger = logging.getLogger("EdgeNode")

SHADOW_NAME = "config"

# Whole sections taken from the shadow.
SHADOW_SECTIONS = ("meta", "gateway", "devices", "database", "web", "display", "wifi")
# Single keys of sections that otherwise stay local.
SHADOW_KEYS = {"modbus": ("simulate",)}

# (section, key or None for the whole section) that EdgeNode reads only at
# start-up, so a change needs a gateway restart to take effect.
RESTART_KEYS = (("modbus", "simulate"), ("database", None))

# Read at start-up by the separate akvo-history-web service, which the
# gateway can't restart (push_site.sh does).
OTHER_SERVICE_SECTIONS = ("web",)

# Exit code for a restart the gateway asks for itself (any non-zero exit
# makes systemd's Restart=on-failure start it again).
RESTART_EXIT_CODE = 75


def shadow_subset(config):
    """The part of a config.json that travels through the shadow."""
    out = {s: config[s] for s in SHADOW_SECTIONS if s in config}
    for section, keys in SHADOW_KEYS.items():
        part = {k: config[section][k] for k in keys if k in config.get(section, {})}
        if part:
            out[section] = part
    return out


def config_hash(config):
    """sha256 of shadow_subset(config), key order independent."""
    data = json.dumps(shadow_subset(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def merge_desired(current, desired):
    """current config.json with the shadow-managed parts of `desired`
    replacing their counterparts. Returns a new dict."""
    new = copy.deepcopy(current)
    for s in SHADOW_SECTIONS:
        if s in desired:
            new[s] = copy.deepcopy(desired[s])
    for section, keys in SHADOW_KEYS.items():
        part = desired.get(section)
        if isinstance(part, dict):
            for k in keys:
                if k in part:
                    new.setdefault(section, {})[k] = part[k]
    return new


def ignored_keys(desired):
    """Keys of `desired` the gateway won't take - logged, so a push that
    tries to change aws/serial settings doesn't fail silently."""
    out = []
    for key, value in desired.items():
        if key in SHADOW_SECTIONS or key == "config_hash":
            continue
        allowed = SHADOW_KEYS.get(key, ())
        if isinstance(value, dict) and allowed:
            out += [f"{key}.{k}" for k in value if k not in allowed]
        else:
            out.append(key)
    return out


def restart_needed(old, new):
    """The RESTART_KEYS whose value differs between two configs."""
    changed = []
    for section, key in RESTART_KEYS:
        a, b = old.get(section, {}), new.get(section, {})
        if key is not None:
            a, b = a.get(key), b.get(key)
        if a != b:
            changed.append(section if key is None else f"{section}.{key}")
    return changed


def write_json_atomic(path, data):
    """Temp file in the same directory, fsync, rename: config_watcher (or a
    power cut) never sees a half-written config.json."""
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        # mkstemp makes the file 0600; keep the old file's mode so the
        # dashboard and OLED services can still read it.
        try:
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


class ShadowSync:
    """Keeps config.json in step with the "config" shadow's desired state.

    AWS library callbacks only queue work; one daemon thread does the
    subscribing, file I/O and reporting, so nothing blocks the AWS event
    loop (same rule as MQTTManager._on_interrupted)."""

    def __init__(self, mqtt_mgr, config_path, thing, restart_fn=None, timeout=10):
        self.mqtt = mqtt_mgr
        self.path = config_path
        self.thing = thing
        self.restart_fn = restart_fn
        self.timeout = timeout
        self.base = f"$aws/things/{thing}/shadow/name/{SHADOW_NAME}"
        self.queue = queue.Queue()
        self.thread = None
        self.last_error = None
        self.last_applied = None

    # --- called from other threads -------------------------------------
    def start(self):
        self.thread = threading.Thread(target=self._run, name="ShadowSync", daemon=True)
        self.thread.start()
        return self.thread

    def on_connected(self):
        """Registered with MQTTManager: after every connect and resume."""
        self.queue.put(("connected", None))

    def _on_get_accepted(self, topic, payload, **kwargs):
        self.queue.put(("document", payload))

    def _on_get_rejected(self, topic, payload, **kwargs):
        self.queue.put(("rejected", payload))

    def _on_delta(self, topic, payload, **kwargs):
        self.queue.put(("delta", payload))

    # --- worker thread --------------------------------------------------
    def _run(self):
        while True:
            kind, payload = self.queue.get()
            try:
                self.handle(kind, payload)
            except Exception as e:
                logger.error(f"Shadow sync error ({kind}): {e}")

    def handle(self, kind, payload):
        if kind == "connected":
            self._subscribe()
            self._report(self._current_state())
            self._request_document()
        elif kind == "delta":
            # A delta holds only what differs; the merge needs the whole
            # desired document.
            self._request_document()
        elif kind == "document":
            desired = json.loads(payload).get("state", {}).get("desired")
            if desired:
                self.apply(desired)
        elif kind == "rejected":
            err = json.loads(payload)
            if err.get("code") == 404:
                logger.info("Config shadow not created yet - keeping the local config.json")
            else:
                logger.warning(f"Config shadow get rejected: {err}")

    def _subscribe(self):
        for suffix, callback in (("update/delta", self._on_delta),
                                 ("get/accepted", self._on_get_accepted),
                                 ("get/rejected", self._on_get_rejected)):
            future = self.mqtt.subscribe(f"{self.base}/{suffix}", callback)
            if future is not None:
                future.result(self.timeout)

    def _request_document(self):
        self.mqtt.publish(f"{self.base}/get", {})

    def _read(self):
        with open(self.path) as f:
            return json.load(f)

    def _current_state(self, config=None):
        # None deletes a key from "reported" (shadow merge semantics), which
        # is what clears an old error or restart note. applied_at is left
        # out rather than None after a restart, so the last one stays.
        config = config if config is not None else self._read()
        state = {"config_hash": config_hash(config),
                 "git_rev": config.get("meta", {}).get("git_rev"),
                 "simulate": bool(config.get("modbus", {}).get("simulate")),
                 "error": self.last_error,
                 "restart_for": None,
                 "pending_service_restart": None}
        if self.last_applied:
            state["applied_at"] = self.last_applied
        return state

    def apply(self, desired):
        """Merge, validate, write, report. Returns True if the file changed."""
        ignored = ignored_keys(desired)
        if ignored:
            logger.warning(f"Config shadow: ignoring keys the gateway never takes remotely: {ignored}")

        current = self._read()
        new = merge_desired(current, desired)
        new_hash = config_hash(new)
        if new_hash == config_hash(current):
            if self.last_error:
                self.last_error = None
                self._report(self._current_state(current))
            return False

        expected = desired.get("config_hash")
        if expected and expected != new_hash:
            logger.warning(f"Config shadow: hash of the merged config ({new_hash[:12]}) differs "
                           f"from desired.config_hash ({expected[:12]})")

        # load() resolves cert paths in place; validate a copy so the file
        # keeps its relative paths.
        errors, _ = validate_config(copy.deepcopy(new))
        if errors:
            self.last_error = "; ".join(errors)[:1000]
            logger.error(f"Config shadow: desired config refused, keeping the current one: {self.last_error}")
            self._report(self._current_state(current))
            return False

        write_json_atomic(self.path, new)
        self.last_error = None
        self.last_applied = datetime.now(timezone.utc).isoformat()
        restart = restart_needed(current, new)
        others = [s for s in OTHER_SERVICE_SECTIONS if current.get(s) != new.get(s)]
        logger.info(f"Config shadow: applied {new_hash[:12]} (git {new.get('meta', {}).get('git_rev')})")
        if others:
            logger.warning(f"Config shadow: {others} changed - read by another service at its start; "
                           f"restart it (push_site.sh does) for the change to take effect")

        state = self._current_state(new)
        state["restart_for"] = restart or None
        state["pending_service_restart"] = others or None
        if restart and self.restart_fn is not None:
            # Wait for the report to reach AWS before the process exits.
            future = self._report(state)
            if future is not None:
                try:
                    future.result(self.timeout)
                except Exception as e:
                    logger.warning(f"Config shadow: report before restart not confirmed: {e}")
            logger.warning(f"Config shadow: {restart} changed - restarting the gateway")
            self.restart_fn()
        else:
            self._report(state)
        return True

    def _report(self, state):
        return self.mqtt.publish(f"{self.base}/update", {"state": {"reported": state}})
