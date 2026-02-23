# Raspberry Pi Deployment Guide

Step-by-step setup for deploying a temperature sensor client on Raspberry Pi.

Tested on Pi Zero 2W with Pi OS Lite (Bookworm/Trixie).

## Prerequisites

- Raspberry Pi with Pi OS installed (Lite or Desktop)
- A supported sensor connected (see wiring in [README.md](README.md#hardware-pi-only))
- SSH access to the Pi
- Network connectivity (WiFi or Ethernet)

## Step 1: Install system packages

Pi OS Lite doesn't include `git` or Python dev headers. Install them:

```bash
sudo apt-get update
sudo apt-get install -y python3-dev git
```

**DHT22 only** — also install the GPIO daemon library:
```bash
sudo apt-get install -y libgpiod2
```

## Step 2: Clone the repository

```bash
cd ~
git clone https://github.com/simbru/temp_sensor temp_sensor
cd temp_sensor
```

## Step 3: Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

Verify it works:
```bash
uv --version
```

## Step 4: Enable I2C (if using an I2C sensor)

**Skip this step if using DHT22.**

Required for: AHT20, BME280, Sensor Stick, Enviro+.

```bash
sudo raspi-config
```

Navigate to: **Interface Options** > **I2C** > **Enable** > **Finish**

Verify I2C is enabled:
```bash
ls /dev/i2c-1
```

## Step 5: Install Python dependencies

Choose the command that matches your sensor:

| Sensor | Install command |
|--------|----------------|
| DHT22, AHT20, BME280, Sensor Stick | `uv sync --group client --extra pi-hardware` |
| Enviro+ | `uv sync --group client --extra pi-hardware --extra enviroplus` |

**Important:** Always include `--group client` — it provides FastAPI and uvicorn for the API server.

## Step 6: Configure

```bash
cp config.ini.example config.ini
nano config.ini
```

Set at minimum:
```ini
device_name = Room 307          # Unique name for this sensor
sensor_type = AUTO              # Or: DHT22, AHT20, BME280, SENSOR_STICK, ENVIROPLUS
loginterval_s = 10              # Seconds between readings
```

Save and exit (`Ctrl+X`, `Y`, `Enter`).

## Step 7: Test manually

```bash
uv run python run_client.py
```

You should see sensor readings like:
```
[2026-02-18 16:16:26] 23.8°C  28.2%
```

Press `Ctrl+C` to stop. If readings appear, the sensor is working.

## Step 8: Install as a service

This sets up auto-start on boot with automatic restart on failure:

```bash
cd ~/temp_sensor
bash install_service.sh
```

The script auto-detects your username and paths, then asks to enable and start the service.

Verify it's running:
```bash
sudo systemctl status tempsens.service
sudo journalctl -u tempsens.service -f    # Live logs
```

## Step 9: Connect to server

On the lab server, add this Pi to `server/config_server.ini`:

```ini
[SENSORS]
Room_307 = http://<PI_TAILSCALE_IP>:5000, 60
```

Then restart the server dashboard. See [DEPLOY_SERVER.md](DEPLOY_SERVER.md) or [server/SERVICE.md](server/SERVICE.md).

---

## Updating

```bash
cd ~/temp_sensor
git pull
uv sync --group client --extra pi-hardware   # Match your original install command
sudo systemctl restart tempsens.service
```

## Service management

```bash
sudo systemctl status tempsens.service       # Check status
sudo systemctl restart tempsens.service      # Restart (e.g. after config change)
sudo systemctl stop tempsens.service         # Stop
sudo journalctl -u tempsens.service -f       # Live logs
sudo journalctl -u tempsens.service -n 50    # Last 50 lines
```

## Troubleshooting

### "No module named 'uvicorn'" or missing FastAPI
You installed sensor extras without `--group client`. Fix:
```bash
uv sync --group client --extra pi-hardware   # Adjust extras for your sensor
```

### "No such file or directory: '/dev/i2c-1'"
I2C is not enabled. Run `sudo raspi-config` > Interface Options > I2C > Enable, then retry.

### Sensor returns None / hardware failure
- Check wiring matches your sensor type (see [README.md](README.md#hardware-pi-only))
- For I2C sensors, verify detection: `i2cdetect -y 1`
  - AHT20 → address `0x38`
  - BME280 → address `0x76`
- DHT22 checksum errors are normal (~20% failure rate) — the code retries automatically

### Service won't start
```bash
sudo journalctl -u tempsens.service -n 50    # Check error details
```

Common causes: missing config.ini, wrong sensor_type, I2C not enabled, missing dependencies.

### API not reachable from server
```bash
# Test locally on Pi
curl http://localhost:5000/status

# Test from server
curl http://<PI_IP>:5000/status
```
