#!/usr/bin/python

import os
import socket
from time import sleep
from random import randint


# now we can import custom libs
from gota_lib  import *

#ssid aka wifi name
name = "Vocho"

#psk aka wifi pass
pwd =  "22444Arcadia"

def get_ssid():
    try:
        ssid=os.popen("sudo iwgetid -r").read()
        return ssid
    except:
        return "None"

def get_host_ip():
    try:
        testIP = "8.8.8.8"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((testIP, 0))
        ipaddr = s.getsockname()[0]
        host = socket.gethostname()
        return ipaddr
    except:
        return "127.0.0."

def check_connection():
    try:
        host = socket.gethostbyname('www.google.com')
        s = socket.create_connection((host, 80), 2)
        return True
    except:
        return False



def CreateWifiConfig(SSID, password):

    #setting up file contents
    config_lines = [
        'ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev',
        'update_config=1',
        'country=US',
        '\n',
        'network={',
        '\tssid="{}"'.format(SSID),
        '\tpsk="{}"'.format(password),
        '}'
        ]
    config = '\n'.join(config_lines)

    #display additions

    #give access and writing. may have to do this manually beforehand
    os.popen("sudo chmod a+w /etc/wpa_supplicant/wpa_supplicant.conf")

    #writing to file
    with open("/etc/wpa_supplicant/wpa_supplicant.conf", "w") as wifi:
        wifi.write(config)

    #displaying success
    #print("wifi config added:  ", wifi)
    sleep(10)
    os.popen("sudo wpa_cli -i wlan0 reconfigure")
    return config
#run function, with vars as parameters
#CreateWifiConfig(name, pwd)


#reboot, which impliments changes
#os.popen("sudo reboot")
