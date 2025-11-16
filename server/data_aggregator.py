"""
Data aggregator that polls multiple sensors and maintains a local cache.
Uses SQLite for persistent storage of sensor data.
"""
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .api_client import MultiSensorClient

logger = logging.getLogger(__name__)


class DataAggregator:
    """Aggregates data from multiple temperature sensors into a local SQLite database."""

    def __init__(
        self,
        multi_sensor_client: MultiSensorClient,
        db_path: str = "sensor_data.db",
        poll_interval: int = 30
    ):
        """
        Initialize data aggregator.

        Args:
            multi_sensor_client: Client for fetching data from sensors
            db_path: Path to SQLite database file
            poll_interval: Seconds between polling cycles
        """
        self.client = multi_sensor_client
        self.db_path = Path(db_path)
        self.poll_interval = poll_interval
        self._stop_polling = threading.Event()
        self._polling_thread = None
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with tables for each sensor."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Create metadata table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_metadata (
                    sensor_name TEXT PRIMARY KEY,
                    device_ip TEXT,
                    last_update TEXT,
                    last_error TEXT,
                    status TEXT
                )
            """)

            # Create a table for each sensor
            for sensor_name in self.client.get_all_sensor_names():
                table_name = self._get_table_name(sensor_name)
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        temperature REAL,
                        humidity REAL,
                        UNIQUE(timestamp)
                    )
                """)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_timestamp
                    ON {table_name}(timestamp)
                """)

            conn.commit()

    @staticmethod
    def _get_table_name(sensor_name: str) -> str:
        """Convert sensor name to valid SQL table name."""
        # Replace spaces and special chars with underscores
        return "sensor_" + "".join(c if c.isalnum() else "_" for c in sensor_name).lower()

    def update_sensor_data(self, sensor_name: str, limit: int = 1000):
        """
        Fetch latest data from a sensor and update local database.

        Implements intelligent resync: if gap detected, fetches full dataset once,
        otherwise fetches only recent records for efficiency.

        Args:
            sensor_name: Name of sensor to update
            limit: Number of recent readings to fetch (default 1000)
        """
        table_name = self._get_table_name(sensor_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Fetching data from sensor: {sensor_name}")
        logger.info(f"Updating data for sensor: {sensor_name}")

        try:
            # Get latest timestamp in our database
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT MAX(timestamp) FROM {table_name}")
                last_timestamp = cursor.fetchone()[0]

            logger.debug(f"Last timestamp in DB for {sensor_name}: {last_timestamp}")

            # Detect if we need a full resync (gap in data)
            fetch_limit = limit
            fetch_timeout = None  # Use default timeout
            if last_timestamp is not None:
                # First, peek at sensor's recent data to check for gaps
                peek_data = self.client.get_sensor_data(sensor_name, limit=10)
                if peek_data and len(peek_data.get('data', {}).get('time', [])) > 0:
                    oldest_recent = peek_data['data']['time'][0]
                    # If our last record is older than sensor's oldest recent record,
                    # there's a gap - fetch full dataset to resync
                    if last_timestamp < oldest_recent:
                        logger.warning(f"Gap detected for {sensor_name}: our last={last_timestamp}, sensor oldest recent={oldest_recent}")
                        print(f"[{timestamp}] Gap detected, requesting full dataset for resync...")
                        fetch_limit = None  # Request all data
                        fetch_timeout = 30  # Use longer timeout for full resync (SD card can be slow)

            # Fetch new data from sensor
            data = self.client.get_sensor_data(sensor_name, limit=fetch_limit, timeout=fetch_timeout)

            if data is None:
                self._update_metadata(sensor_name, status="error", error="Failed to fetch data")
                logger.warning(f"Failed to fetch data from {sensor_name}")
                return

            logger.debug(f"Fetched {len(data.get('data', {}).get('time', []))} records from {sensor_name}")

            sensor_data = data["data"]
            device_ip = data.get("device_ip", "unknown")

            # Filter to only new data points
            new_records = []
            for i in range(len(sensor_data["time"])):
                timestamp = sensor_data["time"][i]
                if last_timestamp is None or timestamp > last_timestamp:
                    new_records.append((
                        timestamp,
                        sensor_data["temperature"][i],
                        sensor_data["humidity"][i]
                    ))

            # Insert new records
            if new_records:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany(
                        f"INSERT OR IGNORE INTO {table_name} (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                        new_records
                    )
                    conn.commit()

                logger.info(f"Added {len(new_records)} new records for {sensor_name}")

            # Update metadata
            self._update_metadata(
                sensor_name,
                device_ip=device_ip,
                status="active",
                error=None
            )

        except Exception as e:
            logger.error(f"Error updating data for {sensor_name}: {e}")
            self._update_metadata(sensor_name, status="error", error=str(e))

    def _update_metadata(
        self,
        sensor_name: str,
        device_ip: Optional[str] = None,
        status: str = "unknown",
        error: Optional[str] = None
    ):
        """Update sensor metadata in database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sensor_metadata
                (sensor_name, device_ip, last_update, last_error, status)
                VALUES (?, ?, ?, ?, ?)
            """, (sensor_name, device_ip, datetime.now().isoformat(), error, status))
            conn.commit()

    def get_sensor_data(
        self,
        sensor_name: str,
        limit: Optional[int] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> Dict:
        """
        Get sensor data from local database.

        Args:
            sensor_name: Name of sensor
            limit: If specified, return last N readings
            start_time: Start timestamp (ISO format)
            end_time: End timestamp (ISO format)

        Returns:
            Dictionary with 'time', 'temperature', 'humidity' arrays (as milliseconds for Bokeh)
        """
        table_name = self._get_table_name(sensor_name)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Build query based on parameters
            if limit is not None:
                # Get last N readings in descending order, then reverse to chronological
                query = f"""
                    SELECT timestamp, temperature, humidity
                    FROM (
                        SELECT timestamp, temperature, humidity
                        FROM {table_name}
                        ORDER BY timestamp DESC
                        LIMIT ?
                    )
                    ORDER BY timestamp ASC
                """
                cursor.execute(query, (limit,))
            elif start_time or end_time:
                conditions = []
                params = []

                if start_time:
                    conditions.append("timestamp >= ?")
                    params.append(start_time)
                if end_time:
                    conditions.append("timestamp <= ?")
                    params.append(end_time)

                where_clause = " AND ".join(conditions)
                query = f"SELECT timestamp, temperature, humidity FROM {table_name} WHERE {where_clause} ORDER BY timestamp"
                cursor.execute(query, params)
            else:
                query = f"SELECT timestamp, temperature, humidity FROM {table_name} ORDER BY timestamp"
                cursor.execute(query)

            rows = cursor.fetchall()

        if not rows:
            return {"time": np.array([]), "temperature": np.array([]), "humidity": np.array([])}

        # Convert to arrays and format time as milliseconds since epoch (for Bokeh)
        times_str = [row[0] for row in rows]
        temperatures = np.array([row[1] for row in rows], dtype=np.float32)
        humidities = np.array([row[2] for row in rows], dtype=np.float32)

        # Convert ISO timestamps to numpy datetime64, then to milliseconds since epoch
        times_dt64 = np.array(times_str, dtype=np.datetime64)
        times_ms = times_dt64.astype('datetime64[ms]').astype(np.int64).astype(np.float64)

        return {
            "time": times_ms,
            "temperature": temperatures,
            "humidity": humidities
        }

    def get_sensor_metadata(self, sensor_name: str) -> Optional[Dict]:
        """Get metadata for a specific sensor."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT device_ip, last_update, last_error, status FROM sensor_metadata WHERE sensor_name = ?",
                (sensor_name,)
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            "device_ip": row[0],
            "last_update": row[1],
            "last_error": row[2],
            "status": row[3]
        }

    def start_polling(self):
        """Start background polling thread."""
        if self._polling_thread and self._polling_thread.is_alive():
            logger.warning("Polling thread already running")
            return

        self._stop_polling.clear()
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sensor_names = self.client.get_all_sensor_names()
        print(f"\n{'='*60}")
        print(f"[{timestamp}] Data Aggregator Started")
        print(f"Monitoring {len(sensor_names)} sensor(s): {', '.join(sensor_names)}")
        print(f"Poll interval: {self.poll_interval} seconds")
        print(f"{'='*60}\n")
        logger.info("Started background polling")

    def stop_polling(self):
        """Stop background polling thread."""
        self._stop_polling.set()
        if self._polling_thread:
            self._polling_thread.join(timeout=10)
        logger.info("Stopped background polling")

    def _polling_loop(self):
        """Background polling loop."""
        while not self._stop_polling.is_set():
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] Polling all sensors...")
                logger.info("Polling all sensors...")
                for sensor_name in self.client.get_all_sensor_names():
                    if self._stop_polling.is_set():
                        break
                    print(f"[{timestamp}] Updating sensor: {sensor_name}")
                    self.update_sensor_data(sensor_name)

                print(f"[{timestamp}] Polling complete. Next poll in {self.poll_interval} seconds.")
                logger.info(f"Polling complete. Next poll in {self.poll_interval} seconds.")

            except Exception as e:
                print(f"[{timestamp}] ERROR in polling loop: {e}")
                logger.error(f"Error in polling loop: {e}")

            # Wait for next poll cycle (or until stop signal)
            self._stop_polling.wait(self.poll_interval)

    def poll_once(self):
        """Poll all sensors once (synchronous)."""
        for sensor_name in self.client.get_all_sensor_names():
            self.update_sensor_data(sensor_name)
