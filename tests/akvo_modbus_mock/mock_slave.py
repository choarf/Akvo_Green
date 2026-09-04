#!/usr/bin/env python3
import argparse, struct, time
import serial

def crc16(data):
    crc=0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8): crc=(crc>>1)^0xA001 if crc&1 else crc>>1
    return crc

def add_crc(data): return data + struct.pack('<H', crc16(data))
def valid(f): return len(f)>=4 and crc16(f[:-2])==struct.unpack('<H',f[-2:])[0]

class MockSlave:
    def __init__(self, port, slave):
        self.slave=slave
        self.holding=[0]*100; self.input=[0]*100; self.coils=[False]*200; self.discrete=[False]*200
        self.holding[0:4]=[1234,250,1000,65535]; self.holding[10]=3141
        self.input[0:3]=[500,750,1250]; self.input[10]=4200
        self.coils[0:4]=[True,False,True,False]; self.discrete[0:4]=[True,True,False,True]
        self.ser=serial.Serial(port,9600,bytesize=8,parity='N',stopbits=1,timeout=.05)
    def exc(self,fc,code): return add_crc(bytes([self.slave,fc|0x80,code]))
    def read(self,fc,a,q):
        if q<=0:return self.exc(fc,3)
        if fc==1: vals=self.coils; maxq=2000
        elif fc==2: vals=self.discrete; maxq=2000
        elif fc==3: vals=self.holding; maxq=125
        else: vals=self.input; maxq=125
        if q>maxq or a+q>len(vals): return self.exc(fc,2)
        vals=vals[a:a+q]
        if fc in (1,2):
            payload=bytearray((q+7)//8)
            for i,v in enumerate(vals):
                if v: payload[i//8]|=1<<(i%8)
        else: payload=b''.join(int(v).to_bytes(2,'big') for v in vals)
        return add_crc(bytes([self.slave,fc,len(payload)])+bytes(payload))
    def handle(self,f):
        if len(f)<8 or not valid(f) or f[0]!=self.slave:return None
        fc=f[1]; a=int.from_bytes(f[2:4],'big'); q=int.from_bytes(f[4:6],'big')
        print(f'RX FC={fc:02X} addr={a} value/count={q}',flush=True)
        if fc in (1,2,3,4): return self.read(fc,a,q)
        if fc==5: self.coils[a]=(q==0xFF00); return add_crc(f[:6])
        if fc==6: self.holding[a]=q; return add_crc(f[:6])
        if fc in (15,16):
            bc=f[6]; p=f[7:7+bc]
            if a+q>(len(self.coils) if fc==15 else len(self.holding)): return self.exc(fc,2)
            if fc==15:
                for i in range(q): self.coils[a+i]=bool(p[i//8]&(1<<(i%8)))
            else:
                for i in range(q): self.holding[a+i]=int.from_bytes(p[i*2:i*2+2],'big')
            return add_crc(f[:6])
        return self.exc(fc,1)
    def run(self):
        print(f'Mock slave {self.slave} listening on {self.ser.port}',flush=True); buf=bytearray()
        while True:
            d=self.ser.read(256)
            if not d: continue
            buf.extend(d); time.sleep(.004)
            while len(buf)>=2:
                fc=buf[1]
                if fc in (1,2,3,4,5,6): n=8
                elif fc in (15,16):
                    if len(buf)<7: break
                    n=9+buf[6]
                else: n=8
                if len(buf)<n: break
                f=bytes(buf[:n]); del buf[:n]
                r=self.handle(f)
                if r: self.ser.write(r); self.ser.flush(); print('TX',r.hex(' '),flush=True)

a=argparse.ArgumentParser(); a.add_argument('--port',required=True); a.add_argument('--slave',type=int,default=1); x=a.parse_args(); MockSlave(x.port,x.slave).run()
