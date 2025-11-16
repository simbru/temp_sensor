# Temperature Monitoring System

A distributed temperature and humidity monitoring system for Raspberry Pi with DHT22 sensors.

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
git clone <your-repo-url> temp_sensor
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
uv run python -m bokeh serve server/bokeh_app.py --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=<SERVER_IP>:80
```

**Access:** `http://<SERVER_IP>/bokeh_app` from any campus computer

### 2. Pi Client Setup (Raspberry Pi)

**What you need:** Raspberry Pi with DHT22 sensor wired to GPIO4

```bash
# Clone and install
cd ~
git clone <your-repo-url> temp_sensor
cd temp_sensor
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv sync --group client --extra pi-hardware

# Configure
cp config.ini.example config.ini
nano config.ini  # Set device_name = "Room 307"

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
- Raspberry Pi with GPIO
- DHT22 sensor: VCC→3.3V, GND→GND, Data→GPIO4

### Software
- Python 3.11+ with `uv` package manager
- Git

### Network
- Pi must be reachable from server (campus network or VPN)
- Server must have campus-accessible IP
- Port 5000 on each Pi (API)
- Port 80 on server (dashboard)

---

## Auto-Start Services

### Raspberry Pi (systemd)

```bash
sudo nano /etc/systemd/system/tempsensor.service
```

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable tempsensor.service
sudo systemctl start tempsensor.service
sudo systemctl status tempsensor.service
```

### Windows Server (Task Scheduler)

Create a batch file `start_dashboard.bat`:
```batch
cd C:\path\to\temp_sensor
uv run python -m bokeh serve server/bokeh_app.py --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=<SERVER_IP>:80
```

Use Task Scheduler to run at startup.

---

## Dashboard Features

- **Sensor dropdown:** Switch between sensors
- **Status indicator:** 🟢 receiving data · 🟠 connected/waiting · 🟡 syncing backlog · 🔴 offline/error
- **Time window:** Preset buttons (10min - 1 week) or custom
- **Moving average:** Adjustable smoothing
- **CSV export:** Download current sensor's data

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
- Failed sensor reads are skipped (not written to HDF5) to reduce lock contention
- Gap detection runs server-side during plot rendering, not on raw data
- This enables fast polling (2s sensor interval, 5s server polling) without performance degradation

**Visual accuracy:**
- Setting moving average window to 1 shows exact sensor behavior with gaps
- Larger windows smooth noise while preserving gap visibility
- Phantom lines connecting across outages are prevented by NaN insertion with interpolated timestamps

---

## API Reference

Each Pi exposes a REST API:

**GET /status** - Device status and latest reading
```bash
curl http://<PI_IP>:5000/status
```

**GET /data/latest?limit=100** - Last N readings
```bash
curl "http://<PI_IP>:5000/data/latest?limit=50"
```

**GET /data/range?start=...&end=...** - Time range query
```bash
curl "http://<PI_IP>:5000/data/range?start=2025-11-14%2010:00:00&end=2025-11-14%2012:00:00"
```

---

## Troubleshooting

### "no dht sensor, generated data"
**Fix:**
```bash
uv sync --group client --extra pi-hardware  # Ensure hardware libs installed
# Check wiring: VCC→3.3V, GND→GND, Data→GPIO4
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

---

## Configuration Reference

### Client (`config.ini`)
```ini
[DEFAULT]
loginterval_s = 10                    # Seconds between sensor reads
device_name = Room 307                # Display name
api_port = 5000                       # API server port
outputfile = templog.h5               # Local HDF5 backup
```

### Server (`server/config_server.ini`)
```ini
[SERVER]
poll_interval_s = 2                   # Seconds between polling sensors
dashboard_port = 8000                 # Bokeh server port
database_path = sensor_data.db        # SQLite cache
dashboard_update_ms = 2000            # Dashboard refresh (ms)

[SENSORS]
Room_307 = http://<PI_IP_1>:5000
Lab_Bench = http://<PI_IP_2>:5000
```

---

## Adding New Sensors

1. **On new Pi:** Follow "Pi Client Setup", set unique `device_name`
2. **On server:** Edit `server/config_server.ini`, add sensor to `[SENSORS]`
3. **Restart:** Server will auto-detect new sensor

---

## Development & Testing

### Local Development (No Hardware Required)

You can develop and test the entire system on your local machine using simulated sensors:

**1. Start simulated sensors:**
```bash
# Starts 3 test sensors with simulated data on ports 5001-5003
uv run python dev/run_test_clients.py
```

This creates:
- 3 simulated DHT22 sensors with random temperature/humidity
- API servers at `http://localhost:5001`, `5002`, `5003`
- Local HDF5 files: `dev/templog_test1.h5`, `dev/templog_test2.h5`, `dev/templog_test3.h5`

**2. Configure server to use test sensors:**

Edit `server/config_server.ini`:
```ini
[SENSORS]
Test_Sensor_1 = http://localhost:5001
Test_Sensor_2 = http://localhost:5002
Test_Sensor_3 = http://localhost:5003
```

**3. Start dashboard:**
```bash
uv run bokeh serve server/bokeh_app.py --show
# Opens at http://localhost:5006/bokeh_app
```

**4. Test features:**
- Switch between sensors in dropdown
- Adjust time windows and moving averages
- Download CSV exports
- Verify polling logs in server console

**Dev folder contents:**
- `dev/run_test_clients.py` - Multi-client test harness
- `dev/config_test1.ini` - Test sensor 1 config (port 5001)
- `dev/config_test2.ini` - Test sensor 2 config (port 5002)
- `dev/config_test3.ini` - Test sensor 3 config (port 5003)

---

## Architecture

```
┌─────────────────┐
│  Raspberry Pi   │  DHT22 → sensor.py → templog.h5
│                 │                   ↓
│  FastAPI :5000  │  ←──── Serves data via REST API
└────────┬────────┘
         │
         │ Campus Network / VPN
         │
         ↓ HTTP polling every 2s
┌─────────────────┐
│  Lab Server     │  data_aggregator.py → sensor_data.db
│                 │                     ↓
│  Bokeh :8000    │  ←──── Dashboard queries SQLite
└────────┬────────┘
         │
         │ Port 80 forwarding
         │
         ↓ WebSocket
┌─────────────────┐
│  User Browser   │  http://<SERVER_IP>/bokeh_app
└─────────────────┘
```

---

## Optional: VPN Setup (Headscale)

**Note:** Campus network direct connection worked fine for our deployment. VPN is optional if cross-subnet routing is available.

If you need isolated Pi ↔ Server communication, see [Headscale documentation](https://headscale.net/) for VPN setup. Replace campus IPs with Headscale IPs (e.g., `100.64.0.x`) in configs.

---

## License

MIT License

## Support

- Check [CLAUDE.md](CLAUDE.md) for detailed architecture
- Open GitHub issues for bugs/questions
- See troubleshooting section above

## Credits

Built with [Bokeh](https://bokeh.org/), [FastAPI](https://fastapi.tiangolo.com/), [Adafruit DHT](https://github.com/adafruit/Adafruit_CircuitPython_DHT), and [uv](https://github.com/astral-sh/uv)
