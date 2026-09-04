# AKVO Modbus Client V3 Mock Environment

1. Install: `sudo apt install socat` and `python3 -m pip install pyserial pymodbus`
2. Terminal 1: `./start_virtual_serial.sh`
3. Terminal 2: `python3 mock_slave.py --port /tmp/akvo_modbus_slave --slave 1`
4. Terminal 3: `python3 test_modbus_client.py --port /tmp/akvo_modbus_master`
5. Stop: `./stop_virtual_serial.sh`

The mock implements FC01/02/03/04/05/06/15/16 and deterministic registers/coils.
Use `--slave 2` in the client test to exercise a no-response timeout.
