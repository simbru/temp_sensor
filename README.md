# Temperature Monitoring System

A distributed temperature and humidity monitoring system for Raspberry Pi with DHT22 sensors, designed for lab environments with multiple monitoring points.

## System Overview

This system uses a **client-server architecture**:
- **Raspberry Pi Clients**: Each Pi reads DHT22 sensor data, logs locally to HDF5, and exposes a REST API
- **Central Server**: Polls all Pi sensors, caches data in SQLite, and hosts a web dashboard
- **Headscale VPN**: Provides secure private network for Pi-to-Server communication
- **Campus Access**: Dashboard accessible from any campus computer via server's public IP

**Key Features**:
- Multi-sensor dashboard with dropdown selection
- Real-time monitoring with moving averages
- CSV export with timestamps and sensor identification
- Local backup on each Pi (survives network outages)
- Responsive UI that adapts to browser window size

---

## Quick Start

### Server Deployment (Lab Computer)

**What you need**: Lab server with campus network access

```bash
# 1. Clone repository
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor

# 2. Install dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
# Add uv to PATH (choose one):
source $HOME/.local/bin/env              # sh, bash, zsh
# source $HOME/.local/bin/env.fish       # fish
# or restart your shell
uv sync --group server

# 3. Configure sensors (first-time setup)
cp server/config_server.ini.example server/config_server.ini
nano server/config_server.ini
# Add your Pi sensors in [SENSORS] section:
# Room_397 = http://100.64.0.5:5000

# 4. Run dashboard (replace <SERVER_PUBLIC_IP> with your server's IP)
uv run bokeh serve server/bokeh_app.py --port 5006 --allow-websocket-origin=<SERVER_PUBLIC_IP>:5006
```

**Access**: `http://<SERVER_PUBLIC_IP>:5006/bokeh_app` from any campus computer

### Client Deployment (Raspberry Pi)

**What you need**: Raspberry Pi with DHT22 sensor and Headscale VPN

```bash
# 1. Clone repository
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor

# 2. Install dependencies
curl -LsSf https://astral.sh/uv/install.sh | sh
# Add uv to PATH (choose one):
source $HOME/.local/bin/env              # sh, bash, zsh
# source $HOME/.local/bin/env.fish       # fish
# or restart your shell
uv sync --group client --extra pi-hardware

# 3. Configure sensor (first-time setup)
cp config.ini.example config.ini
nano config.ini
# Set: device_name = "Room 397"

# 4. Run client
uv run python run_client.py
```

**Note**: Client exposes API at `http://<pi-headscale-ip>:5000` for server to poll

---

## Prerequisites

### Hardware (Client Only)
- **Raspberry Pi** (any model with GPIO, tested on Pi Zero 2 W)
- **DHT22 sensor** (temperature/humidity)
- Wiring: VCC→3.3V, GND→GND, Data→GPIO4 (pin 7)

### Software (Both)
- **Headscale VPN** configured with all Pis and server connected
- **Python 3.11+** with `uv` package manager
- **Git** for cloning repository
- **SSH access** to Raspberry Pi and server

### Network Setup
- **Client**: Each Pi must have a Headscale IP (e.g., `100.64.0.5`, `100.64.0.6`, etc.)
- **Client**: Port 5000 open on each Pi (for API)
- **Server**: Must have Headscale IP AND campus-accessible public IP
- **Server**: Port 5006 open on server (for dashboard)

---

## Detailed Deployment Guide

### Step 1: Deploy Raspberry Pi Client(s)

SSH into your Raspberry Pi:
```bash
ssh pi@<PI_PUBLIC_IP>
```

#### 1.1 Clone Repository
```bash
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor
```

#### 1.2 Install uv Package Manager
```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh
# Add uv to PATH (choose one):
source $HOME/.local/bin/env              # sh, bash, zsh
# source $HOME/.local/bin/env.fish       # fish
# or restart your shell
```

#### 1.3 Install Dependencies
```bash
# Install with Raspberry Pi hardware support (includes adafruit_dht)
uv sync --group client --extra pi-hardware
```

#### 1.4 Configure Client
Edit `config.ini`:
```bash
nano config.ini
```

Update these key settings:
```ini
[DEFAULT]
loginterval_s = 10                    # Seconds between sensor reads (10 is good for Pi Zero 2 W)
device_name = Room 397                # Human-readable name for this sensor
api_port = 5000                       # Port for REST API (keep as 5000)
outputfile = templog.h5               # Local HDF5 backup file
temperature_window_c = 10             # Default temperature plot range
humidity_window_pct = 50              # Default humidity plot range
```

#### 1.5 Test Client
```bash
# Test run (will create templog.h5 and start logging)
uv run python run_client.py
```

You should see:
- "found dht sensor" (or "no dht sensor, generated data" if testing without hardware)
- Temperature/humidity readings logging to console
- "Uvicorn running on http://0.0.0.0:5000"

Test the API from another machine:
```bash
curl http://100.64.0.5:5000/status
```

Press `Ctrl+C` to stop.

#### 1.6 Create Systemd Service (Run on Boot)
Create service file:
```bash
sudo nano /etc/systemd/system/tempsensor.service
```

Paste this configuration (update paths as needed):
```ini
[Unit]
Description=Temperature Sensor Client
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/temp_sensor
ExecStart=/home/pi/.cargo/bin/uv run python run_client.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tempsensor.service
sudo systemctl start tempsensor.service
```

Check status:
```bash
sudo systemctl status tempsensor.service
```

View logs:
```bash
sudo journalctl -u tempsensor.service -f
```

#### 1.7 Get Headscale IP
Find your Pi's Headscale IP (you'll need this for server config):
```bash
tailscale status
# Or: headscale nodes list (if using headscale command)
# Or: ip addr show tailscale0
```

Note the IP (e.g., `100.64.0.5`) - you'll use this in Step 2.

---

### Step 2: Deploy Server Dashboard

SSH into your server:
```bash
ssh user@<SERVER_PUBLIC_IP>
```

#### 2.1 Clone Repository
```bash
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor
```

#### 2.2 Install uv Package Manager
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# Add uv to PATH (choose one):
source $HOME/.local/bin/env              # sh, bash, zsh
# source $HOME/.local/bin/env.fish       # fish
# or restart your shell
```

#### 2.3 Install Dependencies
```bash
# Install server dependencies (no Pi hardware drivers)
uv sync --group server
```

#### 2.4 Configure Server
Edit `server/config_server.ini`:
```bash
nano server/config_server.ini
```

Update sensor list with your Raspberry Pis:
```ini
[SERVER]
poll_interval_s = 2                   # How often to poll sensors (seconds)
dashboard_port = 5006                 # Bokeh dashboard port
database_path = sensor_data.db        # SQLite cache file
dashboard_update_ms = 5000            # Dashboard refresh interval (milliseconds)

[SENSORS]
# Format: Sensor_Name = http://headscale_ip:port
# Use underscores in names (converted to spaces in UI)
Room_397 = http://100.64.0.5:5000
Lab_Bench = http://100.64.0.6:5000
Incubator = http://100.64.0.7:5000
```

#### 2.5 Test Server Dashboard

**On Windows Server with WSL2:**

```bash
# In WSL, run the dashboard
uv run python -m bokeh serve server/bokeh_app.py --port 8000 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=127.0.0.1:8000 --address 127.0.0.1
```

**Add Windows Firewall Rule (PowerShell as Admin):**
```powershell
New-NetFirewallRule -DisplayName "Temperature Dashboard" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
```

**Access Options:**

1. **Same subnet**: `http://<SERVER_IP>:8000/bokeh_app`
2. **Different subnet (SSH tunnel)**:
   ```bash
   # From your PC
   ssh -L 8000:127.0.0.1:8000 user@<SERVER_IP>

   # Then access: http://localhost:8000/bokeh_app
   ```
3. **Via Headscale VPN**: `http://<SERVER_HEADSCALE_IP>:8000/bokeh_app`

You should see:
- "Loaded N sensor configurations"
- "Performing initial data poll..."
- "Started background polling thread"
- "Bokeh app running at: http://127.0.0.1:8000/bokeh_app"

Press `Ctrl+C` to stop.

#### 2.6 Create Systemd Service (Run on Boot)
Create service file:
```bash
sudo nano /etc/systemd/system/tempserver.service
```

Paste this configuration (update paths and IP as needed):
```ini
[Unit]
Description=Temperature Dashboard Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/temp_sensor
ExecStart=/home/youruser/.local/bin/uv run python -m bokeh serve server/bokeh_app.py --port 8000 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=127.0.0.1:8000 --address 127.0.0.1
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tempserver.service
sudo systemctl start tempserver.service
```

Check status:
```bash
sudo systemctl status tempserver.service
```

View logs:
```bash
sudo journalctl -u tempserver.service -f
```

#### 2.7 Optional: Configure SSH Tunnel Shortcut

For easy access from different subnets, add to your SSH config (`~/.ssh/config`):

```
Host tempserver
    HostName <SERVER_IP>
    User <your-username>
    LocalForward 8000 127.0.0.1:8000
```

Then simply run `ssh tempserver` and access dashboard at `http://localhost:8000/bokeh_app`

---

## Using the Dashboard

### Accessing the Dashboard

**Option 1: Same subnet**
```
http://<SERVER_IP>:8000/bokeh_app
```

**Option 2: Different subnet (SSH tunnel)**
```bash
ssh -L 8000:127.0.0.1:8000 user@<SERVER_IP>
# Then access: http://localhost:8000/bokeh_app
```

**Option 3: Via Headscale VPN**
```
http://<SERVER_HEADSCALE_IP>:8000/bokeh_app
```

### Dashboard Features

#### Sensor Selection
- **Dropdown menu**: Switch between connected sensors
- **Status indicator**: 🟢 Active (receiving data) / 🔴 Offline (no recent data)
- **Device info**: Shows sensor name, Headscale IP, and latest reading time

#### Time Window Controls
**Preset buttons**: Quick time ranges
- 10 min, 1 hour, 3 hours, 12 hours, 24 hours, 1 week, All data

**Custom window**: Enter specific time ranges
- Days, Hours, Minutes, Seconds (no upper limits - enter any value)
- Example: Set 120 minutes or 48 hours

#### Data Visualization
**Moving Average**:
- Adjust smoothing window (default: 10 samples)
- Higher values = smoother line, more lag
- Lower values = more responsive, more noise

**Raw Data Toggle**:
- ON: Show semi-transparent raw readings behind moving average
- OFF: Show only smoothed line

**Display Range**:
- Temperature window: Adjust °C range for temperature plot
- Humidity window: Adjust % range for humidity plot
- Auto-centers on current values with 10-second update limit

#### Data Export
**CSV Download**:
- Click "📥 Download CSV" button
- Downloads current sensor's data with columns: `sensor_name`, `time`, `temperature`, `humidity`
- Filename format: `{SensorName}_{timestamp}.csv`
- Contains all data currently visible in plots

---

## Configuration Reference

### Client Configuration (`config.ini`)

```ini
[DEFAULT]
# Sensor reading interval
loginterval_s = 10              # Seconds between DHT22 reads
                                # Recommended: 10 for Pi Zero 2 W, 5 for Pi 3/4/5

# Device identification
device_name = Temperature Sensor  # Display name in dashboard
api_port = 5000                   # FastAPI server port (keep 5000)

# Local storage
outputfile = templog.h5          # HDF5 backup file path

# Dashboard defaults (for standalone mode)
temperature_window_c = 10        # Temperature plot range (°C)
humidity_window_pct = 50         # Humidity plot range (%)
temperature_range_update_s = 10  # Min seconds between auto y-axis updates
humidity_range_update_s = 10     # Min seconds between auto y-axis updates
```

### Server Configuration (`server/config_server.ini`)

```ini
[SERVER]
# Polling settings
poll_interval_s = 2              # Seconds between polling all sensors
                                 # Recommended: 2-5 seconds

# Dashboard settings
dashboard_port = 5006            # Bokeh server port
dashboard_update_ms = 5000       # Dashboard refresh interval (milliseconds)

# Data storage
database_path = sensor_data.db   # SQLite cache file path

[SENSORS]
# Sensor list (format: Name = http://headscale_ip:port)
# Use underscores in names (displayed as spaces in UI)
Room_397 = http://100.64.0.5:5000
Lab_Bench = http://100.64.0.6:5000
Incubator = http://100.64.0.7:5000
```

---

## API Reference

Each Raspberry Pi exposes a REST API on port 5000:

### GET /status
Returns device status and latest reading.

**Example**:
```bash
curl http://100.64.0.5:5000/status
```

**Response**:
```json
{
  "device_name": "Room 397",
  "device_ip": "100.64.0.5",
  "status": "active",
  "latest_reading": {
    "time": "2025-11-14 15:30:45",
    "temperature": 22.5,
    "humidity": 45.2
  },
  "uptime_seconds": 86400
}
```

### GET /data/latest?limit=100
Returns last N readings (default 100, max 10000).

**Example**:
```bash
curl "http://100.64.0.5:5000/data/latest?limit=50"
```

**Response**:
```json
{
  "device_name": "Room 397",
  "data": {
    "time": ["2025-11-14 15:30:00", "2025-11-14 15:30:10", ...],
    "temperature": [22.5, 22.6, ...],
    "humidity": [45.2, 45.1, ...]
  }
}
```

### GET /data/range?start=...&end=...
Returns readings within time range.

**Example**:
```bash
curl "http://100.64.0.5:5000/data/range?start=2025-11-14%2010:00:00&end=2025-11-14%2012:00:00"
```

### GET /config
Returns sensor configuration.

**Example**:
```bash
curl http://100.64.0.5:5000/config
```

---

## Development & Testing

### Local Testing (Without Hardware)

You can test the entire system on your development machine using simulated sensors:

#### Run Multiple Test Clients
```bash
# Starts 3 simulated sensors on ports 5001, 5002, 5003
uv run python run_test_clients.py
```

This creates:
- `templog_test1.h5`, `templog_test2.h5`, `templog_test3.h5`
- APIs at `localhost:5001`, `localhost:5002`, `localhost:5003`

#### Run Test Server
Edit `server/config_server.ini` to use test sensors:
```ini
[SENSORS]
Test_Sensor_1 = http://localhost:5001
Test_Sensor_2 = http://localhost:5002
Test_Sensor_3 = http://localhost:5003
```

Start server:
```bash
uv run bokeh serve server/bokeh_app.py --show
```

Dashboard opens at `http://localhost:5006/bokeh_app`

### Development Setup (WSL/Mac/Linux)

```bash
# Clone and install (no Pi hardware libraries)
git clone <your-repo-url>
cd temp_sensor
uv sync

# Run single test client
uv run python run_client.py
# Uses simulated data, API at localhost:5000
```

---

## Troubleshooting

### Raspberry Pi Issues

#### "No dht sensor, generated data"
**Cause**: DHT22 sensor not detected or `adafruit_dht` not installed

**Fix**:
```bash
# Ensure Pi hardware libraries installed
uv sync --extra pi

# Check wiring: VCC→3.3V, GND→GND, Data→GPIO4
# Try running with sudo (some GPIO access needs root)
sudo uv run python run_client.py
```

#### API Not Accessible from Server
**Cause**: Firewall blocking port 5000 or Headscale not working

**Fix**:
```bash
# Check Headscale connection
tailscale status

# Test API locally on Pi
curl http://localhost:5000/status

# Check if port is open
sudo netstat -tlnp | grep 5000

# Allow port in firewall (if using ufw)
sudo ufw allow 5000
```

#### Service Won't Start
**Cause**: Path issues or permission problems

**Fix**:
```bash
# Check service logs
sudo journalctl -u tempsensor.service -n 50

# Verify paths in service file
which uv  # Should match ExecStart path
pwd       # Should match WorkingDirectory

# Test command manually
cd /home/pi/temp_sensor
/home/pi/.cargo/bin/uv run python run_client.py
```

### Server Issues

#### "No sensors configured" Error
**Cause**: `server/config_server.ini` missing or malformed

**Fix**:
```bash
# Verify config file exists
cat server/config_server.ini

# Check [SENSORS] section has entries
# Ensure format: Name = http://ip:port
```

#### Dashboard Shows "⚠️ No data available"
**Cause**: Can't reach Pi API or no data in database

**Fix**:
```bash
# Test Pi API from server
curl http://100.64.0.5:5000/status

# Check polling logs
sudo journalctl -u tempserver.service -f

# Verify database exists
ls -lh sensor_data.db

# Check Headscale connection from server
tailscale status
ping 100.64.0.5
```

#### Dashboard Not Accessible from Campus
**Cause**: Firewall blocking port 5006 or wrong origin

**Fix**:
```bash
# Check Bokeh is listening on all interfaces
netstat -tlnp | grep 5006
# Should show 0.0.0.0:5006 or ::5006

# Ensure --allow-websocket-origin matches public IP
# In systemd service or command line:
--allow-websocket-origin=<SERVER_PUBLIC_IP>:5006

# Allow port in firewall
sudo ufw allow 5006
```

#### Sensor Shows 🔴 Offline
**Cause**: Pi API unreachable or returning errors

**Fix**:
```bash
# Check Pi is running
ssh pi@100.64.0.5

# Check Pi service status
sudo systemctl status tempsensor.service

# Test API from server
curl http://100.64.0.5:5000/status

# Check server polling logs
sudo journalctl -u tempserver.service -f
```

### Performance Issues

#### Dashboard Slow or Laggy
**Fix**:
```bash
# Reduce polling frequency in server/config_server.ini
poll_interval_s = 5  # Instead of 2

# Increase dashboard update interval
dashboard_update_ms = 10000  # Instead of 5000

# Reduce moving average window in UI (try 5-10 samples)
```

#### Pi Running Hot or Slow
**Fix**:
```bash
# Increase logging interval in config.ini (Pi Zero 2 W)
loginterval_s = 15  # Instead of 10

# Check CPU temperature
vcgencmd measure_temp

# Reduce CPU load (disable unnecessary services)
```

---

## File Structure

```
temp_sensor/
├── tempsens/                      # Client library package
│   ├── __init__.py               # Package initialization
│   ├── io_funcs.py               # Data I/O, HDF5 operations, sensor reading
│   ├── sensor.py                 # Background logging process
│   └── api_server.py             # FastAPI REST API server
│
├── server/                        # Server components
│   ├── __init__.py               # Package initialization
│   ├── api_client.py             # HTTP client for polling Pi APIs
│   ├── data_aggregator.py        # Polling logic & SQLite caching
│   ├── bokeh_app.py              # Multi-sensor dashboard (main UI)
│   └── config_server.ini         # Server configuration
│
├── run_client.py                  # Client startup script (sensor + API)
├── run_test_clients.py            # Test framework (3 simulated sensors)
│
├── config.ini                     # Client configuration (edit per Pi)
├── config_test1.ini               # Test client 1 config
├── config_test2.ini               # Test client 2 config
├── config_test3.ini               # Test client 3 config
│
├── pyproject.toml                 # Python dependencies (uv)
├── uv.lock                        # Dependency lock file
├── .gitignore                     # Git ignore rules
│
├── CLAUDE.md                      # Detailed architecture documentation
└── README.md                      # This file

# Generated files (gitignored):
├── templog.h5                     # Client HDF5 data (each Pi)
├── templog_test*.h5               # Test client HDF5 files
├── sensor_data.db                 # Server SQLite cache
├── settings.ini                   # UI preferences (per user)
└── .venv/                         # Python virtual environment
```

---

## System Architecture

### Communication Flow
```
┌─────────────────┐
│  Raspberry Pi   │  DHT22 Sensor
│  100.64.0.5     │  ↓
│                 │  sensor.py (reads & logs to templog.h5)
│                 │  ↓
│  FastAPI        │  api_server.py (port 5000)
│  REST API       │
└────────┬────────┘
         │
         │ Headscale VPN (100.64.0.x)
         │
         ↓ HTTP polling every 2s
┌─────────────────┐
│  Lab Server     │
│  100.64.0.4     │  data_aggregator.py
│                 │  ↓
│                 │  SQLite cache (sensor_data.db)
│                 │  ↓
│  Bokeh Server   │  bokeh_app.py (port 5006)
└────────┬────────┘
         │
         │ Campus Network (public IP)
         │
         ↓ WebSocket (wss://)
┌─────────────────┐
│  User Browser   │  http://<SERVER_PUBLIC_IP>:5006/bokeh_app
│  Campus PC      │  (any campus computer)
└─────────────────┘
```

### Data Flow
1. **Pi Sensor** → Reads DHT22 every 10 seconds
2. **Pi Logger** → Appends to local `templog.h5` (backup)
3. **Server Poller** → Fetches latest 100 readings via API every 2 seconds
4. **Server Cache** → Stores in SQLite (`sensor_data.db`)
5. **Dashboard** → Queries SQLite every 5 seconds
6. **User Browser** → Receives updates via WebSocket, renders plots

---

## Adding a New Sensor

### On the New Raspberry Pi:
```bash
# 1. Clone repo and configure
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor
uv sync --extra pi

# 2. Edit config.ini (set unique device_name)
nano config.ini

# 3. Set up systemd service (see Step 1.6 above)
# 4. Get Headscale IP
tailscale status
```

### On the Server:
```bash
# 1. Edit server config
cd ~/temp_sensor
nano server/config_server.ini

# 2. Add new sensor to [SENSORS] section
# New_Sensor_Name = http://100.64.0.8:5000

# 3. Restart server service
sudo systemctl restart tempserver.service

# 4. Verify in dashboard
# New sensor should appear in dropdown within 5 seconds
```

---

## License

MIT License - See LICENSE file for details

## Support

For issues, questions, or contributions:
- Open an issue on GitHub
- Check CLAUDE.md for detailed architecture documentation
- Review troubleshooting section above

---

## Credits

Built with:
- [Bokeh](https://bokeh.org/) - Interactive visualization
- [FastAPI](https://fastapi.tiangolo.com/) - REST API framework
- [Adafruit DHT](https://github.com/adafruit/Adafruit_CircuitPython_DHT) - Sensor library
- [uv](https://github.com/astral-sh/uv) - Fast Python package manager
