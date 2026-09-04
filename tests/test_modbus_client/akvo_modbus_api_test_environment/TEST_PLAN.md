# AKVO Modbus Client API V3 — Test Plan

## 1. Objective

Verify that `modbus_client.py` provides a stable, predictable and thread-safe application API for Modbus RTU communication.

The plan covers:

- Connection lifecycle
- Validation
- FC01, FC02, FC03, FC04
- FC05, FC06, FC15, FC16
- Error handling
- Timeout/no-response behavior
- Statistics
- Reconnection
- Runtime settings
- Concurrent access
- API contract behavior

## 2. Test environment

The environment uses a virtual serial pair created by `socat`.

```text
pytest / test runner
       |
       v
modbus_client.py
       |
       v
/tmp/akvo_modbus_master
       |
     socat
       |
/tmp/akvo_modbus_slave
       |
       v
mock_slave.py
```

No physical RS485 adapter or sensor is required.

## 3. Entry criteria

- Ubuntu/Linux system
- Python installed
- `socat` installed
- Project dependencies installed
- V3 `modbus_client.py` present
- Virtual serial pair successfully created

## 4. Exit criteria

The API is considered test-ready when:

- All mandatory automated tests pass.
- All eight supported function codes pass.
- Write/read-back tests pass.
- Invalid input tests pass.
- No-response behavior is correct.
- Statistics are internally consistent.
- Concurrent transactions do not corrupt results.
- No unexpected traceback is produced for normal communication failures.

## 5. Test cases

| ID | Area | Test | Expected |
|---|---|---|---|
| TC-001 | Import | Import `ModbusClient` | PASS |
| TC-002 | Connection | Connect to mock slave | True |
| TC-003 | Connection | Disconnect | Client becomes disconnected |
| TC-004 | Connection | Reconnect | True |
| TC-005 | Connection | `ensure_connection()` while connected | True |
| TC-006 | FC03 | Read holding registers | Expected register values |
| TC-007 | FC04 | Read input registers | Expected input values |
| TC-008 | FC01 | Read coils | Expected booleans |
| TC-009 | FC02 | Read discrete inputs | Expected booleans |
| TC-010 | FC06 | Write single register | True |
| TC-011 | FC16 | Write multiple registers | True |
| TC-012 | FC05 | Write single coil | True |
| TC-013 | FC15 | Write multiple coils | True |
| TC-014 | Read-back | Verify FC06 value | Written value returned |
| TC-015 | Read-back | Verify FC16 values | Written values returned |
| TC-016 | Statistics | Successful transactions | successes increment |
| TC-017 | Statistics | Failed transaction | errors increment |
| TC-018 | Statistics | Success rate | Correct percentage |
| TC-019 | Error | Invalid slave ID | `ValueError` |
| TC-020 | Error | Invalid register address | `ValueError` |
| TC-021 | Error | Invalid count | `ValueError` |
| TC-022 | Error | Invalid register value | `ValueError` |
| TC-023 | Error | Read while disconnected | Empty list + error |
| TC-024 | Error | Wrong slave ID | Empty result + error |
| TC-025 | Configuration | Get connection info | Correct settings |
| TC-026 | Configuration | Change timeout | Stored timeout changes |
| TC-027 | Lifecycle | Context manager | Disconnect on exit |
| TC-028 | Concurrency | Multiple read threads | No transaction corruption |
| TC-029 | Regression | Repeated reads | Stable results |
| TC-030 | Regression | Repeated writes/read-back | Stable results |

## 6. Performance observations

The client exposes:

```python
stats["last_response_ms"]
```

Record:

- Minimum response time
- Maximum response time
- Average response time
- Number of failures

Performance thresholds should be established from the target hardware rather than assumed from the mock environment.

## 7. Failure injection

The mock environment supports the most important no-response test by selecting a slave ID that does not exist:

```bash
python3 test_modbus_client.py --slave 2
```

The mock slave listens on slave ID 1.

Additional future failure injection can include:

- Delayed responses
- Corrupted CRC
- Exception responses
- Dropped frames
- Disconnecting the virtual serial endpoint

## 8. Test evidence

Each test run should retain:

- Console output
- Pytest report
- Coverage report
- `logs/akvo_modbus.log`
- Python and PyModbus versions

## 9. Release recommendation

Do not connect the V3 client to production hardware until:

1. Automated mock tests pass.
2. A real-device integration test passes.
3. Register addressing is verified against the device manual.
4. Serial parameters are verified.
5. Error and timeout behavior is observed with the real RS485 installation.


# Real Hardware / Field Testing

The test environment also includes `real_hardware_tests/` for validation against
physical RS485/Modbus RTU equipment.

See:
- `real_hardware_tests/REAL_TEST_PLAN.md`
- `real_hardware_tests/README.md`
- `real_hardware_tests/config.json`

The real-hardware suite covers connection, ping, FC01/02/03/04 reads, optional
FC05/06/15/16 writes, statistics, disconnect/recovery, and long-duration polling.

Write tests are disabled by default and must only be enabled after safe target
registers/coils have been identified.
