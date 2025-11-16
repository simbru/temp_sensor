"""
HTTP client for fetching data from Raspberry Pi temperature sensor APIs.
"""
import logging
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SensorAPIClient:
    """Client for interacting with a single sensor's API."""

    def __init__(self, base_url: str, timeout: int = 10):
        """
        Initialize API client.

        Args:
            base_url: Base URL of sensor API (e.g., "http://100.64.0.5:5000")
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._last_error = None
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
            response = self._session.get(f"{self.base_url}/", timeout=5)
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
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Get readings within a time range.

        Args:
            start: Start timestamp (ISO format: 'YYYY-MM-DD HH:MM:SS')
            end: End timestamp (ISO format: 'YYYY-MM-DD HH:MM:SS')
            timeout: Optional custom timeout in seconds

        Returns:
            Data dictionary with 'device_name', 'device_ip', 'data', 'metadata' or None
        """
        params = {}
        if start:
            params["start"] = start
        if end:
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
        start: Optional[str] = None,
        end: Optional[str] = None,
        range_limit: Optional[int] = None,
        timeout: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Get data from a specific sensor.

        Args:
            sensor_name: Name of sensor to query
            limit: If specified, get last N readings (ignores time range)
            start: Start timestamp for range query
            end: End timestamp for range query
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
