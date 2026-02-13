#!/usr/bin/env python3
"""
Debug script to diagnose sensor detection issues.

Run this on the Raspberry Pi to see detailed information about
which sensors can be detected and why detection might be failing.
"""


def test_dht22():
    """Test DHT22 sensor detection."""
    print("\n=== Testing DHT22 ===")
    try:
        import adafruit_dht
        import board
        print("✓ DHT22 libraries imported successfully")

        try:
            sensor = adafruit_dht.DHT22(board.D4, use_pulseio=False)
            print("✓ DHT22 sensor object created")

            try:
                temp = sensor.temperature
                hum = sensor.relative_humidity
                print(f"✓ DHT22 read successful: {temp}°C, {hum}%")
                return True
            except RuntimeError as e:
                print(f"⚠ DHT22 read failed (checksum/timeout): {e}")
                print("  This is normal for DHT22 - sensor exists but read failed")
                return True
            finally:
                sensor.exit()
        except Exception as e:
            print(f"✗ DHT22 initialization failed: {e}")
            return False

    except ImportError as e:
        print(f"✗ DHT22 libraries not available: {e}")
        print("  Install with: uv sync --extra pi-hardware")
        return False


def test_aht20():
    """Test AHT20 sensor detection."""
    print("\n=== Testing AHT20 ===")
    try:
        import board
        import busio
        import adafruit_ahtx0
        print("✓ AHT20 libraries imported successfully")

        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            print("✓ I2C bus initialized")

            try:
                sensor = adafruit_ahtx0.AHTx0(i2c)
                print("✓ AHT20 sensor detected on I2C bus (address 0x38)")

                try:
                    temp = sensor.temperature
                    hum = sensor.relative_humidity
                    print(f"✓ AHT20 read successful: {temp}°C, {hum}%")
                    return True
                except Exception as e:
                    print(f"✗ AHT20 read failed: {e}")
                    return False
            finally:
                i2c.deinit()
        except ValueError as e:
            print(f"✗ AHT20 not found on I2C bus: {e}")
            print("  Check wiring: SDA→GPIO2, SCL→GPIO3")
            return False
        except Exception as e:
            print(f"✗ AHT20 initialization failed: {e}")
            return False

    except ImportError as e:
        print(f"✗ AHT20 libraries not available: {e}")
        print("  Install with: pip install adafruit-circuitpython-ahtx0")
        return False


def test_bme280_adafruit():
    """Test BME280 sensor detection via Adafruit CircuitPython library."""
    print("\n=== Testing BME280 (Adafruit) ===")
    try:
        import board
        import busio
        import adafruit_bme280.basic as adafruit_bme280
        print("✓ BME280 (Adafruit) libraries imported successfully")

        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            print("✓ I2C bus initialized")

            try:
                sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c)
                print("✓ BME280 sensor detected on I2C bus (address 0x76 or 0x77)")

                try:
                    temp = sensor.temperature
                    hum = sensor.relative_humidity
                    pressure = sensor.pressure
                    print(f"✓ BME280 read successful: {temp}°C, {hum}%, {pressure}hPa")
                    return True
                except Exception as e:
                    print(f"✗ BME280 read failed: {e}")
                    return False
            finally:
                i2c.deinit()
        except ValueError as e:
            print(f"✗ BME280 not found on I2C bus: {e}")
            print("  Check wiring: SDA→GPIO2, SCL→GPIO3")
            return False
        except Exception as e:
            print(f"✗ BME280 initialization failed: {e}")
            return False

    except ImportError as e:
        print(f"✗ BME280 (Adafruit) libraries not available: {e}")
        print("  Install with: pip install adafruit-circuitpython-bme280")
        return False


def test_bme280_pimoroni():
    """Test BME280 sensor detection via Pimoroni library (smbus2)."""
    print("\n=== Testing BME280 (Pimoroni) ===")
    try:
        from smbus2 import SMBus
        from bme280 import BME280
        print("✓ Pimoroni BME280 libraries imported successfully (smbus2 + bme280)")

        try:
            bus = SMBus(1)
            sensor = BME280(i2c_dev=bus)
            print("✓ BME280 sensor initialized on I2C bus 1 (address 0x76)")

            try:
                temp = sensor.get_temperature()
                hum = sensor.get_humidity()
                pressure = sensor.get_pressure()
                print(f"✓ BME280 read successful: {temp:.1f}°C, {hum:.1f}%, {pressure:.1f}hPa")
                return True
            except Exception as e:
                print(f"✗ BME280 read failed: {e}")
                return False
        except Exception as e:
            print(f"✗ BME280 initialization failed: {e}")
            print("  Check wiring: SDA→GPIO2, SCL→GPIO3")
            return False

    except ImportError as e:
        print(f"✗ Pimoroni BME280 libraries not available: {e}")
        print("  Install with: pip install pimoroni-bme280 smbus2")
        return False


def test_ltr559():
    """Test LTR-559 light/proximity sensor detection."""
    print("\n=== Testing LTR-559 ===")
    try:
        from ltr559 import LTR559
        print("✓ LTR-559 library imported successfully")

        try:
            sensor = LTR559()
            print("✓ LTR-559 sensor initialized (address 0x23)")

            try:
                lux = sensor.get_lux()
                prox = sensor.get_proximity()
                print(f"✓ LTR-559 read successful: {lux:.1f} lux, proximity={prox}")
                return True
            except Exception as e:
                print(f"✗ LTR-559 read failed: {e}")
                return False
        except Exception as e:
            print(f"✗ LTR-559 initialization failed: {e}")
            print("  Check I2C wiring: SDA→GPIO2, SCL→GPIO3")
            return False

    except ImportError as e:
        print(f"✗ LTR-559 library not available: {e}")
        print("  Install with: pip install ltr559")
        return False


def test_enviroplus():
    """Test Enviro+ board detection (BME280 + LTR559 + gas via enviroplus package)."""
    print("\n=== Testing Enviro+ ===")
    try:
        from enviroplus import gas
        print("✓ Enviro+ package imported successfully (has gas/noise sensor)")
        has_enviroplus_pkg = True
    except ImportError:
        print("✗ Enviro+ package not available (no gas/noise sensor support)")
        has_enviroplus_pkg = False

    # Enviro+ requires both BME280 and LTR559 via Pimoroni libs
    try:
        from smbus2 import SMBus
        from bme280 import BME280
        from ltr559 import LTR559

        bus = SMBus(1)
        bme = BME280(i2c_dev=bus)
        ltr = LTR559()

        temp = bme.get_temperature()
        lux = ltr.get_lux()
        print(f"✓ Enviro+ sensors working: {temp:.1f}°C, {lux:.1f} lux")
        return True
    except ImportError as e:
        print(f"✗ Enviro+ sensor libraries not available: {e}")
        print("  Install with: uv sync --extra enviroplus")
        return False
    except Exception as e:
        print(f"✗ Enviro+ sensor initialization failed: {e}")
        return False


def check_i2c_devices():
    """Check what devices are visible on the I2C bus."""
    print("\n=== I2C Bus Scan ===")
    try:
        import subprocess
        result = subprocess.run(['i2cdetect', '-y', '1'], capture_output=True, text=True)
        print(result.stdout)
        print("Known addresses:")
        print("  0x23 - LTR-559 (light/proximity)")
        print("  0x38 - AHT20")
        print("  0x76 - BME280")
        print("  0x6a - LSM6DS3 (accelerometer/gyroscope)")
    except FileNotFoundError:
        print("✗ i2cdetect command not found")
        print("  Install with: sudo apt-get install i2c-tools")
    except Exception as e:
        print(f"✗ I2C scan failed: {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("Sensor Detection Debug Tool")
    print("=" * 60)

    # Check I2C bus first
    check_i2c_devices()

    # Test each sensor type
    results = {}
    results["DHT22"] = test_dht22()
    results["AHT20"] = test_aht20()
    results["BME280 (Adafruit)"] = test_bme280_adafruit()
    results["BME280 (Pimoroni)"] = test_bme280_pimoroni()
    results["LTR-559"] = test_ltr559()
    results["Enviro+"] = test_enviroplus()

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for sensor, detected in results.items():
        status = "✓ DETECTED" if detected else "✗ NOT FOUND"
        print(f"{sensor:20s} {status}")

    print("\n" + "=" * 60)
    print("Recommendation")
    print("=" * 60)

    pimoroni_bme = results.get("BME280 (Pimoroni)", False)
    ltr559 = results.get("LTR-559", False)
    enviroplus = results.get("Enviro+", False)
    adafruit_bme = results.get("BME280 (Adafruit)", False)
    aht20 = results.get("AHT20", False)
    dht22 = results.get("DHT22", False)

    if enviroplus:
        print("Set sensor_type = ENVIROPLUS in config.ini")
        print("  Uses Pimoroni libraries (BME280 + LTR559 + gas)")
    elif pimoroni_bme and ltr559:
        print("BME280 + LTR-559 detected via Pimoroni libraries.")
        print("  This is compatible with Pimoroni Multi-Sensor Stick")
        print("  and similar boards using Pimoroni Python libraries.")
        print("Set sensor_type = ENVIROPLUS in config.ini")
        print("  (uses the same Pimoroni BME280/LTR559 driver)")
    elif pimoroni_bme:
        print("BME280 detected via Pimoroni library (but no LTR-559).")
        print("Set sensor_type = ENVIROPLUS in config.ini")
    elif adafruit_bme:
        print("Set sensor_type = BME280 in config.ini")
        print("  (using Adafruit CircuitPython library)")
    elif aht20:
        print("Set sensor_type = AHT20 in config.ini")
    elif dht22:
        print("Set sensor_type = DHT22 in config.ini")
    else:
        print("No sensors detected. Check wiring and library installation.")
        print("\nInstall options:")
        print("  Adafruit sensors:  uv sync --extra pi-hardware")
        print("  Pimoroni sensors:  pip install pimoroni-bme280 ltr559 smbus2")
        print("  Enviro+ board:     uv sync --extra enviroplus")
