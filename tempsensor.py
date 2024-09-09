try:
    import adafruit_dht
    from board import D4 as pin
    print("found dht sensor")
    sensor_found = True
except ModuleNotFoundError:
    print("no dht sensor")
    sensor_found = False
except ImportError:
    print("no dht sensor")
    sensor_found = False
import time 
import datetime
import numpy as np
import pathlib
# Local imports
import io_funcs

CONFIG = io_funcs.fetch_config()

def write_data(temperature, humidity, filename = CONFIG["DEFAULT"]["outputfile"]):        
    if pathlib.Path(filename).exists() is False:
        with open(filename, "w") as f:
            f.write("time,temperature,humidity\n")
    with open(filename, "a") as f:
        f.write(f"{datetime.datetime.now()},{temperature},{humidity}\n")

def print_to_console(temperature, humidity):
    if temperature is not None and humidity is not None:
        print(datetime.datetime.now(), temperature,"C ",humidity,"%")
    else:
        print(datetime.datetime.now(), "failed read")

def run_tempsensor():
    global sensor_found
    if sensor_found is False:
        print("no sensor, generating sample data")
    i = True
    while i is True:
        try:
            import adafruit_dht
            from board import D4 as pin
            sensor_found = True
        except ModuleNotFoundError:
            sensor_found = False
        except ImportError:
            sensor_found = False
        if sensor_found is True:
            sensor = adafruit_dht.DHT22
            # pin = 7
            dht_device = adafruit_dht.DHT22(pin)
            try:
                temperature = dht_device.temperature
                humidity = dht_device.humidity
            except RuntimeError:
                temperature = None
                humidity = None
        else:
            def simulate_tempsens(tempbaseline = 20, tempvar = 5, humbaseline = 50, humvar = 5):
                temp = tempbaseline + np.random.randint(tempvar)
                hum = humbaseline + np.random.randint(humvar)
                # Sensor fails read sometimes, simulate that:
                random_fail = np.random.randint(10)
                if random_fail == 1:
                    temp = None
                    hum = None
                return temp, hum
            temperature, humidity = simulate_tempsens()
        write_data(temperature, humidity)
        print_to_console(temperature, humidity)
        time.sleep(float(CONFIG["DEFAULT"]["loginterval_s"]))


if __name__ == "__main__":
    run_tempsensor()