import configparser
import pathlib
import os
import h5py
import numpy as np
import sched, time
import datetime
import threading

# Import handling based on RasPi/dev
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

#CONFIGPATH = r"/home/weatherstation/.config.ini"
CONFIGPATH = "config.ini" # need to get from Raspi and copy to somewhere in repo

file_lock = threading.Lock()

def gen_default_config(config_loc = CONFIGPATH):
    """
    Initialises a default config.ini file for parameters
    """
    config = configparser.ConfigParser()
    config["DEFAULT"] = {'loginterval_s' : '1',
                        'RemoteLOGINTERVAL_S' : '86400',
                        'OutputFile' : 'templog.csv',
                        'TestWrite' : '8'}
    if not os.path.exists(CONFIGPATH):
        with open(CONFIGPATH, 'w') as configfile:
            config.write(configfile)
    else:
        print(f"File already exists at {CONFIGPATH}")
        print("Do you want to overwrite it?")
        overwrite = input("y/n:")
        if overwrite == "y":
            with open(CONFIGPATH, 'w') as configfile:
                config.write(configfile)
        else:
            print("Aborting... No default config generated.")

def fetch_config(config_loc = CONFIGPATH):
    """
    Generates a configparser object and reads the .config.ini into it,
    to fetch user paramters.
    """
    # Read config_loc into pathlib.Path for sanity
    config_loc = pathlib.Path(config_loc)
    # Read config
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_loc)
    return config

def write_to_config(param_str, value, config_loc = CONFIGPATH):
    # Get the config file
    config = fetch_config(CONFIGPATH)
    config.optionxform = str
    # Get contents DEFAULT section
    current_content = list(config["DEFAULT"].keys())
    if param_str not in current_content:
        raise KeyError(f"Passed str '{param_str}, which is not a parameter in config file at {CONFIGPATH}'") 
    else:
        print(f"{param_str} set to {value} in {config_loc}")
        config["DEFAULT"][param_str] = f"{value}"
        with open(CONFIGPATH, 'w') as configfile:
            config.write(configfile)

CONFIG = fetch_config(CONFIGPATH)
FILENAME = CONFIG["DEFAULT"]["outputfile"]
LOGINTERVAL = float(CONFIG["DEFAULT"]["loginterval_s"])
schedule = sched.scheduler(time.time, time.sleep)
latest_output_dict = None

def simulate_tempsens(tempbaseline = 20, tempvar = 5, humbaseline = 50, humvar = 5):
    temp = tempbaseline + np.random.randint(tempvar)
    hum = humbaseline + np.random.randint(humvar)
    # Sensor fails read sometimes, simulate that:
    random_fail = np.random.randint(5)
    if random_fail == 1:
        temp = None
        hum = None
    return temp, hum

def print_to_console(timestamp, temperature, humidity):
    if temperature is not None and humidity is not None:
        print(timestamp, temperature,"C ",humidity,"%")
    else:
        print(timestamp, "failed read")

def init_data_hdf5(filename = CONFIG["DEFAULT"]["outputfile"]):
    # Create file if it doesn't exist
    if pathlib.Path(CONFIG["DEFAULT"]["outputfile"]).exists() is False:
        print("Save file doesn't exist, creating it at", CONFIG["DEFAULT"]["outputfile"])
        with h5py.File(filename, "w") as f:
            f.create_dataset("time", (0,), maxshape = (None,), dtype = h5py.string_dtype())
            f.create_dataset("temperature", (0,), maxshape = (None,), dtype = 'f')
            f.create_dataset("humidity", (0,), maxshape = (None,), dtype = 'f')

# def write_data(temperature, humidity, filename = CONFIG["DEFAULT"]["outputfile"]):        
#     if pathlib.Path(filename).exists() is False:
#         with open(filename, "w") as f:
#             f.write("time,temperature,humidity\n")
#     with open(filename, "a") as f:
#         f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')},{temperature},{humidity}\n")


def write_data_hdf5(timestamp, temperature, humidity, filename = CONFIG["DEFAULT"]["outputfile"]):
    # Append to file and resize continously
    with file_lock:
        with h5py.File(filename, "a") as f:
            f["time"].resize((f["time"].shape[0] + 1,))
            f["time"][-1] = timestamp
            f["temperature"].resize((f["temperature"].shape[0] + 1,))
            f["temperature"][-1] = temperature
            f["humidity"].resize((f["humidity"].shape[0] + 1,))
            f["humidity"][-1] = humidity


def log_data(filename = CONFIG["DEFAULT"]["outputfile"], gen_data = False):
    print("Im logging!")
    global sensor_found
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    if sensor_found is False:
        temperature, humidity = simulate_tempsens()
    if sensor_found is True:
        dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
        good_read = False
        while good_read is False:
            try:
                print("try")
                temperature = dht_device.temperature
                humidity = dht_device.humidity
            except RuntimeError as e:
                print("BAD READ TRYING AGAIN")
                continue
            else:
                break
                # temperature = None
                # humidity = None
    # Append to file
    print(temperature, humidity)
    write_data_hdf5(timestamp, temperature, humidity)
    # Print to console
    print_to_console(timestamp, temperature, humidity)
    # # Schedule the next run
    schedule.enter(LOGINTERVAL, 1, log_data)
    return None 


def _fetchlog(filename = FILENAME):
    """
    Fetches data from .h5 file and returns it as a dictionary
    """
    global latest_output_dict
    with file_lock:
        with h5py.File(filename, "r") as f:     
            # Now construct a dictionary out of the file
            times = np.char.decode(np.array(list(f["time"]))).astype(np.datetime64)
            temps = np.array(list(f["temperature"]))
            hums = np.array(list(f["humidity"]))
            output_dict = {"time": times, "temperature": temps, "humidity": hums}
            latest_output_dict = output_dict
            return latest_output_dict
    
def fetch_log_data():
    print("im fetching!")
    schedule.enter(LOGINTERVAL, 1, fetch_log_data)
    # global latest_output_dict
    # schedule.enter(LOGINTERVAL, 2, _fetchlog)
    # schedule.run()
    # return latest_output_dict
    return _fetchlog()