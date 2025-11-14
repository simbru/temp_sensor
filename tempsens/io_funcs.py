import configparser
import pathlib
import os
import h5py
import numpy as np
import datetime
import threading
import time 
import sched

# Import handling based on RasPi/dev
try:
    import adafruit_dht
    from board import D4 as pin
    print("found dht sensor")
    sensor_found = True
except (ModuleNotFoundError, ImportError):
    print("no dht sensor, generated data")
    sensor_found = False

#CONFIGPATH = r"/home/weatherstation/.config.ini"
CONFIGPATH = "config.ini" # need to get from Raspi and copy to somewhere in repo

DEFAULT_CONFIG_VALUES = {
    "loginterval_s": "1",
    "remoteinterval_s": "86400",
    "outputfile": "templog.h5",
    "temperature_min_c": "0",
    "temperature_max_c": "40",
    "temperature_margin_pct": "0.05",
    "temperature_window_c": "10",
    "temperature_range_update_s": "10",
    "humidity_min_pct": "0",
    "humidity_max_pct": "100",
    "humidity_margin_pct": "0.05",
    "humidity_window_pct": "50",
    "humidity_range_update_s": "10",
    "LastRead": "None",
}

file_lock = threading.Lock()

def gen_default_config(config_loc=CONFIGPATH, force=False):
    """Write a default config file with sane parameters."""
    config_loc = pathlib.Path(config_loc)
    config = configparser.ConfigParser()
    config.optionxform = str  # type: ignore[attr-defined]
    config["DEFAULT"] = DEFAULT_CONFIG_VALUES.copy()

    if config_loc.exists() and not force:
        return config

    with config_loc.open('w') as configfile:
        config.write(configfile)
    return config

def fetch_config(config_loc = CONFIGPATH):
    """
    Generates a configparser object and reads the .config.ini into it,
    to fetch user paramters.
    """
    # Read config_loc into pathlib.Path for sanity
    config_loc = pathlib.Path(config_loc)
    if not config_loc.exists():
        gen_default_config(str(config_loc))
    # Read config
    config = configparser.ConfigParser()
    config.optionxform = str  # type: ignore[attr-defined]
    config.read(str(config_loc))

    defaults = config["DEFAULT"]
    updated = False
    for key, value in DEFAULT_CONFIG_VALUES.items():
        if key not in defaults:
            defaults[key] = value
            updated = True

    if updated:
        with config_loc.open('w') as configfile:
            config.write(configfile)
    return config

def write_to_config(param_str, value, config_loc = CONFIGPATH):
    # Get the config file
    config = fetch_config(CONFIGPATH)
    config.optionxform = str  # type: ignore[attr-defined]
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
        with h5py.File(filename, "w", locking = True) as f:
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
        with h5py.File(filename, "a", locking = False) as f:
            f["time"].resize((f["time"].shape[0] + 1,))
            f["temperature"].resize((f["temperature"].shape[0] + 1,))
            f["humidity"].resize((f["humidity"].shape[0] + 1,))
            f["time"][-1] = timestamp
            f["temperature"][-1] = temperature
            f["humidity"][-1]  = humidity

def log_data(filename = CONFIG["DEFAULT"]["outputfile"], gen_data = False):
    # print("Im logging!")
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
            except RuntimeError:
                print("BAD READ TRYING AGAIN")
                continue
            else:
                break
                # temperature = None
                # humidity = None
    # Append to file
    write_data_hdf5(timestamp, temperature, humidity)
    # Print to console
    print_to_console(timestamp, temperature, humidity)
    # # Schedule the next run
    schedule.enter(LOGINTERVAL, 0, log_data)
    return None 

latest_output_dict = None
def _fetchlog(filename=FILENAME):
    """
    Fetches data from .h5 file and returns it as a dictionary.
    Time is returned as milliseconds since epoch (float) for Bokeh compatibility.
    """
    with file_lock:
        with h5py.File(filename, "r", locking = False) as f:
            temps = np.array(f["temperature"], dtype = "float32")
            hums  = np.array(f["humidity"], dtype = "float32")
            times_dt64 = np.array(f["time"], dtype = np.datetime64)

    # Convert datetime64 to milliseconds since epoch for Bokeh compatibility
    # This prevents type mixing issues between datetime64 and float
    times_ms = times_dt64.astype('datetime64[ms]').astype(np.int64).astype(np.float64)

    # Assign to global variable only after processing
    global latest_output_dict
    latest_output_dict = {"time": times_ms, "temperature": temps, "humidity": hums}
    return None

def fetch_log_data():
    # Schedule the next run
    schedule.enter(LOGINTERVAL, 1, fetch_log_data)
    # Fetch immediately the first time to ensure data is loaded
    _fetchlog()
    # Return the latest data
    global latest_output_dict
    return latest_output_dict

def cleanup(PID_FILE):
    if os.path.isfile(PID_FILE):
        os.remove(PID_FILE) 

