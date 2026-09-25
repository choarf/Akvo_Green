from modbus_client import ModbusClient

client = ModbusClient()

print("Connecting to /dev/ttyUSB0...")

if not client.connect(
    port="/dev/ttyUSB0",
    baudrate=9600,
    parity="N",
    stopbits=1,
    bytesize=8,
    timeout=1.0,
):
    print("CONNECTION FAILED")
    print("Error:", client.get_last_error())
    raise SystemExit(1)

print("CONNECTED")

slave = 1

print(f"Reading Slave {slave}, Holding Register 0...")

values = client.read_holding_registers(
    slave=slave,
    address=0,
    count=1,
)

if values:
    print("READ OK")
    print("Value:", values)
else:
    print("READ FAILED")
    print("Error:", client.get_last_error())

print()
print("Communication statistics:")
print(client.get_statistics())

client.disconnect()
print("Disconnected")
