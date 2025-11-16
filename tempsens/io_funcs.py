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
    import board
    pin = board.D4
    print("found dht sensor")
    sensor_found = True
except (ModuleNotFoundError, ImportError):
    print("no dht sensor, generated data")
    sensor_found = False

# Allow override via environment variable for running multiple test instances
CONFIGPATH = os.environ.get('TEMPSENS_CONFIG', "config.ini")

DEFAULT_CONFIG_VALUES = {
    "loginterval_s": "2",
    "outputfile": "templog.h5",
    "device_name": "Temperature Sensor",
    "api_port": "5000",
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
    # Sensor fails read sometimes, simulate that (20% chance)
    random_fail = np.random.randint(5)
    if random_fail == 1:
        raise RuntimeError("Simulated sensor read failure (checksum error)")
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
        with h5py.File(filename, "w", locking = False) as f:
            f.create_dataset("time", (0,), maxshape = (None,), dtype = h5py.string_dtype())
            f.create_dataset("temperature", (0,), maxshape = (None,), dtype = 'f')
            f.create_dataset("humidity", (0,), maxshape = (None,), dtype = 'f')

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

def log_data(filename = CONFIG["DEFAULT"]["outputfile"]):
    global sensor_found
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

    # Common retry logic for both real and simulated sensors
    good_read = False
    retry_count = 0
    max_retries = 5
    temperature, humidity = None, None

    while good_read is False and retry_count < max_retries:
        try:
            if sensor_found is False:
                temperature, humidity = simulate_tempsens()
            else:
                dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
                temperature = dht_device.temperature
                humidity = dht_device.humidity
            good_read = True
        except RuntimeError as e:
            retry_count += 1
            if retry_count == 1:
                # Only print on first retry to reduce noise
                print(f"[{timestamp}] Sensor read failed, retrying... (checksum/timeout error is normal)")
            elif retry_count >= max_retries:
                print(f"[{timestamp}] ERROR: Failed to read sensor after {max_retries} attempts")
                temperature, humidity = None, None
            time.sleep(0.1)  # Small delay between retries
            continue

    # Skip writing failed reads to save storage and reduce lock contention
    # Server-side dashboard will insert NaN for visualization where gaps exist
    if temperature is not None and humidity is not None:
        write_data_hdf5(timestamp, temperature, humidity)
        print_to_console(timestamp, temperature, humidity)

    # ALTERNATIVE: Write NaN for failed reads (increases lock contention with networked API)
    # write_data_hdf5(timestamp, temperature, humidity)
    # print_to_console(timestamp, temperature, humidity)

    # Schedule the next run
    schedule.enter(LOGINTERVAL, 0, log_data)
    return None

def fetch_log_data_range(filename=FILENAME, start_time=None, end_time=None, limit=None):
    """
    Fetches data from .h5 file with optional time range filtering or limit.
    Returns data as a dictionary with ISO-formatted time strings (for API compatibility).

    Args:
        filename: Path to HDF5 file
        start_time: Start timestamp as string (ISO format: 'YYYY-MM-DD HH:MM:SS')
        end_time: End timestamp as string (ISO format: 'YYYY-MM-DD HH:MM:SS')
        limit: If specified, return only the last N readings (ignores time filters)

    Returns:
        Dictionary with keys 'time' (ISO strings), 'temperature', 'humidity'
    """
    # Use timeout on lock to prevent API hanging during sensor retries
    lock_acquired = file_lock.acquire(timeout=5.0)
    if not lock_acquired:
        raise TimeoutError("Could not acquire file lock within 5 seconds")

    try:
        with h5py.File(filename, "r", locking=False) as f:
            # Optimize: If limit specified, only read last N records from HDF5
            # This avoids loading the entire file (which can be slow on SD cards)
            if limit is not None:
                total_records = len(f["time"])
                start_idx = max(0, total_records - limit)
                temps = np.array(f["temperature"][start_idx:], dtype="float32")
                hums = np.array(f["humidity"][start_idx:], dtype="float32")
                times_str = np.array(f["time"][start_idx:], dtype=str)
            else:
                # Read entire dataset (needed for time range filtering)
                temps = np.array(f["temperature"], dtype="float32")
                hums = np.array(f["humidity"], dtype="float32")
                times_str = np.array(f["time"], dtype=str)
    finally:
        file_lock.release()

    # Convert string times to datetime64 for filtering
    times_dt64 = np.array(times_str, dtype=np.datetime64)

    # Apply time range filtering if specified (and no limit)
    if start_time is not None or end_time is not None:
        mask = np.ones(len(times_dt64), dtype=bool)

        if start_time is not None:
            start_dt64 = np.datetime64(start_time)
            mask &= times_dt64 >= start_dt64

        if end_time is not None:
            end_dt64 = np.datetime64(end_time)
            mask &= times_dt64 <= end_dt64

        temps = temps[mask]
        hums = hums[mask]
        times_str = times_str[mask]

    return {
        "time": times_str.tolist(),
        "temperature": temps.tolist(),
        "humidity": hums.tolist()
    }
