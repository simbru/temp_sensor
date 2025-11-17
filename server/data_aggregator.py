"""
Data aggregator that polls multiple sensors and maintains a local cache.
Uses SQLite for persistent storage of sensor data.
"""
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .api_client import MultiSensorClient

logger = logging.getLogger(__name__)


class DataAggregator:
    """Aggregates data from multiple temperature sensors into a local SQLite database."""

    def __init__(
        self,
        multi_sensor_client: MultiSensorClient,
        db_path: str = "sensor_data.db",
        poll_interval: int = 30,
        min_gap_threshold: int = 60
    ):
        """
        Initialize data aggregator.

        Args:
            multi_sensor_client: Client for fetching data from sensors
            db_path: Path to SQLite database file
            poll_interval: Seconds between polling cycles
            min_gap_threshold: Minimum seconds before marking sensor as offline (default 60)
        """
        self.client = multi_sensor_client
        self.db_path = Path(db_path)
        self.poll_interval = poll_interval
        self._resync_batch_limit = 5000
        self._resync_timeout = max(15, poll_interval * 2)
        self._min_gap_threshold = min_gap_threshold
        self._start_of_time = "0001-01-01 00:00:00"
        self._stop_polling = threading.Event()
        self._polling_thread = None
        self._start_time = datetime.now()  # Track when aggregator started
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with tables for each sensor."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Enable WAL mode for crash safety and better concurrency
            cursor.execute("PRAGMA journal_mode=WAL")
            
            # Performance optimizations for multi-sensor aggregation
            cursor.execute("PRAGMA synchronous=NORMAL")  # Balance safety/speed
            cursor.execute("PRAGMA cache_size=-64000")   # 64MB cache
            cursor.execute("PRAGMA temp_store=MEMORY")   # Use RAM for temp tables
            cursor.execute("PRAGMA mmap_size=268435456") # 256MB memory-mapped I/O

            # Create metadata table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sensor_metadata (
                    sensor_name TEXT PRIMARY KEY,
                    device_ip TEXT,
                    last_update TEXT,
                    last_error TEXT,
                    status TEXT,
                    cpu_percent REAL,
                    memory_percent REAL,
                    client_db_size_mb REAL,
                    sensor_type TEXT,
                    total_records INTEGER,
                    client_total_records INTEGER
                )
            """)

            # Migrate existing databases: add new columns if they don't exist
            cursor.execute("PRAGMA table_info(sensor_metadata)")
            existing_columns = {row[1] for row in cursor.fetchall()}
            new_columns = {
                "cpu_percent": "REAL",
                "memory_percent": "REAL",
                "client_db_size_mb": "REAL",
                "sensor_type": "TEXT",
                "total_records": "INTEGER",
                "client_total_records": "INTEGER"
            }
            for col_name, col_type in new_columns.items():
                if col_name not in existing_columns:
                    cursor.execute(f"ALTER TABLE sensor_metadata ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Added column {col_name} to sensor_metadata table")

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
                    ON {table_name}(timestamp DESC)
                """)

            conn.commit()

    @staticmethod
    def _get_table_name(sensor_name: str) -> str:
        """Convert sensor name to valid SQL table name."""
        # Replace spaces and special chars with underscores
        return "sensor_" + "".join(c if c.isalnum() else "_" for c in sensor_name).lower()

    def _backfill_all_historical_data(self, sensor_name: str) -> bool:
        """
        Backfill all historical data from a sensor on initial sync.

        Fetches all data from client in batches to avoid memory issues.

        Args:
            sensor_name: Name of sensor to backfill

        Returns:
            True if successful, False otherwise
        """
        table_name = self._get_table_name(sensor_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Starting full historical backfill for {sensor_name}...")
        logger.info(f"Backfilling all historical data for {sensor_name}")

        try:
            # Fetch all data from client (no limit)
            data = self.client.get_sensor_data(sensor_name, limit=None)
            if data is None:
                logger.error(f"Failed to fetch historical data from {sensor_name}")
                return False

            sensor_data = data.get("data", {})
            times = sensor_data.get("time", [])
            temperatures = sensor_data.get("temperature", [])
            humidities = sensor_data.get("humidity", [])

            if len(times) == 0:
                logger.warning(f"No historical data available for {sensor_name}")
                return False

            # Prepare records for insertion
            records = list(zip(times, temperatures, humidities))
            total_records = len(records)

            # Insert in batches to avoid memory issues
            batch_size = 10000
            inserted_count = 0

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                for i in range(0, total_records, batch_size):
                    batch = records[i:i + batch_size]
                    cursor.executemany(
                        f"INSERT OR IGNORE INTO {table_name} (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                        batch
                    )
                    inserted_count += len(batch)
                    if (i + batch_size) % 50000 == 0:
                        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Backfilled {inserted_count:,} / {total_records:,} records...")
                conn.commit()

            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Backfill complete: {total_records:,} records synced for {sensor_name}")
            logger.info(f"Successfully backfilled {total_records} records for {sensor_name}")
            return True

        except Exception as e:
            logger.error(f"Error during historical backfill for {sensor_name}: {e}")
            return False

    def update_sensor_data(self, sensor_name: str, limit: int = 1000):
        """
        Fetch latest data from a sensor and update local database.

        On first sync, performs full historical backfill of all client data.
        Subsequent syncs fetch only recent data for efficiency.

        Args:
            sensor_name: Name of sensor to update
            limit: Number of recent readings to fetch (default 1000)
        """
        table_name = self._get_table_name(sensor_name)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Fetching data from sensor: {sensor_name}")
        logger.info(f"Updating data for sensor: {sensor_name}")

        try:
            last_timestamp = self._fetch_last_timestamp(table_name)
            logger.debug(f"Last timestamp in DB for {sensor_name}: {last_timestamp}")

            # If this is the first sync (no data in server DB), do full historical backfill
            if last_timestamp is None:
                logger.info(f"First sync detected for {sensor_name}, initiating full historical backfill")
                print(f"[{timestamp}] First sync - backfilling all historical data for {sensor_name}...")
                success = self._backfill_all_historical_data(sensor_name)
                if not success:
                    self._update_metadata(sensor_name, status="error", error="Failed to backfill historical data")
                    return
                # After backfill, update last_timestamp
                last_timestamp = self._fetch_last_timestamp(table_name)

            device_ip = "unknown"
            status_value = "idle"
            new_records: List[Tuple[str, float, float]] = []

            # Fetch data first (single request instead of peek + fetch)
            data = self.client.get_sensor_data(sensor_name, limit=limit)
            if data is None:
                logger.warning(f"Failed to fetch data from {sensor_name}")
                self._handle_fetch_failure(sensor_name, last_timestamp, "Failed to fetch data")
                return

            sensor_data = data.get("data", {})
            device_ip = data.get("device_ip", "unknown")

            # Fetch system metrics from client
            metrics = self.client.get_sensor_metrics(sensor_name)
            cpu_percent = metrics.get("cpu_percent") if metrics else None
            memory_percent = metrics.get("memory_percent") if metrics else None
            client_db_size_mb = metrics.get("database_size_mb") if metrics else None
            sensor_type = metrics.get("sensor_type") if metrics else None
            client_total_records = metrics.get("total_records") if metrics else None

            # Check if resync needed based on fetched data
            needs_resync = self._check_gap_in_data(sensor_data, last_timestamp)

            if needs_resync:
                logger.warning(f"Gap detected for {sensor_name}; initiating ranged resync")
                print(f"[{timestamp}] Gap detected, streaming backlog for {sensor_name}...")
                new_records, device_ip = self._resync_sensor(sensor_name, last_timestamp)
                status_value = "syncing" if new_records else "idle"
            else:
                new_records = self._extract_new_records(sensor_data, last_timestamp)
                status_value = "active" if new_records else "idle"

            if new_records:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany(
                        f"INSERT OR IGNORE INTO {table_name} (timestamp, temperature, humidity) VALUES (?, ?, ?)",
                        new_records
                    )
                    conn.commit()

                logger.info(f"Added {len(new_records)} new records for {sensor_name}")

            self._update_metadata(
                sensor_name,
                device_ip=device_ip,
                status=status_value,
                error=None,
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
                client_db_size_mb=client_db_size_mb,
                sensor_type=sensor_type,
                client_total_records=client_total_records
            )

        except Exception as e:
            logger.error(f"Error updating data for {sensor_name}: {e}")
            self._update_metadata(sensor_name, status="error", error=str(e))

    def _handle_fetch_failure(
        self,
        sensor_name: str,
        last_timestamp: Optional[str],
        error_message: str
    ) -> None:
        """Mark short-lived fetch failures as idle, otherwise flag error."""
        if last_timestamp is None:
            self._update_metadata(sensor_name, status="error", error=error_message)
            return

        last_dt = self._parse_ts(last_timestamp)
        if last_dt is None:
            self._update_metadata(sensor_name, status="error", error=error_message)
            return

        now_dt = datetime.now()
        transient_window_seconds = max(self.poll_interval * 2, self._min_gap_threshold)
        if (now_dt - last_dt) <= timedelta(seconds=transient_window_seconds):
            # Treat single missed polls as "connected · waiting" so dashboard shows 🟠 instead of 🔴.
            logger.info(
                "Transient fetch miss for %s (last sample %s); marking as idle",
                sensor_name,
                last_timestamp,
            )
            self._update_metadata(sensor_name, status="idle", error=None)
            return

        self._update_metadata(sensor_name, status="error", error=error_message)

    def _update_metadata(
        self,
        sensor_name: str,
        device_ip: Optional[str] = None,
        status: str = "unknown",
        error: Optional[str] = None,
        cpu_percent: Optional[float] = None,
        memory_percent: Optional[float] = None,
        client_db_size_mb: Optional[float] = None,
        sensor_type: Optional[str] = None,
        client_total_records: Optional[int] = None
    ):
        """Update sensor metadata in database."""
        # Get total records count (server-side)
        total_records = self.get_total_records(sensor_name)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sensor_metadata
                (sensor_name, device_ip, last_update, last_error, status,
                 cpu_percent, memory_percent, client_db_size_mb, sensor_type, total_records, client_total_records)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sensor_name, device_ip, datetime.now().isoformat(), error, status,
                  cpu_percent, memory_percent, client_db_size_mb, sensor_type, total_records, client_total_records))
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
                """SELECT device_ip, last_update, last_error, status,
                          cpu_percent, memory_percent, client_db_size_mb,
                          sensor_type, total_records, client_total_records
                   FROM sensor_metadata WHERE sensor_name = ?""",
                (sensor_name,)
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return {
            "device_ip": row[0],
            "last_update": row[1],
            "last_error": row[2],
            "status": row[3],
            "cpu_percent": row[4],
            "memory_percent": row[5],
            "client_db_size_mb": row[6],
            "sensor_type": row[7],
            "total_records": row[8],
            "client_total_records": row[9]
        }

    def get_uptime(self) -> timedelta:
        """Get server uptime as a timedelta."""
        return datetime.now() - self._start_time

    def get_total_records(self, sensor_name: str) -> int:
        """Get total number of records for a specific sensor."""
        table_name = self._get_table_name(sensor_name)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
        return count

    def get_server_metrics(self) -> Dict:
        """Get server-side metrics (database size, active connections, etc)."""
        # Get database size in MB
        try:
            db_size_bytes = self.db_path.stat().st_size
            db_size_mb = db_size_bytes / (1024 * 1024)
        except Exception:
            db_size_mb = 0.0

        # Count active sensors
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sensor_metadata WHERE status = 'active'")
            active_sensors = cursor.fetchone()[0]

            # Get total records across all sensors
            cursor.execute("SELECT sensor_name FROM sensor_metadata")
            all_sensors = [row[0] for row in cursor.fetchall()]

        total_records = sum(self.get_total_records(sensor) for sensor in all_sensors)
        total_sensors = len(self.client.get_all_sensor_names())

        return {
            "database_size_mb": round(db_size_mb, 2),
            "total_records": total_records,
            "active_sensors": active_sensors,
            "total_sensors": total_sensors,
            "uptime": self.get_uptime()
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
        """Background polling loop with parallel sensor updates."""
        # Create a thread pool for parallel sensor polling
        # Use max_workers based on sensor count (but cap at 10 to avoid overwhelming)
        max_workers = min(len(self.client.get_all_sensor_names()), 10)
        
        while not self._stop_polling.is_set():
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] Polling all sensors in parallel (max {max_workers} concurrent)...")
                logger.info("Polling all sensors in parallel...")
                
                sensor_names = self.client.get_all_sensor_names()
                
                # Poll sensors in parallel using ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all sensor updates
                    futures = {
                        executor.submit(self.update_sensor_data, sensor_name): sensor_name
                        for sensor_name in sensor_names
                    }
                    
                    # Wait for all to complete and log any errors
                    for future in as_completed(futures):
                        sensor_name = futures[future]
                        try:
                            future.result()
                        except Exception as e:
                            logger.error(f"Error polling {sensor_name}: {e}")
                            print(f"[{timestamp}] ERROR polling {sensor_name}: {e}")

                print(f"[{timestamp}] Polling complete. Next poll in {self.poll_interval} seconds.")
                logger.info(f"Polling complete. Next poll in {self.poll_interval} seconds.")

            except Exception as e:
                print(f"[{timestamp}] ERROR in polling loop: {e}")
                logger.error(f"Error in polling loop: {e}")

            # Wait for next poll cycle (or until stop signal)
            self._stop_polling.wait(self.poll_interval)

    def poll_once(self):
        """Poll all sensors once (synchronous but in parallel)."""
        sensor_names = self.client.get_all_sensor_names()
        max_workers = min(len(sensor_names), 10)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.update_sensor_data, sensor_name): sensor_name
                for sensor_name in sensor_names
            }
            
            for future in as_completed(futures):
                sensor_name = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error in poll_once for {sensor_name}: {e}")

    def _fetch_last_timestamp(self, table_name: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT MAX(timestamp) FROM {table_name}")
            return cursor.fetchone()[0]

    def _check_gap_in_data(self, sensor_data: Dict, last_timestamp: Optional[str]) -> bool:
        """
        Check if there's a significant gap between last DB timestamp and fetched data.
        This method uses already-fetched data to avoid an extra HTTP request.
        
        Args:
            sensor_data: Data dict with 'time', 'temperature', 'humidity' arrays
            last_timestamp: Last timestamp in database
            
        Returns:
            True if a resync is needed, False otherwise
        """
        if last_timestamp is None:
            return True
            
        times = sensor_data.get("time", [])
        if not times:
            return False
            
        # Check the oldest timestamp in the fetched data
        oldest_fetched = times[0] if times else None
        if not oldest_fetched:
            return False
            
        oldest_dt = self._parse_ts(oldest_fetched)
        last_dt = self._parse_ts(last_timestamp)
        
        if oldest_dt is None or last_dt is None:
            return False
            
        # If oldest fetched data is newer than our last timestamp by more than threshold,
        # we have a gap and need to resync
        gap_threshold = max(self.poll_interval * 6, self._min_gap_threshold)
        return (oldest_dt - last_dt) > timedelta(seconds=gap_threshold)

    def _should_resync(self, sensor_name: str, last_timestamp: Optional[str]) -> bool:
        """
        DEPRECATED: This method is kept for backward compatibility but is no longer used.
        Use _check_gap_in_data instead to avoid extra HTTP requests.
        """
        if last_timestamp is None:
            return True

        peek_data = self.client.get_sensor_data(sensor_name, limit=10)
        if not peek_data:
            return False

        peek_times = peek_data.get("data", {}).get("time", [])
        if not peek_times:
            return False

        newest_dt = self._parse_ts(peek_times[-1])
        last_dt = self._parse_ts(last_timestamp)
        if newest_dt is None or last_dt is None:
            return False

        gap_threshold = max(self.poll_interval * 6, self._min_gap_threshold)
        return (newest_dt - last_dt) > timedelta(seconds=gap_threshold)

    def _resync_sensor(self, sensor_name: str, start_timestamp: Optional[str]) -> Tuple[List[Tuple[str, float, float]], Optional[str]]:
        records: List[Tuple[str, float, float]] = []
        current_last = start_timestamp
        device_ip: Optional[str] = None

        while True:
            request_start = current_last if current_last is not None else self._start_of_time
            data = self.client.get_sensor_data(
                sensor_name,
                start=request_start,
                range_limit=self._resync_batch_limit,
                timeout=self._resync_timeout
            )

            if data is None:
                break

            device_ip = data.get("device_ip", device_ip)
            sensor_data = data.get("data", {})
            chunk = self._extract_new_records(sensor_data, current_last)

            if not chunk:
                break

            records.extend(chunk)
            current_last = chunk[-1][0]

            if len(sensor_data.get("time", [])) < self._resync_batch_limit:
                break

        return records, device_ip

    @staticmethod
    def _extract_new_records(sensor_data: Dict, last_timestamp: Optional[str]) -> List[Tuple[str, float, float]]:
        times = sensor_data.get("time", [])
        temps = sensor_data.get("temperature", [])
        hums = sensor_data.get("humidity", [])

        new_records: List[Tuple[str, float, float]] = []
        for i in range(len(times)):
            timestamp = times[i]
            if last_timestamp is None or timestamp > last_timestamp:
                new_records.append((timestamp, temps[i], hums[i]))

        return new_records

    @staticmethod
    def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            return None
