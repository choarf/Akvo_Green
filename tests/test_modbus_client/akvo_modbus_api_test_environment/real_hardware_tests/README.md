# AKVO Modbus API — Real Hardware Test Suite

This directory contains tests intended for a physical RS485/Modbus RTU environment.

## Hardware

Typical setup:

Ubuntu Gateway/PC
  -> USB-RS485 adapter
  -> RS485 A/B
  -> real Modbus RTU device

Start with READ-ONLY tests. Only run write tests after identifying registers/coils
that are explicitly safe to modify on the target device.

## Configure

Edit `real_hardware_tests/config.json`.

Example:

```json
{
  "port": "/dev/ttyUSB0",
  "baudrate": 9600,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "timeout": 1.0,
  "slave_id": 1,
  "holding_address": 0,
  "holding_count": 2,
  "input_address": 0,
  "input_count": 2,
  "coil_address": 0,
  "coil_count": 4,
  "discrete_address": 0,
  "discrete_count": 4,
  "write_tests_enabled": false,
  "safe_write_register": 0,
  "safe_write_value": 1,
  "safe_write_registers": [1, 2],
  "safe_write_coil": 0,
  "safe_write_coil_value": true,
  "safe_write_coils": [true, false]
}
```

## Run

From the project root:

```bash
python3 real_hardware_tests/test_real_hardware.py
```

Or:

```bash
python3 -m real_hardware_tests.test_real_hardware
```

The script produces a timestamped result file in `real_hardware_tests/results/`.

## Test sequence

1. Confirm the serial port.
2. Confirm baudrate/parity/stopbits/bytesize.
3. Confirm slave ID.
4. Run connection and ping.
5. Run FC01/FC02/FC03/FC04 read tests.
6. Verify response timing and statistics.
7. Test cable/device recovery.
8. Enable write tests only after confirming safe addresses.
9. Perform long-duration polling separately.

## Important

Do not enable write tests against an unknown device. Writing an arbitrary register
or coil can change machine/process behavior.
