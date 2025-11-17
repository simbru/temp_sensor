import configparser
import pathlib
import os
import sqlite3
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
    "outputfile": "templog.db",
    "device_name": "Temperature Sensor",
    "api_port": "5000",
    "max_temp_delta_c": "3.0",
    "max_humidity_delta_pct": "10.0",
}

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

# Store last valid reading for spike filtering
last_valid_reading = {"temperature": None, "humidity": None}

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

def init_database(filename=None, use_wal=None):
    """Initialize SQLite database with WAL mode for crash safety.

    Args:
        filename: Path to database file (uses config default if None)
        use_wal: Force WAL mode on/off. If None, auto-detect (disable on WSL /mnt/ paths)
    """
    if filename is None:
        filename = CONFIG["DEFAULT"]["outputfile"]

    db_path = pathlib.Path(filename)

    if not db_path.exists():
        print(f"Database doesn't exist, creating it at {filename}")

    # Auto-detect if we should use WAL mode
    if use_wal is None:
        # Disable WAL on WSL /mnt/ paths (Windows filesystem mount)
        # WAL mode doesn't work reliably across filesystem boundaries
        abs_path = pathlib.Path(filename).resolve()
        use_wal = not str(abs_path).startswith('/mnt/')

    with sqlite3.connect(filename) as conn:
        cursor = conn.cursor()

        # Enable WAL mode for crash safety and better concurrency (or DELETE for WSL)
        if use_wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        else:
            cursor.execute("PRAGMA journal_mode=DELETE")
            print(f"✓ Initialized database (journal_mode=DELETE for WSL compatibility)")

        # SD card optimizations for Raspberry Pi
        cursor.execute("PRAGMA synchronous=NORMAL")   # Balance safety/speed (still crash-safe with WAL)
        cursor.execute("PRAGMA cache_size=-64000")     # 64MB cache to reduce SD wear
        cursor.execute("PRAGMA temp_store=MEMORY")     # Use RAM for temporary tables
        cursor.execute("PRAGMA mmap_size=268435456")   # 256MB memory-mapped I/O for faster reads

        # Create table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL UNIQUE,
                temperature REAL,
                humidity REAL
            )
        """)

        # Create index on timestamp for fast queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON sensor_data(timestamp DESC)
        """)

        conn.commit()

def write_data(timestamp, temperature, humidity, filename=None):
    """Write sensor reading to SQLite database."""
    if filename is None:
        filename = CONFIG["DEFAULT"]["outputfile"]

    with sqlite3.connect(filename) as conn:
        cursor = conn.cursor()
        # Ensure journal mode matches initialization (critical for WSL)
        abs_path = pathlib.Path(filename).resolve()
        if str(abs_path).startswith('/mnt/'):
            cursor.execute("PRAGMA journal_mode=DELETE")
        cursor.execute(
            "INSERT OR IGNORE INTO sensor_data (timestamp, temperature, humidity) VALUES (?, ?, ?)",
            (timestamp, temperature, humidity)
        )
        conn.commit()

def log_data(filename = CONFIG["DEFAULT"]["outputfile"]):
    global sensor_found, last_valid_reading
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

    # Spike filtering: reject readings with unrealistic deltas from previous reading
    # (DHT22 sensors sometimes produce spurious readings that pass checksum but are physically impossible)
    if temperature is not None and humidity is not None:
        max_temp_delta = float(CONFIG["DEFAULT"]["max_temp_delta_c"])
        max_humidity_delta = float(CONFIG["DEFAULT"]["max_humidity_delta_pct"])

        # Check if we have a previous valid reading to compare against
        if last_valid_reading["temperature"] is not None:
            temp_delta = abs(temperature - last_valid_reading["temperature"])
            humidity_delta = abs(humidity - last_valid_reading["humidity"])

            if temp_delta > max_temp_delta or humidity_delta > max_humidity_delta:
                print(f"[{timestamp}] SPIKE DETECTED: temp delta={temp_delta:.1f}°C, humidity delta={humidity_delta:.1f}% - rejecting reading")
                # Skip this reading entirely - don't write to database
                temperature, humidity = None, None

    # Skip writing failed reads or spike-filtered reads to save storage
    # Server-side dashboard will insert NaN for visualization where gaps exist
    if temperature is not None and humidity is not None:
        write_data(timestamp, temperature, humidity, filename)
        print_to_console(timestamp, temperature, humidity)
        # Update last valid reading after successful write
        last_valid_reading["temperature"] = temperature
        last_valid_reading["humidity"] = humidity

    # Schedule the next run
    schedule.enter(LOGINTERVAL, 0, log_data)
    return None

def fetch_log_data_range(filename=FILENAME, start_time=None, end_time=None, limit=None):
    """
    Fetches data from SQLite database with optional time range filtering or limit.
    Returns data as a dictionary with ISO-formatted time strings (for API compatibility).

    Args:
        filename: Path to SQLite database file
        start_time: Start timestamp as string (ISO format: 'YYYY-MM-DD HH:MM:SS')
        end_time: End timestamp as string (ISO format: 'YYYY-MM-DD HH:MM:SS')
        limit: If specified, return only the last N readings (ignores time filters)

    Returns:
        Dictionary with keys 'time' (ISO strings), 'temperature', 'humidity'
    """
    with sqlite3.connect(filename) as conn:
        cursor = conn.cursor()

        # Build query based on parameters
        if limit is not None and not start_time and not end_time:
            # Original behavior: get last N readings regardless of range
            query = """
                SELECT timestamp, temperature, humidity
                FROM (
                    SELECT timestamp, temperature, humidity
                    FROM sensor_data
                    ORDER BY timestamp DESC
                    LIMIT ?
                )
                ORDER BY timestamp ASC
            """
            cursor.execute(query, (limit,))
        else:
            conditions = []
            params = []

            if start_time:
                conditions.append("timestamp >= ?")
                params.append(start_time)
            if end_time:
                conditions.append("timestamp <= ?")
                params.append(end_time)

            where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
            query = f"SELECT timestamp, temperature, humidity FROM sensor_data{where_clause} ORDER BY timestamp"

            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)

        rows = cursor.fetchall()

    if not rows:
        return {"time": [], "temperature": [], "humidity": []}

    # Convert to lists
    times_str = [row[0] for row in rows]
    temps = [row[1] for row in rows]
    hums = [row[2] for row in rows]

    return {
        "time": times_str,
        "temperature": temps,
        "humidity": hums
    }
