# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **distributed** Raspberry Pi temperature and humidity monitoring system with two deployment modes:

### Client Mode (Raspberry Pi)
1. Reads data from a DHT22 sensor (via `adafruit_dht`)
2. Logs measurements to a local HDF5 file (`templog.h5`)
3. Exposes data via FastAPI REST API
4. Can run standalone with local dashboard

### Server Mode (Lab Server)
1. Polls multiple Raspberry Pi clients via their APIs
2. Aggregates data into a SQLite database
3. Hosts a multi-sensor Bokeh dashboard
4. Accessible from campus network

The system supports both **standalone** (single Pi with local dashboard) and **distributed** (multiple Pis + central server) architectures.

## Project Structure

```
tempsens/                    # Main package directory (client-side)
├── __init__.py             # Package initialization
├── io_funcs.py             # Data I/O and sensor reading
├── sensor.py               # Background logging process
├── api_server.py           # FastAPI server for exposing sensor data
└── dashboard/              # Original single-sensor dashboard
    ├── __init__.py
    └── bokeh_app.py        # Bokeh Server entry point (local mode)

server/                      # Server-side components (central dashboard)
├── __init__.py
├── api_client.py           # HTTP client for fetching from Pi APIs
├── data_aggregator.py      # Polls sensors and caches to SQLite
├── bokeh_app.py            # Multi-sensor dashboard
└── config_server.ini       # Server configuration (sensor list)

config.ini                   # Client configuration
run_client.py               # Client startup script (sensor + API)
templog.h5                   # HDF5 data file (generated on Pi)
sensor_data.db              # SQLite cache (generated on server)
```

## Cross-Platform Development

**Critical**: The `adafruit_dht` package only builds on Raspberry Pi hardware. The codebase handles this with:

- **Optional dependency group**: `adafruit_dht` is in `[project.optional-dependencies]` under the `pi` group
- **Graceful import handling**: `tempsens/io_funcs.py:11-19` catches ImportError and sets `sensor_found = False`, falling back to `simulate_tempsens()`

**On development machines (WSL/local)**:
```bash
uv sync                             # Install dependencies (no hardware libs)
uv run python -m tempsens.sensor    # Run with simulated data
```

**On Raspberry Pi**:
```bash
uv sync --extra pi                  # Install dependencies including adafruit_dht
uv run python -m tempsens.sensor    # Run with real sensor
```

## Architecture

### Client Components (Raspberry Pi)

**`tempsens/io_funcs.py`**: Central data I/O module
- Configuration management (via `config.ini`)
- Sensor reading (real DHT22 or simulated data)
- HDF5 file operations with thread-safe locking
- `fetch_log_data_range()`: Time-range filtering for API responses
- Global `sensor_found` flag determines real vs simulated sensor

**`tempsens/sensor.py`**: Background logging process
- Runs continuous temperature/humidity logging loop
- Uses PID file (`tempsens_running.pid`) to prevent duplicate processes
- `run_tempsensor_test()`: Direct execution mode
- `tempsensor_subprocess()`: Spawns subprocess with PID tracking

**`tempsens/api_server.py`**: FastAPI REST API server
- Exposes sensor data to remote dashboard servers
- Endpoints: `/status`, `/data/latest`, `/data/range`, `/config`
- Returns JSON with device name, IP, and sensor readings
- Runs on port specified in `config.ini` (default: 5000)

**`run_client.py`**: Client startup script
- Launches sensor logger and API server as parallel processes
- Handles graceful shutdown via Ctrl+C

### Server Components (Lab Server)

**`server/api_client.py`**: HTTP client for sensor APIs
- `SensorAPIClient`: Single sensor communication
- `MultiSensorClient`: Manages multiple sensors
- Handles connection failures gracefully

**`server/data_aggregator.py`**: Data polling and caching
- Polls configured sensors at regular intervals
- Stores data in SQLite database (one table per sensor)
- Tracks sensor metadata (status, last update, errors)
- Background polling thread with configurable interval
- **Timestamp conversion**: Converts ISO strings from Pi APIs to milliseconds since epoch for Bokeh
  - `get_sensor_data()` returns timestamps as `float` milliseconds, not ISO strings
  - This format is required by Bokeh for efficient datetime plotting
  - Dashboard gap detection must use `pd.to_datetime(timestamps, unit='ms')`

**`server/bokeh_app.py`**: Multi-sensor dashboard
- Dropdown to select which sensor to view
- Temperature and humidity plots with moving averages
- Fetches data from SQLite cache (not directly from Pis)
- Real-time status indicators for each sensor
- **Gap detection and visualization**: Server-side insertion of NaN markers to show data gaps
  - Timestamps from `data_aggregator` are milliseconds since epoch (float), not ISO strings
  - `_insert_gap_markers()` detects gaps > 60 seconds (client offline)
  - Inserts proportional NaN with interpolated millisecond timestamps
  - Prevents moving average from falsely interpolating across large gaps
  - Small sensor glitches (< 60s): Lines connect successful readings with no visual breaks
  - Large client outages (> 60s): Both raw and moving average lines show visual breaks

**`server/config_server.ini`**: Server configuration
- List of sensor names and API URLs (Headscale IPs)
- Polling interval and dashboard settings

### Data Flow

**Distributed Mode (Client + Server)**:
1. **Pi Client**: `sensor.py` → `log_data()` → `write_data_hdf5()` → local HDF5
2. **Pi Client**: `api_server.py` serves data via HTTP endpoints
3. **Server**: `data_aggregator` polls Pi APIs every N seconds
4. **Server**: Aggregator writes to SQLite database
5. **Server**: Dashboard reads from SQLite and updates Bokeh plots

**Standalone Mode (Single Pi)**:
1. `sensor.py` → `log_data()` → `write_data_hdf5()` → local HDF5
2. `dashboard/bokeh_app.py` reads HDF5 directly

### Configuration

**Client `config.ini`**:
- `loginterval_s`: Seconds between sensor reads
- `outputfile`: Path to HDF5 data file
- `device_name`: Display name for this sensor
- `api_port`: Port for FastAPI server (default 5000)
- Temperature/humidity window and range update settings

**Server `config_server.ini`**:
- `[SERVER]`: Poll interval, dashboard port, database path
- `[SENSORS]`: Map of sensor names to API URLs
  ```ini
  Room_397 = http://100.64.0.5:5000
  Lab_Bench = http://100.64.0.6:5000
  ```

**Client `settings.ini`** (optional, git-ignored):
- User interface preferences for local dashboard
- Device name, display window sizes
- Created on demand, persists across restarts

## Common Commands

### Development (WSL/Local)

**Install dependencies**:
```bash
uv sync
```

**Test client API server with simulated data**:
```bash
uv run python run_client.py
# API available at http://localhost:5000
```

**Test server dashboard (requires configured sensors in config_server.ini)**:
```bash
uv run bokeh serve --show server/bokeh_app.py
# Dashboard at http://localhost:5006
```

### Raspberry Pi Deployment

**Install dependencies**:
```bash
uv sync --extra pi
```

**Configure device**:
Edit `config.ini`:
```ini
device_name = Room 397
api_port = 5000
loginterval_s = 10
```

**Run client (sensor logging + API server)**:
```bash
uv run python run_client.py
```

**Or run standalone with local dashboard**:
```bash
uv run bokeh serve --show tempsens/dashboard/bokeh_app.py
```

### Lab Server Deployment

**Configure sensors**:
Edit `server/config_server.ini`:
```ini
[SERVER]
poll_interval_s = 30
dashboard_port = 5006

[SENSORS]
Room_397 = http://100.64.0.5:5000
Lab_Bench = http://100.64.0.6:5000
```

**Run multi-sensor dashboard**:
```bash
uv run bokeh serve --show server/bokeh_app.py --port 5006
# Access from campus network at http://139.184.163.16:5006
```

### API Testing

**Check sensor status**:
```bash
curl http://100.64.0.5:5000/status
```

**Get latest 100 readings**:
```bash
curl http://100.64.0.5:5000/data/latest?limit=100
```

**Get readings in time range**:
```bash
curl "http://100.64.0.5:5000/data/range?start=2025-11-14%2010:00:00&end=2025-11-14%2012:00:00"
```

## HDF5 File Locking

The codebase uses manual file locking (`threading.Lock()`) because HDF5's built-in locking can cause issues:
- `file_lock` in `io_funcs.py:24` protects concurrent read/write
- Environment variable `HDF5_USE_FILE_LOCKING = 'FALSE'` set in `sensor.py`
- All HDF5 operations use `locking=False` parameter and rely on `file_lock` instead

## Important Notes

- **Client mode**: Each Pi maintains its own HDF5 file for backup and runs an API server
- **Server mode**: Dashboard polls clients via Headscale network, caches in SQLite
- **No authentication**: System relies on Headscale network isolation for security
- **Sensor reads can fail**: Code handles `RuntimeError` and retries (even on real hardware)
- **HDF5 datasets**: Use resizable arrays (`maxshape=(None,)`) for continuous appending
- **Time data**: Stored as string in HDF5, converted to milliseconds for Bokeh plots
- **API responses**: Return ISO-formatted timestamps as strings for JSON compatibility

## Network Architecture (Headscale VPN)

### Overview
- **Raspberry Pis**: Connected to Headscale VPN with private IPs (e.g., 100.64.0.5, 100.64.0.6)
- **Lab Server**: Also on Headscale (e.g., 100.64.0.4) + campus network (139.184.163.16)
- **Dashboard Access**: Campus users access server's public IP, no VPN needed

### Communication Flow
```
Raspberry Pi (100.64.0.5:5000)
    ↓ [Headscale VPN]
Lab Server (100.64.0.4)
    ↓ [Campus Network]
User Browser (139.184.163.16:5006)
```

### Advantages
- **Simplified access**: Users visit server dashboard, don't need Headscale client
- **Centralized monitoring**: One dashboard for all sensors
- **Resilient**: Each Pi logs locally, survives network outages
- **Secure**: Private Headscale network, campus firewall protection

### Setup Steps
1. Deploy `run_client.py` on each Pi with unique `device_name` in `config.ini`
2. Configure server's `config_server.ini` with all Pi API endpoints (using campus IPs)
3. Run `bokeh serve server/bokeh_app.py` on lab server
4. Access dashboard from campus network

## Windows Deployment

### Recommended: Native Windows (No WSL)

**The server can run natively on Windows** without WSL, which is the preferred deployment method.

**Setup:**
```powershell
# Install server dependencies
uv sync --group server

# Run server
uv run python -m bokeh serve server/bokeh_app.py --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=<SERVER_IP>:80

# Set up port 80 forwarding (one-time, PowerShell as Admin)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=127.0.0.1

# Add firewall rule
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any
```

**Advantages:**
- Simple networking: `127.0.0.1` never changes (unlike WSL2 dynamic IP)
- No virtualization overhead
- Easier to set up as Windows service
- Survives reboots without manual intervention

**Note:** The server code doesn't import `adafruit-circuitpython-dht` or any Pi-specific libraries, so it runs fine on Windows. Only the client (Raspberry Pi) needs the hardware dependencies.

---

### Alternative: WSL2 Deployment (Legacy)

### WSL2 Networking Quirks

When running the server in WSL2 on Windows, network configuration is more complex than native Linux:

**The Problem:**
- WSL2 runs in a virtualized network with its own IP address (e.g., `172.22.87.2`)
- WSL2's `127.0.0.1` is **separate** from Windows's `127.0.0.1`
- External clients connect to Windows IP, not WSL IP
- Direct port binding in WSL2 is not accessible from other machines

**The Solution:**
Use Windows `netsh` port proxy to forward traffic from Windows to WSL2:

```powershell
# 1. Get WSL IP address (from WSL terminal)
wsl hostname -I
# Example output: 172.22.87.2

# 2. Set up port forwarding (Windows PowerShell as Admin)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=172.22.87.2

# 3. Add Windows Firewall rule
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any

# 4. Verify port proxy
netsh interface portproxy show all
```

**Traffic Flow:**
```
Client Browser (139.184.160.232)
    ↓ HTTP request to 139.184.163.16:80
Windows Host (139.184.163.16:80)
    ↓ netsh port proxy forwards to
WSL2 VM (172.22.87.2:8000)
    ↓ Bokeh server responds
```

### Important Notes

1. **WSL IP can change after Windows reboot!** You'll need to:
   - Manually update the port proxy after reboot, OR
   - Create a startup script to auto-detect WSL IP and update proxy

2. **Port 80 vs Port 8000:**
   - University network blocks non-standard ports (like 8000) between subnets
   - Port 80 (standard HTTP) typically allowed across subnets
   - Use port 80 for external access, WSL runs on 8000 internally

3. **Server startup command in WSL:**
```bash
uv run python -m bokeh serve server/bokeh_app.py \
  --port 8000 \
  --address 0.0.0.0 \
  --allow-websocket-origin=139.184.163.16:80 \
  --allow-websocket-origin=139.184.163.16:8000 \
  --allow-websocket-origin=localhost:8000
```

**Must use `--address 0.0.0.0`** to listen on all interfaces, not just `127.0.0.1`!

### Alternative: Native Windows Deployment

Running the server natively on Windows (without WSL) would simplify networking, but currently blocked by:
- `adafruit-circuitpython-dht` checks `/proc/cpuinfo` on import (Linux-specific)
- Fails on Windows even though server doesn't need sensor hardware
- Potential solutions:
  - Lazy import: defer hardware library imports until needed
  - Separate packages: `tempsens-client` (with hardware) vs `tempsens-server` (no hardware)
  - Mock `/proc/cpuinfo`: patch library for cross-platform compatibility
