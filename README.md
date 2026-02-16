# Temperature Monitoring System

A distributed temperature and humidity monitoring system for Raspberry Pi with multiple sensor support (DHT22, AHT20, BME280, Enviro+, Pimoroni Sensor Stick).

**Features:**
- Multi-sensor dashboard with real-time updates
- CSV export with timestamps
- Local backup on each Pi (survives network outages)
- Cross-subnet access via standard HTTP (port 80)

---

## Quick Start

### 1. Server Setup (Windows)

**What you need:** Windows computer on campus network

```powershell
# Clone and install
git clone https://github.com/simbru/temp_sensor temp_sensor
cd temp_sensor
irm https://astral.sh/uv/install.ps1 | iex
uv sync --group server

# Configure sensors
cp server/config_server.ini.example server/config_server.ini
# Edit server/config_server.ini and add your Pi sensors:
# Room_307 = http://<PI_IP>:5000

# One-time port setup (PowerShell as Admin)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=127.0.0.1
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any

# Run dashboard (replace <SERVER_IP> with your server's campus IP)
uv run python -m bokeh serve server/bokeh_app --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=<SERVER_IP>:80
```

**Access:** `http://<SERVER_IP>/bokeh_app` from any campus computer

**For production use**, the dashboard should run as a Windows service (auto-start on boot, auto-restart on crash). See `server/SERVICE.md` for WinSW setup.

### 2. Pi Client Setup (Raspberry Pi)

**What you need:** Raspberry Pi with a supported temperature/humidity sensor:
- **DHT22** wired to GPIO4 (digital sensor)
- **AHT20** connected via I2C (more accurate, less prone to read failures)
- **BME280** connected via I2C (temp, humidity, pressure)
- **Pimoroni Sensor Stick** via I2C cable (BME280 + LTR559 light sensor, cable-mounted away from CPU)
- **Enviro+** board (temp, humidity, pressure, light, noise — with CPU heat compensation)

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install python3-dev git

# Clone and install
cd ~
git clone https://github.com/simbru/temp_sensor temp_sensor
cd temp_sensor
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync --group client --extra pi-hardware

# Configure
cp config.ini.example config.ini
nano config.ini  # Set device_name = "Room 307"
                 # Set sensor_type = "AUTO" (or "DHT22", "AHT20", "BME280", "SENSOR_STICK", "ENVIROPLUS")

# Run client
uv run python run_client.py
```

**Note:** Client API runs on port 5000. Add this Pi's IP to server config.

---

## Managing Configuration Files

**Config files are gitignored** to prevent overwriting your local settings:

- `config.ini` (Pi client config)
- `server/config_server.ini` (Server config)

**First-time setup:**
```bash
cp config.ini.example config.ini
cp server/config_server.ini.example server/config_server.ini
# Edit with your settings
```

**After setup:**
```bash
git pull  # Your configs won't be touched!
```

**Best practice:**
- Keep `.example` files as templates in git
- Real configs stay local and gitignored
- Safe to pull/push without losing your settings

---

## Prerequisites

### Hardware (Pi Only)

**Required components:**
- Raspberry Pi with GPIO pins (for DHT22) or I2C support (for AHT20/BME280)
- One of the following sensors:
  - **DHT22** - Digital temperature/humidity sensor
  - **AHT20** - I2C temperature/humidity sensor (more accurate, fewer read failures)
  - **BME280** - I2C temp/humidity/pressure sensor (Adafruit)
  - **Pimoroni Multi-Sensor Stick** - BME280 + LTR559 via I2C cable (temp, humidity, pressure, light)
  - **Enviro+** - Pimoroni board with BME280, LTR559, MEMS mic (temp, humidity, pressure, light, noise)

#### DHT22 Wiring (GPIO)
- **VCC (or +)** → Pin 1 (3.3V power)
- **Data (or OUT)** → Pin 7 (GPIO4)
- **GND (or -)** → Pin 9 (Ground)

![DHT22 Wiring Diagram](https://www.cedarwarman.com/img/blog/2022-03-09_DHT22_RPi0.jpg)

*Image credit: [Cedar Warman](https://www.cedarwarman.com/2022/03/08/raspberry-pi-dht22-sensor.html)*

**Note:** This assumes you're using a DHT22 module (breakout board with built-in pull-up resistor). If using a bare DHT22 sensor, you'll need to add a 10kΩ pull-up resistor between VCC and Data.

#### AHT20/BME280 Wiring (I2C)
- **VCC** → Pin 1 (3.3V power)
- **SDA** → Pin 3 (I2C Data - GPIO2)
- **SCL** → Pin 5 (I2C Clock - GPIO3)
- **GND** → Pin 9 (Ground)

**Advantages of I2C sensors:**
- More accurate readings than DHT22
- Fewer read failures (~99% success vs ~80% for DHT22)
- Multiple sensors can share the same I2C bus
- BME280 includes pressure sensing for future features

#### Sensor Auto-Detection

The system automatically detects which sensor is connected. Detection order: Enviro+ > Sensor Stick > AHT20 > BME280 > DHT22 > Simulated.

To manually specify:

```ini
# config.ini
sensor_type = AUTO           # Auto-detect (default)
# sensor_type = DHT22        # Force DHT22
# sensor_type = AHT20        # Force AHT20
# sensor_type = BME280       # Force BME280
# sensor_type = SENSOR_STICK # Force Pimoroni Multi-Sensor Stick
# sensor_type = ENVIROPLUS   # Force Enviro+
```

### Software

**Required:**
- **Python 3.11+**
- **uv** (package manager) - [Installation guide](#installing-uv)
- **Git**
- **Raspberry Pi only:** `python3-dev` (for compiling hardware libraries)

**Installing uv:**

**Linux/macOS/Raspberry Pi:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env  # Add to PATH
```

**Windows (PowerShell):**
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

**Verify installation:**
```bash
uv --version
```

For more details, see the [official uv documentation](https://docs.astral.sh/uv/).

### Network
- Pi must be reachable from server (campus network or VPN)
- Server must have campus-accessible IP
- Port 5000 on each Pi (API)
- Port 80 on server (dashboard)

---

## Auto-Start Services

### Raspberry Pi (systemd)

Use the automated installer:
```bash
cd ~/temp_sensor
bash install_service.sh
```

The script auto-detects your username and paths, creates `tempsens.service`, and enables auto-start.

**Manual setup:** See [DEPLOY_PI.md](DEPLOY_PI.md) for detailed instructions.

**Service management:**
```bash
sudo systemctl start tempsens.service    # Start
sudo systemctl stop tempsens.service     # Stop
sudo systemctl restart tempsens.service  # Restart
sudo systemctl status tempsens.service   # Status
```

### Windows Server (WinSW Service)

The dashboard runs as a native Windows service via [WinSW](https://github.com/winsw/winsw). This provides auto-start on boot and auto-restart on crash with no console window.

See [server/SERVICE.md](server/SERVICE.md) for full setup, management, and troubleshooting.

**Quick reference:**
```powershell
C:\tools\BokehDashboard.exe status     # Check if running
C:\tools\BokehDashboard.exe restart    # Restart
C:\tools\BokehDashboard.exe stop       # Stop
```

---

## Updating Code (Git Pull)

### On Raspberry Pi

```bash
cd ~/temp_sensor
git pull
uv sync --extra pi-hardware  # Only if dependencies changed
sudo systemctl restart tempsens.service
```

**Check it worked:**
```bash
sudo systemctl status tempsens.service
sudo journalctl -u tempsens.service -f  # View live logs
```

### On Windows Server

```powershell
cd C:\Users\main\temp_sensor
git pull
uv sync --group server    # Only if dependencies changed
C:\tools\BokehDashboard.exe restart
```

**Check it worked:**
```powershell
C:\tools\BokehDashboard.exe status
type logs\BokehDashboard.err.log
```

### Common Issues After Update

**"Module not found" error:**
```bash
# Pi: Reinstall dependencies
cd ~/temp_sensor
uv sync --extra pi-hardware --reinstall
sudo systemctl restart tempsens.service

# Windows: Reinstall dependencies
cd C:\Users\main\temp_sensor
uv sync --group server --reinstall
C:\tools\BokehDashboard.exe restart
```

**"Port already in use" (Windows):**
```powershell
# Kill any lingering Python processes
Get-Process | Where-Object {$_.Name -like "*python*"} | Stop-Process -Force
C:\tools\BokehDashboard.exe start
```

**Service won't start (Pi):**
```bash
# Check for errors
sudo journalctl -u tempsens.service -n 50

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart tempsens.service
```

---

## Dashboard Features

- **Sensor dropdown:** Switch between sensors
- **Status indicator:** 🟢 receiving data · 🟠 connected/waiting · 🟡 syncing backlog · 🔴 offline/error
- **Time window:** Preset buttons (10min - 1 week) or custom
- **Moving average:** Adjustable smoothing
- **CSV export:** Download current sensor's data
- **Backlog resync:** Server automatically streams missing data in chunks via `/data/range`, so even long outages resync fully (no 20k cap)
- **All-data view:** Selecting "All data" fetches the entire SQLite history for that sensor so you can inspect multi-day/month trends without truncation

### Gap Visualization

The dashboard accurately represents missing data to ensure scientific accuracy:

**How it works:**
- **Raw data line:** Connects all successful readings, shows breaks only when client is offline > 60 seconds
- **Moving average:** Smooths noise and interpolates over sensor glitches, breaks at large gaps
- **Timestamp format:** Data stored in milliseconds since epoch for Bokeh plotting
- **Gap detection:** Server-side insertion of NaN markers to break line continuity at outages

**What you'll see:**
1. **Sensor glitches** (< 60s): DHT22 sensors fail ~20% of reads due to checksum errors. Failed reads are NOT written to disk. Dashboard connects remaining successful readings with continuous lines. Small time jumps exist but no visual breaks appear.
2. **Client outages** (> 60s): Network issues or Pi offline. Dashboard inserts proportional NaN markers to create visual breaks in both raw and averaged plots.

**Storage efficiency:**
- Failed sensor reads are skipped (not written to SQLite) to reduce database writes
- Gap detection runs server-side during plot rendering, not on raw data
- This enables fast polling (2s sensor interval, 5s server polling) without performance degradation

**Visual accuracy:**
- Setting moving average window to 1 shows exact sensor behavior with gaps
- Larger windows smooth noise while preserving gap visibility
- Phantom lines connecting across outages are prevented by NaN insertion with interpolated timestamps

---

## API Reference

Each Pi exposes a REST API with v2 (preferred) and v1 (legacy) endpoints.

**GET /poll** (v2, preferred) - Single request returns data + metrics + hardware status + config
```bash
curl "http://<PI_IP>:5000/poll?limit=10"
curl "http://<PI_IP>:5000/poll?since=1700000000000&limit=100"
```

**GET /status** (v1) - Device status and latest reading
```bash
curl http://<PI_IP>:5000/status
```

**GET /data/latest?limit=100** (v1) - Last N readings
```bash
curl "http://<PI_IP>:5000/data/latest?limit=50"
```

**GET /data/range?start=...&end=...** (v1) - Time range query (timestamps in ms since epoch)
```bash
curl "http://<PI_IP>:5000/data/range?start=1700000000000&end=1700100000000"
```

---

## Troubleshooting

### "No hardware sensors detected, using simulated sensor"
**Cause:** System couldn't detect any connected sensors (DHT22, AHT20, or BME280)

**Fix:**
```bash
# Ensure hardware libraries are installed
uv sync --group client --extra pi-hardware

# Check wiring matches sensor type:
# DHT22: VCC→3.3V, GND→GND, Data→GPIO4
# AHT20/BME280: VCC→3.3V, GND→GND, SDA→GPIO2, SCL→GPIO3

# For I2C sensors, verify they appear on the bus
i2cdetect -y 1
# AHT20 should show at address 0x38
# BME280 should show at address 0x76 or 0x77

# Force specific sensor type if auto-detect fails
nano config.ini  # Set sensor_type = "DHT22" or "AHT20" or "BME280"
```

### Dashboard can't reach Pi
**Fix:**
```bash
# On Pi, test API locally
curl http://localhost:5000/status

# From server, test Pi API
curl http://<PI_IP>:5000/status

# Check firewall allows port 5000
```

### Dashboard not accessible
**Fix:**
```powershell
# Verify port forwarding
netsh interface portproxy show all
# Should show: 0.0.0.0:80 → 127.0.0.1:8000

# Check firewall
Get-NetFirewallRule -DisplayName "Temperature Dashboard HTTP"
```

### Server logs flooded with errors
**Symptom:** Rapid error messages like `"Failed to fetch data from Room 307"` filling terminal

**Cause:** Sensor is unreachable (network issue, Pi offline) and server is polling faster than the timeout

**Automatic behavior:**
- Server uses **exponential backoff** when sensors fail
- First error: Retry after `poll_interval` seconds
- Subsequent errors: Doubles delay (2s → 4s → 8s → 16s...) up to 5 minutes max
- Errors logged every 5th failure to reduce spam
- When sensor recovers, normal polling resumes immediately

**Manual intervention (optional):**
```powershell
# Temporarily increase poll interval in server/config_server.ini
[SERVER]
poll_interval_s = 30  # Up from 2s

# Or disable specific sensors by commenting them out
[SENSORS]
# Room_307 = http://100.64.0.5:5000  # Temporarily disabled
```

**Best practice:**
- Set `poll_interval_s` ≥ sensor's `loginterval_s` to avoid overlapping requests
- For slow-changing environments (room temp), use 60-900s poll intervals
- For fast-changing environments (perfusion), use 1-5s poll intervals
- Backoff prevents log flooding during transient network issues

---

## Configuration Reference

### Client (`config.ini`)
```ini
[DEFAULT]
loginterval_s = 10                    # Seconds between sensor reads
device_name = Room 307                # Display name
api_port = 5000                       # API server port
outputfile = templog.db               # Local SQLite database
sensor_type = AUTO                    # AUTO, DHT22, AHT20, BME280, SENSOR_STICK, ENVIROPLUS, or SIMULATED
max_temp_delta_c = 3.0                # Spike filter: reject readings >3°C from last valid
max_humidity_delta_pct = 10.0         # Spike filter: reject readings >10% from last valid
enable_lcd_display = False            # Enable ST7735 LCD on Enviro+ boards

# Sensor calibration (see "Sensor Calibration" section below)
cpu_temp_factor = 2.25
temp_scale = 1.0
temp_calibration_offset = 0.0
humidity_scale = 1.0
humidity_calibration_offset = 0.0
```

### Server (`server/config_server.ini`)
```ini
[SERVER]
poll_interval_s = 30                  # Default poll interval (fallback)
min_gap_threshold_s = 60              # Minimum gap before marking offline
dashboard_port = 8000                 # Bokeh server port
database_path = sensor_data.db        # SQLite cache
dashboard_update_ms = 5000            # Dashboard refresh (ms)
max_plot_points = 50000               # Maximum data points on plot

[SENSORS]
# Format: sensor_name = url, poll_interval_s (optional)
Room_307 = http://<PI_IP_1>:5000, 60        # Poll every 60s
Lab_Bench = http://<PI_IP_2>:5000, 1        # Fast polling: 1s
Incubator = http://<PI_IP_3>:5000           # Uses default: 30s
```

---

## Adding New Sensors

1. **On new Pi:** Follow "Pi Client Setup", set unique `device_name`
2. **On server:** Edit `server/config_server.ini`, add sensor to `[SENSORS]`
3. **Restart:** Server will auto-detect new sensor

---

## Sensor Calibration

All sensors support calibration against a known-good reference. This is especially useful for the **Enviro+** board where CPU heat affects readings.

### Calibration Formula

Readings are processed in two steps:

1. **Sensor-specific compensation** (Enviro+ only): CPU heat correction using `cpu_temp_factor`
2. **Linear calibration**: `final = (compensated * scale) + offset`

### Configuration Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `cpu_temp_factor` | 2.25 | CPU heat compensation factor (Enviro+ only). Lower = more aggressive cooling. Try 1.0-1.5 if readings still too warm. |
| `temp_scale` | 1.0 | Temperature multiplier |
| `temp_calibration_offset` | 0.0 | Temperature offset in °C (negative = cooler readings) |
| `humidity_scale` | 1.0 | Humidity multiplier |
| `humidity_calibration_offset` | 0.0 | Humidity offset in % |
| `pressure_scale` | 1.0 | Pressure multiplier |
| `pressure_calibration_offset` | 0.0 | Pressure offset in hPa |
| `light_scale` | 1.0 | Light multiplier |
| `light_calibration_offset` | 0.0 | Light offset in lux |
| `noise_scale` | 1.0 | Noise multiplier |
| `noise_calibration_offset` | 0.0 | Noise offset |

### Calibration Procedure

1. Place your sensor next to a trusted reference (e.g., lab thermometer, calibrated hygrometer)
2. Wait 15-30 minutes for thermal equilibrium
3. Compare readings and calculate the offset:
   - If sensor reads 25°C but reference shows 23°C, set `temp_calibration_offset = -2.0`
   - If sensor reads 40% RH but reference shows 45%, set `humidity_calibration_offset = 5.0`
4. Restart the client service: `sudo systemctl restart tempsens`

### Enviro+ CPU Heat Notes

The Pimoroni Enviro+ places the BME280 close to the Pi's CPU, causing elevated temperature readings. The compensation uses:

```
corrected_temp = raw_temp - ((cpu_temp - raw_temp) / cpu_temp_factor)
```

Humidity is also compensated using a dewpoint-based correction after temperature adjustment.

**Typical values:**
- `cpu_temp_factor = 2.25` (Pimoroni's default, works well for idle Pi)
- `cpu_temp_factor = 1.2` (more aggressive, may undercorrect humidity)

---

## Development & Testing

### Quick Test Environment (Recommended)

The easiest way to test the system with realistic data:

**PowerShell (Windows):**
```powershell
# Full test with 7 days of historical data
.\dev\test.ps1

# Quick test with 1 day (faster)
.\dev\test.ps1 1

# Keep existing server cache (faster restart)
.\dev\test.ps1 -NoClean

# Custom configuration
.\dev\test.ps1 3 -NoClean -NoBrowser
```

**Bash (Linux/WSL/macOS):**
```bash
# Make executable (first time only)
chmod +x dev/test.sh

# Full test with 7 days of historical data
./dev/test.sh

# Quick test with 1 day
./dev/test.sh --days 1

# Keep existing server cache
./dev/test.sh --no-clean

# Custom configuration
./dev/test.sh --days 3 --no-clean --no-browser
```

**What it does:**
1. Cleans all test databases (client + server) for fresh state
2. Generates historical data (configurable days at 2-second intervals)
3. Starts 3 simulated sensor clients on ports 5001-5003
4. Starts Bokeh dashboard server on port 5006
5. Opens browser automatically to dashboard
6. Shows backfill progress as server pulls all historical data

**Result:** A data-heavy test environment with ~43,000 data points per day per sensor, perfect for validating dashboard features like gap detection, moving averages, and CSV export.

---

### Manual Testing (Advanced)

For more control over the test environment:

**1. Install dependencies:**
```bash
# Local development (no Pi hardware required)
uv sync --group local-dev
```

**2. Start simulated sensors manually:**
```bash
# Starts 3 test sensors with simulated data
uv run python dev/run_test_clients.py

# With historical data generation
uv run python dev/run_test_clients.py --historical-days 7

# Force regeneration of historical data
uv run python dev/run_test_clients.py --historical-days 3 --force-regenerate
```

This creates:
- 3 simulated DHT22 sensors with random temperature/humidity
- API servers at `http://localhost:5001`, `5002`, `5003`
- SQLite databases: `dev/templog_test1.db`, `dev/templog_test2.db`, `dev/templog_test3.db`

**3. Configure server to use test sensors:**

`server/config_server.ini` is already configured for test sensors:
```ini
[SENSORS]
Test_Sensor_1 = http://localhost:5001, 2
Test_Sensor_2 = http://localhost:5002, 2
Test_Sensor_3 = http://localhost:5003, 2
```

**4. Start dashboard manually:**
```bash
# In a separate terminal
uv run bokeh serve server/bokeh_app --show
# Opens at http://localhost:5006/bokeh_app
```

**5. Test features:**
- Switch between sensors in dropdown
- Adjust time windows (10min to 1 week or custom)
- Adjust moving average window
- Download CSV exports
- Observe backfill on first sync
- Verify gap detection and visualization

---

### Dev Folder Contents

- `dev/test.ps1` - Quick test environment launcher (PowerShell)
- `dev/test.sh` - Quick test environment launcher (Bash)
- `dev/run_full_test_environment.py` - Complete test orchestration script
- `dev/run_test_clients.py` - Multi-client test harness with historical data generation
- `dev/config_test1.ini` - Test sensor 1 config (port 5001)
- `dev/config_test2.ini` - Test sensor 2 config (port 5002)
- `dev/config_test3.ini` - Test sensor 3 config (port 5003)

---

## Architecture

```
┌─────────────────┐
│  Raspberry Pi   │  Sensor → sensor.py → templog.db
│                 │  (DHT22/AHT20/BME280/Sensor Stick/Enviro+)
│  FastAPI :5000  │  ←──── Serves data via REST API (v2 /poll)
└────────┬────────┘
         │
         │ Tailscale VPN (100.x.x.x)
         │
         ↓ HTTP polling (configurable per sensor)
┌─────────────────┐
│  Lab Server     │  data_aggregator.py → sensor_data.db
│  (Windows)      │                     ↓
│  Bokeh :8000    │  ←──── Dashboard queries SQLite
│  (WinSW svc)   │
└────────┬────────┘
         │
         │ Port 80 → 8000 (netsh portproxy)
         │
         ↓ WebSocket
┌─────────────────┐
│  User Browser   │  http://<SERVER_IP>/bokeh_app
└─────────────────┘
```

---

## Tailscale VPN Setup (Recommended for Campus Deployment)

### Why Use Tailscale?

**The Problem:** Campus network assigns dynamic IPs to devices via DHCP. When a Raspberry Pi reboots, it may receive a different IP address, causing sensor outages until `server/config_server.ini` is manually updated.

**The Solution:** Use Tailscale VPN to provide stable private IPs (100.x.x.x range) to all sensors and the server. Devices connect securely via Tailscale's mesh network.

**Benefits:**
- **Stable IPs:** Sensors keep the same private IP even after reboots
- **Fast deployment:** Add new sensors without waiting for IT to assign static campus IPs
- **Reduced bureaucracy:** No IT approval needed for each new sensor
- **Automatic reconnection:** Sensors auto-connect to Tailscale on startup
- **Secure:** Encrypted peer-to-peer connections

**User Access:** Dashboard users still access via the server's static campus IP - **no VPN needed for viewing the dashboard**.

---

### Setup Steps

**1. Install Tailscale client on each device:**

**Raspberry Pi:**
```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

**Windows Server:**
- Download installer from [tailscale.com](https://tailscale.com/download)
- Install and run

**2. Connect to the lab's Tailscale network:**

**On Raspberry Pi:**
```bash
# Authenticate with Tailscale (opens browser login)
sudo tailscale up
```

Copy the URL shown and open it in a browser to authenticate. Contact the lab admin to be added to the shared Tailscale network.

**On Windows Server:**
- Open Tailscale app
- Sign in with your account
- Request access to the lab's tailnet from the admin

**3. Verify connectivity:**

```bash
# Check Tailscale status
tailscale status

# Note the assigned private IP (100.x.x.x)
tailscale ip -4

# Test connectivity between server and Pi
ping <tailscale-ip>
```

**4. Update server config to use Tailscale IPs:**

Edit `server/config_server.ini`:
```ini
[SENSORS]
Room_307 = http://100.x.x.x:5000   # Use the Tailscale IP from 'tailscale ip -4'
Lab_Bench = http://100.x.x.x:5000
Incubator = http://100.x.x.x:5000
```

**5. Configure auto-start:**

Tailscale automatically starts on boot (systemd on Pi, Windows service on server). Sensors will reconnect after reboots without manual intervention.

---

### Joining the Lab's Tailscale Network

To add a new sensor to the lab's Tailscale network, contact the lab admin with:
- Device name (e.g., "Room 307 Sensor")
- Tailscale email/account you used to sign up

The admin will approve your device to join the shared tailnet.

---

### Troubleshooting

**Pi can't connect to Tailscale:**
```bash
# Check Tailscale status
sudo tailscale status

# Restart Tailscale
sudo systemctl restart tailscaled

# Re-authenticate if needed
sudo tailscale up
```

**Server can't reach Pi:**
```bash
# Verify Pi is online in tailnet
tailscale status

# Test API directly via Tailscale IP
curl http://100.x.x.x:5000/status
```

---

### Alternative: Direct Campus Network (Not Recommended)

If you choose to use campus IPs directly without Tailscale, be aware:
- **Sensor IPs may change on reboot** → outages until config updated
- **IT approval required** for static IP assignment (bureaucratic delay)
- Only viable if cross-subnet routing is enabled and IPs are made static by IT

For production deployment on campus, **Tailscale is strongly recommended**.

---

## License

MIT License

## Support

- Check [CLAUDE.md](CLAUDE.md) for detailed architecture
- Open GitHub issues for bugs/questions
- See troubleshooting section above

## Credits

Built with [Bokeh](https://bokeh.org/), [FastAPI](https://fastapi.tiangolo.com/), [Adafruit CircuitPython](https://github.com/adafruit/Adafruit_CircuitPython_DHT), [Pimoroni Enviro+](https://github.com/pimoroni/enviroplus-python), and [uv](https://github.com/astral-sh/uv)
