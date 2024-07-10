import adafruit_dht
from board import D4 as pin
import time 
import datetime
from io_funcs import fetch_config

# Get config content
config = fetch_config() # path in definition
log_interval_s = int(config["DEFAULT"]["LogInterval_s"])

# Connect to sensor
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

def start_log(running = True, log_interval = log_interval_s,
    to_csv = True, _print = True):
    while running == True:
        temp, hum = log()
        time.sleep(log_interval_s)
        if _print is True:
            print(temp, hum)

if __name__ == "__main__":
    start_log()
    # print(test_config_access(config))