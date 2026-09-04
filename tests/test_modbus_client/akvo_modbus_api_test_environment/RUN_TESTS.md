# Running the Test Environment

## Install

```bash
cd akvo_modbus_api_test_environment

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

sudo apt install socat
```

## Test 1 — Unit tests

These do not require a serial port:

```bash
python -m pytest tests/test_api_unit.py -v
```

## Test 2 — Start virtual serial

Terminal 1:

```bash
./start_virtual_serial.sh
```

## Test 3 — Start mock slave

Terminal 2:

```bash
python3 mock_slave.py \
  --port /tmp/akvo_modbus_slave \
  --slave 1
```

## Test 4 — Run human-readable integration test

Terminal 3:

```bash
python3 test_modbus_client.py
```

## Test 5 — Run automated integration tests

With the mock slave still running:

```bash
python -m pytest tests/test_api_integration.py -v
```

Or all tests:

```bash
python -m pytest -v
```

## Test 6 — Coverage

```bash
python -m pytest --cov=modbus_client --cov-report=term-missing
```

## Test failure/no-response

The mock slave uses slave ID 1.

Try:

```bash
python3 test_modbus_client.py --slave 2
```

The client should report a communication failure rather than falsely reporting success.

## Stop

```bash
./stop_virtual_serial.sh
```
