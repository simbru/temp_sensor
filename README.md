# Temperature Monitoring System

A distributed temperature and humidity monitoring system for Raspberry Pi with DHT22 sensors, designed for lab environments with multiple monitoring points.

## Features

- **Distributed Architecture**: Multiple Raspberry Pi sensors + central dashboard server
- **Real-time Monitoring**: Live temperature and humidity data with moving averages
- **Multi-Sensor Dashboard**: Single interface to view all sensors via dropdown selection
- **Local Backup**: Each Pi maintains its own HDF5 log file
- **Network Resilient**: Survives network outages with local storage
- **Secure**: Uses Headscale VPN for private sensor network
- **Data Export**: Download CSV files with device identification
- **Flexible Deployment**: Run standalone on single Pi or distributed with central server

## Deployment Modes

### Mode 1: Distributed (Recommended for Labs)
Multiple Raspberry Pis send data to a central server that hosts a unified dashboard.

**Advantages**: Easy access from campus network, centralized monitoring, no VPN needed for users

### Mode 2: Standalone
Single Raspberry Pi with local dashboard accessed directly.

**Advantages**: Simple setup, no server required, good for single-room monitoring

---

## Quick Start: Distributed Mode

### 1. Raspberry Pi Client Setup

```bash
# Clone and install
git clone <your-repo-url>
cd temp_sensor
uv sync --extra pi

# Configure device
nano config.ini
# Set: device_name = "Room 397"
#      api_port = 5000
#      loginterval_s = 10

# Run client (sensor + API)
uv run python run_client.py
```

### 2. Lab Server Setup

```bash
# Install dependencies
uv sync

# Configure sensors
nano server/config_server.ini
# Add your sensors:
# [SENSORS]
# Room_397 = http://100.64.0.5:5000
# Lab_Bench = http://100.64.0.6:5000

# Run dashboard
uv run bokeh serve --show server/bokeh_app.py --port 5006
```

**Access**: Visit `http://139.184.163.16:5006` from any campus computer

---

## Quick Start: Standalone Mode

```bash
# Clone and install
git clone <your-repo-url>
cd temp_sensor
uv sync --extra pi

# Run standalone dashboard
uv run bokeh serve --show tempsens/dashboard/bokeh_app.py
```

**Access**:
- Local: `http://localhost:5006`
- Remote: `http://<pi-ip-address>:5006`

---

## Dashboard Controls

### Time Window
- Preset buttons: 10 min, 1 hour, 3 hours, 12 hours, 24 hours, 1 week, All data
- Custom intervals: Set specific days/hours/minutes/seconds

### Data Visualization
- **Moving Average**: Adjust smoothing window (default: 10 samples)
- **Raw Data Toggle**: Show/hide raw sensor readings
- **Display Range**: Configure temperature/humidity window widths (persist across sessions)
- **Smart Y-Axis**: Auto-centers on current values with deadbands to reduce jitter

### Multi-Sensor (Server Dashboard)
- **Sensor Dropdown**: Switch between different Pis
- **Status Indicators**: 🟢 Active / 🔴 Offline
- **Device Info**: Shows sensor name, IP, and last update time

---

## API Endpoints (Client)

The Raspberry Pi exposes a REST API for the server to poll:

### Get Status
```bash
GET http://100.64.0.5:5000/status
```
Returns device info, latest reading, uptime

### Get Latest Data
```bash
GET http://100.64.0.5:5000/data/latest?limit=100
```
Returns last N readings (max 10000)

### Get Time Range
```bash
GET http://100.64.0.5:5000/data/range?start=2025-11-14%2010:00:00&end=2025-11-14%2012:00:00
```
Returns readings within time range

### Get Configuration
```bash
GET http://100.64.0.5:5000/config
```
Returns sensor configuration

---

## Configuration Files

### Client (`config.ini`)
```ini
[DEFAULT]
loginterval_s = 10              # Seconds between readings
device_name = Room 397          # Display name for sensor
api_port = 5000                 # FastAPI server port
outputfile = templog.h5         # Local HDF5 backup
temperature_window_c = 10       # Default temperature range
humidity_window_pct = 50        # Default humidity range
```

### Server (`server/config_server.ini`)
```ini
[SERVER]
poll_interval_s = 30            # Seconds between polling all sensors
dashboard_port = 5006           # Bokeh dashboard port
database_path = sensor_data.db  # SQLite cache path

[SENSORS]
Room_397 = http://100.64.0.5:5000
Lab_Bench = http://100.64.0.6:5000
Incubator = http://100.64.0.7:5000
```

---

## Network Architecture

### Headscale VPN Setup

1. **Connect Pis to Headscale**: Each gets a 100.64.0.x IP
2. **Connect Server**: Server also joins Headscale network
3. **Server on Campus Network**: Also has public IP (139.184.163.16)
4. **Users Access Server**: No VPN needed, visit server's public IP

### Communication Flow
```
Raspberry Pi (100.64.0.5:5000) → API Server
         ↓ [Headscale VPN]
Lab Server (100.64.0.4) → Data Aggregator → SQLite
         ↓ [Campus Network]
User Browser (139.184.163.16:5006) → Bokeh Dashboard
```

---

## Development & Testing

### Test Client Locally (WSL/Mac)
```bash
uv sync
uv run python run_client.py
# Uses simulated sensor data
# API at http://localhost:5000
```

### Test Server Dashboard
```bash
# Add test sensors to server/config_server.ini
uv run bokeh serve --show server/bokeh_app.py
# Dashboard at http://localhost:5006
```

---

## Troubleshooting

### Pi: Sensor Not Found
- Check DHT22 wiring (VCC→3.3V, GND→GND, Data→GPIO4)
- Verify installation: `uv sync --extra pi`
- Run as root if needed: `sudo uv run python run_client.py`

### Server: Sensor Offline
- Check Pi is running: `ssh pi@100.64.0.5`
- Test API: `curl http://100.64.0.5:5000/status`
- Check Headscale: `tailscale status` (or `headscale`)
- View polling logs in server terminal

### Dashboard: No Data
- Verify `sensor_data.db` exists and contains data
- Check sensors in `server/config_server.ini`
- Look for errors in Bokeh server output

### Performance Issues
- **Pi Zero 2 W**: Use 10-second intervals (default)
- **Pi 3/4/5**: Can handle 5 seconds or faster
- Reduce moving average window if dashboard feels sluggish

---

## File Structure

```
.
├── tempsens/                   # Client library
│   ├── io_funcs.py            # Data I/O, sensor reading, HDF5
│   ├── sensor.py              # Background logging process
│   ├── api_server.py          # FastAPI REST API
│   └── dashboard/             # Standalone single-sensor dashboard
│       └── bokeh_app.py
├── server/                     # Server components
│   ├── api_client.py          # HTTP client for Pi APIs
│   ├── data_aggregator.py    # Polling & SQLite caching
│   ├── bokeh_app.py          # Multi-sensor dashboard
│   └── config_server.ini     # Server configuration
├── run_client.py              # Client startup script
├── config.ini                 # Client configuration
├── pyproject.toml             # Python dependencies
├── CLAUDE.md                  # Detailed architecture docs
└── README.md                  # This file
```

---

## License

MIT

## Contributing

Issues and pull requests welcome!
