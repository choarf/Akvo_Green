# AKVO Modbus API — Real Environment Test Plan

## Objective

Validate the `modbus_client.py` API against physical Modbus RTU equipment over an
actual RS485 interface.

## Test environment

- Ubuntu Linux gateway/PC
- USB-to-RS485 adapter
- RS485 A/B wiring
- Real Modbus RTU sensor, meter, PLC, or other slave
- `modbus_client.py`
- PyModbus
- Python 3

## Preconditions

- Confirm the target device supports Modbus RTU.
- Confirm slave ID.
- Confirm baudrate, parity, stop bits and data bits.
- Confirm register map.
- Confirm RS485 A/B polarity.
- Confirm termination/biasing requirements.
- Confirm which registers/coils are safe to write.

## Test cases

| ID | Test | Expected result |
|---|---|---|
| RT-001 | Open configured serial port | Client connects |
| RT-002 | Ping configured slave | Valid response |
| RT-003 | FC03 holding registers | Correct count returned |
| RT-004 | FC04 input registers | Correct count returned |
| RT-005 | FC01 coils | Correct count returned |
| RT-006 | FC02 discrete inputs | Correct count returned |
| RT-007 | Statistics | Requests/success/errors updated |
| RT-008 | FC06 safe register | Write succeeds |
| RT-009 | FC16 safe registers | Write succeeds |
| RT-010 | FC05 safe coil | Write succeeds |
| RT-011 | FC15 safe coils | Write succeeds |
| RT-012 | Disconnect | Port closes cleanly |
| RT-013 | Wrong slave ID | No-response/error handled |
| RT-014 | RS485 cable removed | Communication failure handled |
| RT-015 | Cable reconnected | Communication recovers |
| RT-016 | Slave power cycle | Communication recovers |
| RT-017 | Gateway/client reconnect | Connection recovers |
| RT-018 | Repeated polling | Stable communication |
| RT-019 | Long-duration polling | No progressive failure |
| RT-020 | Response-time monitoring | Timing remains within expected range |

## Failure injection

### Wrong slave

Temporarily configure a slave ID that does not exist. The client should report a
communication error without crashing the application.

### Cable disconnect

Remove the RS485 connection during polling. Verify that the API returns its
documented failure value and records the error.

### Recovery

Reconnect the cable and verify that subsequent requests succeed.

### Device power cycle

Power-cycle the Modbus device and verify that the client can recover once the
device returns.

## Long-duration test

Recommended production validation:

- 1 hour minimum
- 8 hours extended
- 24 hours final soak

Record:

- total requests
- successful requests
- errors
- success rate
- response times
- last error
- number of reconnects
- unexpected process exits

## Acceptance criteria

A release candidate should:

1. Complete all applicable read tests.
2. Complete write tests only against approved safe addresses.
3. Recover from communication interruption.
4. Not crash on a missing slave.
5. Maintain stable polling over the selected soak period.
6. Produce usable communication statistics.
7. Leave the target device in the documented expected state.
