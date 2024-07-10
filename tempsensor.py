import adafruit_dht
from board import D4 as pin
import time 
import datetime

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

def start_log(running = True, to_csv = True, _print = True):
    while running == True:
        temp, hum = log()
        time.sleep(1)
        if _print is True:
            print(temp, hum)

if __name__ == "__main__":
    start_log()