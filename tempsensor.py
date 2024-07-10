import adafruit_dht
from board import D4 as pin
import time 
import datetime
import configparser
import pathlib
import os

# Read config
config_loc = pathlib.Path(r"/home/weatherstation/.config.ini")
config = configparser.ConfigParser()
config.read(config_loc)

# Sensor stuff
sensor = adafruit_dht.DHT22
dht_device = adafruit_dht.DHT22(pin)

def log():
    try:
        temperature = dht_device.temperature
        humidity = dht_device.humidity
    except RuntimeError:
        temperature = ""
        humidity = ""
    return temperature, humidity

def test_config_access(config, diagnostic_print = True):
    if diagnostic_print is True:
        print("Running from:", os.getcwd())
        print("File at:", config_loc)
        print("File exists:", config_loc.exists())
        print("Default:", dict(config["DEFAULT"]))
        print("Other sections:", config.sections())
    return 0

def test_csv_access():
    return 0

def start_log(running = True, to_csv = True, _print = True):
    while running == True:
        temp, hum = log()
        time.sleep(1)
        if _print is True:
            print(temp, hum)

if __name__ == "__main__":
    #start_log()
    print(test_config_access(config))