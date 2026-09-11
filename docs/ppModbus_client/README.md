# AKVO Modbus Client API V3

Production-oriented Python API for Modbus RTU communication in the AKVO Modbus Tool.

## Overview

`modbus_client.py` is the communication abstraction layer between the AKVO application and Modbus RTU devices.

```text
AKVO GUI
   |
   v
ModbusClient V3 API
   |
   v
PyModbus
   |
   v
Serial / RS485
   |
   +---- Modbus Slave 1
   +---- Modbus Slave 2
   +---- Modbus Slave N
```

Application modules should use the `ModbusClient` API rather than accessing PyModbus directly.

## Requirements

Python 3.10+ is recommended.

```bash
python3 -m pip install pymodbus pyserial
```

The client also expects:

```text
utils/logger.py
```

## Basic usage

```python
from modbus_client import ModbusClient

client = ModbusClient()

if client.connect(
    port="/dev/ttyUSB0",
    baudrate=9600,
    parity="N",
    stopbits=1,
    bytesize=8,
    timeout=1.0,
):
    values = client.read_holding_registers(
        slave=1,
        address=0,
        count=2,
    )
    print(values)

client.disconnect()
```

## Connection API

```python
client.connect(
    port="/dev/ttyUSB0",
    baudrate=9600,
    parity="N",
    stopbits=1,
    bytesize=8,
    timeout=1.0,
)
```

Returns `True` on success and `False` on failure.

```python
client.disconnect()
client.reconnect()
client.ensure_connection()
client.is_connected()
```

## Supported Modbus functions

| Function | API method | Operation |
|---|---|---|
| FC01 | `read_coils()` | Read coils |
| FC02 | `read_discrete_inputs()` | Read discrete inputs |
| FC03 | `read_holding_registers()` | Read holding registers |
| FC04 | `read_input_registers()` | Read input registers |
| FC05 | `write_coil()` | Write single coil |
| FC06 | `write_register()` | Write single register |
| FC15 | `write_coils()` | Write multiple coils |
| FC16 | `write_registers()` | Write multiple registers |

## Read examples

```python
values = client.read_holding_registers(slave=1, address=100, count=2)
values = client.read_input_registers(slave=1, address=0, count=4)
values = client.read_coils(slave=1, address=0, count=8)
values = client.read_discrete_inputs(slave=1, address=0, count=8)
```

Read operations return lists. A failed read returns `[]`.

## Write examples

```python
success = client.write_register(slave=1, address=100, value=500)

success = client.write_registers(
    slave=1,
    address=100,
    values=[100, 200, 300],
)

success = client.write_coil(slave=1, address=10, value=True)

success = client.write_coils(
    slave=1,
    address=10,
    values=[True, False, True],
)
```

Write operations return `True` or `False`.

## Slave IDs and addressing

Valid slave IDs are `1–247`.

The API uses zero-based addresses as supplied to PyModbus. Vendor register notation such as `40001` may need conversion before calling the API.

## Error handling

Use:

```python
client.get_last_error()
```

Example:

```text
FC03: No response from slave.
FC06: Illegal Data Address
```

Normal application reads return `[]` on communication failure; writes return `False`.

## Statistics

```python
stats = client.get_statistics()
```

Example:

```python
{
    "requests": 100,
    "successes": 96,
    "errors": 4,
    "last_response_ms": 14.27,
    "success_rate": 96.0
}
```

Reset with:

```python
client.reset_statistics()
```

A Modbus exception response is counted as an error, not a success.

## Diagnostics

```python
client.ping(slave=1)
client.get_last_error()
client.clear_last_error()
client.communication_summary()
```

`ping()` currently tests communication by reading holding register address `0`. A device that does not implement register 0 may therefore return `False`.

## Configuration

```python
client.get_settings()
client.get_connection_info()
client.set_timeout(2.0)
client.set_baudrate(19200)
```

A baudrate change should be followed by a reconnect before relying on the new serial configuration.

## Thread safety

`ModbusClient` serializes Modbus transactions with an internal lock so Reader, Writer, Scanner, and Monitor components can safely share one client instance.

Recommended architecture:

```text
ConnectionTab
ReaderTab
WriterTab
ScannerTab
MonitorTab
      |
      v
ModbusClient V3
      |
      v
PyModbus
```

## Context manager

```python
with ModbusClient() as client:
    if client.connect("/dev/ttyUSB0"):
        values = client.read_holding_registers(1, 0, 1)
```

The connection is closed automatically.

## Logging

The client uses the AKVO logger:

```python
from utils.logger import get_logger
```

Recommended project layout:

```text
AKVO_Modbus/
├── main.py
├── modbus_client.py
├── tabs/
└── utils/
    ├── __init__.py
    └── logger.py
```

## Mock testing

The API can be tested without physical hardware using a virtual serial link and a mock Modbus slave:

```text
test_modbus_client.py
        |
        v
modbus_client.py
        |
        v
virtual serial
        |
        v
mock_slave.py
```

This permits testing the supported function codes and error handling before connecting real RS485 equipment.

## Design principle

The rest of the AKVO application should depend on `ModbusClient`, not on PyModbus internals.

This keeps the GUI independent of the underlying Modbus implementation and provides a stable application-level interface.
