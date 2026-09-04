#!/usr/bin/env python3
import argparse
from modbus_client import ModbusClient
p=argparse.ArgumentParser(); p.add_argument('--port',default='/tmp/akvo_modbus_master'); p.add_argument('--slave',type=int,default=1); a=p.parse_args()
c=ModbusClient()
if not c.connect(a.port,9600,'N',1,8,1.0): print('CONNECT FAIL:',c.get_last_error()); raise SystemExit(1)
print('CONNECT PASS')
tests=[('FC03',lambda:c.read_holding_registers(a.slave,0,4)),('FC04',lambda:c.read_input_registers(a.slave,0,3)),('FC01',lambda:c.read_coils(a.slave,0,4)),('FC02',lambda:c.read_discrete_inputs(a.slave,0,4)),('FC06',lambda:c.write_register(a.slave,20,4321)),('FC16',lambda:c.write_registers(a.slave,21,[11,22,33])),('FC05',lambda:c.write_coil(a.slave,4,True)),('FC15',lambda:c.write_coils(a.slave,5,[True,False,True]))]
for n,fn in tests:
    try: print(n, 'PASS' if fn() else 'FAIL')
    except Exception as e: print(n,'EXCEPTION',e)
print('Readback FC06:',c.read_holding_registers(a.slave,20,1))
print('Readback FC16:',c.read_holding_registers(a.slave,21,3))
print('Statistics:',c.get_statistics()); print('Last error:',c.get_last_error() or '<none>'); c.disconnect()
