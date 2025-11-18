# Windows Server Deployment Guide

Complete setup guide for deploying the multi-sensor dashboard on Windows lab server with auto-start on boot.

## Prerequisites

- Windows 10/11 or Windows Server
- Network connectivity (campus network + Headscale VPN)
- Administrator access
- Git installed

## Initial Setup (One-Time)

### 1. Install System Dependencies

**Install uv (Python package manager):**

Open PowerShell and run:
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell to refresh PATH.

### 2. Clone Repository

```powershell
# Clone to your preferred location
cd C:\Users\$env:USERNAME
git clone https://github.com/YOUR_USERNAME/temp_sensor.git
cd temp_sensor

# Install Python dependencies (server-only, no hardware libs)
uv sync
```

### 3. Configure Dashboard

```powershell
# Edit server config
notepad server\config_server.ini
```

Configure your sensors:
```ini
[SERVER]
poll_interval_s = 30              # Default poll interval
min_gap_threshold_s = 60          # Sensor offline threshold
dashboard_port = 5006
database_path = sensor_data.db
dashboard_update_ms = 4000

[SENSORS]
# Format: sensor_name = url, poll_interval_s
# Poll interval is optional - defaults to poll_interval_s from [SERVER]
Room_307 = http://100.64.0.5:5000, 900     # 15 min intervals
Lab_Bench = http://100.64.0.6:5000, 60     # 1 min intervals
Incubator = http://100.64.0.7:5000, 10     # 10 sec intervals
```

Use Headscale private IPs (100.64.0.x) for stable connectivity.

### 4. Test Dashboard Manually

```powershell
# Test that dashboard works
uv run bokeh serve --show server\bokeh_app.py
```

Dashboard should open in browser. Press Ctrl+C to stop.

## Auto-Start Setup

### 5. Install as Windows Service

**Right-click PowerShell → Run as Administrator**, then:

```powershell
cd C:\Users\$env:USERNAME\temp_sensor

# Run the install script (auto-downloads NSSM if needed)
.\install_dashboard_service.ps1
```

The script will:
- Check for dependencies (uv, NSSM)
- Offer to download NSSM if not installed
- Install dashboard as Windows Service
- Configure auto-start on boot
- Ask if you want to start now

### 6. Verify Service is Running

```powershell
# Check service status
Get-Service -Name TempDashboard

# View logs
Get-Content logs\dashboard.log -Tail 50 -Wait
```

Expected output:
```
Status   Name               DisplayName
------   ----               -----------
Running  TempDashboard      Temperature Sensor Dashboard
```

### 7. Configure Port Forwarding (Optional)

If you want external access on port 80 (standard HTTP):

**In PowerShell as Administrator:**
```powershell
# Forward port 80 to dashboard port 5006
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=5006 connectaddress=127.0.0.1

# Add firewall rule
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any

# Verify
netsh interface portproxy show all
```

Now accessible at `http://139.184.163.16` (no port number needed).

## Boot Sequence

After setup, on **every boot**:

1. **Windows boots** → System starts
2. **Network initializes** → Campus network connects
3. **Headscale starts** → Docker container auto-starts (if using Headscale on this machine)
4. **Dashboard service starts** → `TempDashboard` Windows Service auto-starts
5. **Dashboard available** → Polls sensors, serves web interface

**No manual intervention required!**

## Service Management Commands

```powershell
# Start service
Start-Service -Name TempDashboard

# Stop service
Stop-Service -Name TempDashboard

# Restart service (after config or code changes)
Restart-Service -Name TempDashboard

# Check status
Get-Service -Name TempDashboard

# View service details
Get-Service -Name TempDashboard | Format-List *

# View logs (last 50 lines)
Get-Content logs\dashboard.log -Tail 50

# View logs (live/follow mode)
Get-Content logs\dashboard.log -Tail 50 -Wait

# View error logs
Get-Content logs\dashboard-error.log -Tail 50
```

## Updating Code

When you pull new code from git:

```powershell
cd C:\Users\$env:USERNAME\temp_sensor
git pull
uv sync  # Update dependencies if needed
Restart-Service -Name TempDashboard
```

The service automatically picks up the new code on restart.

## Troubleshooting

### Service won't start

```powershell
# Check service status
Get-Service -Name TempDashboard

# Check error logs
Get-Content logs\dashboard-error.log -Tail 100

# Try running manually to see errors
uv run bokeh serve server\bokeh_app.py

# Common issues:
# 1. Config file missing or invalid
# 2. Port already in use
# 3. Database locked by another process
```

### Can't connect to sensors

```powershell
# Test Headscale connectivity from server
ping 100.64.0.5

# Test sensor API
curl http://100.64.0.5:5000/status

# Check Headscale/Tailscale status
tailscale status

# Common issues:
# 1. Headscale container not running
# 2. Sensor Pi offline or not connected to Headscale
# 3. Wrong IP in config_server.ini
```

### Port 80 forwarding not working

```powershell
# Verify port proxy
netsh interface portproxy show all

# Verify firewall rule
Get-NetFirewallRule -DisplayName "Temperature Dashboard HTTP"

# Test locally first
curl http://localhost:5006

# Then test forwarded port
curl http://localhost:80

# Common issues:
# 1. Another service using port 80 (IIS, Apache)
# 2. Firewall blocking port
# 3. Port proxy not configured correctly
```

### Database growing too large

The dashboard polls sensors continuously and stores data:

```powershell
# Check database size
Get-Item sensor_data.db | Select-Object Name, @{Name="SizeMB";Expression={$_.Length / 1MB}}

# Old data can be deleted safely (dashboard queries recent data)
# To archive and reset:
Stop-Service -Name TempDashboard
Move-Item sensor_data.db sensor_data_backup_$(Get-Date -Format 'yyyyMMdd').db
Start-Service -Name TempDashboard
# Service will create new database automatically
```

### Service log rotation

Logs automatically rotate at 10MB. To change:

```powershell
# Open NSSM GUI editor
# (Replace path with your NSSM installation)
& "C:\Program Files\nssm\nssm.exe" edit TempDashboard

# Or via command line:
& "C:\Program Files\nssm\nssm.exe" set TempDashboard AppRotateBytes 52428800  # 50MB
Restart-Service -Name TempDashboard
```

## Uninstalling Service

To completely remove the service:

```powershell
# Stop service
Stop-Service -Name TempDashboard

# Remove service
& "C:\Program Files\nssm\nssm.exe" remove TempDashboard confirm

# Optionally delete logs and database
Remove-Item logs -Recurse -Force
Remove-Item sensor_data.db*
```

## Security Notes

- Dashboard runs on localhost (127.0.0.1) by default
- Configure `--allow-websocket-origin` for external access
- No authentication built-in (relies on network isolation)
- Port forwarding exposes dashboard to campus network
- Consider adding reverse proxy (nginx, IIS) for HTTPS

## Performance Considerations

**Database:**
- SQLite with WAL mode for concurrent access
- Automatic indexing on timestamp column
- Server polls continuously and caches in database
- Dashboard reads from cache, not directly from Pis

**Polling:**
- Each sensor has independent thread
- Fast sensors (1s) don't slow down slow sensors (15min)
- Per-sensor configurable intervals
- Failed polls don't block other sensors

**Memory:**
- Dashboard fetches only visible window (2x for smooth panning)
- Data cached in memory to reduce database queries
- Moving average computed on-demand, cached until data changes
- Gap detection computed during data preparation

## Advanced Configuration

### Custom Port

To change dashboard port, edit the install script before running:

```powershell
notepad install_dashboard_service.ps1
# Change: $Port = 5006
# To:     $Port = 8080
```

Or manually after installation:

```powershell
& "C:\Program Files\nssm\nssm.exe" edit TempDashboard
# Change --port argument
Restart-Service -Name TempDashboard
```

### Multiple Dashboard Instances

To run multiple dashboards (e.g., dev + prod):

```powershell
# Install with different service name and port
# Edit install script:
# $ServiceName = "TempDashboardDev"
# $Port = 5007
.\install_dashboard_service.ps1
```

### Running as Specific User

By default, service runs as Local System. To run as specific user:

```powershell
& "C:\Program Files\nssm\nssm.exe" edit TempDashboard
# Go to "Log on" tab
# Select "This account" and enter credentials
Restart-Service -Name TempDashboard
```

## Next Steps

After server is set up and running:

1. Deploy sensor clients on Raspberry Pis (see DEPLOY_PI.md)
2. Configure each Pi to auto-start (see install_service.sh)
3. Add Pi IPs to server's config_server.ini
4. Verify data flows in dashboard
5. Set up calibration offsets if needed
6. Configure port forwarding for external access

## Related Documentation

- **DEPLOY_PI.md** - Raspberry Pi sensor client deployment
- **HEADSCALE.md** - VPN setup (internal only, not committed)
- **README.md** - Project overview and architecture
