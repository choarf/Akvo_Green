

############## FileGenerator #################
import logging
import time
import sys
import os
from pymodbus.client import ModbusSerialClient
from pymodbus import (ExceptionResponse, ModbusException)
from include import *
import time
from pyModbusTCP.server import ModbusServer, DataBank
# need https://github.com/dbader/schedule
import schedule
from random import uniform
import datetime
import pytz





############################ init clases#################################

############## ModBus Set up #################

client = ModbusSerialClient(port=port, timeout=timeout, baudrate=baudrate, bytesize=bytesize, parity=parity, stopbits= stopbits)


Temp_Pt100= SensorModbusReadHR("Temp_Pt100", 0x00, 0x01, 0x02, client)

def RawDataTemp_Pt100():
    time.sleep(1)
    return(Temp_Pt100.ReadSensor())
Resistance_PT100= SensorModbusReadHR("Resistance_PT100", 0x20, 0x01, 0x02, client)

def RawDataResistance_PT100():
    time.sleep(1)
    return(Resistance_PT100.ReadSensor())
TempTH= SensorModbusReadHR("TempTH", 0x00, 0x01, 0x03, client)

def RawDataTempTH():
    time.sleep(1)
    return(TempTH.ReadSensor())
HumTH= SensorModbusReadHR("HumTH", 0x01, 0x01, 0x03, client)

def RawDataHumTH():
    time.sleep(1)
    return(HumTH.ReadSensor())
Temp_Pt100x= SensorModbusReadHR("Temp_Pt100x", 0x00, 0x01, 0x02, client)

def RawDataTemp_Pt100x():
    time.sleep(1)
    return(Temp_Pt100x.ReadSensor())
Resistance_PT100x= SensorModbusReadHR("Resistance_PT100x", 0x20, 0x01, 0x02, client)

def RawDataResistance_PT100x():
    time.sleep(1)
    return(Resistance_PT100x.ReadSensor())
TempTHx= SensorModbusReadHR("TempTHx", 0x00, 0x01, 0x03, client)

def RawDataTempTHx():
    time.sleep(1)
    return(TempTHx.ReadSensor())
HumTHx= SensorModbusReadHR("HumTHx", 0x01, 0x01, 0x03, client)

def RawDataHumTHx():
    time.sleep(1)
    return(HumTHx.ReadSensor())

SenorsReadList=[RawDataTemp_Pt100, RawDataResistance_PT100, RawDataTempTH, RawDataHumTH, RawDataTemp_Pt100x, RawDataResistance_PT100x, RawDataTempTHx, RawDataHumTHx]

DataTemperature1= DataClass("Temperature1", 0, 100, 200, -1, 10, 1, raw)
DataResistanceT1= DataClass("ResistanceT1", 0, 100, 1300, -1, 10, 1, raw)
DataTemperature2= DataClass("Temperature2", 0, 1000, 3000, -1, 10, 1, raw)
DataHumidity= DataClass("Humidity", 0, 100, 10000, -1, 10, 1, raw)
DataTemperature1x= DataClass("Temperature1x", 0, 100, 200, -1, 10, 1, raw)
DataResistanceT1x= DataClass("ResistanceT1x", 0, 100, 1300, -1, 10, 1, raw)
DataTemperature2x= DataClass("Temperature2x", 0, 1000, 3000, -1, 10, 1, raw)
DataHumidityx= DataClass("Humidityx", 0, 100, 10000, -1, 10, 1, raw)

DataList=[DataTemperature1, DataResistanceT1, DataTemperature2, DataHumidity, DataTemperature1x, DataResistanceT1x, DataTemperature2x, DataHumidityx]


############## PAHO  Set up #################

mqttc = paho.Client()
mqttc.on_connect = on_connect
mqttc.on_message = on_message
#mqttc.on_log = on_log

mqttc.tls_set(caPath, certfile=certPath, keyfile=keyPath, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2, ciphers=None)
mqttc.connect(awshost, awsport, keepalive=60)


############## AWS  Set up #################

def payload():
    x =DataList[0]
    print(x)
    MeanMaxMinValue(x)
    print(x.name +'  raw:' +str(x.raw_value)+'  mean:' +str(x.mean_value)+'  min:' +str(x.min_value)+' max:' +str(x.max_value))

'''
def SensorJSONMg():
    json_init =  '{ '
    json_end = ' }'

    msg =  PH1.jsonData() +', \n' +Temp1.jsonData()+', \n' +ORP1.jsonData()
    msg1 =(json_init +msg + json_end)
    return msg1
'''

###########################################
########### MAIN  #########################
###########################################

if __name__ == "__main__":
    pid = os.getpid()
    print("PID:", pid)

############### Check RTU connection######

    status = client.connect()
    print("conected to Modbus RTU port:  " + str(status))

    if status == False:

        error = " client not able to connect"
        os.system("sudo reboot")
############### Coding ###################
    while loop < main_loop:
        loop=loop+1
        ihelp=0
        utc_now = datetime.datetime.now(pytz.utc)
        print(utc_now)


        try:

############## RTU  Set up #################
            for x in DataList:
                temp = SenorsReadList[ihelp]().replace("]", "")
                #x.raw_value = Value2FloatList(SenorsReadList[ihelp]())

                MeanMaxMinValue(x)
                #print(x.name +'  raw:' +str(x.raw_value)+'  mean:' +str(x.mean_value)+'  min:' +str(x.min_value)+' max:' +str(x.max_value))
                ihelp = ihelp+1
                print(x.jsonData())



############## PAHO  RUN #################

                #print('Send {"message": "Hello from AKVO"}')
                mqttc.publish(Topic, '{"message": "Hello from AKVO"}', qos=1)
                mqttc.loop_start()


############### Coding ###################

        except KeyboardInterrupt:
            client.close()
            print("  Abort by keyboard")
            sys.exit()
