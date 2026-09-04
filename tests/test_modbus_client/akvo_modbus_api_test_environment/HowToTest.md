# AKVO Modbus API — How To Test

Complete instructions for testing `modbus_client.py` in both a **virtual Modbus RTU environment** and a **real RS485/Modbus RTU environment**.

---

## 1. Test Strategy

The AKVO Modbus API should be tested in stages:

```text
                 modbus_client.py
                       |
          +------------+------------+
          |                         |
          v                         v
     VIRTUAL TEST               REAL TEST
          |                         |
       socat                    USB-RS485
          |                         |
    Mock Modbus Slave         Real Modbus Device
```

### Virtual testing

Used to validate:

- Python/API behavior
- Modbus RTU communication
- FC01
- FC02
- FC03
- FC04
- FC05
- FC06
- FC15
- FC16
- error handling
- statistics
- repeated requests

No physical Modbus equipment is required.

### Real testing

Used to validate:

- USB-RS485 communication
- physical RS485 wiring
- real slave ID
- real serial settings
- actual register map
- communication reliability
- disconnect/reconnect behavior
- device power-cycle recovery
- long-duration operation

---

# 2. Test Environment Directory

After extracting the test environment, the directory should contain:

```text
akvo_modbus_api_test_environment/
├── modbus_client.py
├── mock_slave.py
├── test_modbus_client.py
├── requirements.txt
├── TEST_PLAN.md
├── RUN_TESTS.md
├── run_tests.sh
├── start_virtual_serial.sh
├── stop_virtual_serial.sh
├── tests/
│   ├── test_api_unit.py
│   └── test_api_integration.py
├── utils/
│   ├── __init__.py
│   └── logger.py
└── real_hardware_tests/
    ├── README.md
    ├── REAL_TEST_PLAN.md
    ├── config.json
    ├── test_real_hardware.py
    ├── long_duration_test.py
    ├── run_real_tests.sh
    └── results/
```

---

# 3. Ubuntu Software Setup

Install the required system packages:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip socat
```

Verify:

```bash
python3 --version
socat -V
```

---

# 4. Create the Python Environment

Enter the project:

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
```

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

The terminal should show:

```text
(.venv)
```

Upgrade pip:

```bash
python -m pip install --upgrade pip
```

Install the test dependencies:

```bash
python -m pip install -r requirements.txt
```

Verify:

```bash
python -c "import pymodbus; print('PyModbus:', pymodbus.__version__)"
pytest --version
```

---

# 5. Virtual Modbus Test

## 5.1 Start the Virtual Serial Ports

Open **Terminal 1**.

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
```

Start the virtual serial pair:

```bash
./start_virtual_serial.sh
```

The script creates:

```text
/tmp/akvo_modbus_master
/tmp/akvo_modbus_slave
```

Verify:

```bash
ls -l /tmp/akvo_modbus_*
```

The communication path is:

```text
AKVO Modbus Client
       |
       | /tmp/akvo_modbus_master
       |
     socat
       |
       | /tmp/akvo_modbus_slave
       |
       v
Mock Modbus Slave
```

---

## 5.2 Start the Mock Modbus Slave

Open **Terminal 2**.

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
```

Start the virtual slave:

```bash
python3 mock_slave.py     --port /tmp/akvo_modbus_slave     --slave 1
```

Leave this terminal running.

The virtual device uses:

```text
Slave ID: 1
```

---

# 6. Run Unit Tests

Open **Terminal 3**.

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
```

Run:

```bash
python -m pytest tests/test_api_unit.py -v
```

Unit tests do not require the mock slave.

Expected result:

```text
PASSED
PASSED
PASSED
...
```

---

# 7. Run Integration Tests

Make sure the mock slave is still running.

Run:

```bash
python -m pytest tests/test_api_integration.py -v
```

These tests exercise the complete path:

```text
test
  |
modbus_client.py
  |
PyModbus
  |
virtual serial
  |
socat
  |
mock_slave.py
```

---

# 8. Run All Virtual Tests

Run the complete pytest suite:

```bash
python -m pytest -v
```

Or:

```bash
./run_tests.sh
```

If necessary:

```bash
chmod +x run_tests.sh
./run_tests.sh
```

---

# 9. Run the Human-Readable API Test

The test runner exercises the Modbus API directly:

```bash
python3 test_modbus_client.py
```

This verifies the supported Modbus operations against the virtual slave.

---

# 10. Test All Modbus Function Codes

The test environment is designed to exercise:

| Function | Operation |
|---|---|
| FC01 | Read Coils |
| FC02 | Read Discrete Inputs |
| FC03 | Read Holding Registers |
| FC04 | Read Input Registers |
| FC05 | Write Single Coil |
| FC06 | Write Single Register |
| FC15 | Write Multiple Coils |
| FC16 | Write Multiple Registers |

Write tests should verify the operation and, where applicable, read the value back.

---

# 11. Test Error Handling

The virtual slave uses slave ID `1`.

Test an invalid slave:

```bash
python3 test_modbus_client.py --slave 2
```

This should produce a communication failure/no-response condition.

The important behavior is:

```text
Request
   |
No slave response
   |
Timeout/error
   |
API records error
   |
Application continues
```

The API should not crash.

---

# 12. Run Coverage

Run:

```bash
python -m pytest     --cov=modbus_client     --cov-report=term-missing
```

This shows which portions of `modbus_client.py` are covered by the tests.

---

# 13. Stop the Virtual Test Environment

When testing is finished:

```bash
./stop_virtual_serial.sh
```

Verify:

```bash
ls /tmp/akvo_modbus_*
```

The virtual serial devices should no longer be present.

---

# 14. Real Hardware Test

## 14.1 Required Hardware

Typical setup:

```text
Ubuntu PC / Gateway
        |
       USB
        |
USB → RS485 Adapter
        |
       A/B
        |
        v
Real Modbus RTU Device
```

The device may be:

- Modbus sensor
- pressure sensor
- flow meter
- energy meter
- PLC
- industrial gauge
- Modbus I/O device

---

# 15. Detect the USB-RS485 Adapter

Connect the adapter.

Run:

```bash
ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Typical result:

```text
/dev/ttyUSB0
```

Check the kernel messages:

```bash
dmesg | tail -30
```

Look for the adapter being attached to `ttyUSB0` or `ttyACM0`.

---

# 16. Check Serial Port Permissions

Check:

```bash
ls -l /dev/ttyUSB0
```

If your user does not have access, add the user to `dialout`:

```bash
sudo usermod -aG dialout $USER
```

Log out and log back in.

Verify:

```bash
groups
```

You should see:

```text
dialout
```

---

# 17. Determine the Real Device Settings

Before testing, obtain the Modbus communication settings from the device documentation.

You need:

```text
Slave ID
Baudrate
Data bits
Parity
Stop bits
```

Example:

```text
Slave ID: 1
Baudrate: 9600
Data bits: 8
Parity: None
Stop bits: 1
```

This is:

```text
9600 8N1
```

Do not assume that every device uses 9600 8N1.

---

# 18. Connect RS485

Typical wiring:

```text
USB-RS485 Adapter       Modbus Device

A / D+  --------------  A / D+
B / D-  --------------  B / D-
```

Some manufacturers use different labels or polarity conventions. Always follow the target device documentation.

If communication fails, check A/B polarity first.

---

# 19. Configure the Real Test

Edit:

```bash
nano real_hardware_tests/config.json
```

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

  "write_tests_enabled": false
}
```

### Important

The register addresses above are examples.

Replace them with the actual addresses from the target device's Modbus register map.

---

# 20. First Real Test — READ ONLY

Keep:

```json
"write_tests_enabled": false
```

Run:

```bash
python3 real_hardware_tests/test_real_hardware.py
```

Or:

```bash
./real_hardware_tests/run_real_tests.sh
```

The initial sequence is:

```text
Connect
  |
Ping
  |
FC03
  |
FC04
  |
FC01
  |
FC02
  |
Statistics
  |
Disconnect
```

Do not enable writes until the real register map and safe writable addresses are confirmed.

---

# 21. Expected Real Test Output

A successful test should look similar to:

```text
AKVO Modbus API - REAL HARDWARE TEST
=======================================================
Port       : /dev/ttyUSB0
Slave ID   : 1
Serial     : 9600 8N1
Timeout    : 1.0 s
Writes     : False

[PASS] RT-001 Connect
[PASS] RT-002 Ping
[PASS] RT-003 FC03 Holding Registers
[PASS] RT-004 FC04 Input Registers
[PASS] RT-005 FC01 Coils
[PASS] RT-006 FC02 Discrete Inputs
[PASS] RT-007 Statistics

Write tests skipped (write_tests_enabled=false).

[PASS] RT-012 Disconnect
```

The exact results depend on the real device's supported function codes and register map.

---

# 22. Real Write Tests

Write tests are disabled by default.

Only enable them after identifying safe registers/coils.

Edit:

```bash
nano real_hardware_tests/config.json
```

Set:

```json
"write_tests_enabled": true
```

Configure:

```json
"safe_write_register": 123,
"safe_write_value": 1,
"safe_write_registers": [1, 2],
"safe_write_coil": 0,
"safe_write_coil_value": true,
"safe_write_coils": [true, false]
```

The addresses above are examples only.

### WARNING

Never write arbitrary addresses to an unknown industrial device.

A write can:

- change a setpoint
- start/stop equipment
- change calibration
- change configuration
- trigger an output
- affect a production process

Only use addresses explicitly approved for testing.

---

# 23. Test Wrong Slave ID

Temporarily change:

```json
"slave_id": 99
```

Run:

```bash
python3 real_hardware_tests/test_real_hardware.py
```

The real device should not respond.

Restore the correct slave ID afterward.

---

# 24. Test RS485 Cable Failure

With normal polling:

1. Start the test.
2. Disconnect the RS485 connection.
3. Observe communication errors.
4. Reconnect the RS485 connection.
5. Verify communication recovers.

Expected behavior:

```text
Normal communication
       |
       v
Cable disconnected
       |
       v
Communication errors
       |
       v
Cable reconnected
       |
       v
Communication recovery
```

The application should not crash.

---

# 25. Test Device Power Cycle

With the client communicating:

1. Power off the Modbus device.
2. Observe communication errors.
3. Power the device back on.
4. Wait for it to boot.
5. Verify communication returns.

This validates recovery from a common industrial failure condition.

---

# 26. Long-Duration Test

The real test environment includes:

```text
real_hardware_tests/run_soak_test.py
```

Default duration:

```bash
python3 real_hardware_tests/run_soak_test.py
```

The default is 1 hour.

For a 10-minute test:

```bash
python3 real_hardware_tests/run_soak_test.py 600
```

For 10 minutes with a 2-second polling interval:

```bash
python3 real_hardware_tests/run_soak_test.py 600 2
```

Recommended soak testing:

```text
1 hour   — initial test
8 hours  — extended test
24 hours — production acceptance test
```

Monitor:

- request count
- successful requests
- errors
- success rate
- response time
- last error
- communication recovery
- application stability

---

# 27. Test Reports

Real hardware results are stored in:

```text
real_hardware_tests/results/
```

Example:

```text
real_test_20260904_180500.json
```

Long-duration results are also saved there.

These reports can be retained as part of the production acceptance record.

---

# 28. Recommended Full Test Sequence

Run testing in this order:

```text
PHASE 1 — SOFTWARE
|
+-- Install dependencies
+-- Unit tests
+-- Coverage
|
v
PHASE 2 — VIRTUAL MODBUS
|
+-- Start socat
+-- Start mock slave
+-- FC01
+-- FC02
+-- FC03
+-- FC04
+-- FC05
+-- FC06
+-- FC15
+-- FC16
+-- Read-back
+-- Wrong slave
+-- Error handling
|
v
PHASE 3 — REAL HARDWARE
|
+-- Detect USB-RS485
+-- Check permissions
+-- Verify wiring
+-- Verify serial settings
+-- Verify slave ID
+-- Verify register map
+-- FC03 / FC04
+-- FC01 / FC02
+-- Statistics
|
v
PHASE 4 — FAILURE RECOVERY
|
+-- Wrong slave
+-- Disconnect RS485
+-- Reconnect RS485
+-- Power-cycle device
+-- Verify recovery
|
v
PHASE 5 — SOAK TEST
|
+-- 1 hour
+-- 8 hours
+-- 24 hours
|
v
PRODUCTION ACCEPTANCE
```

---

# 29. Quick Virtual Test

After the environment is installed:

### Terminal 1

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
./start_virtual_serial.sh
python3 mock_slave.py --port /tmp/akvo_modbus_slave --slave 1
```

Leave it running.

### Terminal 2

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
python -m pytest -v
```

Then:

```bash
python3 test_modbus_client.py
```

Stop the virtual ports:

```bash
./stop_virtual_serial.sh
```

---

# 30. Quick Real Test

Configure:

```text
real_hardware_tests/config.json
```

Then:

```bash
cd ~/Downloads/akvo_modbus_api_test_environment
source .venv/bin/activate
python3 real_hardware_tests/test_real_hardware.py
```

Start with:

```json
"write_tests_enabled": false
```

---

# 31. Production Acceptance Checklist

Before declaring the API ready for production:

- [ ] Unit tests pass
- [ ] Virtual integration tests pass
- [ ] All supported function codes tested
- [ ] Wrong slave handled correctly
- [ ] Timeout handled correctly
- [ ] No-response condition handled correctly
- [ ] Real RS485 connection tested
- [ ] Real FC03/FC04 tested
- [ ] Real FC01/FC02 tested where supported
- [ ] Safe write operations tested where required
- [ ] Cable disconnect recovery tested
- [ ] Device power-cycle recovery tested
- [ ] Reconnect behavior tested
- [ ] 1-hour soak test passed
- [ ] 8-hour soak test passed
- [ ] 24-hour soak test passed
- [ ] Test reports archived
- [ ] Target device returned to expected operating state

---

## 32. Important Current API Note

During testing, pay attention to communication statistics.

The current implementation updates the success statistics before checking whether a Modbus response is actually an error response. This can cause a Modbus exception response to be counted as both a success and an error.

This should be corrected before using `success_rate` as a production acceptance metric.

The relevant implementation is in `_execute_read()` and `_execute_write()`.

---

## 33. Final Goal

The complete AKVO validation environment should provide:

```text
             AKVO Modbus API
                    |
        +-----------+-----------+
        |                       |
        v                       v
    AUTOMATED                REAL WORLD
    SOFTWARE TEST              TEST
        |                       |
   Unit + Integration       RS485 Hardware
        |                       |
        +-----------+-----------+
                    |
                    v
             Test Reports
                    |
                    v
           Production Release
```

This document is the primary **How To Test** reference for the AKVO Modbus API test environment.
