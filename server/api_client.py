"""
HTTP client for fetching data from Raspberry Pi temperature sensor APIs.
"""
import logging
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SensorAPIClient:
    """Client for interacting with a single sensor's API."""

    def __init__(self, base_url: str, timeout: tuple = (3, 8)):
        """
        Initialize API client.

        Args:
            base_url: Base URL of sensor API (e.g., "http://100.64.0.5:5000")
            timeout: Request timeout as (connect_seconds, read_seconds).
                     3s connect = fast fail for offline sensors.
                     8s read = enough time for large data responses.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._last_error = None
        self._api_version = None  # None = unknown, 2 = v2 /poll, 1 = v1 legacy
        # Use a session for connection pooling and reuse
        self._session = requests.Session()
        # Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=2,
            max_retries=0
        )
        self._session.mount('http://', adapter)
        self._session.mount('https://', adapter)

    @property
    def is_available(self) -> bool:
        """Check if sensor is currently reachable."""
        try:
            response = self._session.get(f"{self.base_url}/", timeout=(3, 5))
            return response.status_code == 200
        except Exception as e:
            self._last_error = str(e)
            return False

    def get_status(self) -> Optional[Dict]:
        """
        Get sensor status including device info and latest reading.

        Returns:
            Status dictionary or None if request fails
        """
        try:
            response = self._session.get(f"{self.base_url}/status", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get status from {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    def get_latest_data(self, limit: int = 100, timeout: Optional[int] = None) -> Optional[Dict]:
        """
        Get the most recent N readings.

        Args:
            limit: Number of readings to fetch (1-10000)
            timeout: Optional custom timeout in seconds (uses default if not specified)

        Returns:
            Data dictionary with 'device_name', 'device_ip', 'data', 'metadata' or None
        """
        try:
            response = self._session.get(
                f"{self.base_url}/data/latest",
                params={"limit": limit},
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get latest data from {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    def get_data_range(
        self,
        start: Optional[int] = None,
        end: Optional[int] = None,
        limit: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Get readings within a time range.

        Args:
            start: Start timestamp (INTEGER milliseconds since epoch)
            end: End timestamp (INTEGER milliseconds since epoch)
            limit: Optional maximum number of records to return
            timeout: Optional custom timeout in seconds

        Returns:
            Data dictionary with 'device_name', 'device_ip', 'data', 'metadata' or None
        """
        params = {}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if limit is not None:
            params["limit"] = limit

        try:
            response = self._session.get(
                f"{self.base_url}/data/range",
                params=params,
                timeout=timeout or self.timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get data range from {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    def get_config(self) -> Optional[Dict]:
        """
        Get sensor configuration.

        Returns:
            Configuration dictionary or None if request fails
        """
        try:
            response = self._session.get(f"{self.base_url}/config", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get config from {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    def get_metrics(self) -> Optional[Dict]:
        """
        Get system metrics (CPU, memory, database size).

        Returns:
            Metrics dictionary or None if request fails
        """
        try:
            response = self._session.get(f"{self.base_url}/metrics", timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get metrics from {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    def poll(self, since: Optional[int] = None, limit: int = 10) -> Optional[Dict]:
        """
        Consolidated v2 poll endpoint — fetches data + metrics + status + config in one HTTP request.
        Falls back to None if the sensor doesn't support v2 (404), allowing the caller to
        use legacy v1 endpoints instead.

        Args:
            since: Fetch data newer than this timestamp (ms since epoch). If None, fetches latest `limit` records.
            limit: Number of recent readings if `since` is not provided.

        Returns:
            Full poll response dict, or None if the endpoint is unavailable or the request fails.
        """
        params = {"limit": limit}
        if since is not None:
            params["since"] = since

        try:
            response = self._session.get(
                f"{self.base_url}/poll",
                params=params,
                timeout=self.timeout
            )

            if response.status_code == 404:
                # Sensor doesn't have v2 endpoint — mark as v1
                self._api_version = 1
                return None

            response.raise_for_status()
            result = response.json()
            self._api_version = 2
            return result

        except requests.exceptions.HTTPError:
            # Non-404 HTTP error — don't change api_version, just fail this attempt
            return None
        except Exception as e:
            logger.debug(f"Poll failed for {self.base_url}: {e}")
            self._last_error = str(e)
            return None

    @property
    def api_version(self) -> Optional[int]:
        """Detected API version: 2 = v2 /poll, 1 = v1 legacy, None = unknown."""
        return self._api_version

    @property
    def last_error(self) -> Optional[str]:
        """Get the last error message."""
        return self._last_error

    def close(self):
        """Close the session and cleanup resources."""
        self._session.close()


class MultiSensorClient:
    """Client for managing multiple sensor APIs."""

    def __init__(self, sensor_configs: List[Dict[str, str]]):
        """
        Initialize multi-sensor client.

        Args:
            sensor_configs: List of sensor configurations, each with:
                - 'name': Display name for sensor
                - 'url': Base URL of sensor API
        """
        self.sensors = {
            config["name"]: SensorAPIClient(config["url"])
            for config in sensor_configs
        }

    def get_all_statuses(self) -> Dict[str, Optional[Dict]]:
        """
        Get status from all sensors.

        Returns:
            Dictionary mapping sensor name to status (or None if unavailable)
        """
        return {
            name: client.get_status()
            for name, client in self.sensors.items()
        }

    def get_sensor_data(
        self,
        sensor_name: str,
        limit: Optional[int] = None,
        start: Optional[int] = None,
        end: Optional[int] = None,
        range_limit: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Get data from a specific sensor.

        Args:
            sensor_name: Name of sensor to query
            limit: If specified, get last N readings (ignores time range)
            start: Start timestamp (INTEGER milliseconds since epoch)
            end: End timestamp (INTEGER milliseconds since epoch)
            range_limit: Optional limit on number of records in range query
            timeout: Optional custom timeout in seconds

        Returns:
            Data dictionary or None if sensor not found or request fails
        """
        if sensor_name not in self.sensors:
            logger.error(f"Sensor '{sensor_name}' not found")
            return None

        client = self.sensors[sensor_name]

        if limit is not None:
            return client.get_latest_data(limit, timeout=timeout)
        else:
            return client.get_data_range(start, end, limit=range_limit, timeout=timeout)

    def get_available_sensors(self) -> List[str]:
        """
        Get list of sensor names that are currently available.

        Returns:
            List of sensor names
        """
        return [
            name for name, client in self.sensors.items()
            if client.is_available
        ]

    def get_all_sensor_names(self) -> List[str]:
        """Get list of all configured sensor names."""
        return list(self.sensors.keys())

    def get_sensor_metrics(self, sensor_name: str) -> Optional[Dict]:
        """
        Get system metrics from a specific sensor.

        Args:
            sensor_name: Name of sensor to query

        Returns:
            Metrics dictionary or None if sensor not found or request fails
        """
        if sensor_name not in self.sensors:
            logger.error(f"Sensor '{sensor_name}' not found")
            return None

        client = self.sensors[sensor_name]
        return client.get_metrics()

    def get_sensor_config(self, sensor_name: str) -> Optional[Dict]:
        """
        Get configuration from a specific sensor.

        Args:
            sensor_name: Name of sensor to query

        Returns:
            Configuration dictionary or None if sensor not found or request fails
        """
        if sensor_name not in self.sensors:
            logger.error(f"Sensor '{sensor_name}' not found")
            return None

        client = self.sensors[sensor_name]
        return client.get_config()

    def poll_sensor(
        self,
        sensor_name: str,
        since: Optional[int] = None,
        limit: int = 10
    ) -> Optional[Dict]:
        """
        Consolidated v2 poll for a single sensor.

        Args:
            sensor_name: Name of sensor to poll
            since: Fetch data newer than this timestamp (ms since epoch)
            limit: Number of recent readings if since is not provided

        Returns:
            Full poll response dict, or None if sensor not found or v2 not supported
        """
        if sensor_name not in self.sensors:
            logger.error(f"Sensor '{sensor_name}' not found")
            return None

        client = self.sensors[sensor_name]
        return client.poll(since=since, limit=limit)

    def get_sensor_api_version(self, sensor_name: str) -> Optional[int]:
        """Get detected API version for a sensor (1, 2, or None if unknown)."""
        if sensor_name not in self.sensors:
            return None
        return self.sensors[sensor_name].api_version

    def get_sensor_url(self, sensor_name: str) -> Optional[str]:
        """
        Get the configured URL for a specific sensor.

        Args:
            sensor_name: Name of sensor to query

        Returns:
            Base URL string or None if sensor not found
        """
        if sensor_name not in self.sensors:
            logger.error(f"Sensor '{sensor_name}' not found")
            return None

        client = self.sensors[sensor_name]
        return client.base_url
