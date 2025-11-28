import configparser
import pathlib
import os
import sqlite3
import datetime
import threading
import time
import sched

# Import sensor drivers module
from . import sensor_drivers

# Global sensor instance (initialized on first log_data() call)
_sensor_instance = None

# Allow override via environment variable for running multiple test instances
CONFIGPATH = os.environ.get('TEMPSENS_CONFIG', "config.ini")

DEFAULT_CONFIG_VALUES = {
    "loginterval_s": "2",
    "outputfile": "templog.db",
    "device_name": "Temperature Sensor",
    "api_port": "5000",
    "max_temp_delta_c": "3.0",
    "max_humidity_delta_pct": "10.0",
    "temp_offset_c": "0.0",
    "humidity_offset_pct": "0.0",
    "sensor_type": "AUTO",  # Options: AUTO, DHT22, AHT20, BME280, ENVIROPLUS, SIMULATED
    "enable_lcd_display": "False",  # Enable LCD display for Enviro+ boards
    "write_log_file": "True",  # Write logs to file (can disable for systemd journal only)
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
last_valid_reading: dict[str, float | None] = {"temperature": None, "humidity": None}

# Track consecutive None readings for hardware failure detection
HARDWARE_FAILURE_THRESHOLD = 5  # Consider hardware failed after 5 consecutive None readings

# Track number of successful readings for spike detection warmup
# (Enviro+ BME280 needs 5 samples for CPU compensation to stabilize)
_successful_reading_count = 0
SPIKE_DETECTION_WARMUP_SAMPLES = 5  # Skip spike detection for first N readings

def _get_consecutive_failures():
    """Get consecutive failure count from file (shared across processes)."""
    try:
        failure_file = pathlib.Path(CONFIG["DEFAULT"]["outputfile"]).parent / ".sensor_failures"
        if failure_file.exists():
            return int(failure_file.read_text().strip())
    except Exception:
        pass
    return 0

def _set_consecutive_failures(count):
    """Set consecutive failure count to file (shared across processes)."""
    try:
        failure_file = pathlib.Path(CONFIG["DEFAULT"]["outputfile"]).parent / ".sensor_failures"
        failure_file.write_text(str(count))
    except Exception:
        pass

def _get_sensor():
    """
    Get or initialize the sensor instance based on config.

    Returns:
        Sensor object implementing SensorInterface
    """
    global _sensor_instance

    if _sensor_instance is None:
        sensor_type = CONFIG["DEFAULT"].get("sensor_type", "AUTO")
        
        # Read sensor calibration settings from config
        calibration_kwargs = {
            "compensation_factor": CONFIG["DEFAULT"].getfloat("cpu_temp_factor", 2.25),
            "temp_scale": CONFIG["DEFAULT"].getfloat("temp_scale", 1.0),
            "temp_offset": CONFIG["DEFAULT"].getfloat("temp_calibration_offset", 0.0),
            "humidity_scale": CONFIG["DEFAULT"].getfloat("humidity_scale", 1.0),
            "humidity_offset": CONFIG["DEFAULT"].getfloat("humidity_calibration_offset", 0.0),
            "pressure_scale": CONFIG["DEFAULT"].getfloat("pressure_scale", 1.0),
            "pressure_offset": CONFIG["DEFAULT"].getfloat("pressure_calibration_offset", 0.0),
            "light_scale": CONFIG["DEFAULT"].getfloat("light_scale", 1.0),
            "light_offset": CONFIG["DEFAULT"].getfloat("light_calibration_offset", 0.0),
            "noise_scale": CONFIG["DEFAULT"].getfloat("noise_scale", 1.0),
            "noise_offset": CONFIG["DEFAULT"].getfloat("noise_calibration_offset", 0.0),
        }
        
        _sensor_instance = sensor_drivers.get_sensor(sensor_type, **calibration_kwargs)
        print(f"Initialized {_sensor_instance.name} sensor")

    return _sensor_instance

def format_timestamp(timestamp_ms):
    """Convert milliseconds timestamp to human-readable string."""
    from datetime import datetime
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def print_to_console(timestamp, temperature, humidity):
    """Print reading to console with human-readable timestamp."""
    timestamp_str = format_timestamp(timestamp)

    if temperature is not None and humidity is not None:
        print(f"[{timestamp_str}] {temperature:.1f}°C  {humidity:.1f}%")
    else:
        print(f"[{timestamp_str}] failed read")

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

        # Create table with extended sensor support (pressure, light, noise)
        # Columns are nullable - only store what the sensor provides
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL UNIQUE,
                temperature REAL,
                humidity REAL,
                pressure REAL,
                light REAL,
                noise REAL
            )
        """)

        # Create index on timestamp for fast queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON sensor_data(timestamp DESC)
        """)

        conn.commit()

def write_data(timestamp, temperature, humidity, filename=None, pressure=None, light=None, noise=None):
    """Write sensor reading to SQLite database.

    Args:
        timestamp: Integer timestamp in milliseconds since epoch
        temperature: Temperature in Celsius (or None)
        humidity: Humidity percentage (or None)
        filename: Path to database file (uses config default if None)
        pressure: Atmospheric pressure in hPa (optional, for Enviro+/BME280)
        light: Light level in lux (optional, for Enviro+)
        noise: Noise level in dBA (optional, for Enviro+)
    """
    if filename is None:
        filename = CONFIG["DEFAULT"]["outputfile"]

    with sqlite3.connect(filename) as conn:
        cursor = conn.cursor()
        # Ensure journal mode matches initialization (critical for WSL)
        abs_path = pathlib.Path(filename).resolve()
        if str(abs_path).startswith('/mnt/'):
            cursor.execute("PRAGMA journal_mode=DELETE")

        # Defensive: Ensure table exists before writing (protects against race condition)
        # This is idempotent and fast - SQLite checks schema cache before executing
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL UNIQUE,
                temperature REAL,
                humidity REAL,
                pressure REAL,
                light REAL,
                noise REAL
            )
        """)

        cursor.execute(
            """INSERT OR IGNORE INTO sensor_data
               (timestamp, temperature, humidity, pressure, light, noise)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp, temperature, humidity, pressure, light, noise)
        )
        conn.commit()

def log_data(filename = CONFIG["DEFAULT"]["outputfile"]):
    global last_valid_reading, _successful_reading_count
    # Generate INTEGER timestamp (milliseconds since epoch)
    timestamp = int(datetime.datetime.now().timestamp() * 1000)

    # Get sensor instance (initializes on first call)
    sensor = _get_sensor()

    # Common retry logic for all sensors
    good_read = False
    retry_count = 0
    max_retries = 5
    temperature, humidity = None, None
    pressure, light, noise = None, None, None

    # Check if sensor supports extended interface (Enviro+, etc.)
    has_extended_data = hasattr(sensor, 'read_extended')

    while good_read is False and retry_count < max_retries:
        try:
            if has_extended_data:
                # Use extended interface for multi-sensor devices
                data = sensor.read_extended()
                temperature = data.get("temperature")
                humidity = data.get("humidity")
                pressure = data.get("pressure")
                light = data.get("light")
                noise = data.get("noise")
            else:
                # Standard temp/humidity only
                temperature, humidity = sensor.read()
            good_read = True
        except RuntimeError as e:
            retry_count += 1
            if retry_count == 1:
                # Only print on first retry to reduce noise
                print(f"[{format_timestamp(timestamp)}] Sensor read failed, retrying... (checksum/timeout error is normal)")
            elif retry_count >= max_retries:
                print(f"[{format_timestamp(timestamp)}] ERROR: Failed to read sensor after {max_retries} attempts")
                temperature, humidity = None, None
                pressure, light, noise = None, None, None
            time.sleep(0.1)  # Small delay between retries
            continue

    # Check for None readings from sensor (hardware failure)
    if temperature is None or humidity is None:
        consecutive_none_readings = _get_consecutive_failures() + 1
        _set_consecutive_failures(consecutive_none_readings)
        print(f"[{format_timestamp(timestamp)}] ERROR: Sensor returned None values (temp={temperature}, hum={humidity}) - possible hardware failure!")
        if consecutive_none_readings >= HARDWARE_FAILURE_THRESHOLD:
            print(f"[{format_timestamp(timestamp)}] CRITICAL: {consecutive_none_readings} consecutive None readings - hardware failure detected!")
        print(f"[{format_timestamp(timestamp)}] Hardware may need replacement. Skipping write to database.")
    else:
        # Reset counter on successful read
        _set_consecutive_failures(0)

    # Apply calibration offsets to raw readings
    if temperature is not None and humidity is not None:
        temp_offset = float(CONFIG["DEFAULT"]["temp_offset_c"])
        humidity_offset = float(CONFIG["DEFAULT"]["humidity_offset_pct"])
        temperature += temp_offset
        humidity += humidity_offset

    # Spike filtering: reject readings with unrealistic deltas from previous reading
    # (DHT22 sensors sometimes produce spurious readings that pass checksum but are physically impossible)
    # Skip spike detection during warmup period (first N readings) to allow sensor stabilization
    if temperature is not None and humidity is not None:
        max_temp_delta = float(CONFIG["DEFAULT"]["max_temp_delta_c"])
        max_humidity_delta = float(CONFIG["DEFAULT"]["max_humidity_delta_pct"])

        # During warmup period, increment counter but DON'T write to database
        # This prevents unstable initial readings from polluting the data
        if _successful_reading_count < SPIKE_DETECTION_WARMUP_SAMPLES:
            _successful_reading_count += 1
            print(f"[{format_timestamp(timestamp)}] Warmup reading {_successful_reading_count}/{SPIKE_DETECTION_WARMUP_SAMPLES} - skipping write to database")
            # Schedule next run and return early - don't write warmup readings
            schedule.enter(LOGINTERVAL, 0, log_data)
            return None

        # After warmup: apply spike detection
        # Check if we have a previous valid reading to compare against
        if last_valid_reading["temperature"] is not None and last_valid_reading["humidity"] is not None:
            temp_delta = abs(temperature - last_valid_reading["temperature"])
            humidity_delta = abs(humidity - last_valid_reading["humidity"])

            if temp_delta > max_temp_delta or humidity_delta > max_humidity_delta:
                print(f"[{format_timestamp(timestamp)}] SPIKE DETECTED: temp delta={temp_delta:.1f}°C, humidity delta={humidity_delta:.1f}% - rejecting reading")
                # Skip this reading entirely - don't write to database
                temperature, humidity = None, None
                pressure, light, noise = None, None, None

    # Skip writing failed reads or spike-filtered reads to save storage
    # Server-side dashboard will insert NaN for visualization where gaps exist
    if temperature is not None and humidity is not None:
        write_data(timestamp, temperature, humidity, filename, pressure, light, noise)
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
    Returns data as a dictionary with INTEGER timestamps (milliseconds since epoch).

    Args:
        filename: Path to SQLite database file
        start_time: Start timestamp as INTEGER (milliseconds since epoch) or None
        end_time: End timestamp as INTEGER (milliseconds since epoch) or None
        limit: If specified, return only the last N readings (ignores time filters)

    Returns:
        Dictionary with keys 'time' (INTEGER milliseconds), 'temperature', 'humidity',
        and optionally 'pressure', 'light', 'noise' if available from sensor
    """
    with sqlite3.connect(filename) as conn:
        cursor = conn.cursor()

        # Build query based on parameters
        if limit is not None and not start_time and not end_time:
            # Original behavior: get last N readings regardless of range
            query = """
                SELECT timestamp, temperature, humidity, pressure, light, noise
                FROM (
                    SELECT timestamp, temperature, humidity, pressure, light, noise
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
            query = f"SELECT timestamp, temperature, humidity, pressure, light, noise FROM sensor_data{where_clause} ORDER BY timestamp"

            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)

        rows = cursor.fetchall()

    if not rows:
        return {
            "time": [],
            "temperature": [],
            "humidity": [],
            "pressure": [],
            "light": [],
            "noise": []
        }

    # Convert to lists (timestamps are already INTEGER milliseconds from database)
    times_ms = [row[0] for row in rows]
    temps = [row[1] for row in rows]
    hums = [row[2] for row in rows]
    pressures = [row[3] for row in rows]
    lights = [row[4] for row in rows]
    noises = [row[5] for row in rows]

    return {
        "time": times_ms,
        "temperature": temps,
        "humidity": hums,
        "pressure": pressures,
        "light": lights,
        "noise": noises
    }
