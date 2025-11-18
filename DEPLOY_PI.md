# Raspberry Pi Deployment Guide

Complete setup guide for deploying temperature sensor client on Raspberry Pi with auto-start on boot.

## Prerequisites

- Raspberry Pi with Raspberry Pi OS installed
- DHT22 sensor connected to GPIO4 (pin 7)
- Network connectivity (WiFi or Ethernet)
- SSH access to the Pi

## Initial Setup (One-Time)

### 1. Install System Dependencies

```bash
# Update package lists
sudo apt update

# Install Python dependencies for DHT22 sensor
sudo apt install -y python3-dev python3-pip libgpiod2

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add uv to PATH for current session
source $HOME/.cargo/env
```

### 2. Clone Repository

```bash
# Clone to home directory
cd ~
git clone https://github.com/YOUR_USERNAME/temp_sensor.git
cd temp_sensor

# Install Python dependencies (including DHT22 hardware support)
uv sync --extra pi
```

### 3. Configure Sensor

```bash
# Edit config file
nano config.ini
```

Set these values:
```ini
[DEFAULT]
device_name = Room 307           # Descriptive name for this sensor
api_port = 5000                  # Port for API server
loginterval_s = 60               # Seconds between readings
temp_offset_c = 0.0              # Calibration offset for temperature
humidity_offset_pct = 0.0        # Calibration offset for humidity
```

Save and exit (Ctrl+X, Y, Enter).

### 4. Test Sensor Manually

```bash
# Test that sensor works
uv run python run_client.py
```

You should see readings like:
```
[2025-11-18 14:30:45] 22.3°C  45.2%
```

Press Ctrl+C to stop. If you see readings, the sensor is working!

## Auto-Start Setup

### 5. Verify Tailscale Auto-Start

```bash
# Check if Tailscale service is enabled
sudo systemctl status tailscaled

# If not enabled, enable it
sudo systemctl enable tailscaled

# Verify Headscale connection
tailscale status
```

Expected output: Should show your Headscale IP (e.g., 100.64.0.5) and "online" status.

### 6. Install Sensor Service

```bash
# Copy service file to systemd directory
sudo cp ~/temp_sensor/tempsens.service /etc/systemd/system/

# IMPORTANT: Check username matches your system
# The service file uses "weatherstation" as the user
# If your username is different (e.g., "pi"), edit the service file:
sudo nano /etc/systemd/system/tempsens.service
# Change "User=weatherstation" to match your username
# Change all "/home/weatherstation/" paths to match your home directory

# Reload systemd to recognize new service
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable tempsens.service

# Start service now (without rebooting)
sudo systemctl start tempsens.service
```

### 7. Verify Service is Running

```bash
# Check service status
sudo systemctl status tempsens.service
```

Expected output:
```
● tempsens.service - Temperature Sensor Client (DHT22)
   Loaded: loaded (/etc/systemd/system/tempsens.service; enabled)
   Active: active (running) since Mon 2025-11-18 14:30:00 UTC
```

```bash
# View live logs
sudo journalctl -u tempsens.service -f
```

You should see sensor readings scrolling by.

### 8. Test API Endpoint

```bash
# From the Pi itself
curl http://localhost:5000/status

# From another machine on the network (use Pi's Headscale IP)
curl http://100.64.0.5:5000/status
```

Expected output:
```json
{
  "device_name": "Room 307",
  "status": "running",
  "ip": "100.64.0.5",
  ...
}
```

## Boot Sequence

After setup, on **every boot**:

1. **Pi boots** → Raspberry Pi OS starts
2. **Network initializes** → WiFi/Ethernet connects
3. **Tailscale starts** → `tailscaled.service` auto-starts, connects to Headscale
4. **Sensor starts** → `tempsens.service` auto-starts after network is online
5. **Readings begin** → Sensor starts logging, API available

**No manual intervention required!**

## Service Management Commands

```bash
# Start service
sudo systemctl start tempsens.service

# Stop service
sudo systemctl stop tempsens.service

# Restart service (after config changes)
sudo systemctl restart tempsens.service

# Check status
sudo systemctl status tempsens.service

# View logs (last 50 lines)
sudo journalctl -u tempsens.service -n 50

# View logs (live/follow mode)
sudo journalctl -u tempsens.service -f

# Disable auto-start
sudo systemctl disable tempsens.service

# Enable auto-start
sudo systemctl enable tempsens.service
```

## Updating Code

When you pull new code from git:

```bash
cd ~/temp_sensor
git pull
uv sync --extra pi  # Update dependencies if needed
sudo systemctl restart tempsens.service
```

## Troubleshooting

### Service won't start

```bash
# Check service status
sudo systemctl status tempsens.service

# Check detailed logs
sudo journalctl -u tempsens.service -n 100

# Common issues:
# 1. Wrong WorkingDirectory in service file
# 2. uv not in PATH
# 3. Config file missing or invalid
```

### Sensor read failures

```bash
# Check if sensor is connected to GPIO4
# Check wiring: VCC → 3.3V, GND → GND, DATA → GPIO4

# Test sensor manually
cd ~/temp_sensor
uv run python -c "from tempsens import io_funcs; io_funcs.log_data()"
```

### Tailscale not connected

```bash
# Check Tailscale status
tailscale status

# Reconnect to Headscale (replace with your server IP and auth key)
sudo tailscale up --login-server=http://139.184.163.16:8080 --authkey=YOUR_KEY

# Check Tailscale logs
sudo journalctl -u tailscaled -n 50
```

### API not accessible from server

```bash
# On Pi: Check if API is listening
netstat -tulpn | grep 5000

# On Pi: Test locally
curl http://localhost:5000/status

# On server: Test Headscale connectivity
ping 100.64.0.5

# On server: Test API
curl http://100.64.0.5:5000/status

# Common issues:
# 1. Headscale connection down
# 2. Wrong IP in server config
# 3. Firewall blocking port 5000 (unlikely on Pi)
```

### Database corruption after power loss

The sensor uses SQLite with WAL mode for crash safety, but SD card corruption can still occur with improper shutdowns.

```bash
# Check database integrity
cd ~/temp_sensor
uv run python -c "import sqlite3; conn = sqlite3.connect('templog.db'); conn.execute('PRAGMA integrity_check').fetchall()"

# If corrupted, delete and restart (sensor will recreate)
rm templog.db templog.db-wal templog.db-shm
sudo systemctl restart tempsens.service
```

### Service log verbosity

To increase logging verbosity, edit the service file:

```bash
sudo nano /etc/systemd/system/tempsens.service
```

Add to `[Service]` section:
```
Environment="TEMPSENS_DEBUG=1"
```

Then reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart tempsens.service
```

## Configuration Changes

After modifying `config.ini`:

```bash
# Changes are picked up on restart
sudo systemctl restart tempsens.service

# No need to reinstall service unless service file itself changed
```

## Hardware Considerations

**SD Card Longevity:**
- SQLite is configured with SD card optimizations (see `tempsens/io_funcs.py:154-158`)
- `synchronous=NORMAL` balances safety and write reduction
- 64MB cache reduces write frequency
- WAL mode minimizes write amplification

**Power Supply:**
- Use quality power supply (2.5A minimum for Pi 3/4)
- Poor power can cause sensor read failures and SD corruption
- Consider UPS or battery backup for critical deployments

**Sensor Reliability:**
- DHT22 occasionally fails reads (checksum errors) - this is normal
- Code retries up to 5 times per reading
- Spike filtering rejects physically impossible readings
- Failed reads are not written to database (gaps shown in plots)

## Security Notes

- API has no authentication (relies on Headscale network isolation)
- Only accessible via Headscale VPN (100.64.0.x IPs)
- Not exposed to internet or campus network directly
- Server dashboard provides the public interface

## Next Steps

After Pi is set up and running:

1. Add Pi's Headscale IP to server's `config_server.ini`
2. Restart server dashboard to see new sensor
3. Verify data is flowing in dashboard
4. Set calibration offsets if needed (compare to reference thermometer)

## Additional Sensors

To deploy additional Pis:

1. Repeat this guide on each Pi
2. Use unique `device_name` in each `config.ini`
3. Each Pi gets unique Headscale IP automatically
4. Add all sensors to server's `config_server.ini`

Example `server/config_server.ini`:
```ini
[SENSORS]
Room_307 = http://100.64.0.5:5000, 900     # 15 min intervals
Lab_Bench = http://100.64.0.6:5000, 60     # 1 min intervals
Incubator = http://100.64.0.7:5000, 10     # 10 sec intervals
```
