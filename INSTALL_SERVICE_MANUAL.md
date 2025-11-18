# Manual Windows Service Installation

Simple step-by-step guide to install the dashboard as a Windows service without the PowerShell script.

## Prerequisites

1. **Install uv** (if not already installed):
   ```powershell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Close and reopen PowerShell after installing.

2. **Download NSSM** (Non-Sucking Service Manager):
   - Go to: https://nssm.cc/download
   - Download `nssm-2.24.zip`
   - Extract to `C:\nssm\`
   - Ensure `C:\nssm\win64\nssm.exe` exists

## Installation Steps

### Step 1: Test Dashboard Manually

First, verify the dashboard works:

```powershell
cd C:\users\main\temp_sensor
uv run bokeh serve server\bokeh_app.py
```

If it opens in browser, press Ctrl+C and continue. If not, fix any errors first.

### Step 2: Create Service Using NSSM GUI

Open PowerShell **as Administrator**:

```powershell
cd C:\nssm\win64
.\nssm.exe install TempDashboard
```

A GUI window will open. Fill in the fields:

**Application Tab:**
- **Path:** `C:\Users\main\.local\bin\uv.exe` (find with: `(Get-Command uv).Source`)
- **Startup directory:** `C:\users\main\temp_sensor`
- **Arguments:**
  ```
  run python -m bokeh serve server/bokeh_app.py --port 5006 --address 0.0.0.0 --allow-websocket-origin=localhost:5006 --session-token-expiration 360000000
  ```

**Details Tab:**
- **Display name:** Temperature Sensor Dashboard
- **Description:** Multi-sensor temperature and humidity monitoring dashboard
- **Startup type:** Automatic

**I/O Tab:**
- **Output (stdout):** `C:\users\main\temp_sensor\logs\dashboard.log`
- **Error (stderr):** `C:\users\main\temp_sensor\logs\dashboard-error.log`

**File rotation Tab:**
- Check "Rotate files"
- **Rotate files bigger than:** 10240 KB (10 MB)

Click **Install service**

### Step 3: Create Logs Directory

```powershell
New-Item -ItemType Directory -Path C:\users\main\temp_sensor\logs -Force
```

### Step 4: Start Service

```powershell
Start-Service -Name TempDashboard
```

### Step 5: Verify

```powershell
# Check service status
Get-Service -Name TempDashboard

# Should show: Status = Running
```

Open browser to: `http://localhost:5006`

## Service Management

```powershell
# Start service
Start-Service -Name TempDashboard

# Stop service
Stop-Service -Name TempDashboard

# Restart service
Restart-Service -Name TempDashboard

# Check status
Get-Service -Name TempDashboard

# View logs
Get-Content C:\users\main\temp_sensor\logs\dashboard.log -Tail 50 -Wait
```

## Uninstall Service

```powershell
# Stop service
Stop-Service -Name TempDashboard

# Remove service
C:\nssm\win64\nssm.exe remove TempDashboard confirm
```

## After Code Updates

```powershell
cd C:\users\main\temp_sensor
git pull
uv sync
Restart-Service -Name TempDashboard
```

## Troubleshooting

### Service won't start

Check error log:
```powershell
Get-Content C:\users\main\temp_sensor\logs\dashboard-error.log -Tail 50
```

### Can't find uv.exe

Find the correct path:
```powershell
(Get-Command uv).Source
```

Use that exact path in NSSM Application Path field.

### Port already in use

Another process is using port 5006. Find it:
```powershell
netstat -ano | findstr :5006
```

Kill the process or change the port in the Arguments field.

## Command-Line Alternative to GUI

If you prefer command-line over GUI:

```powershell
cd C:\nssm\win64

# Install service
.\nssm.exe install TempDashboard "C:\Users\main\.local\bin\uv.exe" "run python -m bokeh serve server/bokeh_app.py --port 5006 --address 0.0.0.0 --allow-websocket-origin=localhost:5006 --session-token-expiration 360000000"

# Configure
.\nssm.exe set TempDashboard AppDirectory "C:\users\main\temp_sensor"
.\nssm.exe set TempDashboard DisplayName "Temperature Sensor Dashboard"
.\nssm.exe set TempDashboard Description "Multi-sensor temperature and humidity monitoring dashboard"
.\nssm.exe set TempDashboard Start SERVICE_AUTO_START
.\nssm.exe set TempDashboard AppStdout "C:\users\main\temp_sensor\logs\dashboard.log"
.\nssm.exe set TempDashboard AppStderr "C:\users\main\temp_sensor\logs\dashboard-error.log"
.\nssm.exe set TempDashboard AppRotateFiles 1
.\nssm.exe set TempDashboard AppRotateBytes 10485760

# Create logs directory
New-Item -ItemType Directory -Path "C:\users\main\temp_sensor\logs" -Force

# Start service
Start-Service -Name TempDashboard
```

## Done!

The dashboard will now auto-start on boot. No need to manually start it.
