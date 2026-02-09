"""
Data aggregator that polls multiple sensors and maintains a local cache.
Uses SQLite for persistent storage of sensor data.
"""
import logging
import queue
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np

from .api_client import MultiSensorClient

logger = logging.getLogger(__name__)


class DataAggregator:
    """Aggregates data from multiple temperature sensors into a local SQLite database."""

    def __init__(
        self,
        multi_sensor_client: MultiSensorClient,
        sensor_configs: List[Dict] = None,
        db_path: str = "sensor_data.db",
        poll_interval: int = 30,
        min_gap_threshold: int = 60,
        retention_config: Optional[Dict] = None
    ):
        """
        Initialize data aggregator.

        Args:
            multi_sensor_client: Client for fetching data from sensors
            sensor_configs: List of sensor configs with 'name' and 'poll_interval_s' (optional)
            db_path: Path to SQLite database file
            poll_interval: Default seconds between polling cycles (fallback)
            min_gap_threshold: Minimum seconds before marking sensor as offline (default 60)
            retention_config: Data retention settings (hot/warm/cold tiers)
        """
        self.client = multi_sensor_client
        self.db_path = Path(db_path)
        self.poll_interval = poll_interval  # Keep for backward compatibility

        # Data retention config
        rc = retention_config or {}
        self._hot_retention_days = rc.get("hot_retention_days", 7)
        self._warm_retention_days = rc.get("warm_retention_days", 90)
        self._warm_resolution_s = rc.get("warm_resolution_s", 60)      # 1-min averages
        self._cold_resolution_s = rc.get("cold_resolution_s", 900)     # 15-min averages
        self._retention_thread = None
        self._resync_batch_limit = 5000
        self._resync_timeout = max(15, poll_interval * 2)
        self._min_gap_threshold = min_gap_threshold
        self._start_of_time = 0  # INTEGER timestamp: Unix epoch (Jan 1, 1970)
        self._stop_polling = threading.Event()
        self._polling_threads = {}  # Map sensor_name -> thread
        self._start_time = None  # Will be set from database in _init_database()
        self._last_poll_time = {}  # Track last successful poll for each sensor (for health checks)

        # Build sensor poll intervals map
        self._sensor_poll_intervals = {}
        if sensor_configs:
            for config in sensor_configs:
                sensor_name = config.get("name")
                poll_interval_s = config.get("poll_interval_s", poll_interval)
                self._sensor_poll_intervals[sensor_name] = poll_interval_s

        # For any sensors not in config, use default poll_interval
        for sensor_name in self.client.get_all_sensor_names():
            if sensor_name not in self._sensor_poll_intervals:
                self._sensor_poll_intervals[sensor_name] = poll_interval

        # Per-sensor poll locks — prevents overlapping polls for the same sensor
        self._poll_locks = {}  # sensor_name -> threading.Lock()
        self._backfill_in_progress = set()  # sensor names currently backfilling

        # Metadata fetch throttling — only fetch metrics/status/config every Nth poll
        self._poll_counts = {}  # sensor_name -> int
        self._metadata_fetch_interval = 10  # Fetch client metrics every 10th poll
        self._cached_metadata = {}  # sensor_name -> last fetched metadata dict

        # Single-writer DB pattern: all writes go through a queue processed by one thread.
        # This eliminates "database is locked" errors from concurrent sqlite3 access.
        self._write_queue = queue.Queue()
        self._writer_thread = None

        # Thread-local storage for read connections (each thread gets its own)
        self._local = threading.local()

        # In-memory record count cache (updated on each insert, avoids extra DB queries)
        self._record_counts = {}  # sensor_name -> int

        self._init_database()
        self._start_writer_thread()

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

            # Create server metadata table (stores server start time)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS server_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Check if server start time exists, if not create it
            cursor.execute("SELECT value FROM server_metadata WHERE key = 'start_time'")
            result = cursor.fetchone()
            if result is None:
                # First time - store current time as server start
                start_time_str = datetime.now().isoformat()
                cursor.execute("INSERT INTO server_metadata (key, value) VALUES ('start_time', ?)",
                             (start_time_str,))
                self._start_time = datetime.now()
                logger.info(f"Server started at {start_time_str}")
            else:
                # Load existing start time from database
                self._start_time = datetime.fromisoformat(result[0])
                logger.info(f"Server originally started at {result[0]} (uptime: {datetime.now() - self._start_time})")

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
                "client_total_records": "INTEGER",
                "log_interval_s": "REAL"
            }
            for col_name, col_type in new_columns.items():
                if col_name not in existing_columns:
                    cursor.execute(f"ALTER TABLE sensor_metadata ADD COLUMN {col_name} {col_type}")
                    logger.info(f"Added column {col_name} to sensor_metadata table")

            # Create a table for each sensor with extended fields for multi-sensor devices
            for sensor_name in self.client.get_all_sensor_names():
                table_name = self._get_table_name(sensor_name)
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        temperature REAL,
                        humidity REAL,
                        pressure REAL,
                        light REAL,
                        noise REAL,
                        UNIQUE(timestamp)
                    )
                """)
                cursor.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table_name}_timestamp
                    ON {table_name}(timestamp DESC)
                """)

                # Migrate existing tables: add extended sensor columns if they don't exist
                cursor.execute(f"PRAGMA table_info({table_name})")
                existing_cols = {row[1] for row in cursor.fetchall()}
                new_sensor_cols = ["pressure", "light", "noise"]
                for col_name in new_sensor_cols:
                    if col_name not in existing_cols:
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} REAL")
                        logger.info(f"Added column {col_name} to {table_name}")

                # Create aggregation tables for warm/cold tiers
                for suffix, res_label in [("1min", "warm"), ("15min", "cold")]:
                    agg_table = f"{table_name}_{suffix}"
                    cursor.execute(f"""
                        CREATE TABLE IF NOT EXISTS {agg_table} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            bucket_start INTEGER NOT NULL,
                            count INTEGER NOT NULL,
                            temperature_avg REAL,
                            temperature_min REAL,
                            temperature_max REAL,
                            humidity_avg REAL,
                            humidity_min REAL,
                            humidity_max REAL,
                            pressure_avg REAL,
                            light_avg REAL,
                            noise_avg REAL,
                            UNIQUE(bucket_start)
                        )
                    """)
                    cursor.execute(f"""
                        CREATE INDEX IF NOT EXISTS idx_{agg_table}_bucket
                        ON {agg_table}(bucket_start DESC)
                    """)

            # Initialize record counts from database while connection is open
            for sensor_name in self.client.get_all_sensor_names():
                table_name = self._get_table_name(sensor_name)
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    self._record_counts[sensor_name] = cursor.fetchone()[0]
                except Exception:
                    self._record_counts[sensor_name] = 0

            conn.commit()
    def _start_writer_thread(self):
        """Start the single-writer background thread for all DB writes."""
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            daemon=True,
            name="DB-Writer"
        )
        self._writer_thread.start()
        logger.info("Started DB writer thread")

    def _writer_loop(self):
        """
        Single-writer thread that processes all DB write operations from the queue.
        Only ONE connection is ever used for writing, eliminating lock contention.
        """
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")

        while True:
            try:
                op = self._write_queue.get()
                if op is None:
                    self._write_queue.task_done()
                    break  # Shutdown signal

                op_type = op[0]
                max_retries = 3

                for attempt in range(max_retries):
                    try:
                        if op_type == "insert_data":
                            _, table_name, records, sensor_name = op
                            cursor = conn.cursor()
                            cursor.executemany(
                                f"INSERT OR IGNORE INTO {table_name} "
                                f"(timestamp, temperature, humidity, pressure, light, noise) "
                                f"VALUES (?, ?, ?, ?, ?, ?)",
                                records
                            )
                            conn.commit()
                            inserted = cursor.rowcount
                            if sensor_name and inserted > 0:
                                self._record_counts[sensor_name] = \
                                    self._record_counts.get(sensor_name, 0) + inserted

                        elif op_type == "update_metadata":
                            _, sensor_name, metadata_dict = op
                            total_records = self._record_counts.get(sensor_name, 0)
                            cursor = conn.cursor()
                            cursor.execute("""
                                INSERT OR REPLACE INTO sensor_metadata
                                (sensor_name, device_ip, last_update, last_error, status,
                                 cpu_percent, memory_percent, client_db_size_mb, sensor_type,
                                 total_records, client_total_records, log_interval_s)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                sensor_name,
                                metadata_dict.get("device_ip"),
                                datetime.now().isoformat(),
                                metadata_dict.get("error"),
                                metadata_dict.get("status", "unknown"),
                                metadata_dict.get("cpu_percent"),
                                metadata_dict.get("memory_percent"),
                                metadata_dict.get("client_db_size_mb"),
                                metadata_dict.get("sensor_type"),
                                total_records,
                                metadata_dict.get("client_total_records"),
                                metadata_dict.get("log_interval_s"),
                            ))
                            conn.commit()

                        elif op_type == "insert_batch":
                            # Used by backfill — large batch insert
                            _, table_name, records, sensor_name = op
                            cursor = conn.cursor()
                            cursor.executemany(
                                f"INSERT OR IGNORE INTO {table_name} "
                                f"(timestamp, temperature, humidity, pressure, light, noise) "
                                f"VALUES (?, ?, ?, ?, ?, ?)",
                                records
                            )
                            conn.commit()
                            inserted = cursor.rowcount
                            if sensor_name and inserted > 0:
                                self._record_counts[sensor_name] = \
                                    self._record_counts.get(sensor_name, 0) + inserted

                        elif op_type == "insert_warm":
                            # Retention: insert 1-min aggregated rows
                            _, agg_table, rows = op
                            cursor = conn.cursor()
                            cursor.executemany(
                                f"INSERT OR IGNORE INTO {agg_table} "
                                f"(bucket_start, count, "
                                f"temperature_avg, temperature_min, temperature_max, "
                                f"humidity_avg, humidity_min, humidity_max, "
                                f"pressure_avg, light_avg, noise_avg) "
                                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                rows
                            )
                            conn.commit()

                        elif op_type == "insert_cold":
                            # Retention: insert 15-min aggregated rows
                            _, agg_table, rows = op
                            cursor = conn.cursor()
                            cursor.executemany(
                                f"INSERT OR IGNORE INTO {agg_table} "
                                f"(bucket_start, count, "
                                f"temperature_avg, temperature_min, temperature_max, "
                                f"humidity_avg, humidity_min, humidity_max, "
                                f"pressure_avg, light_avg, noise_avg) "
                                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                rows
                            )
                            conn.commit()

                        elif op_type == "delete_old":
                            # Retention: delete rows older than cutoff
                            _, table_name, cutoff_ms, ts_column = op
                            ts_col = ts_column or "timestamp"
                            cursor = conn.cursor()
                            cursor.execute(
                                f"DELETE FROM {table_name} WHERE {ts_col} < ?",
                                (cutoff_ms,)
                            )
                            conn.commit()
                            logger.info(f"Retention: deleted {cursor.rowcount} rows from {table_name}")

                        break  # Success — exit retry loop

                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e) and attempt < max_retries - 1:
                            logger.warning(f"DB write retry {attempt + 1}/{max_retries}: {e}")
                            time.sleep(0.1 * (attempt + 1))
                        else:
                            logger.error(f"DB write failed after {max_retries} retries: {e}")
                            break

                # Mark task as done
                self._write_queue.task_done()

            except Exception as e:
                logger.error(f"Error in writer thread: {e}", exc_info=True)
                # Still mark as done to avoid blocking join()
                try:
                    self._write_queue.task_done()
                except ValueError:
                    pass  # task_done() called too many times

        conn.close()

    def _submit_write(self, op_tuple):
        """Submit a write operation to the writer queue."""
        self._write_queue.put(op_tuple)

    def _get_read_conn(self):
        """Get a thread-local read-only DB connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA query_only=ON")
        return self._local.conn

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
        logger.info(f"Starting full historical backfill for {sensor_name}...")

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
            pressures = sensor_data.get("pressure", [None] * len(times))  # Default to None if not present
            lights = sensor_data.get("light", [None] * len(times))
            noises = sensor_data.get("noise", [None] * len(times))

            if len(times) == 0:
                logger.warning(f"No historical data available for {sensor_name}")
                return False

            # Prepare records for insertion (6-tuple now)
            records = list(zip(times, temperatures, humidities, pressures, lights, noises))
            total_records = len(records)

            # Insert in batches via the write queue
            batch_size = 10000
            inserted_count = 0

            for i in range(0, total_records, batch_size):
                batch = records[i:i + batch_size]
                self._submit_write(("insert_batch", table_name, batch, sensor_name))
                inserted_count += len(batch)
                if (i + batch_size) % 50000 == 0:
                    logger.info(f"Backfilled {inserted_count:,} / {total_records:,} records for {sensor_name}...")

            logger.info(f"Backfill complete: {total_records:,} records synced for {sensor_name}")
            return True

        except Exception as e:
            logger.error(f"Error during historical backfill for {sensor_name}: {e}")
            return False

    def update_sensor_data(self, sensor_name: str, limit: int = 10):
        """
        Fetch latest data from a sensor and update local database.

        Tries the v2 /poll endpoint first (single HTTP request for everything).
        Falls back to v1 legacy endpoints if the sensor doesn't support v2.

        On first sync, performs full historical backfill of all client data.
        Subsequent syncs fetch only recent data for efficiency.

        Args:
            sensor_name: Name of sensor to update
            limit: Number of recent readings to fetch (default 10)
        """
        table_name = self._get_table_name(sensor_name)
        logger.debug(f"Fetching data from sensor: {sensor_name}")

        # Resolve device IP from configured URL — always available even if sensor is offline
        sensor_url = self.client.get_sensor_url(sensor_name)
        if sensor_url:
            parsed = urlparse(sensor_url)
            device_ip = parsed.hostname or "unknown"
        else:
            device_ip = "unknown"

        try:
            last_timestamp = self._fetch_last_timestamp(table_name)
            logger.debug(f"Last timestamp in DB for {sensor_name}: {last_timestamp}")

            # If this is the first sync (no data in server DB), do full historical backfill
            if last_timestamp is None:
                if sensor_name in self._backfill_in_progress:
                    logger.debug(f"Backfill already in progress for {sensor_name}, skipping")
                    return
                self._backfill_in_progress.add(sensor_name)
                try:
                    logger.info(f"First sync detected for {sensor_name}, initiating full historical backfill")
                    success = self._backfill_all_historical_data(sensor_name)
                    if not success:
                        self._update_metadata(sensor_name, device_ip=device_ip, status="error", error="Failed to backfill historical data")
                        return
                    last_timestamp = self._fetch_last_timestamp(table_name)
                finally:
                    self._backfill_in_progress.discard(sensor_name)

            # Try v2 consolidated poll first (1 HTTP request instead of 1-4)
            api_version = self.client.get_sensor_api_version(sensor_name)
            if api_version != 1:  # Unknown or v2 — try v2
                poll_result = self.client.poll_sensor(
                    sensor_name,
                    since=last_timestamp,
                    limit=limit
                )
                if poll_result is not None:
                    self._process_v2_poll(sensor_name, table_name, poll_result, last_timestamp, device_ip)
                    return

            # Fallback: v1 legacy path (separate HTTP requests)
            self._update_sensor_data_v1(sensor_name, table_name, last_timestamp, limit, device_ip)

        except Exception as e:
            logger.error(f"Error updating data for {sensor_name}: {e}")
            self._update_metadata(sensor_name, device_ip=device_ip, status="error", error=str(e))

    def _process_v2_poll(
        self,
        sensor_name: str,
        table_name: str,
        poll_result: Dict,
        last_timestamp: Optional[int],
        device_ip: str = "unknown"
    ):
        """
        Process a v2 /poll response: extract data, metrics, status from one response.
        This is the fast path — one HTTP request replaces 1-4 legacy requests.
        """
        sensor_data = poll_result.get("data", {})
        metrics = poll_result.get("metrics", {})
        config = poll_result.get("config", {})
        hardware_status = poll_result.get("hardware_status", "ok")

        # Cache metadata from the v2 response (always fresh — no throttling needed)
        self._cached_metadata[sensor_name] = {
            "cpu_percent": metrics.get("cpu_percent"),
            "memory_percent": metrics.get("memory_percent"),
            "client_db_size_mb": metrics.get("database_size_mb"),
            "sensor_type": config.get("sensor_type"),
            "client_total_records": metrics.get("total_records"),
            "hardware_status": hardware_status,
            "log_interval_s": config.get("log_interval_s"),
        }

        # Check if resync needed based on fetched data
        needs_resync = self._check_gap_in_data(sensor_data, last_timestamp)

        if needs_resync:
            logger.warning(f"Gap detected for {sensor_name}; streaming backlog...")
            new_records, device_ip = self._resync_sensor(sensor_name, last_timestamp)
            status_value = "syncing" if new_records else "idle"
        else:
            new_records = self._extract_new_records(sensor_data, last_timestamp)
            status_value = self._determine_status(sensor_name, new_records, last_timestamp)

        if new_records:
            self._submit_write(("insert_data", table_name, new_records, sensor_name))
            logger.info(f"Added {len(new_records)} new records for {sensor_name} (v2)")

        # Override status if hardware failure detected
        if hardware_status == "hardware_failure":
            status_value = "hardware_failure"
            error_msg = "Sensor hardware failure: returning None values"
        else:
            error_msg = None

        cached = self._cached_metadata.get(sensor_name, {})
        self._update_metadata(
            sensor_name,
            device_ip=device_ip,
            status=status_value,
            error=error_msg,
            cpu_percent=cached.get("cpu_percent"),
            memory_percent=cached.get("memory_percent"),
            client_db_size_mb=cached.get("client_db_size_mb"),
            sensor_type=cached.get("sensor_type"),
            client_total_records=cached.get("client_total_records"),
            log_interval_s=cached.get("log_interval_s"),
        )

    def _update_sensor_data_v1(
        self,
        sensor_name: str,
        table_name: str,
        last_timestamp: Optional[int],
        limit: int,
        device_ip: str = "unknown"
    ):
        """
        Legacy v1 polling path: separate HTTP requests for data, metrics, status, config.
        Used as fallback when a sensor doesn't support the v2 /poll endpoint.
        """
        new_records: List[Tuple[int, float, float, float, float, float]] = []

        # Fetch data (single request instead of peek + fetch)
        data = self.client.get_sensor_data(sensor_name, limit=limit)
        if data is None:
            logger.warning(f"Failed to fetch data from {sensor_name}")
            self._handle_fetch_failure(sensor_name, last_timestamp, "Failed to fetch data")
            return

        sensor_data = data.get("data", {})

        # Throttle metadata HTTP requests: only fetch metrics/status/config every Nth poll
        # This cuts HTTP requests from 4 to 1 per normal poll cycle
        self._poll_counts[sensor_name] = self._poll_counts.get(sensor_name, 0) + 1
        fetch_metadata = (self._poll_counts[sensor_name] % self._metadata_fetch_interval == 1)

        if fetch_metadata:
            metrics = self.client.get_sensor_metrics(sensor_name)
            status_info = self.client.sensors[sensor_name].get_status() if sensor_name in self.client.sensors else None
            config = self.client.get_sensor_config(sensor_name)

            # Cache the metadata for use on non-metadata polls
            self._cached_metadata[sensor_name] = {
                "cpu_percent": metrics.get("cpu_percent") if metrics else None,
                "memory_percent": metrics.get("memory_percent") if metrics else None,
                "client_db_size_mb": metrics.get("database_size_mb") if metrics else None,
                "sensor_type": metrics.get("sensor_type") if metrics else None,
                "client_total_records": metrics.get("total_records") if metrics else None,
                "hardware_status": status_info.get("hardware_status") if status_info else "ok",
                "log_interval_s": config.get("log_interval_s") if config else None,
            }

        cached = self._cached_metadata.get(sensor_name, {})
        hardware_status = cached.get("hardware_status", "ok")

        # Check if resync needed based on fetched data
        needs_resync = self._check_gap_in_data(sensor_data, last_timestamp)

        if needs_resync:
            logger.warning(f"Gap detected for {sensor_name}; streaming backlog...")
            new_records, device_ip = self._resync_sensor(sensor_name, last_timestamp)
            status_value = "syncing" if new_records else "idle"
        else:
            new_records = self._extract_new_records(sensor_data, last_timestamp)
            status_value = self._determine_status(sensor_name, new_records, last_timestamp)

        if new_records:
            self._submit_write(("insert_data", table_name, new_records, sensor_name))
            logger.info(f"Added {len(new_records)} new records for {sensor_name} (v1)")

        # Override status if hardware failure detected
        if hardware_status == "hardware_failure":
            status_value = "hardware_failure"
            error_msg = "Sensor hardware failure: returning None values"
        else:
            error_msg = None

        self._update_metadata(
            sensor_name,
            device_ip=device_ip,
            status=status_value,
            error=error_msg,
            cpu_percent=cached.get("cpu_percent"),
            memory_percent=cached.get("memory_percent"),
            client_db_size_mb=cached.get("client_db_size_mb"),
            sensor_type=cached.get("sensor_type"),
            client_total_records=cached.get("client_total_records"),
            log_interval_s=cached.get("log_interval_s"),
        )

    def _handle_fetch_failure(
        self,
        sensor_name: str,
        last_timestamp: Optional[int],
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

    def _determine_status(
        self,
        sensor_name: str,
        new_records: list,
        last_timestamp: Optional[int]
    ) -> str:
        """
        Determine sensor status based on data recency, not just new records.
        Prevents status flickering when polling faster than logging.
        """
        if new_records:
            return "active"
        if last_timestamp is not None:
            now_ms = int(datetime.now().timestamp() * 1000)
            time_since_last_ms = now_ms - last_timestamp
            poll_interval_s = self._sensor_poll_intervals.get(sensor_name, self.poll_interval)
            fresh_threshold_ms = max(poll_interval_s * 2, self._min_gap_threshold) * 1000
            if time_since_last_ms <= fresh_threshold_ms:
                return "active"  # Recent data, sensor is healthy
            return "idle"  # Data is getting stale
        return "idle"  # No data at all

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
        client_total_records: Optional[int] = None,
        log_interval_s: Optional[float] = None
    ):
        """Update sensor metadata via the write queue."""
        # Check for hardware failure pattern: error message contains "None values"
        if error and "None values" in error:
            status = "hardware_failure"

        self._submit_write(("update_metadata", sensor_name, {
            "device_ip": device_ip,
            "status": status,
            "error": error,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "client_db_size_mb": client_db_size_mb,
            "sensor_type": sensor_type,
            "client_total_records": client_total_records,
            "log_interval_s": log_interval_s,
        }))

    def get_sensor_data(
        self,
        sensor_name: str,
        limit: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None
    ) -> Dict:
        """
        Get sensor data from local database with transparent tiered storage.

        For recent data (within hot tier): returns full-resolution raw data.
        For older data: returns aggregated data from warm/cold tiers,
        seamlessly merged with raw data so the caller sees a single result.

        Args:
            sensor_name: Name of sensor
            limit: If specified, return last N readings
            start_time: Start timestamp (INTEGER milliseconds since epoch)
            end_time: End timestamp (INTEGER milliseconds since epoch)

        Returns:
            Dictionary with arrays for 'time', 'temperature', 'humidity', and optionally
            'pressure', 'light', 'noise' (as milliseconds for Bokeh compatibility)
        """
        table_name = self._get_table_name(sensor_name)
        conn = self._get_read_conn()
        cursor = conn.cursor()

        # Determine hot-tier cutoff
        now_ms = int(datetime.now().timestamp() * 1000)
        hot_cutoff_ms = now_ms - (self._hot_retention_days * 86400 * 1000)
        warm_cutoff_ms = now_ms - (self._warm_retention_days * 86400 * 1000)

        # For limit queries or recent-only queries, just use the raw table
        needs_tiered = False
        if limit is None and start_time is not None and start_time < hot_cutoff_ms:
            needs_tiered = True
        elif limit is None and start_time is None and end_time is None:
            # "All data" — check if aggregation tables have data
            needs_tiered = True

        if needs_tiered:
            return self._get_tiered_sensor_data(
                sensor_name, table_name, cursor,
                start_time, end_time,
                hot_cutoff_ms, warm_cutoff_ms
            )

        # Standard query from raw table (hot tier only)
        if limit is not None:
            query = f"""
                SELECT timestamp, temperature, humidity, pressure, light, noise
                FROM (
                    SELECT timestamp, temperature, humidity, pressure, light, noise
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
            query = f"SELECT timestamp, temperature, humidity, pressure, light, noise FROM {table_name} WHERE {where_clause} ORDER BY timestamp"
            cursor.execute(query, params)
        else:
            query = f"SELECT timestamp, temperature, humidity, pressure, light, noise FROM {table_name} ORDER BY timestamp"
            cursor.execute(query)

        return self._rows_to_result(cursor.fetchall())

    def _get_tiered_sensor_data(self, sensor_name, table_name, cursor,
                                 start_time, end_time,
                                 hot_cutoff_ms, warm_cutoff_ms):
        """
        Query across cold → warm → hot tiers and merge into a single result.
        Older data comes from aggregation tables; recent data from raw table.
        Falls back to raw table for any time range where aggregation tables
        have no data (e.g. before retention has run for the first time).
        """
        warm_table = f"{table_name}_1min"
        cold_table = f"{table_name}_15min"
        all_rows = []

        effective_start = start_time or 0
        effective_end = end_time or int(datetime.now().timestamp() * 1000)

        # Track whether aggregation tiers had data (for fallback logic)
        agg_had_data = False

        # Cold tier: data older than warm cutoff
        if effective_start < warm_cutoff_ms:
            cold_end = min(warm_cutoff_ms, effective_end)
            try:
                cursor.execute(f"""
                    SELECT bucket_start, temperature_avg, humidity_avg,
                           pressure_avg, light_avg, noise_avg
                    FROM {cold_table}
                    WHERE bucket_start >= ? AND bucket_start < ?
                    ORDER BY bucket_start
                """, (effective_start, cold_end))
                cold_rows = cursor.fetchall()
                if cold_rows:
                    all_rows.extend(cold_rows)
                    agg_had_data = True
            except sqlite3.OperationalError:
                pass  # Table doesn't exist yet
            except Exception as e:
                logger.warning(f"Unexpected error querying cold tier for {sensor_name}: {e}")

        # Warm tier: data between warm and hot cutoffs
        if effective_start < hot_cutoff_ms and effective_end >= warm_cutoff_ms:
            warm_start = max(effective_start, warm_cutoff_ms)
            warm_end = min(hot_cutoff_ms, effective_end)
            try:
                cursor.execute(f"""
                    SELECT bucket_start, temperature_avg, humidity_avg,
                           pressure_avg, light_avg, noise_avg
                    FROM {warm_table}
                    WHERE bucket_start >= ? AND bucket_start < ?
                    ORDER BY bucket_start
                """, (warm_start, warm_end))
                warm_rows = cursor.fetchall()
                if warm_rows:
                    all_rows.extend(warm_rows)
                    agg_had_data = True
            except sqlite3.OperationalError:
                pass  # Table doesn't exist yet
            except Exception as e:
                logger.warning(f"Unexpected error querying warm tier for {sensor_name}: {e}")

        # Fallback: if aggregation tiers had no data, query raw table for
        # the full range. This happens before retention first runs (all data
        # is still in the raw table) or if aggregation tables are empty.
        if not agg_had_data and effective_start < hot_cutoff_ms:
            raw_fallback_end = min(hot_cutoff_ms, effective_end)
            cursor.execute(f"""
                SELECT timestamp, temperature, humidity, pressure, light, noise
                FROM {table_name}
                WHERE timestamp >= ? AND timestamp < ?
                ORDER BY timestamp
            """, (effective_start, raw_fallback_end))
            all_rows.extend(cursor.fetchall())

        # Hot tier: recent raw data
        if effective_end >= hot_cutoff_ms:
            raw_start = max(effective_start, hot_cutoff_ms)
            cursor.execute(f"""
                SELECT timestamp, temperature, humidity, pressure, light, noise
                FROM {table_name}
                WHERE timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
            """, (raw_start, effective_end))
            all_rows.extend(cursor.fetchall())

        return self._rows_to_result(all_rows)

    @staticmethod
    def _rows_to_result(rows) -> Dict:
        """Convert database rows to the standard result dict format."""
        if not rows:
            return {
                "time": np.array([]),
                "temperature": np.array([]),
                "humidity": np.array([]),
                "pressure": np.array([]),
                "light": np.array([]),
                "noise": np.array([])
            }

        times_ms = np.array([row[0] for row in rows], dtype=np.float64)
        temperatures = np.array([row[1] for row in rows], dtype=np.float32)
        humidities = np.array([row[2] for row in rows], dtype=np.float32)
        pressures = np.array([row[3] if row[3] is not None else np.nan for row in rows], dtype=np.float32)
        lights = np.array([row[4] if row[4] is not None else np.nan for row in rows], dtype=np.float32)
        noises = np.array([row[5] if row[5] is not None else np.nan for row in rows], dtype=np.float32)

        result = {
            "time": times_ms,
            "temperature": temperatures,
            "humidity": humidities
        }

        if not np.all(np.isnan(pressures)):
            result["pressure"] = pressures
        if not np.all(np.isnan(lights)):
            result["light"] = lights
        if not np.all(np.isnan(noises)):
            result["noise"] = noises

        return result

    def get_sensor_metadata(self, sensor_name: str) -> Optional[Dict]:
        """Get metadata for a specific sensor."""
        conn = self._get_read_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT device_ip, last_update, last_error, status,
                      cpu_percent, memory_percent, client_db_size_mb,
                      sensor_type, total_records, client_total_records, log_interval_s
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
            "client_total_records": row[9],
            "log_interval_s": row[10]
        }

    def get_uptime(self) -> timedelta:
        """Get server uptime as a timedelta."""
        return datetime.now() - self._start_time

    def get_total_records(self, sensor_name: str) -> int:
        """Get total number of records for a specific sensor (from in-memory cache)."""
        return self._record_counts.get(sensor_name, 0)

    def get_last_timestamp(self, sensor_name: str) -> Optional[int]:
        """Get the most recent timestamp for a sensor (milliseconds since epoch)."""
        table_name = self._get_table_name(sensor_name)
        return self._fetch_last_timestamp(table_name)

    def get_server_metrics(self) -> Dict:
        """Get server-side metrics (database size, active connections, etc)."""
        # Get database size in MB
        try:
            db_size_bytes = self.db_path.stat().st_size
            db_size_mb = db_size_bytes / (1024 * 1024)
        except Exception:
            db_size_mb = 0.0

        # Count active sensors
        conn = self._get_read_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_metadata WHERE status = 'active'")
        active_sensors = cursor.fetchone()[0]

        total_records = sum(self._record_counts.values())
        total_sensors = len(self.client.get_all_sensor_names())

        return {
            "database_size_mb": round(db_size_mb, 2),
            "total_records": total_records,
            "active_sensors": active_sensors,
            "total_sensors": total_sensors,
            "uptime": self.get_uptime()
        }

    def _initial_connectivity_check(self, sensor_names):
        """Check connectivity and fetch initial data from all sensors on startup."""
        logger.info("Checking connectivity to all sensors...")

        for sensor_name in sensor_names:
            try:
                # Quick connectivity check with short timeout (3s instead of default 10s)
                import requests
                sensor_url = self.client.get_sensor_url(sensor_name)
                parsed = urlparse(sensor_url) if sensor_url else None
                device_ip = parsed.hostname if parsed else "unknown"
                response = requests.get(f"{sensor_url}/status", timeout=3)
                status = response.json() if response.status_code == 200 else None

                if status:
                    logger.info(f"  {sensor_name}: Connected")
                    # Do an immediate data fetch to populate dashboard
                    poll_interval = self._sensor_poll_intervals.get(sensor_name, self.poll_interval)
                    limit = max(10, int(poll_interval * 3))
                    self.update_sensor_data(sensor_name, limit=limit)
                else:
                    logger.warning(f"  {sensor_name}: Unreachable")
                    self._update_metadata(sensor_name, device_ip=device_ip, status="error", error="Unreachable on startup")
            except Exception as e:
                logger.warning(f"  {sensor_name}: Error - {e}")
                self._update_metadata(sensor_name, device_ip=device_ip, status="error", error=str(e))

        logger.info("Connectivity check complete")

    def start_polling(self):
        """Start background polling threads (one per sensor)."""
        # Check if any threads are already running (prevents duplicate thread spawning)
        if self._polling_threads:
            # Verify threads are actually alive, not just stale references
            alive_threads = {name: t for name, t in self._polling_threads.items() if t.is_alive()}
            if alive_threads:
                logger.warning(f"Polling threads already running for {len(alive_threads)} sensors: {list(alive_threads.keys())}")
                return
            else:
                logger.info("Clearing stale thread references")
                self._polling_threads.clear()

        self._stop_polling.clear()
        sensor_names = self.client.get_all_sensor_names()

        logger.info(f"Data Aggregator Started — monitoring {len(sensor_names)} sensor(s)")
        for sensor_name in sensor_names:
            poll_interval = self._sensor_poll_intervals.get(sensor_name, self.poll_interval)
            logger.info(f"  {sensor_name}: polling every {poll_interval}s")

        # NOTE: We no longer call _initial_connectivity_check() here.
        # It was blocking startup for 3-10s per sensor (sequential HTTP requests).
        # The polling threads below discover connectivity naturally on their first cycle.

        # Create one polling thread per sensor
        for sensor_name in sensor_names:
            poll_interval = self._sensor_poll_intervals.get(sensor_name, self.poll_interval)

            thread = threading.Thread(
                target=self._poll_sensor_loop,
                args=(sensor_name, poll_interval),
                daemon=True,
                name=f"Poll-{sensor_name}"
            )
            thread.start()
            self._polling_threads[sensor_name] = thread

        logger.info(f"Started {len(self._polling_threads)} polling threads")

        # Start retention thread (hourly aggregation + purge)
        self._start_retention_thread()

    def stop_polling(self):
        """Stop all background polling threads."""
        self._stop_polling.set()

        # Wait for all sensor polling threads to finish
        for sensor_name, thread in self._polling_threads.items():
            if thread and thread.is_alive():
                thread.join(timeout=10)
                logger.info(f"Stopped polling thread for {sensor_name}")

        self._polling_threads.clear()
        logger.info("Stopped all background polling threads")

    def stop(self):
        """Gracefully stop all background threads and close resources."""
        self.stop_polling()

        # Stop retention thread (uses same _stop_polling event)
        if self._retention_thread and self._retention_thread.is_alive():
            self._retention_thread.join(timeout=10)
            logger.info("Stopped retention thread")

        # Send shutdown sentinel to writer thread
        try:
            self._write_queue.put(None, timeout=5)
        except queue.Full:
            logger.warning("Write queue full during shutdown — writer may be stuck")

        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=10)
            logger.info("Stopped writer thread")

        logger.info("DataAggregator stopped")

    # ── Data Retention (tiered storage) ─────────────────────────────────

    def _start_retention_thread(self):
        """Start the background retention thread (runs hourly)."""
        self._retention_thread = threading.Thread(
            target=self._retention_loop,
            daemon=True,
            name="Retention"
        )
        self._retention_thread.start()
        logger.info("Started data retention thread")

    def _retention_loop(self):
        """Hourly loop that aggregates old data and purges raw rows."""
        # Wait 60s after startup before first run (let data settle)
        self._stop_polling.wait(60)

        while not self._stop_polling.is_set():
            try:
                self._run_retention()
            except Exception as e:
                logger.error(f"Retention task failed: {e}", exc_info=True)

            # Sleep 1 hour (or until stop signal)
            self._stop_polling.wait(3600)

    def _run_retention(self):
        """
        Aggregate and purge data according to retention tiers.

        Tiers:
          Hot  (0 – hot_days):    Full resolution in main table
          Warm (hot – warm_days): 1-minute aggregates in _1min table
          Cold (warm+):           15-minute aggregates in _15min table

        Process for each sensor:
          1. Aggregate hot→warm: bucket raw data older than hot_days into 1-min averages
          2. Aggregate warm→cold: bucket 1-min data older than warm_days into 15-min averages
          3. Delete raw rows older than hot_days
          4. Delete 1-min rows older than warm_days
        """
        now_ms = int(datetime.now().timestamp() * 1000)
        hot_cutoff_ms = now_ms - (self._hot_retention_days * 86400 * 1000)
        warm_cutoff_ms = now_ms - (self._warm_retention_days * 86400 * 1000)

        sensor_names = self.client.get_all_sensor_names()
        total_aggregated = 0
        total_deleted = 0

        for sensor_name in sensor_names:
            table_name = self._get_table_name(sensor_name)
            warm_table = f"{table_name}_1min"
            cold_table = f"{table_name}_15min"

            try:
                agg, deleted = self._retain_sensor(
                    table_name, warm_table, cold_table,
                    hot_cutoff_ms, warm_cutoff_ms
                )
                total_aggregated += agg
                total_deleted += deleted
            except Exception as e:
                logger.error(f"Retention failed for {sensor_name}: {e}")

        if total_aggregated > 0 or total_deleted > 0:
            logger.info(
                f"Retention complete: aggregated {total_aggregated:,} buckets, "
                f"deleted {total_deleted:,} raw rows"
            )

    def _retain_sensor(self, table_name, warm_table, cold_table,
                       hot_cutoff_ms, warm_cutoff_ms):
        """Run retention for a single sensor. Returns (aggregated_count, deleted_count)."""
        conn = self._get_read_conn()
        cursor = conn.cursor()
        aggregated = 0
        deleted = 0

        # ── Step 1: Aggregate raw → warm (1-min buckets) ──────────────
        # Find the latest bucket already aggregated so we don't redo work
        cursor.execute(f"SELECT MAX(bucket_start) FROM {warm_table}")
        row = cursor.fetchone()
        warm_start_ms = row[0] if row and row[0] is not None else 0

        # Only aggregate data that is older than hot_cutoff AND newer than last warm bucket
        bucket_ms = self._warm_resolution_s * 1000
        cursor.execute(f"""
            SELECT
                (timestamp / {bucket_ms}) * {bucket_ms} AS bucket,
                COUNT(*) AS cnt,
                AVG(temperature), MIN(temperature), MAX(temperature),
                AVG(humidity), MIN(humidity), MAX(humidity),
                AVG(pressure), AVG(light), AVG(noise)
            FROM {table_name}
            WHERE timestamp < ? AND timestamp > ?
            GROUP BY bucket
            ORDER BY bucket
        """, (hot_cutoff_ms, warm_start_ms))

        warm_rows = cursor.fetchall()
        if warm_rows:
            # Submit aggregated rows via write queue
            self._submit_write(("insert_warm", warm_table, warm_rows))
            aggregated += len(warm_rows)

        # ── Step 2: Aggregate warm → cold (15-min buckets) ────────────
        cursor.execute(f"SELECT MAX(bucket_start) FROM {cold_table}")
        row = cursor.fetchone()
        cold_start_ms = row[0] if row and row[0] is not None else 0

        cold_bucket_ms = self._cold_resolution_s * 1000
        cursor.execute(f"""
            SELECT
                (bucket_start / {cold_bucket_ms}) * {cold_bucket_ms} AS bucket,
                SUM(count),
                SUM(temperature_avg * count) / SUM(count),
                MIN(temperature_min), MAX(temperature_max),
                SUM(humidity_avg * count) / SUM(count),
                MIN(humidity_min), MAX(humidity_max),
                SUM(pressure_avg * count) / SUM(count),
                SUM(light_avg * count) / SUM(count),
                SUM(noise_avg * count) / SUM(count)
            FROM {warm_table}
            WHERE bucket_start < ? AND bucket_start > ?
            GROUP BY bucket
            ORDER BY bucket
        """, (warm_cutoff_ms, cold_start_ms))

        cold_rows = cursor.fetchall()
        if cold_rows:
            self._submit_write(("insert_cold", cold_table, cold_rows))
            aggregated += len(cold_rows)

        # ── Step 3: Delete raw rows older than hot tier ───────────────
        cursor.execute(f"SELECT COUNT(*) FROM {table_name} WHERE timestamp < ?",
                       (hot_cutoff_ms,))
        delete_count = cursor.fetchone()[0]
        if delete_count > 0:
            self._submit_write(("delete_old", table_name, hot_cutoff_ms, None))
            deleted += delete_count
            # Update in-memory record count
            sensor_name_key = None
            for sn in self._record_counts:
                if self._get_table_name(sn) == table_name:
                    sensor_name_key = sn
                    break
            if sensor_name_key:
                self._record_counts[sensor_name_key] = max(
                    0, self._record_counts.get(sensor_name_key, 0) - delete_count
                )

        # ── Step 4: Delete warm rows older than warm tier ─────────────
        cursor.execute(f"SELECT COUNT(*) FROM {warm_table} WHERE bucket_start < ?",
                       (warm_cutoff_ms,))
        warm_delete = cursor.fetchone()[0]
        if warm_delete > 0:
            self._submit_write(("delete_old", warm_table, warm_cutoff_ms, "bucket_start"))

        return aggregated, deleted

    def _poll_sensor_loop(self, sensor_name: str, poll_interval: int):
        """
        Background polling loop for a single sensor with exponential backoff on errors.
        Each sensor has its own thread with its own poll interval.

        Args:
            sensor_name: Name of sensor to poll
            poll_interval: Seconds between polls for this sensor
        """
        logger.info(f"Started polling loop for {sensor_name} (interval: {poll_interval}s)")

        thread_id = threading.current_thread().name
        consecutive_errors = 0
        max_backoff = min(poll_interval * 8, 300)  # Cap at 5 minutes or 8x poll interval
        loop_count = 0
        poll_lock = self._poll_locks.setdefault(sensor_name, threading.Lock())

        while not self._stop_polling.is_set():
            loop_count += 1

            # Prevent overlapping polls: if previous poll still running, skip this cycle
            if not poll_lock.acquire(blocking=False):
                logger.debug(f"[{thread_id}] Skipping poll for {sensor_name} - previous poll still running")
                self._stop_polling.wait(poll_interval)
                continue

            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.debug(f"[{thread_id}] [{timestamp}] Loop #{loop_count} - Polling {sensor_name}...")

                # Poll this sensor with dynamic limit based on poll interval
                # Fetch enough records to cover several polling cycles
                dynamic_limit = max(10, int(poll_interval * 3))
                self.update_sensor_data(sensor_name, limit=dynamic_limit)

                logger.debug(f"[{thread_id}] [{timestamp}] Polling complete for {sensor_name}. Next poll in {poll_interval}s.")

                # Log recovery if there were previous errors
                if consecutive_errors > 0:
                    logger.info(f"{sensor_name}: Reconnected (after {consecutive_errors} failures)")

                # Reset error counter on success
                consecutive_errors = 0

                # Update last poll time for health monitoring
                self._last_poll_time[sensor_name] = datetime.now()

            except Exception as e:
                consecutive_errors += 1

                # Calculate exponential backoff delay
                backoff_delay = min(poll_interval * (2 ** (consecutive_errors - 1)), max_backoff)

                # Log first error immediately, then every 10th to avoid flooding
                if consecutive_errors == 1:
                    logger.warning(f"Polling failed for {sensor_name}: {e}")
                elif consecutive_errors % 10 == 0:
                    logger.warning(f"{sensor_name} still offline ({consecutive_errors} failures, backoff: {backoff_delay:.0f}s)")

                # Wait with backoff before retrying (release lock first!)
                poll_lock.release()
                self._stop_polling.wait(backoff_delay)
                continue

            finally:
                # Always release the poll lock (unless already released in error path)
                if poll_lock.locked():
                    poll_lock.release()

            # Wait for next poll cycle (or until stop signal)
            logger.debug(f"[{thread_id}] Waiting {poll_interval}s until next poll of {sensor_name}")
            self._stop_polling.wait(poll_interval)

        logger.info(f"[{thread_id}] Stopped polling loop for {sensor_name} (ran {loop_count} iterations)")

    def get_polling_health(self) -> Dict:
        """Get health status of all polling threads."""
        health = {}
        now = datetime.now()
        
        for sensor_name, thread in self._polling_threads.items():
            poll_interval = self._sensor_poll_intervals.get(sensor_name, self.poll_interval)
            last_poll = self._last_poll_time.get(sensor_name)
            
            thread_alive = thread.is_alive() if thread else False
            
            if last_poll:
                time_since_poll = (now - last_poll).total_seconds()
                # Consider stalled if no poll in 3x the poll interval
                is_stalled = time_since_poll > (poll_interval * 3)
                
                health[sensor_name] = {
                    "thread_alive": thread_alive,
                    "last_poll": last_poll.isoformat(),
                    "seconds_since_poll": round(time_since_poll, 1),
                    "poll_interval": poll_interval,
                    "status": "stalled" if is_stalled else "healthy"
                }
            else:
                health[sensor_name] = {
                    "thread_alive": thread_alive,
                    "last_poll": None,
                    "seconds_since_poll": None,
                    "poll_interval": poll_interval,
                    "status": "never_polled" if thread_alive else "dead"
                }
        
        return health

    def _polling_loop(self):
        """Background polling loop with parallel sensor updates."""
        # Create a thread pool for parallel sensor polling
        # Use max_workers based on sensor count (but cap at 10 to avoid overwhelming)
        max_workers = min(len(self.client.get_all_sensor_names()), 10)
        
        while not self._stop_polling.is_set():
            try:
                logger.info(f"Polling all sensors in parallel (max {max_workers} concurrent)...")
                
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

                logger.info(f"Polling complete. Next poll in {self.poll_interval} seconds.")

            except Exception as e:
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

    def _fetch_last_timestamp(self, table_name: str) -> Optional[int]:
        """Fetch the most recent timestamp (INTEGER milliseconds) from table."""
        conn = self._get_read_conn()
        cursor = conn.cursor()
        cursor.execute(f"SELECT MAX(timestamp) FROM {table_name}")
        return cursor.fetchone()[0]

    def _check_gap_in_data(self, sensor_data: Dict, last_timestamp: Optional[int]) -> bool:
        """
        Check if there's a significant gap between last DB timestamp and fetched data.
        This method uses already-fetched data to avoid an extra HTTP request.

        Args:
            sensor_data: Data dict with 'time', 'temperature', 'humidity' arrays
            last_timestamp: Last timestamp in database (INTEGER milliseconds)

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

    def _should_resync(self, sensor_name: str, last_timestamp: Optional[int]) -> bool:
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

    def _resync_sensor(self, sensor_name: str, start_timestamp: Optional[int]) -> Tuple[List[Tuple[int, float, float]], Optional[str]]:
        """Resync sensor data from start_timestamp (INTEGER milliseconds) to present."""
        records: List[Tuple[int, float, float]] = []
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
    def _extract_new_records(sensor_data: Dict, last_timestamp: Optional[int]) -> List[Tuple]:
        """
        Extract new records from sensor data, supporting extended sensor fields.

        Returns:
            List of tuples: (timestamp, temp, hum, pressure, light, noise)
            Extended fields default to None if not present in data
        """
        times = sensor_data.get("time", [])
        temps = sensor_data.get("temperature", [])
        hums = sensor_data.get("humidity", [])
        pressures = sensor_data.get("pressure", [None] * len(times))
        lights = sensor_data.get("light", [None] * len(times))
        noises = sensor_data.get("noise", [None] * len(times))

        new_records: List[Tuple] = []
        for i in range(len(times)):
            timestamp = times[i]
            if last_timestamp is None or timestamp > last_timestamp:
                new_records.append((
                    timestamp,
                    temps[i],
                    hums[i],
                    pressures[i] if i < len(pressures) else None,
                    lights[i] if i < len(lights) else None,
                    noises[i] if i < len(noises) else None
                ))

        return new_records

    @staticmethod
    def _parse_ts(ts: Optional[int]) -> Optional[datetime]:
        """Convert INTEGER timestamp (milliseconds) to datetime object."""
        if not ts:
            return None
        try:
            return datetime.fromtimestamp(ts / 1000.0)
        except (ValueError, OSError):
            return None
