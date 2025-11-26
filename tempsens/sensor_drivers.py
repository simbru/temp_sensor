"""
Unified sensor driver interface supporting multiple temperature/humidity sensors.

Supported sensors:
- DHT22: Digital humidity and temperature sensor (GPIO)
- AHT20: I2C temperature and humidity sensor
- BME280: I2C temperature, humidity, and pressure sensor (Enviro module)

Usage:
    sensor = detect_sensor()  # Auto-detect
    sensor = get_sensor("DHT22")  # Manual specification
    temp, humidity = sensor.read()
"""

import time
from typing import Optional, Tuple, Protocol


class SensorInterface(Protocol):
    """Protocol defining the interface all sensors must implement."""

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Read temperature and humidity from sensor.

        Returns:
            Tuple of (temperature_celsius, humidity_percent)
            Returns (None, None) on hardware failure

        Raises:
            RuntimeError: On transient read errors (checksum, timeout)
        """
        ...

    @property
    def name(self) -> str:
        """Return sensor type name (e.g., 'DHT22', 'AHT20')."""
        ...


class DHT22Sensor:
    """Driver for DHT22 digital temperature/humidity sensor."""

    def __init__(self, pin=None):
        """
        Initialize DHT22 sensor.

        Args:
            pin: GPIO pin (defaults to board.D4)
        """
        try:
            import adafruit_dht
            import board
        except ImportError:
            raise ImportError("adafruit_dht not available - install with 'uv sync --extra pi'")

        self.pin = pin if pin is not None else board.D4
        self._sensor = None  # Lazy initialization to avoid file descriptor leaks

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity from DHT22 sensor."""
        import adafruit_dht

        # Create fresh sensor instance for each read to avoid file descriptor issues
        sensor = adafruit_dht.DHT22(self.pin, use_pulseio=False)
        try:
            temperature = sensor.temperature
            humidity = sensor.humidity
            return temperature, humidity
        finally:
            # Clean up to avoid file descriptor leaks
            sensor.exit()

    @property
    def name(self) -> str:
        return "DHT22"

    @staticmethod
    def detect() -> bool:
        """Check if DHT22 hardware libraries are available."""
        try:
            import adafruit_dht
            import board
            return True
        except ImportError:
            return False


class AHT20Sensor:
    """Driver for AHT20 I2C temperature/humidity sensor."""

    def __init__(self, i2c=None):
        """
        Initialize AHT20 sensor.

        Args:
            i2c: I2C bus object (auto-created if None)
        """
        try:
            import board
            import adafruit_ahtx0
        except ImportError:
            raise ImportError("adafruit_ahtx0 not available - install with 'pip install adafruit-circuitpython-ahtx0'")

        if i2c is None:
            import busio
            i2c = busio.I2C(board.SCL, board.SDA)

        self._sensor = adafruit_ahtx0.AHTx0(i2c)

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity from AHT20 sensor."""
        try:
            temperature = self._sensor.temperature
            humidity = self._sensor.relative_humidity
            return temperature, humidity
        except Exception as e:
            # AHT20 can raise various I2C errors
            raise RuntimeError(f"AHT20 read error: {e}")

    @property
    def name(self) -> str:
        return "AHT20"

    @staticmethod
    def detect() -> bool:
        """Attempt to detect AHT20 on I2C bus."""
        try:
            import board
            import busio
            import adafruit_ahtx0

            i2c = busio.I2C(board.SCL, board.SDA)
            # Try to initialize - if sensor exists, this succeeds
            try:
                sensor = adafruit_ahtx0.AHTx0(i2c)
                # Attempt a read to confirm it's working
                _ = sensor.temperature
                return True
            finally:
                # Clean up I2C resources
                i2c.deinit()
        except Exception:
            return False


class BME280Sensor:
    """Driver for BME280 I2C temperature/humidity/pressure sensor (Enviro module)."""

    def __init__(self, i2c=None):
        """
        Initialize BME280 sensor.

        Args:
            i2c: I2C bus object (auto-created if None)
        """
        try:
            import board
            import adafruit_bme280.basic as adafruit_bme280
        except ImportError:
            raise ImportError("adafruit_bme280 not available - install with 'pip install adafruit-circuitpython-bme280'")

        if i2c is None:
            import busio
            i2c = busio.I2C(board.SCL, board.SDA)

        self._sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c)

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity from BME280 sensor (ignore pressure for now)."""
        try:
            temperature = self._sensor.temperature
            humidity = self._sensor.relative_humidity
            return temperature, humidity
        except Exception as e:
            raise RuntimeError(f"BME280 read error: {e}")

    @property
    def name(self) -> str:
        return "BME280"

    @staticmethod
    def detect() -> bool:
        """Attempt to detect BME280 on I2C bus."""
        try:
            import board
            import busio
            import adafruit_bme280.basic as adafruit_bme280

            i2c = busio.I2C(board.SCL, board.SDA)
            try:
                sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c)
                # Attempt a read to confirm it's working
                _ = sensor.temperature
                return True
            finally:
                # Clean up I2C resources
                i2c.deinit()
        except Exception:
            return False


class SimulatedSensor:
    """Simulated sensor for testing without hardware."""

    def __init__(self, temp_baseline=20, temp_var=5, hum_baseline=50, hum_var=5):
        """
        Initialize simulated sensor.

        Args:
            temp_baseline: Base temperature in Celsius
            temp_var: Temperature variation range
            hum_baseline: Base humidity percentage
            hum_var: Humidity variation range
        """
        import numpy as np
        self.temp_baseline = temp_baseline
        self.temp_var = temp_var
        self.hum_baseline = hum_baseline
        self.hum_var = hum_var

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Generate simulated sensor readings."""
        import numpy as np

        temp = self.temp_baseline + np.random.randint(self.temp_var)
        hum = self.hum_baseline + np.random.randint(self.hum_var)

        # Simulate occasional sensor failures (20% chance)
        if np.random.randint(5) == 1:
            raise RuntimeError("Simulated sensor read failure (checksum error)")

        return float(temp), float(hum)

    @property
    def name(self) -> str:
        return "SIMULATED"

    @staticmethod
    def detect() -> bool:
        """Simulated sensor is always available."""
        return True


# Sensor registry for auto-detection (order matters!)
# I2C sensors first (fast, reliable hardware detection via I2C bus)
# DHT22 last (can only check if libraries exist, not if hardware is connected)
SENSOR_REGISTRY = [
    ("AHT20", AHT20Sensor),
    ("BME280", BME280Sensor),
    ("DHT22", DHT22Sensor),
]


def detect_sensor() -> SensorInterface:
    """
    Auto-detect available temperature/humidity sensor.

    Tries sensors in order: DHT22, AHT20, BME280.
    Falls back to simulated sensor if no hardware found.

    Returns:
        Initialized sensor object implementing SensorInterface
    """
    print("Auto-detecting sensors...")

    for sensor_name, sensor_class in SENSOR_REGISTRY:
        print(f"  Checking for {sensor_name}...", end=" ")
        try:
            if sensor_class.detect():
                print("Found!")
                sensor = sensor_class()
                return sensor
        except Exception as e:
            pass
        print("Not found")

    print("  No hardware sensors detected, using simulated sensor")
    return SimulatedSensor()


def get_sensor(sensor_type: str) -> SensorInterface:
    """
    Get sensor by explicit type specification.

    Args:
        sensor_type: Sensor type string (case-insensitive):
                    "DHT22", "AHT20", "BME280", "AUTO", or "SIMULATED"

    Returns:
        Initialized sensor object implementing SensorInterface

    Raises:
        ValueError: If sensor_type is unknown
        ImportError: If required libraries not installed
        RuntimeError: If sensor hardware not detected
    """
    sensor_type = sensor_type.upper().strip()

    if sensor_type == "AUTO":
        return detect_sensor()

    if sensor_type == "SIMULATED":
        return SimulatedSensor()

    # Find sensor in registry
    for name, sensor_class in SENSOR_REGISTRY:
        if name == sensor_type:
            print(f"Initializing {sensor_type} sensor...")
            try:
                sensor = sensor_class()
                print(f"{sensor_type} initialized successfully")
                return sensor
            except ImportError as e:
                raise ImportError(f"Cannot initialize {sensor_type}: {e}")
            except Exception as e:
                raise RuntimeError(f"Failed to initialize {sensor_type}: {e}")

    raise ValueError(
        f"Unknown sensor type: {sensor_type}. "
        f"Valid options: AUTO, SIMULATED, {', '.join(name for name, _ in SENSOR_REGISTRY)}"
    )


# Convenience function for testing
if __name__ == "__main__":
    print("=== Sensor Detection Test ===\n")

    # Test auto-detection
    sensor = detect_sensor()
    print(f"\nActive sensor: {sensor.name}")

    # Test reading
    print("\nTesting sensor read...")
    try:
        temp, hum = sensor.read()
        print(f"Temperature: {temp:.1f}C")
        print(f"Humidity: {hum:.1f}%")
    except Exception as e:
        print(f"Read failed: {e}")
