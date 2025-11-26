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


def test_bme280():
    """Test BME280 sensor detection."""
    print("\n=== Testing BME280 ===")
    try:
        import board
        import busio
        import adafruit_bme280.basic as adafruit_bme280
        print("✓ BME280 libraries imported successfully")

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
        print(f"✗ BME280 libraries not available: {e}")
        print("  Install with: pip install adafruit-circuitpython-bme280")
        return False


def check_i2c_devices():
    """Check what devices are visible on the I2C bus."""
    print("\n=== I2C Bus Scan ===")
    try:
        import subprocess
        result = subprocess.run(['i2cdetect', '-y', '1'], capture_output=True, text=True)
        print(result.stdout)
        print("\nExpected addresses:")
        print("  0x38 - AHT20")
        print("  0x76 or 0x77 - BME280")
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

    # Test each sensor
    results = {
        "DHT22": test_dht22(),
        "AHT20": test_aht20(),
        "BME280": test_bme280(),
    }

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    for sensor, detected in results.items():
        status = "✓ DETECTED" if detected else "✗ NOT FOUND"
        print(f"{sensor:10s} {status}")

    print("\n" + "=" * 60)
    print("Recommendation")
    print("=" * 60)

    detected_sensors = [name for name, found in results.items() if found]
    if detected_sensors:
        print(f"Set sensor_type in config.ini to: {detected_sensors[0]}")
        print(f"  echo 'sensor_type = {detected_sensors[0]}' >> config.ini")
    else:
        print("No sensors detected. Check wiring and library installation.")
        print("\nInstall all sensor libraries with:")
        print("  uv sync --extra pi-hardware")
