#!/usr/bin/env python3
"""
Run multiple test sensor clients concurrently for testing the multi-sensor dashboard.
Each client runs with its own config file and port.

Generates historical data before starting live simulated data.
Historical data interval matches each sensor's loginterval_s config.
"""
import multiprocessing
import sys
import time
import os
import argparse
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np

# Add project root to Python path so imports work in subprocesses
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_metadata_path(config_file: str) -> Path:
    """Get the metadata file path for a given config."""
    from tempsens import io_funcs
    config = io_funcs.fetch_config(config_file)
    db_path = Path(config["DEFAULT"]["outputfile"])
    return db_path.with_suffix('.meta.json')


def load_metadata(metadata_path: Path) -> dict:
    """Load metadata from file, return empty dict if not found."""
    if metadata_path.exists():
        try:
            with open(metadata_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_metadata(metadata_path: Path, days: int, interval_seconds: int, num_points: int):
    """Save generation metadata to file."""
    metadata = {
        "generated_at": datetime.now().isoformat(),
        "days": days,
        "interval_seconds": interval_seconds,
        "num_points": num_points,
        "start_timestamp": (datetime.now() - timedelta(days=days)).isoformat(),
        "end_timestamp": datetime.now().isoformat(),
    }
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)


def should_regenerate(config_file: str, requested_days: int) -> bool:
    """Check if we need to regenerate historical data."""
    from tempsens import io_funcs
    
    config = io_funcs.fetch_config(config_file)
    db_path = Path(config["DEFAULT"]["outputfile"])
    metadata_path = get_metadata_path(config_file)
    
    # If database doesn't exist, must regenerate
    if not db_path.exists():
        return True
    
    # If metadata doesn't exist, assume we need to regenerate
    if not metadata_path.exists():
        return True
    
    # Load metadata and check if parameters match
    metadata = load_metadata(metadata_path)
    
    # Check if days match
    if metadata.get("days") != requested_days:
        return True
    
    # Check if interval matches current config
    interval_seconds = int(config["DEFAULT"]["loginterval_s"])
    if metadata.get("interval_seconds") != interval_seconds:
        return True
    
    # Everything matches, no need to regenerate
    return False


def generate_historical_data(config_file: str, instance_name: str, days: int = 14):
    """Generate historical test data for a sensor.
    
    Args:
        config_file: Path to sensor config file
        instance_name: Display name for logging
        days: Number of days of historical data to generate
    """
    from tempsens import io_funcs

    # Check if we need to regenerate
    if not should_regenerate(config_file, days):
        metadata_path = get_metadata_path(config_file)
        metadata = load_metadata(metadata_path)
        print(f"[{instance_name}] ✓ Historical data already exists ({metadata.get('num_points', 0):,} points, {days} days)")
        print(f"[{instance_name}]   Generated: {metadata.get('generated_at', 'unknown')}")
        print(f"[{instance_name}]   Use --force-regenerate to recreate")
        return

    # Load config to get database path and interval
    config = io_funcs.fetch_config(config_file)
    db_path = config["DEFAULT"]["outputfile"]
    interval_seconds = int(config["DEFAULT"]["loginterval_s"])
    
    # Delete old database if it exists (fresh start)
    db_file = Path(db_path)
    if db_file.exists():
        db_file.unlink()
        print(f"[{instance_name}] Deleted old database: {db_path}")

    # Delete WAL/SHM files if they exist (can cause corruption)
    wal_file = Path(str(db_file) + '-wal')
    shm_file = Path(str(db_file) + '-shm')
    if wal_file.exists():
        wal_file.unlink()
        print(f"[{instance_name}] Deleted WAL file: {wal_file}")
    if shm_file.exists():
        shm_file.unlink()
        print(f"[{instance_name}] Deleted SHM file: {shm_file}")

    # Delete old metadata
    metadata_path = get_metadata_path(config_file)
    if metadata_path.exists():
        metadata_path.unlink()

    # Initialize database (auto-detects WSL and uses appropriate journal mode)
    io_funcs.init_database(db_path)
    
    # Calculate number of data points
    total_seconds = days * 24 * 60 * 60
    num_points = total_seconds // interval_seconds
    
    print(f"[{instance_name}] Generating {days} days of historical data at {interval_seconds}s intervals...")
    print(f"[{instance_name}] Total data points: {num_points:,}")

    # Sensor-specific baselines (so sensors look different)
    sensor_profiles = {
        "Test Sensor 1": {"temp_base": 22.0, "temp_var": 2.0, "hum_base": 45, "hum_var": 5},
        "Test Sensor 2": {"temp_base": 20.0, "temp_var": 1.5, "hum_base": 55, "hum_var": 8},
        "Test Sensor 3": {"temp_base": 24.0, "temp_var": 3.0, "hum_base": 50, "hum_var": 10},
    }

    device_name = config["DEFAULT"]["device_name"]
    profile = sensor_profiles.get(device_name, {"temp_base": 21.0, "temp_var": 2.0, "hum_base": 50, "hum_var": 5})

    # Generate data points at sensor's configured interval for the past N days
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    current_time = start_time
    data_points = 0

    # Initialize random walk values
    temperature = profile["temp_base"]
    humidity = profile["hum_base"]

    # Collect data points in batches for efficient insertion
    data_batch = []
    batch_size = 10000  # Insert in chunks to show progress
    
    print(f"[{instance_name}] Starting generation (this may take a moment)...")

    while current_time <= end_time:
        # Random walk with mean reversion
        temp_change = np.random.normal(0, 0.3)  # Small random changes
        hum_change = np.random.normal(0, 0.5)

        # Mean reversion (drift back toward baseline)
        temperature += temp_change - 0.1 * (temperature - profile["temp_base"])
        humidity += hum_change - 0.1 * (humidity - profile["hum_base"])

        # Add some daily variation (warmer during day, cooler at night)
        hour_of_day = current_time.hour
        daily_temp_offset = 1.0 * np.sin((hour_of_day - 6) * np.pi / 12)  # Peak at ~2pm
        temperature_with_daily = temperature + daily_temp_offset

        # Keep within realistic bounds
        temperature_with_daily = np.clip(temperature_with_daily,
                                         profile["temp_base"] - profile["temp_var"] * 2,
                                         profile["temp_base"] + profile["temp_var"] * 2)
        humidity = np.clip(humidity,
                          profile["hum_base"] - profile["hum_var"] * 2,
                          profile["hum_base"] + profile["hum_var"] * 2)

        # Generate INTEGER timestamp (milliseconds since epoch)
        timestamp_ms = int(current_time.timestamp() * 1000)

        # Add to batch
        data_batch.append((timestamp_ms, temperature_with_daily, humidity))

        data_points += 1
        current_time += timedelta(seconds=interval_seconds)
        
        # Write in batches to show progress and avoid memory issues
        if len(data_batch) >= batch_size:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                # Set journal mode for this connection (WSL compatibility)
                cursor.execute("PRAGMA journal_mode=DELETE")
                cursor.executemany(
                    "INSERT OR IGNORE INTO sensor_data (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                    data_batch
                )
                conn.commit()
            print(f"[{instance_name}] Progress: {data_points:,} / {num_points:,} points ({100*data_points/num_points:.1f}%)")
            data_batch = []

    # Write remaining data points
    if data_batch:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            # Set journal mode for this connection (WSL compatibility)
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.executemany(
                "INSERT OR IGNORE INTO sensor_data (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                data_batch
            )
            conn.commit()

    print(f"[{instance_name}] ✓ Generated {data_points:,} historical data points")
    
    # Save metadata
    save_metadata(get_metadata_path(config_file), days, interval_seconds, data_points)
    print(f"[{instance_name}] ✓ Saved generation metadata")


def run_client_instance(config_file: str, instance_name: str):
    """Run a single client instance with a specific config file."""
    # Set environment variable so io_funcs uses this config
    os.environ['TEMPSENS_CONFIG'] = config_file

    # Import after setting env var so it picks up the right config
    import uvicorn
    from tempsens.api_server import app

    print(f"[{instance_name}] Starting with config: {config_file}")

    # Get config for port info
    from tempsens import io_funcs
    config = io_funcs.fetch_config(config_file)
    api_port = int(config["DEFAULT"].get("api_port", 5000))
    device_name = config["DEFAULT"].get("device_name", "Unknown")

    # Start sensor logger in a subprocess - use custom wrapper to avoid re-init
    sensor_process = multiprocessing.Process(
        target=run_sensor_logging,
        args=(config_file,),
        name=f"SensorLogger-{instance_name}"
    )
    sensor_process.start()

    print(f"[{instance_name}] Starting API server for '{device_name}' on port {api_port}")

    # Run API server
    try:
        uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")
    finally:
        sensor_process.terminate()
        sensor_process.join()


def run_sensor_logging(config_file: str):
    """Run sensor logging loop without re-initializing existing database."""
    # Set environment variable in subprocess
    os.environ['TEMPSENS_CONFIG'] = config_file
    
    from tempsens import io_funcs
    
    # Check if database already exists (from historical data generation)
    config = io_funcs.fetch_config(config_file)
    db_path = config["DEFAULT"]["outputfile"]
    db_exists = Path(db_path).exists()
    
    if db_exists:
        print(f"LOGGING INITIATED: using existing database {db_path}")
    else:
        # Database doesn't exist - initialize it (should not happen with --no-historical flag)
        print(f"LOGGING INITIATED: creating new database {db_path}")
        io_funcs.init_database()

    # Take an immediate reading on startup (no delay)
    io_funcs.schedule.enter(0, 1, io_funcs.log_data)
    while True:
        io_funcs.schedule.run()


def main():
    """Start all test client instances with historical data generation."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Run multiple test sensor clients with historical data generation"
    )
    parser.add_argument(
        "--historical-days",
        type=int,
        default=14,
        help="Number of days of historical data to generate (default: 14)"
    )
    parser.add_argument(
        "--no-historical",
        action="store_true",
        help="Skip historical data generation and start with empty databases"
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="Force regeneration of historical data even if it already exists"
    )
    args = parser.parse_args()
    
    print("=" * 80)
    print("Temperature Sensor Test Client Suite")
    print("=" * 80)

    # Define test instances (using dev/ folder)
    instances = [
        ("dev/config_test1.ini", "Test-1"),
        ("dev/config_test2.ini", "Test-2"),
        ("dev/config_test3.ini", "Test-3"),
    ]

    # Generate historical data for each sensor (sequential to see progress)
    if not args.no_historical:
        print(f"\nGenerating {args.historical_days} days of historical data for each sensor...")
        print("=" * 80)
        
        # If force regenerate, delete all existing databases and metadata first
        if args.force_regenerate:
            print("Force regenerate enabled - deleting existing databases...")
            for config_file, instance_name in instances:
                from tempsens import io_funcs
                config = io_funcs.fetch_config(config_file)
                db_path = Path(config["DEFAULT"]["outputfile"])
                metadata_path = get_metadata_path(config_file)
                if db_path.exists():
                    db_path.unlink()
                    print(f"  Deleted: {db_path}")
                if metadata_path.exists():
                    metadata_path.unlink()
                    print(f"  Deleted: {metadata_path}")
            print()
        
        for config_file, instance_name in instances:
            generate_historical_data(config_file, instance_name, days=args.historical_days)
            print()  # Blank line between sensors
    else:
        print("\nSkipping historical data generation (--no-historical flag set)")
        print("=" * 80)

    print("\n" + "=" * 80)
    print("Starting 3 Test Sensor Clients (Live Data)")
    print("=" * 80)
    print()

    # Start each instance in its own process
    processes = []
    for config_file, instance_name in instances:
        p = multiprocessing.Process(
            target=run_client_instance,
            args=(config_file, instance_name),
            name=instance_name
        )
        p.start()
        processes.append(p)
        time.sleep(1)  # Stagger startup slightly

    print("=" * 80)
    print("✓ All 3 test clients started successfully!")
    print("=" * 80)
    print("  Test Sensor 1: http://localhost:5001")
    print("  Test Sensor 2: http://localhost:5002")
    print("  Test Sensor 3: http://localhost:5003")
    print()
    print("Configure server/config_server.ini to point to these endpoints,")
    print("then run: uv run bokeh serve server/bokeh_app.py --show")
    print()
    print("Press Ctrl+C to stop all clients...")
    print("=" * 80 + "\n")

    try:
        # Wait for all processes
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n\nShutting down all test clients...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join()
        print("All clients stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
