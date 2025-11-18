#!/usr/bin/env python3
"""
Migration script: HDF5 to SQLite
Safely migrates all sensor data from templog.h5 to templog.db
"""
import sys
import sqlite3
from pathlib import Path
import h5py
import numpy as np


def migrate_h5_to_sqlite(h5_file="templog.h5", db_file="templog.db"):
    """Migrate data from HDF5 to SQLite."""
    h5_path = Path(h5_file)
    db_path = Path(db_file)

    # Check if HDF5 file exists
    if not h5_path.exists():
        print(f"ERROR: HDF5 file not found: {h5_file}")
        return False

    # Warn if SQLite file already exists
    if db_path.exists():
        response = input(f"WARNING: {db_file} already exists. Overwrite? (yes/no): ")
        if response.lower() != "yes":
            print("Migration cancelled.")
            return False
        db_path.unlink()

    print(f"Starting migration: {h5_file} -> {db_file}")

    # Read all data from HDF5
    print("Reading HDF5 file...")
    try:
        with h5py.File(h5_file, "r", locking=False) as f:
            times_str = np.array(f["time"], dtype=str)
            temps = np.array(f["temperature"], dtype=float)
            hums = np.array(f["humidity"], dtype=float)
    except Exception as e:
        print(f"ERROR reading HDF5: {e}")
        return False

    total_records = len(times_str)
    print(f"Found {total_records} records")
    print(f"  Oldest: {times_str[0]}")
    print(f"  Newest: {times_str[-1]}")

    # Convert TEXT timestamps to INTEGER milliseconds
    print("Converting timestamps to INTEGER milliseconds...")
    import pandas as pd
    times_dt = pd.to_datetime(times_str)
    times_ms = times_dt.values.astype('datetime64[ms]').astype(np.int64)
    print(f"  Example conversion: {times_str[0]} -> {times_ms[0]} ms")

    # Create SQLite database
    print("\nCreating SQLite database...")
    try:
        with sqlite3.connect(db_file) as conn:
            cursor = conn.cursor()

            # Enable WAL mode for crash safety
            cursor.execute("PRAGMA journal_mode=WAL")

            # Create table with INTEGER timestamps
            cursor.execute("""
                CREATE TABLE sensor_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL UNIQUE,
                    temperature REAL,
                    humidity REAL
                )
            """)

            # Create index on timestamp for fast queries
            cursor.execute("""
                CREATE INDEX idx_timestamp ON sensor_data(timestamp DESC)
            """)

            # Insert data in batches
            print("Inserting records...")
            batch_size = 1000
            records = list(zip(times_ms, temps, hums))

            for i in range(0, total_records, batch_size):
                batch = records[i:i+batch_size]
                cursor.executemany(
                    "INSERT INTO sensor_data (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                    batch
                )
                if (i + batch_size) % 5000 == 0:
                    print(f"  Inserted {min(i + batch_size, total_records)}/{total_records} records...")

            conn.commit()

            # Verify
            cursor.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM sensor_data")
            count, min_ts, max_ts = cursor.fetchone()

            print(f"\nMigration complete!")
            print(f"  Records in SQLite: {count}")
            print(f"  Oldest: {min_ts}")
            print(f"  Newest: {max_ts}")

            if count != total_records:
                print(f"WARNING: Record count mismatch! HDF5={total_records}, SQLite={count}")
                return False

            return True

    except Exception as e:
        print(f"ERROR creating SQLite: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("HDF5 to SQLite Migration Tool")
    print("=" * 60)
    print()

    h5_file = sys.argv[1] if len(sys.argv) > 1 else "templog.h5"
    db_file = sys.argv[2] if len(sys.argv) > 2 else "templog.db"

    success = migrate_h5_to_sqlite(h5_file, db_file)

    if success:
        print("\n✓ Migration successful!")
        print(f"\nNext steps:")
        print(f"  1. Backup your HDF5 file: cp {h5_file} {h5_file}.backup")
        print(f"  2. Update and restart the client")
        print(f"  3. Verify the new client is logging to SQLite")
        print(f"  4. Once verified, you can delete {h5_file}")
        sys.exit(0)
    else:
        print("\n✗ Migration failed!")
        sys.exit(1)
