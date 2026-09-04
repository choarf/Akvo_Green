# AKVO Modbus Client API V3
## Description and Interface Specification

### 1. Purpose

The AKVO Modbus Client API V3 is a Python application-level interface for Modbus RTU communication.

It isolates the AKVO application from PyModbus implementation details and provides connection management, reads, writes, diagnostics, error reporting, statistics, reconnection, and thread-safe transactions.

### 2. System position

```text
+------------------------------+
| AKVO Application             |
| Connection / Reader / Writer |
| Scanner / Monitor            |
+--------------+---------------+
               |
               | Python API
               v
+------------------------------+
|        ModbusClient V3       |
+--------------+---------------+
               |
               v
+------------------------------+
|            PyModbus          |
+--------------+---------------+
               |
               v
+------------------------------+
|       Serial / Modbus RTU    |
+--------------+---------------+
               |
               v
+------------------------------+
|          RS485 Devices       |
+------------------------------+
```

### 3. Public API

#### Connection

```python
connect(port, baudrate=9600, parity="N", stopbits=1, bytesize=8, timeout=1.0) -> bool
disconnect() -> None
reconnect() -> bool
ensure_connection() -> bool
is_connected() -> bool
is_port_open() -> bool
```

#### Reads

```python
read_coils(slave, address, count=1) -> list[bool]
read_discrete_inputs(slave, address, count=1) -> list[bool]
read_holding_registers(slave, address, count=1) -> list[int]
read_input_registers(slave, address, count=1) -> list[int]
```

#### Writes

```python
write_coil(slave, address, value) -> bool
write_register(slave, address, value) -> bool
write_coils(slave, address, values) -> bool
write_registers(slave, address, values) -> bool
```

#### Diagnostics

```python
ping(slave=1) -> bool
get_last_error() -> str
clear_last_error() -> None
communication_summary() -> str
```

#### Statistics

```python
get_statistics() -> dict
reset_statistics() -> None
```

Returned statistics include:

```text
requests
successes
errors
last_response_ms
success_rate
```

#### Configuration

```python
get_settings() -> dict
get_connection_info() -> dict
set_timeout(timeout) -> None
set_baudrate(baudrate) -> None
```

#### Lifecycle

```python
close()
__enter__()
__exit__()
```

### 4. Supported Modbus function codes

| FC | Operation | Return |
|---|---|---|
| 01 | Read Coils | `list[bool]` |
| 02 | Read Discrete Inputs | `list[bool]` |
| 03 | Read Holding Registers | `list[int]` |
| 04 | Read Input Registers | `list[int]` |
| 05 | Write Single Coil | `bool` |
| 06 | Write Single Register | `bool` |
| 15 | Write Multiple Coils | `bool` |
| 16 | Write Multiple Registers | `bool` |

### 5. Transaction model

Each transaction follows:

```text
Validate
   |
Check connection
   |
Count request
   |
Acquire lock
   |
Execute PyModbus operation
   |
Check response
   |
+-------------------------+
| Modbus exception?       |
| YES -> error            |
| NO  -> success          |
+-------------------------+
   |
Measure response time
   |
Return simplified result
```

A Modbus exception response is never counted as a successful transaction.

### 6. Error model

Read failure:

```python
[]
```

Write failure:

```python
False
```

Error details:

```python
client.get_last_error()
```

The API translates Modbus exception responses into human-readable descriptions.

### 7. Threading model

An internal reentrant lock serializes transactions through the single serial connection.

This is intended to allow multiple AKVO application components to share one `ModbusClient` instance.

### 8. Application contract

Application modules should use public methods such as:

```python
values = client.read_holding_registers(1, 100, 2)
```

and should not directly call:

```python
client.client.read_holding_registers(...)
```

The latter bypasses the API's validation, locking, statistics, and error handling.

### 9. Recommended architecture

```text
main.py
  |
  v
GUI
  +-- ConnectionTab
  +-- ScannerTab
  +-- ReaderTab
  +-- WriterTab
  +-- MonitorTab
           |
           v
    ModbusClient V3
           |
           v
        PyModbus
           |
           v
       Modbus RTU
           |
           v
          RS485
```

### 10. Scope

V3 is intended for Modbus RTU serial communication.

The public API currently exposes FC01, FC02, FC03, FC04, FC05, FC06, FC15, and FC16.

Modbus TCP is outside this client's intended scope.

### 11. Future extension points

The same abstraction can later support:

- Device profiles
- Register metadata
- Automatic retries
- Per-device statistics
- Scanner operations
- Raw-frame diagnostics
- Config-driven polling
- Async polling
- Device health state
- Alarm integration
- A Modbus TCP implementation behind the same application boundary
