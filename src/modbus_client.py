"""
AKVO Modbus Tool
modbus_client.py

Production V3 Modbus RTU client.

Design goals:
- RTU only
- Thread-safe serial/Modbus operations
- Clean public API compatible with the existing AKVO GUI
- PyModbus device_id API
- Centralized error handling
- Correct success/error statistics
- Response-time measurement
- Connection/reconnection helpers
- Input validation
- Context-manager support
"""

from __future__ import annotations

from threading import RLock
import time
from typing import Any, Callable, Sequence

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException, ModbusIOException

from utils.logger import get_logger


logger = get_logger(__name__)


class ModbusClient:
    """Thread-safe production wrapper around PyModbus RTU."""

    MODBUS_EXCEPTIONS = {
        1: "Illegal Function",
        2: "Illegal Data Address",
        3: "Illegal Data Value",
        4: "Slave Device Failure",
        5: "Acknowledge",
        6: "Slave Device Busy",
        8: "Memory Parity Error",
        10: "Gateway Path Unavailable",
        11: "Gateway Target Failed to Respond",
    }

    DEFAULT_SETTINGS = {
        "port": "COM1",
        "baudrate": 9600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 1.0,
    }

    def __init__(self) -> None:
        self._lock = RLock()
        self.client: ModbusSerialClient | None = None
        self.connected = False

        self.settings = self.DEFAULT_SETTINGS.copy()

        self.stats = {
            "requests": 0,
            "successes": 0,
            "errors": 0,
            "last_response_ms": 0.0,
        }

        self.last_error = ""

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(
        self,
        port: str,
        baudrate: int = 9600,
        parity: str = "N",
        stopbits: int | float = 1,
        bytesize: int = 8,
        timeout: float = 1.0,
    ) -> bool:
        """Open a Modbus RTU serial connection."""

        self._validate_connection_settings(
            port, baudrate, parity, stopbits, bytesize, timeout
        )

        with self._lock:
            self._disconnect_locked()

            self.settings.update(
                {
                    "port": port,
                    "baudrate": baudrate,
                    "parity": parity,
                    "stopbits": stopbits,
                    "bytesize": bytesize,
                    "timeout": timeout,
                }
            )

            try:
                client = ModbusSerialClient(
                    port=port,
                    baudrate=baudrate,
                    parity=parity,
                    stopbits=stopbits,
                    bytesize=bytesize,
                    timeout=timeout,
                )

                connected = bool(client.connect())

                if not connected:
                    self.connected = False
                    self.client = None
                    self.last_error = f"Unable to connect to {port}"
                    logger.warning(self.last_error)
                    return False

                self.client = client
                self.connected = True
                self.clear_last_error()
                self.reset_statistics(log=False)

                logger.info(
                    "Connected | Port=%s Baud=%s Parity=%s Stop=%s Bits=%s Timeout=%.3f",
                    port,
                    baudrate,
                    parity,
                    stopbits,
                    bytesize,
                    timeout,
                )
                return True

            except Exception as ex:
                self.client = None
                self.connected = False
                self.last_error = str(ex)
                logger.exception("Connection failed | Port=%s", port)
                return False

    def disconnect(self) -> None:
        """Close the current serial connection."""

        with self._lock:
            self._disconnect_locked()

    def _disconnect_locked(self) -> None:
        """Disconnect. Caller must hold _lock."""

        if self.client is None:
            self.connected = False
            return

        try:
            self.client.close()
            logger.info("Serial port closed")
        except Exception as ex:
            self.last_error = str(ex)
            logger.exception("Disconnect failed")
        finally:
            self.connected = False
            self.client = None

    def reconnect(self) -> bool:
        """Reconnect using the previously stored settings."""

        with self._lock:
            settings = self.settings.copy()

        logger.info("Reconnecting | Port=%s", settings["port"])
        return self.connect(**settings)

    def ensure_connection(self) -> bool:
        """Return True when connected; otherwise attempt one reconnect."""

        with self._lock:
            if self.connected and self.client is not None:
                return True

        logger.warning("Connection lost. Reconnecting...")
        return self.reconnect()

    def is_connected(self) -> bool:
        """Return current connection state."""

        with self._lock:
            return self.connected and self.client is not None

    def is_port_open(self) -> bool:
        """Backward-compatible alias for is_connected()."""

        return self.is_connected()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_connection_settings(
        port: str,
        baudrate: int,
        parity: str,
        stopbits: int | float,
        bytesize: int,
        timeout: float,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Serial port must be a non-empty string.")

        if baudrate <= 0:
            raise ValueError("Baudrate must be greater than zero.")

        if parity.upper() not in {"N", "E", "O"}:
            raise ValueError("Parity must be N, E, or O.")

        if stopbits not in {1, 1.0, 2, 2.0}:
            raise ValueError("Stopbits must be 1 or 2.")

        if bytesize not in {5, 6, 7, 8}:
            raise ValueError("Bytesize must be between 5 and 8.")

        if timeout <= 0:
            raise ValueError("Timeout must be greater than zero.")

    @staticmethod
    def _validate_slave(slave: int) -> None:
        if not isinstance(slave, int) or not 1 <= slave <= 247:
            raise ValueError("Slave ID must be an integer from 1 to 247.")

    @staticmethod
    def _validate_address(address: int) -> None:
        if not isinstance(address, int) or not 0 <= address <= 65535:
            raise ValueError("Modbus address must be an integer from 0 to 65535.")

    @staticmethod
    def _validate_count(count: int) -> None:
        if not isinstance(count, int) or count <= 0:
            raise ValueError("Count must be a positive integer.")

    @staticmethod
    def _validate_register_values(values: Sequence[int]) -> list[int]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("Register values must be a sequence of integers.")

        values = list(values)

        if not values:
            raise ValueError("Register values cannot be empty.")

        if len(values) > 123:
            raise ValueError("FC16 supports a maximum of 123 registers.")

        for value in values:
            if not isinstance(value, int) or not 0 <= value <= 65535:
                raise ValueError("Register values must be integers from 0 to 65535.")

        return values

    @staticmethod
    def _validate_coil_values(values: Sequence[bool]) -> list[bool]:
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("Coil values must be a sequence.")

        values = list(values)

        if not values:
            raise ValueError("Coil values cannot be empty.")

        if len(values) > 1968:
            raise ValueError("FC15 supports a maximum of 1968 coils.")

        return [bool(value) for value in values]

    # ------------------------------------------------------------------
    # Error/statistics helpers
    # ------------------------------------------------------------------

    def _check_connection_locked(self) -> None:
        if not self.connected or self.client is None:
            raise ConnectionError("Modbus client is not connected.")

    @classmethod
    def _decode_exception(cls, result: Any) -> str:
        code = getattr(result, "exception_code", None)
        description = cls.MODBUS_EXCEPTIONS.get(
            code, f"Unknown Modbus Exception ({code})"
        )
        return description

    def _record_request(self) -> None:
        self.stats["requests"] += 1

    def _record_success(self, start: float) -> None:
        self.stats["successes"] += 1
        self.stats["last_response_ms"] = (time.perf_counter() - start) * 1000.0
        self.last_error = ""

    def _record_error(self, message: str, start: float | None = None) -> None:
        self.stats["errors"] += 1
        if start is not None:
            self.stats["last_response_ms"] = (
                time.perf_counter() - start
            ) * 1000.0
        self.last_error = message

    def _execute(
        self,
        operation: Callable[[], Any],
        operation_name: str,
    ) -> Any | None:
        """
        Execute one Modbus transaction.

        A transaction is counted as successful only after confirming that
        the returned response is not a Modbus exception response.
        """

        with self._lock:
            try:
                self._check_connection_locked()
                self._record_request()
                start = time.perf_counter()

                result = operation()

                if result is None:
                    message = f"{operation_name}: Empty response."
                    self._record_error(message, start)
                    logger.warning(message)
                    return None

                if result.isError():
                    message = (
                        f"{operation_name}: "
                        f"{self._decode_exception(result)}"
                    )
                    self._record_error(message, start)
                    logger.warning(message)
                    return None

                self._record_success(start)
                return result

            except ModbusIOException as ex:
                message = f"{operation_name}: No response from slave."
                self._record_error(message, start if "start" in locals() else None)
                logger.warning("%s | %s", message, ex)
                return None

            except ModbusException as ex:
                message = f"{operation_name}: {ex}"
                self._record_error(message, start if "start" in locals() else None)
                logger.warning(message)
                return None

            except ConnectionError as ex:
                message = f"{operation_name}: {ex}"
                self._record_error(message)
                logger.warning(message)
                return None

            except Exception as ex:
                message = f"{operation_name}: {ex}"
                self._record_error(message, start if "start" in locals() else None)
                logger.exception("%s Unexpected Error", operation_name)
                return None

    # ------------------------------------------------------------------
    # FC03 - Read Holding Registers
    # ------------------------------------------------------------------

    def read_holding_registers(
        self,
        slave: int,
        address: int,
        count: int = 1,
    ) -> list[int]:
        self._validate_slave(slave)
        self._validate_address(address)
        self._validate_count(count)

        result = self._execute(
            lambda: self.client.read_holding_registers(
                address=address,
                count=count,
                device_id=slave,
            ),
            "FC03",
        )

        return list(result.registers) if result is not None else []

    # ------------------------------------------------------------------
    # FC04 - Read Input Registers
    # ------------------------------------------------------------------

    def read_input_registers(
        self,
        slave: int,
        address: int,
        count: int = 1,
    ) -> list[int]:
        self._validate_slave(slave)
        self._validate_address(address)
        self._validate_count(count)

        result = self._execute(
            lambda: self.client.read_input_registers(
                address=address,
                count=count,
                device_id=slave,
            ),
            "FC04",
        )

        return list(result.registers) if result is not None else []

    # ------------------------------------------------------------------
    # FC01 - Read Coils
    # ------------------------------------------------------------------

    def read_coils(
        self,
        slave: int,
        address: int,
        count: int = 1,
    ) -> list[bool]:
        self._validate_slave(slave)
        self._validate_address(address)
        self._validate_count(count)

        if count > 2000:
            raise ValueError("FC01 supports a maximum of 2000 coils.")

        result = self._execute(
            lambda: self.client.read_coils(
                address=address,
                count=count,
                device_id=slave,
            ),
            "FC01",
        )

        return list(result.bits[:count]) if result is not None else []

    # ------------------------------------------------------------------
    # FC02 - Read Discrete Inputs
    # ------------------------------------------------------------------

    def read_discrete_inputs(
        self,
        slave: int,
        address: int,
        count: int = 1,
    ) -> list[bool]:
        self._validate_slave(slave)
        self._validate_address(address)
        self._validate_count(count)

        if count > 2000:
            raise ValueError("FC02 supports a maximum of 2000 inputs.")

        result = self._execute(
            lambda: self.client.read_discrete_inputs(
                address=address,
                count=count,
                device_id=slave,
            ),
            "FC02",
        )

        return list(result.bits[:count]) if result is not None else []

    # ------------------------------------------------------------------
    # FC06 - Write Single Register
    # ------------------------------------------------------------------

    def write_register(
        self,
        slave: int,
        address: int,
        value: int,
    ) -> bool:
        self._validate_slave(slave)
        self._validate_address(address)

        if not isinstance(value, int) or not 0 <= value <= 65535:
            raise ValueError("Register value must be an integer from 0 to 65535.")

        result = self._execute(
            lambda: self.client.write_register(
                address=address,
                value=value,
                device_id=slave,
            ),
            "FC06",
        )

        return result is not None

    # ------------------------------------------------------------------
    # FC16 - Write Multiple Registers
    # ------------------------------------------------------------------

    def write_registers(
        self,
        slave: int,
        address: int,
        values: Sequence[int],
    ) -> bool:
        self._validate_slave(slave)
        self._validate_address(address)
        values = self._validate_register_values(values)

        result = self._execute(
            lambda: self.client.write_registers(
                address=address,
                values=values,
                device_id=slave,
            ),
            "FC16",
        )

        return result is not None

    # ------------------------------------------------------------------
    # FC05 - Write Single Coil
    # ------------------------------------------------------------------

    def write_coil(
        self,
        slave: int,
        address: int,
        value: bool,
    ) -> bool:
        self._validate_slave(slave)
        self._validate_address(address)

        result = self._execute(
            lambda: self.client.write_coil(
                address=address,
                value=bool(value),
                device_id=slave,
            ),
            "FC05",
        )

        return result is not None

    # ------------------------------------------------------------------
    # FC15 - Write Multiple Coils
    # ------------------------------------------------------------------

    def write_coils(
        self,
        slave: int,
        address: int,
        values: Sequence[bool],
    ) -> bool:
        self._validate_slave(slave)
        self._validate_address(address)
        values = self._validate_coil_values(values)

        result = self._execute(
            lambda: self.client.write_coils(
                address=address,
                values=values,
                device_id=slave,
            ),
            "FC15",
        )

        return result is not None

    # ------------------------------------------------------------------
    # Communication test
    # ------------------------------------------------------------------

    def ping(self, slave: int = 1) -> bool:
        """
        Test slave communication.

        Uses FC03 address 0 because the existing AKVO GUI expects a
        simple boolean ping. Devices that do not expose register 0 may
        therefore return False even when the slave is physically online.
        """

        self._validate_slave(slave)
        return bool(self.read_holding_registers(slave, 0, 1))

    # ------------------------------------------------------------------
    # Statistics/settings
    # ------------------------------------------------------------------

    def reset_statistics(self, log: bool = True) -> None:
        with self._lock:
            self.stats = {
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "last_response_ms": 0.0,
            }

        if log:
            logger.info("Statistics reset")

    def get_statistics(self) -> dict[str, Any]:
        with self._lock:
            stats = self.stats.copy()

        total = stats["requests"]
        stats["success_rate"] = (
            round((stats["successes"] / total) * 100.0, 2)
            if total
            else 0.0
        )

        return stats

    def get_last_error(self) -> str:
        with self._lock:
            return self.last_error

    def clear_last_error(self) -> None:
        with self._lock:
            self.last_error = ""

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return self.settings.copy()

    def get_connection_info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connected": self.connected,
                **self.settings,
            }

    def communication_summary(self) -> str:
        stats = self.get_statistics()
        return (
            f"Requests={stats['requests']}  "
            f"Success={stats['successes']}  "
            f"Errors={stats['errors']}  "
            f"Rate={stats['success_rate']}%"
        )

    # ------------------------------------------------------------------
    # Runtime settings
    # ------------------------------------------------------------------

    def set_timeout(self, timeout: float) -> None:
        """Update timeout and apply it to the active client when possible."""

        if timeout <= 0:
            raise ValueError("Timeout must be greater than zero.")

        with self._lock:
            self.settings["timeout"] = timeout

            if self.client is not None:
                try:
                    self.client.timeout = timeout
                except Exception:
                    logger.debug(
                        "Active PyModbus client does not expose timeout directly."
                    )

    def set_baudrate(self, baudrate: int) -> None:
        """
        Update stored baudrate.

        A live serial connection is intentionally not mutated. Reconnect
        to apply a new baudrate safely.
        """

        if baudrate <= 0:
            raise ValueError("Baudrate must be greater than zero.")

        with self._lock:
            self.settings["baudrate"] = baudrate

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Explicitly close the client."""

        self.disconnect()

    def __enter__(self) -> "ModbusClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.disconnect()

    def __del__(self) -> None:
        try:
            self.disconnect()
        except Exception:
            pass

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"<ModbusClient "
                f"connected={self.connected} "
                f"port={self.settings['port']}>"
            )
