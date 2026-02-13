"""
Unified sensor driver interface supporting multiple temperature/humidity sensors.

Supported sensors:
- DHT22: Digital humidity and temperature sensor (GPIO)
- AHT20: I2C temperature and humidity sensor
- BME280: I2C temperature, humidity, and pressure sensor (Enviro module)
- ENVIROPLUS: Pimoroni Enviro+ board with BME280, light, noise sensors

Usage:
    sensor = detect_sensor()  # Auto-detect
    sensor = get_sensor("DHT22")  # Manual specification
    temp, humidity = sensor.read()

    # For multi-sensor devices like Enviro+
    data = sensor.read_extended()  # Returns dict with all available sensors
"""

import time
import pathlib
from typing import Optional, Tuple, Dict, Any, Protocol


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


class ExtendedSensorInterface(SensorInterface, Protocol):
    """Extended interface for sensors with additional capabilities beyond temp/humidity."""

    def read_extended(self) -> Dict[str, Optional[float]]:
        """
        Read all available sensor data.

        Returns:
            Dictionary with sensor readings. Keys may include:
            - temperature: Temperature in Celsius
            - humidity: Humidity percentage
            - pressure: Atmospheric pressure in hPa
            - light: Light level in lux
            - noise: Noise level in dBA (A-weighted decibels)

        Raises:
            RuntimeError: On transient read errors
        """
        ...

    @property
    def available_sensors(self) -> list[str]:
        """Return list of sensor types this device provides."""
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


class EnviroPlusSensor:
    """Driver for Pimoroni Enviro+ board with multiple environmental sensors."""

    def __init__(self, cpu_temp_compensation=True, compensation_factor=2.25,
                 temp_scale=1.0, temp_offset=0.0,
                 humidity_scale=1.0, humidity_offset=0.0,
                 pressure_scale=1.0, pressure_offset=0.0,
                 light_scale=1.0, light_offset=0.0,
                 noise_scale=1.0, noise_offset=0.0):
        """
        Initialize Enviro+ sensor board.

        Args:
            cpu_temp_compensation: Enable CPU temperature compensation for BME280
            compensation_factor: Factor for CPU heat compensation (default 2.25)
            temp_scale: Linear scaling factor for temperature (default 1.0)
            temp_offset: Offset to add to temperature after scaling (default 0.0)
            humidity_scale: Linear scaling factor for humidity (default 1.0)
            humidity_offset: Offset to add to humidity after scaling (default 0.0)
            pressure_scale: Linear scaling factor for pressure (default 1.0)
            pressure_offset: Offset to add to pressure after scaling (default 0.0)
            light_scale: Linear scaling factor for light/lux (default 1.0)
            light_offset: Offset to add to light after scaling (default 0.0)
            noise_scale: Linear scaling factor for noise (default 1.0)
            noise_offset: Offset to add to noise after scaling (default 0.0)
        """
        try:
            from smbus2 import SMBus
            from bme280 import BME280
            from ltr559 import LTR559
        except ImportError:
            raise ImportError(
                "Enviro+ libraries not available - install with 'pip install enviroplus'"
            )

        # Initialize sensors
        self.bus = SMBus(1)
        self.bme280 = BME280(i2c_dev=self.bus)
        self.ltr559 = LTR559()

        # CPU temperature compensation settings
        self.cpu_temp_compensation = cpu_temp_compensation
        self.compensation_factor = compensation_factor
        self.cpu_temps = []  # Rolling buffer of CPU temperatures
        
        # Linear calibration settings
        self.temp_scale = temp_scale
        self.temp_offset = temp_offset
        self.humidity_scale = humidity_scale
        self.humidity_offset = humidity_offset
        self.pressure_scale = pressure_scale
        self.pressure_offset = pressure_offset
        self.light_scale = light_scale
        self.light_offset = light_offset
        self.noise_scale = noise_scale
        self.noise_offset = noise_offset

        # Try to initialize noise sensor (may not be available on all boards)
        self.has_noise = False
        try:
            from enviroplus import gas
            self.has_noise = True
        except Exception:
            pass

    def _get_cpu_temperature(self) -> Optional[float]:
        """Read CPU temperature from system thermal zone."""
        try:
            temp_file = pathlib.Path("/sys/class/thermal/thermal_zone0/temp")
            if temp_file.exists():
                return float(temp_file.read_text().strip()) / 1000.0
        except Exception:
            pass
        return None

    def _get_compensated_temperature(self, raw_temp: float) -> float:
        """
        Apply CPU temperature compensation to BME280 reading.

        The BME280 sits close to the Raspberry Pi CPU and picks up residual heat.
        This method uses a rolling average of CPU temperatures to estimate and
        compensate for this thermal offset.

        Args:
            raw_temp: Raw temperature reading from BME280

        Returns:
            Compensated temperature in Celsius
        """
        if not self.cpu_temp_compensation:
            return raw_temp

        cpu_temp = self._get_cpu_temperature()
        if cpu_temp is None:
            return raw_temp

        # Maintain rolling buffer of 5 CPU temperature samples
        self.cpu_temps.append(cpu_temp)
        if len(self.cpu_temps) > 5:
            self.cpu_temps.pop(0)

        # Calculate average CPU temp to smooth fluctuations
        avg_cpu_temp = sum(self.cpu_temps) / len(self.cpu_temps)

        # Compensation formula from Pimoroni weather-and-light.py example
        # Corrected temp = raw temp - ((avg CPU temp - raw temp) / factor)
        compensated = raw_temp - ((avg_cpu_temp - raw_temp) / self.compensation_factor)

        return compensated

    def _get_compensated_humidity(self, raw_humidity: float, raw_temp: float, 
                                   comp_temp: float) -> float:
        """
        Compensate humidity reading based on temperature correction.
        
        The BME280's humidity reading is calculated internally using its measured
        temperature. When the sensor runs hot (due to CPU heat), it underestimates
        humidity. This method recalculates humidity using the corrected temperature.
        
        Formula from Pimoroni weather-and-light.py:
        1. Calculate dewpoint from raw readings
        2. Recalculate humidity using corrected temperature and dewpoint
        
        Args:
            raw_humidity: Raw humidity reading from BME280 (%)
            raw_temp: Raw temperature reading from BME280 (°C)
            comp_temp: Compensated temperature (°C)
            
        Returns:
            Compensated humidity percentage (capped at 100%)
        """
        if not self.cpu_temp_compensation:
            return raw_humidity
            
        # Magnus formula approximation for dewpoint
        # dewpoint ≈ T - (100 - RH) / 5
        dewpoint = raw_temp - ((100 - raw_humidity) / 5)
        
        # Recalculate humidity using compensated temperature
        # RH ≈ 100 - 5 * (T_corrected - dewpoint)
        comp_humidity = 100 - (5 * (comp_temp - dewpoint))
        
        # Clamp to valid range
        return max(0, min(100, comp_humidity))

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity (for compatibility with SensorInterface)."""
        try:
            raw_temp = self.bme280.get_temperature()
            raw_humidity = self.bme280.get_humidity()

            # Apply CPU temperature compensation
            temperature = self._get_compensated_temperature(raw_temp)
            
            # Apply humidity compensation based on temperature correction
            humidity = self._get_compensated_humidity(raw_humidity, raw_temp, temperature)
            
            # Apply linear calibration: final = (value * scale) + offset
            temperature = (temperature * self.temp_scale) + self.temp_offset
            humidity = (humidity * self.humidity_scale) + self.humidity_offset
            
            # Clamp humidity to valid range
            humidity = max(0, min(100, humidity))

            return temperature, humidity
        except Exception as e:
            raise RuntimeError(f"Enviro+ BME280 read error: {e}")

    def read_extended(self) -> Dict[str, Optional[float]]:
        """
        Read all available sensors on Enviro+ board.

        Returns:
            Dictionary with keys: temperature, humidity, pressure, light
            Optional: noise (if microphone available)
        """
        try:
            # Read BME280 environmental data
            raw_temp = self.bme280.get_temperature()
            raw_humidity = self.bme280.get_humidity()
            raw_pressure = self.bme280.get_pressure()
            
            # Apply CPU temperature compensation
            temperature = self._get_compensated_temperature(raw_temp)
            
            # Apply humidity compensation based on temperature correction
            humidity = self._get_compensated_humidity(raw_humidity, raw_temp, temperature)
            
            # Apply linear calibration: final = (value * scale) + offset
            temperature = (temperature * self.temp_scale) + self.temp_offset
            humidity = (humidity * self.humidity_scale) + self.humidity_offset
            pressure = (raw_pressure * self.pressure_scale) + self.pressure_offset
            
            # Clamp humidity to valid range
            humidity = max(0, min(100, humidity))

            # Read light sensor and apply calibration
            raw_light = self.ltr559.get_lux()
            light = (raw_light * self.light_scale) + self.light_offset
            light = max(0, light)  # Light can't be negative

            data = {
                "temperature": temperature,
                "humidity": humidity,
                "pressure": pressure,
                "light": light,
            }

            # Add noise if available
            if self.has_noise:
                try:
                    # Note: Actual noise implementation would require additional setup
                    # Placeholder for now - user can implement based on their Enviro+ variant
                    raw_noise = None
                    if raw_noise is not None:
                        noise = (raw_noise * self.noise_scale) + self.noise_offset
                        data["noise"] = max(0, noise)
                    else:
                        data["noise"] = None
                except Exception:
                    data["noise"] = None

            return data

        except Exception as e:
            raise RuntimeError(f"Enviro+ read error: {e}")

    @property
    def name(self) -> str:
        return "ENVIROPLUS"

    @property
    def available_sensors(self) -> list[str]:
        """Return list of available sensor types."""
        sensors = ["temperature", "humidity", "pressure", "light"]
        if self.has_noise:
            sensors.append("noise")
        return sensors

    @staticmethod
    def detect() -> bool:
        """Attempt to detect Enviro+ hardware on I2C bus.

        Requires the enviroplus package to distinguish from other boards
        (like the Multi-Sensor Stick) that use the same BME280 + LTR559 chips.
        """
        try:
            from enviroplus import gas  # noqa: F401 - presence check only
            from smbus2 import SMBus
            from bme280 import BME280
            from ltr559 import LTR559

            # Try to initialize both required sensors
            bus = SMBus(1)
            bme280 = BME280(i2c_dev=bus)
            ltr559 = LTR559()

            # Attempt reads to verify hardware is working
            _ = bme280.get_temperature()
            _ = ltr559.get_lux()

            return True
        except Exception:
            return False


class SensorStickSensor:
    """Driver for Pimoroni Multi-Sensor Stick (BME280 + LTR559 via I2C).

    A standalone I2C sensor board with temperature, humidity, pressure, and light
    sensing. Unlike the Enviro+ HAT, this is cable-connected so no CPU temperature
    compensation is needed.
    """

    def __init__(self, temp_scale=1.0, temp_offset=0.0,
                 humidity_scale=1.0, humidity_offset=0.0,
                 pressure_scale=1.0, pressure_offset=0.0,
                 light_scale=1.0, light_offset=0.0,
                 **kwargs):
        try:
            from smbus2 import SMBus
            from bme280 import BME280
            from ltr559 import LTR559
        except ImportError:
            raise ImportError(
                "Pimoroni sensor libraries not available - install with: "
                "uv sync --extra sensor-stick"
            )

        self.bus = SMBus(1)
        self.bme280 = BME280(i2c_dev=self.bus)
        self.ltr559 = LTR559()

        # Priming read: first BME280 read returns uncalibrated data
        self.bme280.get_temperature()
        self.bme280.get_humidity()
        self.bme280.get_pressure()

        self.temp_scale = temp_scale
        self.temp_offset = temp_offset
        self.humidity_scale = humidity_scale
        self.humidity_offset = humidity_offset
        self.pressure_scale = pressure_scale
        self.pressure_offset = pressure_offset
        self.light_scale = light_scale
        self.light_offset = light_offset

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """Read temperature and humidity from BME280."""
        try:
            temp = self.bme280.get_temperature()
            humidity = self.bme280.get_humidity()

            temp = (temp * self.temp_scale) + self.temp_offset
            humidity = (humidity * self.humidity_scale) + self.humidity_offset
            humidity = max(0, min(100, humidity))

            return temp, humidity
        except Exception as e:
            raise RuntimeError(f"Sensor Stick BME280 read error: {e}")

    def read_extended(self) -> Dict[str, Optional[float]]:
        """Read all sensors: temperature, humidity, pressure, light."""
        try:
            temp = self.bme280.get_temperature()
            humidity = self.bme280.get_humidity()
            pressure = self.bme280.get_pressure()
            lux = self.ltr559.get_lux()

            temp = (temp * self.temp_scale) + self.temp_offset
            humidity = (humidity * self.humidity_scale) + self.humidity_offset
            humidity = max(0, min(100, humidity))
            pressure = (pressure * self.pressure_scale) + self.pressure_offset
            lux = max(0, (lux * self.light_scale) + self.light_offset)

            return {
                "temperature": temp,
                "humidity": humidity,
                "pressure": pressure,
                "light": lux,
            }
        except Exception as e:
            raise RuntimeError(f"Sensor Stick read error: {e}")

    @property
    def name(self) -> str:
        return "SENSOR_STICK"

    @property
    def available_sensors(self) -> list[str]:
        return ["temperature", "humidity", "pressure", "light"]

    @staticmethod
    def detect() -> bool:
        """Check if BME280 + LTR559 are available via Pimoroni libraries."""
        try:
            from smbus2 import SMBus
            from bme280 import BME280
            from ltr559 import LTR559

            bus = SMBus(1)
            bme = BME280(i2c_dev=bus)
            ltr = LTR559()

            _ = bme.get_temperature()
            _ = ltr.get_lux()
            return True
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
# Enviro+ first (most feature-rich)
# Other I2C sensors next (fast, reliable hardware detection via I2C bus)
# DHT22 last (can only check if libraries exist, not if hardware is connected)
SENSOR_REGISTRY = [
    ("ENVIROPLUS", EnviroPlusSensor),
    ("SENSOR_STICK", SensorStickSensor),
    ("AHT20", AHT20Sensor),
    ("BME280", BME280Sensor),
    ("DHT22", DHT22Sensor),
]


def detect_sensor(**kwargs) -> SensorInterface:
    """
    Auto-detect available temperature/humidity sensor.

    Tries sensors in order: ENVIROPLUS, AHT20, BME280, DHT22.
    Falls back to simulated sensor if no hardware found.

    Args:
        **kwargs: Additional arguments passed to sensor constructor (e.g., for EnviroPlusSensor)

    Returns:
        Initialized sensor object implementing SensorInterface
    """
    print("Auto-detecting sensors...", flush=True)

    for sensor_name, sensor_class in SENSOR_REGISTRY:
        print(f"  Checking for {sensor_name}...", end=" ", flush=True)
        try:
            if sensor_class.detect():
                print("Found!", flush=True)
                # Pass kwargs to sensors that support them (like EnviroPlusSensor)
                if sensor_name in ("ENVIROPLUS", "SENSOR_STICK"):
                    sensor = sensor_class(**kwargs)
                else:
                    sensor = sensor_class()
                return sensor
        except Exception as e:
            pass
        print("Not found", flush=True)

    print("  No hardware sensors detected, using simulated sensor", flush=True)
    return SimulatedSensor()


def get_sensor(sensor_type: str, **kwargs) -> SensorInterface:
    """
    Get sensor by explicit type specification.

    Args:
        sensor_type: Sensor type string (case-insensitive):
                    "DHT22", "AHT20", "BME280", "ENVIROPLUS", "AUTO", or "SIMULATED"
        **kwargs: Additional arguments passed to sensor constructor.
                  For ENVIROPLUS, these can include:
                  - compensation_factor: CPU temp compensation factor (default 2.25)
                  - temp_scale: Linear scaling for temperature (default 1.0)
                  - temp_offset: Offset for temperature (default 0.0)
                  - humidity_scale: Linear scaling for humidity (default 1.0)
                  - humidity_offset: Offset for humidity (default 0.0)

    Returns:
        Initialized sensor object implementing SensorInterface

    Raises:
        ValueError: If sensor_type is unknown
        ImportError: If required libraries not installed
        RuntimeError: If sensor hardware not detected
    """
    sensor_type = sensor_type.upper().strip()

    if sensor_type == "AUTO":
        return detect_sensor(**kwargs)

    if sensor_type == "SIMULATED":
        return SimulatedSensor()

    # Find sensor in registry
    for name, sensor_class in SENSOR_REGISTRY:
        if name == sensor_type:
            print(f"Initializing {sensor_type} sensor...")
            try:
                # Pass kwargs to sensors that support them (like EnviroPlusSensor)
                if name in ("ENVIROPLUS", "SENSOR_STICK"):
                    sensor = sensor_class(**kwargs)
                else:
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
