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

### Windows Server (Task Scheduler)

Use the automated installer (PowerShell as Administrator):
```powershell
cd C:\path\to\temp_sensor
.\install_dashboard_task.ps1
```

The script creates a Scheduled Task that auto-starts on boot with optional visible console window.

**Manual setup:** See [DEPLOY_SERVER.md](DEPLOY_SERVER.md) for detailed instructions.

---

## Updating Code (Git Pull)

### On Raspberry Pi

```bash
cd ~/temp_sensor
git pull
uv sync --extra pi  # Only if dependencies changed
sudo systemctl restart tempsens.service
```

**Check it worked:**
```bash
sudo systemctl status tempsens.service
sudo journalctl -u tempsens.service -f  # View live logs
```

### On Windows Server

```powershell
cd C:\users\main\temp_sensor
Stop-ScheduledTask -TaskName TempDashboard
git pull
uv sync  # Only if dependencies changed
Start-ScheduledTask -TaskName TempDashboard
```

**Check it worked:**
```powershell
Get-ScheduledTask -TaskName TempDashboard
Get-Content logs\dashboard.log -Tail 50 -Wait
```

**Alternative (if running manually with visible console):**
```powershell
# Press Ctrl+C in the dashboard console window to stop
git pull
uv sync  # Only if dependencies changed
.\start_dashboard_visible.ps1
```

### Common Issues After Update

**"Module not found" error:**
```bash
# Pi: Reinstall dependencies
cd ~/temp_sensor
uv sync --extra pi --reinstall
sudo systemctl restart tempsens.service

# Windows: Reinstall dependencies
cd C:\users\main\temp_sensor
uv sync --reinstall
Start-ScheduledTask -TaskName TempDashboard
```

**"Port already in use" (Windows):**
```powershell
# Kill any lingering Python processes
Get-Process | Where-Object {$_.Name -like "*python*"} | Stop-Process -Force
Start-ScheduledTask -TaskName TempDashboard
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
outputfile = templog.db               # Local SQLite database
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
uv run bokeh serve server/bokeh_app.py --show
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
│  Raspberry Pi   │  DHT22 → sensor.py → templog.db
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

## Tailscale/Headscale VPN Setup

The system supports two networking modes:

### Option 1: Direct Campus Network (Simpler)
If your Raspberry Pis and server are on the same campus network with cross-subnet routing enabled, you can use campus IPs directly in `server/config_server.ini`. No VPN needed.

### Option 2: Tailscale VPN (More Flexible)
For deployments where Pis are on different networks (home, lab, office) or you want secure isolated communication, use Tailscale.

**Benefits:**
- Pis accessible from anywhere (home, campus, off-site)
- Encrypted peer-to-peer connections
- Stable private IPs (100.x.x.x range)
- Works across firewalls and NAT

**Setup Steps:**

1. **Install Tailscale on each device:**
   - **Raspberry Pi:** `curl -fsSL https://tailscale.com/install.sh | sh`
   - **Windows Server:** Download installer from [tailscale.com](https://tailscale.com/download)

2. **Authenticate devices:**
   - Run `sudo tailscale up` on each Pi
   - Run Tailscale on Windows and sign in
   - **Authentication token:** For unattended setup, use an auth key (available on the lab's internal wiki)
   - Example: `sudo tailscale up --authkey tskey-auth-xxxxx`

3. **Verify connectivity:**
   ```bash
   # Check Tailscale status
   tailscale status

   # Note each device's Tailscale IP (100.x.x.x)
   tailscale ip -4

   # Test connectivity between devices
   ping <tailscale-ip>
   ```

4. **Update configs to use Tailscale IPs:**
   - Edit `server/config_server.ini` and replace campus IPs with Tailscale IPs
   - Example: `Room_307 = http://100.64.0.5:5000`
   - Restart server dashboard

**Security Notes:**
- Keep auth keys private (do not commit to git)
- Use ephemeral keys for testing, reusable keys for production
- Disable key expiration for always-on devices
- Check the lab wiki for current authentication credentials

**Reference:**
- Tailscale docs: https://tailscale.com/kb/
- Headscale (self-hosted alternative): https://headscale.net/

---

## License

MIT License

## Support

- Check [CLAUDE.md](CLAUDE.md) for detailed architecture
- Open GitHub issues for bugs/questions
- See troubleshooting section above

## Credits

Built with [Bokeh](https://bokeh.org/), [FastAPI](https://fastapi.tiangolo.com/), [Adafruit DHT](https://github.com/adafruit/Adafruit_CircuitPython_DHT), and [uv](https://github.com/astral-sh/uv)
