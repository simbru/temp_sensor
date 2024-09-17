try:
    import adafruit_dht
    from board import D4 as pin
    print("found dht sensor")
    sensor_found = True
except ModuleNotFoundError:
    print("no dht sensor, generating data")
    sensor_found = False
except ImportError:
    print("no dht sensor, generating data")
    sensor_found = False
import time 
import datetime
import numpy as np
import pathlib
import h5py
import sched, time
# Local imports
import io_funcs

CONFIG = io_funcs.fetch_config()
print(dict(CONFIG))

LOGINTERVAL = float(CONFIG["DEFAULT"]["loginterval_s"])
schedule = sched.scheduler(time.time, time.sleep)

# def write_data(temperature, humidity, filename = CONFIG["DEFAULT"]["outputfile"]):        
#     if pathlib.Path(filename).exists() is False:
#         with open(filename, "w") as f:
#             f.write("time,temperature,humidity\n")
#     with open(filename, "a") as f:
#         f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')},{temperature},{humidity}\n")

def init_data_hdf5(filename = CONFIG["DEFAULT"]["outputfile"]):
    # Create file if it doesn't exist
    with h5py.File(filename, "w") as f:
        f.create_dataset("time", (0,), maxshape = (None,), dtype = h5py.string_dtype())
        f.create_dataset("temperature", (0,), maxshape = (None,), dtype = 'f')
        f.create_dataset("humidity", (0,), maxshape = (None,), dtype = 'f')

def write_data_hdf5(timestamp, temperature, humidity, filename = CONFIG["DEFAULT"]["outputfile"]):
    # Append to file and resize continously
    with h5py.File(filename, "a") as f:
        f["time"].resize((f["time"].shape[0] + 1,))
        f["time"][-1] = timestamp
        f["temperature"].resize((f["temperature"].shape[0] + 1,))
        f["temperature"][-1] = temperature
        f["humidity"].resize((f["humidity"].shape[0] + 1,))
        f["humidity"][-1] = humidity

def print_to_console(timestamp, temperature, humidity):
    if temperature is not None and humidity is not None:
        print(timestamp, temperature,"C ",humidity,"%")
    else:
        print(timestamp, "failed read")

def run_tempsensor():
    global sensor_found
    if sensor_found is False:
        print("no sensor, generating sample data")
    i = True
    while i is True:
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        if sensor_found is True:
            dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
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
                random_fail = np.random.randint(5)
                if random_fail == 1:
                    temp = None
                    hum = None
                return temp, hum
            temperature, humidity = simulate_tempsens()
        # write_data(temperature, humidity)
        # If file doesn't exist, create it
        if pathlib.Path(CONFIG["DEFAULT"]["outputfile"]).exists() is False:
            init_data_hdf5()
        # Append to file
        write_data_hdf5(timestamp, temperature, humidity)
        # Print to console
        print_to_console(timestamp, temperature, humidity)
        time.sleep(float(CONFIG["DEFAULT"]["loginterval_s"]))

def simulate_tempsens(tempbaseline = 20, tempvar = 5, humbaseline = 50, humvar = 5):
    temp = tempbaseline + np.random.randint(tempvar)
    hum = humbaseline + np.random.randint(humvar)
    # Sensor fails read sometimes, simulate that:
    random_fail = np.random.randint(5)
    if random_fail == 1:
        temp = None
        hum = None
    return temp, hum

def fetch_log_data(filename = CONFIG["DEFAULT"]["outputfile"]):
    global sensor_found
    # If file doesn't exist, create it
    if pathlib.Path(CONFIG["DEFAULT"]["outputfile"]).exists() is False:
        init_data_hdf5()
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    if sensor_found is False:
        temperature, humidity = simulate_tempsens()
    if sensor_found is True:
        dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
        try:
            temperature = dht_device.temperature
            humidity = dht_device.humidity
        except RuntimeError:
            temperature = None
            humidity = None
    # Append to file
    write_data_hdf5(timestamp, temperature, humidity)
    # Print to console
    print_to_console(timestamp, temperature, humidity)
    # Schedule the next run
    schedule.enter(LOGINTERVAL, 1, fetch_log_data)
    return None 

def run_tempsensor_test():
    print("Running tempsensor test")
    schedule.enter(LOGINTERVAL, 1, fetch_log_data)
    schedule.run()

if __name__ == "__main__":
    run_tempsensor_test()