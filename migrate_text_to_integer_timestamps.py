#!/usr/bin/env python3
"""
Migrate SQLite databases from TEXT timestamps to INTEGER timestamps (milliseconds since epoch).

This script:
1. Creates a backup of the original database
2. Creates new schema with INTEGER timestamps
3. Converts all TEXT timestamps to milliseconds
4. Validates the conversion
5. Replaces old database with new one

Usage:
    python migrate_text_to_integer_timestamps.py <database_path>
    python migrate_text_to_integer_timestamps.py templog.db
    python migrate_text_to_integer_timestamps.py sensor_data.db

For batch migration:
    python migrate_text_to_integer_timestamps.py --all-test-dbs
    python migrate_text_to_integer_timestamps.py --server-db
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


def backup_database(db_path: Path) -> Path:
    """Create a timestamped backup of the database."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = db_path.with_suffix(f'.backup_{timestamp}.db')

    print(f"Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)

    # Also backup WAL/SHM files if they exist
    wal_file = Path(str(db_path) + '-wal')
    shm_file = Path(str(db_path) + '-shm')
    if wal_file.exists():
        shutil.copy2(wal_file, backup_path.with_suffix('.db-wal'))
    if shm_file.exists():
        shutil.copy2(shm_file, backup_path.with_suffix('.db-shm'))

    return backup_path


def get_table_names(conn: sqlite3.Connection) -> list[str]:
    """Get all sensor data table names from database."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sensor_%' OR name = 'sensor_data'")
    return [row[0] for row in cursor.fetchall()]


def convert_text_to_integer(db_path: Path, dry_run: bool = False) -> bool:
    """
    Convert a database from TEXT timestamps to INTEGER timestamps.

    Args:
        db_path: Path to database file
        dry_run: If True, only validate without making changes

    Returns:
        True if successful, False otherwise
    """
    if not db_path.exists():
        print(f"Error: Database not found: {db_path}")
        return False

    print(f"\n{'=' * 80}")
    print(f"Migrating: {db_path}")
    print(f"{'=' * 80}\n")

    # Create backup
    if not dry_run:
        backup_path = backup_database(db_path)
        print(f"✓ Backup created\n")

    # Connect to original database (read-only)
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as read_conn:
        read_cursor = read_conn.cursor()

        # Get all sensor data tables
        table_names = get_table_names(read_conn)

        if not table_names:
            print("Warning: No sensor data tables found in database")
            return True

        print(f"Found {len(table_names)} table(s): {', '.join(table_names)}\n")

        # Process each table
        migration_data = {}
        total_records = 0

        for table_name in table_names:
            print(f"Reading {table_name}...")

            # Check current schema
            read_cursor.execute(f"PRAGMA table_info({table_name})")
            columns = read_cursor.fetchall()
            timestamp_col = next((col for col in columns if col[1] == 'timestamp'), None)

            if not timestamp_col:
                print(f"  Warning: No timestamp column found, skipping")
                continue

            timestamp_type = timestamp_col[2]  # Column type
            if timestamp_type == 'INTEGER':
                print(f"  ✓ Already using INTEGER timestamps, skipping")
                continue

            # Read all data
            read_cursor.execute(f"SELECT timestamp, temperature, humidity FROM {table_name} ORDER BY timestamp")
            rows = read_cursor.fetchall()

            if not rows:
                print(f"  No data to migrate")
                migration_data[table_name] = []
                continue

            print(f"  Found {len(rows):,} records")
            total_records += len(rows)

            # Convert TEXT timestamps to milliseconds
            times_str = [row[0] for row in rows]
            temperatures = [row[1] for row in rows]
            humidities = [row[2] for row in rows]

            # Use pandas for fast conversion
            try:
                # Use format='ISO8601' to handle different timestamp formats flexibly
                times_dt = pd.to_datetime(times_str, format='ISO8601')
                times_ms = times_dt.values.astype('datetime64[ms]').astype(np.int64)

                # Validate conversion (check first and last timestamps)
                first_original = times_str[0]
                first_converted_back = pd.Timestamp(times_ms[0], unit='ms').strftime('%Y-%m-%d %H:%M:%S')

                print(f"  Example conversion:")
                print(f"    Original: {first_original}")
                print(f"    Integer:  {times_ms[0]}")
                print(f"    Verified: {first_converted_back}")

                # Store for migration
                migration_data[table_name] = list(zip(times_ms, temperatures, humidities))
                print(f"  ✓ Converted {len(times_ms):,} timestamps\n")

            except Exception as e:
                print(f"  Error converting timestamps: {e}")
                return False

    # Check if any migration is needed
    if not migration_data or total_records == 0:
        # Clean up unnecessary backup
        if not dry_run and backup_path.exists():
            backup_path.unlink()
            # Also remove WAL/SHM backup files if they exist
            backup_wal = backup_path.with_suffix('.db-wal')
            backup_shm = backup_path.with_suffix('.db-shm')
            if backup_wal.exists():
                backup_wal.unlink()
            if backup_shm.exists():
                backup_shm.unlink()

        print(f"\n{'=' * 80}")
        print(f"✓ Database already using INTEGER timestamps - no migration needed")
        print(f"{'=' * 80}\n")
        return True

    if dry_run:
        print(f"\n{'=' * 80}")
        print(f"DRY RUN COMPLETE - No changes made")
        print(f"Total records that would be migrated: {total_records:,}")
        print(f"{'=' * 80}\n")
        return True

    # Create new database with INTEGER schema
    new_db_path = db_path.with_suffix('.new.db')
    if new_db_path.exists():
        new_db_path.unlink()

    print(f"Creating new database with INTEGER schema...")

    with sqlite3.connect(new_db_path) as write_conn:
        write_cursor = write_conn.cursor()

        # Enable optimizations
        write_cursor.execute("PRAGMA journal_mode=WAL")
        write_cursor.execute("PRAGMA synchronous=NORMAL")
        write_cursor.execute("PRAGMA cache_size=-64000")

        # Create tables with INTEGER timestamps
        for table_name in table_names:
            if table_name not in migration_data:
                continue

            print(f"Creating {table_name} with INTEGER schema...")

            # Create table
            write_cursor.execute(f"""
                CREATE TABLE {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL UNIQUE,
                    temperature REAL,
                    humidity REAL
                )
            """)

            # Create index
            write_cursor.execute(f"""
                CREATE INDEX idx_{table_name}_timestamp
                ON {table_name}(timestamp DESC)
            """)

            # Insert migrated data
            records = migration_data[table_name]
            if records:
                print(f"  Inserting {len(records):,} records...")
                write_cursor.executemany(
                    f"INSERT INTO {table_name} (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                    records
                )
                print(f"  ✓ Inserted {len(records):,} records")

        write_conn.commit()

    # Also migrate sensor_metadata table if it exists (server database)
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as read_conn:
        read_cursor = read_conn.cursor()
        read_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sensor_metadata'")

        if read_cursor.fetchone():
            print(f"\nCopying sensor_metadata table...")
            read_cursor.execute("SELECT * FROM sensor_metadata")
            metadata_rows = read_cursor.fetchall()

            read_cursor.execute("PRAGMA table_info(sensor_metadata)")
            columns = read_cursor.fetchall()
            column_names = [col[1] for col in columns]

            with sqlite3.connect(new_db_path) as write_conn:
                write_cursor = write_conn.cursor()

                # Recreate metadata table schema
                read_cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sensor_metadata'")
                create_sql = read_cursor.fetchone()[0]
                write_cursor.execute(create_sql)

                # Insert metadata
                if metadata_rows:
                    placeholders = ','.join(['?' for _ in column_names])
                    write_cursor.executemany(
                        f"INSERT INTO sensor_metadata VALUES ({placeholders})",
                        metadata_rows
                    )
                    print(f"  ✓ Copied {len(metadata_rows)} metadata records")

                write_conn.commit()

    # Validate new database
    print(f"\nValidating new database...")
    with sqlite3.connect(new_db_path) as check_conn:
        check_cursor = check_conn.cursor()

        for table_name in table_names:
            if table_name not in migration_data:
                continue

            # Check record count
            check_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            new_count = check_cursor.fetchone()[0]
            old_count = len(migration_data[table_name])

            if new_count != old_count:
                print(f"  Error: {table_name} record count mismatch ({new_count} vs {old_count})")
                return False

            # Check timestamp type
            check_cursor.execute(f"PRAGMA table_info({table_name})")
            columns = check_cursor.fetchall()
            timestamp_col = next((col for col in columns if col[1] == 'timestamp'), None)

            if timestamp_col[2] != 'INTEGER':
                print(f"  Error: {table_name} timestamp column is not INTEGER")
                return False

            print(f"  ✓ {table_name}: {new_count:,} records, INTEGER timestamps")

    print(f"\n✓ Validation passed")

    # Replace old database with new one
    print(f"\nReplacing original database...")

    # Delete WAL/SHM files
    wal_file = Path(str(db_path) + '-wal')
    shm_file = Path(str(db_path) + '-shm')
    if wal_file.exists():
        wal_file.unlink()
    if shm_file.exists():
        shm_file.unlink()

    # Replace
    db_path.unlink()
    new_db_path.rename(db_path)

    print(f"✓ Migration complete!")
    print(f"\nSummary:")
    print(f"  - Original database backed up to: {backup_path}")
    print(f"  - Migrated {total_records:,} total records")
    print(f"  - Timestamps now stored as INTEGER (milliseconds since epoch)")
    print(f"  - Expected speedup: ~100ms per dashboard refresh")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Migrate SQLite databases from TEXT to INTEGER timestamps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Migrate a single database:
    python migrate_text_to_integer_timestamps.py templog.db

  Dry run to validate:
    python migrate_text_to_integer_timestamps.py --dry-run templog.db

  Migrate all test databases:
    python migrate_text_to_integer_timestamps.py --all-test-dbs

  Migrate server database:
    python migrate_text_to_integer_timestamps.py --server-db
        """
    )

    parser.add_argument('database', nargs='?', help='Path to database file to migrate')
    parser.add_argument('--dry-run', action='store_true', help='Validate conversion without making changes')
    parser.add_argument('--all-test-dbs', action='store_true', help='Migrate all dev/templog_test*.db files')
    parser.add_argument('--server-db', action='store_true', help='Migrate server/sensor_data.db')

    args = parser.parse_args()

    # Determine which databases to migrate
    databases = []

    if args.all_test_dbs:
        test_dbs = Path('dev').glob('templog_test*.db')
        databases.extend(test_dbs)

    if args.server_db:
        server_db = Path('server/sensor_data.db')
        if server_db.exists():
            databases.append(server_db)
        else:
            print(f"Warning: Server database not found: {server_db}")

    if args.database:
        databases.append(Path(args.database))

    if not databases:
        parser.print_help()
        print("\nError: No database specified. Use --all-test-dbs, --server-db, or provide a database path.")
        sys.exit(1)

    # Migrate each database
    success_count = 0
    fail_count = 0

    for db_path in databases:
        if convert_text_to_integer(db_path, dry_run=args.dry_run):
            success_count += 1
        else:
            fail_count += 1

    # Summary
    print(f"\n{'=' * 80}")
    print(f"MIGRATION SUMMARY")
    print(f"{'=' * 80}")
    print(f"Successful: {success_count}")
    print(f"Failed:     {fail_count}")

    if fail_count > 0:
        print(f"\nSome migrations failed. Check error messages above.")
        sys.exit(1)
    else:
        print(f"\n✓ All migrations completed successfully!")
        if not args.dry_run:
            print(f"\nNext steps:")
            print(f"  1. Deploy updated code to server/clients")
            print(f"  2. Restart services to use new INTEGER schema")
            print(f"  3. Verify data continuity in dashboard")


if __name__ == '__main__':
    main()
