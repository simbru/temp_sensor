# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Deployment Context

**Production Environment:**
- **Lab Server (Windows)**: Runs `server/bokeh_app.py` natively on Windows at work/university, accessible from campus network
- **Raspberry Pi Clients**: Deployed in various rooms (Room 307, etc.), connected via Tailscale VPN
- **Live System**: Multiple sensor types (DHT22, AHT20, BME280, Enviro+) logging 24/7, data aggregated to central dashboard

**Local Development:**
- This machine is used for **development and testing only**
- The `dev/` folder contains test harness with simulated sensors
- Local config files (`config.ini`, `server/config_server.ini`) are NOT the production configs
- Production configs exist on deployed devices and are not synced to this repo

**Important:** When making changes, consider impact on the live production system running at work/university.

## Project Overview

This is a **distributed** Raspberry Pi temperature and humidity monitoring system with two deployment modes:

### Client Mode (Raspberry Pi)
1. Reads data from a sensor (DHT22, AHT20, BME280, or Enviro+ — auto-detected or configured)
2. Logs measurements to a local SQLite database (`templog.db`)
3. Implements spike filtering with warmup period to reject spurious readings
4. Tracks hardware failures via consecutive-failure counter
5. Exposes data via FastAPI REST API (v2 `/poll` endpoint + v1 legacy endpoints)
6. Can run standalone with local dashboard
7. Optional LCD display output for Enviro+ boards

### Server Mode (Lab Server)
1. Polls multiple Raspberry Pi clients via their APIs (v2 preferred, v1 fallback)
2. Aggregates data into a SQLite database using single-writer pattern
3. Per-sensor dedicated polling threads with configurable intervals
4. Hosts a multi-sensor Bokeh dashboard
5. Accessible from campus network

The system supports both **standalone** (single Pi with local dashboard) and **distributed** (multiple Pis + central server) architectures.

## Project Structure

```
tempsens/                    # Main package directory (client-side)
├── __init__.py             # Package initialization
├── io_funcs.py             # Data I/O, config, spike filtering, SQLite operations
├── sensor.py               # Background logging process (sched-based loop)
├── sensor_drivers.py       # Multi-sensor driver layer (DHT22, AHT20, BME280, Enviro+, Simulated)
├── api_server.py           # FastAPI server (v2 /poll + v1 legacy endpoints)
├── lcd_display.py          # Enviro+ ST7735 LCD display with proximity mode switching
└── dashboard/              # Original single-sensor dashboard
    ├── __init__.py
    └── bokeh_app.py        # Bokeh Server entry point (local mode)

server/                      # Server-side components (central dashboard)
├── __init__.py
├── api_client.py           # HTTP client with v2/v1 auto-detection per sensor
├── data_aggregator.py      # Per-sensor polling threads, single-writer DB, gap detection
├── bokeh_app.py            # Multi-sensor Bokeh dashboard with singleton aggregator
└── config_server.ini       # Server configuration (sensor list, per-sensor poll intervals)

dev/                         # Development/testing harness
├── run_test_clients.py     # Spawn N test clients with simulated sensors
├── run_full_test_environment.py  # Full test setup (history, clients, dashboard, browser)
├── lcd_simulator.py        # Mock ST7735 + LTR559 for desktop LCD testing
└── config_test*.ini        # Per-instance test configs

config.ini                   # Client configuration (sensor type, calibration, thresholds)
run_client.py               # Client startup (sensor + API + optional LCD as parallel processes)
templog.db                   # SQLite database (generated on Pi)
sensor_data.db              # SQLite database (generated on server)
migrate_h5_to_sqlite.py     # Migration utility from legacy HDF5 format
```

## Cross-Platform Development

**Critical**: Hardware sensor packages (`adafruit_dht`, `adafruit_ahtx0`, `adafruit_bme280`, `enviroplus`) only build on Raspberry Pi. The codebase handles this with:

- **Optional dependency groups**: Hardware libs are in `[project.optional-dependencies]` under `pi-hardware` and `enviroplus` groups
- **Sensor driver layer**: `tempsens/sensor_drivers.py` provides auto-detection and graceful fallback to `SimulatedSensor` when no hardware is found
- **Server isolation**: Server code (`server/`) has zero hardware imports — runs on any platform

**On development machines (Windows/WSL/local)**:
```bash
uv sync                             # Install core + client + server deps (no hardware libs)
uv run python run_client.py         # Run with simulated sensor data
```

**On Raspberry Pi**:
```bash
uv sync --extra pi-hardware         # Install with hardware sensor libraries
uv run python run_client.py         # Run with real sensor (auto-detected or configured)
```

**On Raspberry Pi with Enviro+ board**:
```bash
uv sync --extra pi-hardware --extra enviroplus
uv run python run_client.py         # Includes LCD display if enabled in config
```

## Architecture

### Sensor Driver Layer (`tempsens/sensor_drivers.py`)

Protocol-based multi-sensor abstraction with auto-detection.

**Supported Sensors:**
| Sensor | Interface | Measurements | Notes |
|--------|-----------|-------------|-------|
| DHT22 | GPIO (pin D4 hardcoded) | Temp, humidity | Fresh instance per read (prevents FD leaks) |
| AHT20 | I2C | Temp, humidity | Reliable I2C detection |
| BME280 | I2C (0x76) | Temp, humidity, pressure | `adafruit_bme280` library |
| Enviro+ | I2C + SPI | Temp, humidity, pressure, light, noise | CPU temp compensation, LTR559 light sensor |
| Simulated | None | Temp, humidity | 20% random failure rate for testing |

**Auto-Detection Order** (when `sensor_type = AUTO`):
1. ENVIROPLUS → 2. AHT20 → 3. BME280 → 4. DHT22 → 5. SIMULATED

**Known Issue:** AHT20 detection calls `busio.I2C(board.SCL, board.SDA)` which can hang indefinitely if the I2C bus is in a bad state. Set `sensor_type` explicitly in `config.ini` to skip auto-detection on problematic hardware.

**Interfaces:**
- `SensorInterface`: Basic `read() → (temp, humidity)` protocol
- `ExtendedSensorInterface`: Adds `read_extended() → (temp, humidity, pressure, light, noise)`

### Client Components (Raspberry Pi)

**`tempsens/io_funcs.py`**: Central data I/O module
- Configuration management (via `config.ini`, env override `TEMPSENS_CONFIG`)
- Auto-generates config with defaults on first run
- Lazy sensor initialization via `_get_sensor()` (cached in `_sensor_instance`)
- **Spike filtering**: Rejects readings with unrealistic deltas from previous valid reading
  - Configurable thresholds: `max_temp_delta_c` (default 3.0), `max_humidity_delta_pct` (default 10.0)
  - **Warmup period** (extended sensors): Skips writes for first 10s or 5 readings
  - First reading after startup always accepted (no baseline to compare)
- **Hardware failure tracking**: `.sensor_failures` file tracks consecutive None readings
  - Threshold: 5 consecutive failures = `hardware_failure` state
  - Counter resets to 0 on first successful read
  - File path: `<outputfile_dir>/.sensor_failures`
- **Calibration**: Per-sensor-type scale + offset applied after spike filtering
- SQLite database operations with WAL mode for crash safety
- SD card optimizations: `synchronous=NORMAL`, 64MB cache, 256MB mmap
- WAL mode auto-disabled on WSL `/mnt/` paths (not supported on Windows FS)

**Database Schema (Client):**
```sql
CREATE TABLE sensor_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL UNIQUE,  -- milliseconds since epoch
    temperature REAL,
    humidity REAL,
    pressure REAL,                       -- NULL for basic sensors (DHT22, AHT20)
    light REAL,                          -- NULL unless Enviro+
    noise REAL                           -- NULL unless Enviro+
);
CREATE INDEX idx_timestamp ON sensor_data(timestamp DESC);
```

**`tempsens/sensor.py`**: Background logging process
- Single-threaded `sched.scheduler` loop (lightweight, deterministic)
- Calls `log_data()` at configured interval
- Next read scheduled at end of each `log_data()` call
- 2-second initial delay for DHT22 warmup

**`tempsens/api_server.py`**: FastAPI REST API server
- **v2 API** (preferred): `/poll` endpoint — single request returns data + metrics + status + config
- **v1 API** (legacy): Separate `/status`, `/data/latest`, `/data/range`, `/config`, `/metrics` endpoints
- Error responses are sanitized: generic messages to clients, full details in server-side logs
- Metrics cached for 10 seconds (reduces Pi CPU load)
- Runs on port from `config.ini` (default: 5000)

**API Endpoints:**
| Endpoint | Version | Params | Returns |
|----------|---------|--------|---------|
| `/poll` | v2 | `?since=<ms>&limit=<n>` | Data + metrics + hardware_status + config + metadata |
| `/status` | v1 | — | Device info, latest reading, hardware status |
| `/data/latest` | v1 | `?limit=<1-50000>` | Recent N readings (default 100) |
| `/data/range` | v1 | `?start=<ms>&end=<ms>&limit=<n>` | Time-range filtered data |
| `/config` | v1 | — | Device name, log interval, sensor type |
| `/metrics` | v1 | — | CPU%, memory%, DB size, record count |

**All timestamps in API responses are integer milliseconds since epoch.**

**`tempsens/lcd_display.py`**: Enviro+ LCD display (optional)
- ST7735 160x80 SPI display with 5 display modes (dashboard, temp/humidity/pressure/light graphs)
- LTR559 proximity sensor triggers mode switching (0.3s debounce)
- Reads from SQLite database (not I2C directly) to avoid bus conflicts with sensor logger
- 1-second refresh rate, 160-point history buffer

**`run_client.py`**: Client startup script
- Spawns up to 3 parallel processes via `multiprocessing`:
  1. SensorLogger: `sensor.run_tempsensor_test()`
  2. APIServer: `uvicorn.run(app)`
  3. LCDDisplay (optional): `run_lcd_display_loop()` — only if `enable_lcd_display = True`
- Health monitoring: checks `is_alive()` every 0.5s, exits if any process dies
- File + console logging with 10MB rotation (5 backups)

### Server Components (Lab Server)

**`server/api_client.py`**: HTTP client for sensor APIs
- `SensorAPIClient`: Per-sensor HTTP client with connection pooling
  - Timeout: (3s connect, 8s read) for fast failure detection
  - **API version auto-detection**: First `/poll` call determines v2 (success) or v1 (404)
  - Sticky version tracking: `_api_version` avoids re-probing on every poll
- `MultiSensorClient`: Manages dict of `SensorAPIClient` instances
  - `poll_sensor()` and `get_sensor_api_version()` for version-aware routing

**`server/data_aggregator.py`**: Data polling and caching
- **Threading model**:
  - One dedicated polling thread per sensor (configurable interval each)
  - Single writer thread processes all DB writes from `queue.Queue`
  - Thread-local read connections (`threading.local()`) for concurrent reads
- **v2/v1 routing**: `update_sensor_data()` tries v2 `/poll` first, falls back to v1
  - v2: Single HTTP request, extracts data + metrics + status
  - v1: Separate HTTP requests, metadata throttled to every 10th poll
- **Exponential backoff**: On errors, delay = `2^N × poll_interval` (capped at `min(8×interval, 5min)`)
- **Per-sensor poll locks**: Non-blocking acquire prevents overlapping polls
- **Gap detection**: Compares oldest fetched timestamp vs. last DB timestamp
  - Gap threshold: `max(poll_interval × 6, min_gap_threshold)`
  - Triggers resync: streams data in 5000-record batches
- **Log spam control**: Logs first error and every 10th consecutive error
- **Status tracking**: active, idle, error, syncing, hardware_failure, degraded

**`server/bokeh_app.py`**: Multi-sensor dashboard
- **Singleton pattern**: Aggregator stored in `sys.modules` to survive Bokeh reloads
  - Thread-safe double-checked locking
  - Non-blocking startup: dashboard loads immediately, data arrives asynchronously
- Dropdown to select which sensor to view
- Temperature and humidity plots with configurable moving averages
- Fetches data from local SQLite cache (not directly from Pis)
- Real-time status indicators for each sensor
- **Gap visualization**: Server-side NaN marker insertion
  - Timestamps from aggregator are milliseconds since epoch (float)
  - `_insert_gap_markers()` detects gaps > configured threshold
  - Prevents moving average from falsely interpolating across outages
  - Small glitches (< threshold): lines connect across gaps
  - Large outages (> threshold): visual breaks in both raw and moving average lines

**Database Schema (Server):**
```sql
CREATE TABLE server_metadata (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE sensor_metadata (
    sensor_name TEXT PRIMARY KEY,
    device_ip TEXT, last_update TEXT, last_error TEXT,
    status TEXT,  -- active|idle|error|syncing|hardware_failure|degraded
    cpu_percent REAL, memory_percent REAL, client_db_size_mb REAL,
    sensor_type TEXT, total_records INTEGER, client_total_records INTEGER,
    log_interval_s REAL
);

CREATE TABLE sensor_<name> (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL, temperature REAL, humidity REAL,
    pressure REAL, light REAL, noise REAL, UNIQUE(timestamp)
);
```

### Data Flow

**Distributed Mode (Client + Server)**:
1. **Pi Client**: `sensor.py` → `log_data()` → spike filtering → warmup check → `write_data()` → local SQLite
2. **Pi Client**: `api_server.py` serves data via v2 `/poll` (or v1 legacy endpoints)
3. **Server**: Per-sensor polling thread calls `update_sensor_data()` → tries v2, falls back to v1
4. **Server**: Write queue → single writer thread → SQLite (WAL mode)
5. **Server**: Dashboard periodic callback reads from SQLite → updates Bokeh plots

**Standalone Mode (Single Pi)**:
1. `sensor.py` → `log_data()` → spike filtering → `write_data()` → local SQLite
2. `dashboard/bokeh_app.py` reads SQLite directly

**Spike Filtering Behavior**:
- First reading after startup always accepted (no previous reading to compare)
- Extended sensors have warmup period (10s or 5 readings) before any writes
- Subsequent readings compared to last valid reading
- Rejected readings logged to console but not written to database
- Calibration offsets applied after spike filtering
- Gaps appear in dashboard plots (visualized as breaks in line, not phantom connections)

### Configuration

**Client `config.ini`** (auto-generated with defaults on first run):
```ini
[DEFAULT]
loginterval_s = 2              # Seconds between sensor reads
outputfile = templog.db        # SQLite database path
device_name = Temperature Sensor
api_port = 5000
sensor_type = AUTO             # AUTO | DHT22 | AHT20 | BME280 | ENVIROPLUS | SIMULATED
max_temp_delta_c = 3.0         # Spike filter: max temp change per reading
max_humidity_delta_pct = 10.0  # Spike filter: max humidity change per reading
enable_lcd_display = False     # Enviro+ LCD output

# Calibration (applied after spike filtering)
temp_scale = 1.0
temp_offset = 0.0
humidity_scale = 1.0
humidity_offset = 0.0
pressure_scale = 1.0
pressure_offset = 0.0
light_scale = 1.0
light_offset = 0.0
noise_scale = 1.0
noise_offset = 0.0
cpu_temp_factor = 2.25         # Enviro+ CPU temperature compensation factor
```

**Server `config_server.ini`**:
```ini
[SERVER]
poll_interval_s = 30           # Default poll interval (fallback)
min_gap_threshold_s = 60       # Minimum gap before marking offline
dashboard_port = 8000
database_path = sensor_data.db
dashboard_update_ms = 5000     # Dashboard refresh rate
max_plot_points = 50000        # Maximum data points on plot

[SENSORS]
# Format: sensor_name = url, poll_interval_s
# Poll interval is optional - defaults to poll_interval_s from [SERVER] if not specified
Perfusion_Sensor = http://100.64.0.5:5000, 1    # Fast polling: 1s
Room_397 = http://100.64.0.6:5000, 900          # Slow polling: 15min
Lab_Bench = http://100.64.0.7:5000, 60          # Moderate: 1min
Incubator = http://100.64.0.8:5000              # Default: 30s
```

**Client `settings.ini`** (optional, git-ignored):
- User interface preferences for local dashboard
- Device name, display window sizes
- Created on demand, persists across restarts

## Common Commands

### Development (Windows/Local)

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
uv run bokeh serve --show server/bokeh_app.py --port 8000
# Dashboard at http://localhost:8000
```

**Run full test environment (simulated clients + server + dashboard)**:
```bash
uv run python dev/run_full_test_environment.py
```

### Raspberry Pi Deployment

**Install dependencies**:
```bash
uv sync --extra pi-hardware
```

**Configure device** — edit `config.ini`:
```ini
device_name = Room 397
api_port = 5000
loginterval_s = 10
sensor_type = AUTO             # Or explicit: DHT22, AHT20, BME280, ENVIROPLUS
```

**Run client (sensor logging + API server)**:
```bash
uv run python run_client.py
```

**Or run standalone with local dashboard**:
```bash
uv run bokeh serve --show tempsens/dashboard/bokeh_app.py
```

### Lab Server Deployment (Native Windows)

**The server runs natively on Windows** — no WSL needed. Server code has zero hardware imports.

**Configure sensors** — edit `server/config_server.ini` with Tailscale IPs for each Pi.

**Run multi-sensor dashboard**:
```powershell
uv sync --group server

uv run python -m bokeh serve server/bokeh_app.py --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=<SERVER_IP>:80

# Set up port 80 forwarding (one-time, PowerShell as Admin)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=127.0.0.1

# Add firewall rule
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any
```

### API Testing

**Check sensor status (v1)**:
```bash
curl http://100.64.0.5:5000/status
```

**Poll sensor (v2 — preferred)**:
```bash
curl "http://100.64.0.5:5000/poll?limit=10"
curl "http://100.64.0.5:5000/poll?since=1700000000000&limit=100"
```

**Get latest 100 readings (v1)**:
```bash
curl http://100.64.0.5:5000/data/latest?limit=100
```

**Get readings in time range (v1)**:
```bash
curl "http://100.64.0.5:5000/data/range?start=1700000000000&end=1700100000000"
```

## Important Notes

- **Multi-sensor support**: DHT22 (GPIO), AHT20/BME280 (I2C), Enviro+ (I2C+SPI) — auto-detected or explicit
- **Server mode**: Dashboard polls clients via Tailscale network, caches in SQLite
- **No authentication**: System relies on Tailscale network isolation for security
- **Sensor reads can fail**: DHT22 is especially unreliable — code handles `RuntimeError` and retries (5 attempts)
- **Hardware failure detection**: 5+ consecutive None readings triggers `hardware_failure` status
- **I2C bus hangs**: AHT20 auto-detection can hang if I2C bus is locked — set `sensor_type` explicitly to avoid
- **Timestamps**: Integer milliseconds since epoch everywhere (client DB, API, server DB, Bokeh plots)
- **API responses**: Sanitized error messages — no internal details leaked to clients
- **v2/v1 API coexistence**: Server auto-detects each client's API version (v2 preferred, v1 fallback)
- **Single-writer DB pattern**: Server uses write queue to eliminate "database is locked" errors
- **Legacy HDF5**: Migration utility exists (`migrate_h5_to_sqlite.py`) but all active storage is SQLite

## Network Architecture (Tailscale VPN)

### Overview
- **Raspberry Pis**: Connected to Tailscale VPN with private IPs (100.x.x.x range)
- **Lab Server**: Also on Tailscale + campus network (139.184.163.16)
- **Dashboard Access**: Campus users access server's public IP, no VPN needed

### Communication Flow
```
Raspberry Pi (100.x.x.x:5000)
    ↓ [Tailscale VPN]
Lab Server (Windows, 100.x.x.x / 139.184.163.16)
    ↓ [Campus Network via port 80 proxy]
User Browser → http://139.184.163.16
```

### Key Points
- **Simplified access**: Users visit server dashboard, don't need Tailscale
- **Centralized monitoring**: One dashboard for all sensors
- **Resilient**: Each Pi logs locally, survives network outages
- **Secure**: Private Tailscale network, campus firewall protection
- **Port 80**: University network blocks non-standard ports between subnets — use `netsh` port proxy from 80 → 8000

### Deployment Steps
1. Deploy `run_client.py` on each Pi with unique `device_name` in `config.ini`
2. Configure server's `config_server.ini` with all Pi API endpoints (Tailscale IPs)
3. Run `bokeh serve server/bokeh_app.py` on lab server
4. Access dashboard from campus network

## WSL2 Deployment (Legacy, Not Recommended)

Native Windows deployment is now the recommended approach. WSL2 was used historically but has networking complexity (dynamic IPs, port proxy requirements). See git history for WSL2-specific configuration if needed.
