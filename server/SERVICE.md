# Server Service Setup

The Bokeh dashboard runs as a Windows service via WinSW on the lab server (139.184.163.16).

## Files

- `C:\tools\BokehDashboard.exe` — WinSW binary (renamed from WinSW.NET461.exe)
- `C:\tools\BokehDashboard.xml` — Service configuration (copied from repo)
- `server/BokehDashboard.xml` — Source of truth for service config (version-controlled)
- `C:\Users\main\temp_sensor\logs\BokehDashboard.out.log` — stdout
- `C:\Users\main\temp_sensor\logs\BokehDashboard.err.log` — stderr

The exe and xml filenames must match and live in the same directory.

## First-time setup

1. Download `WinSW.NET461.exe` from https://github.com/winsw/winsw/releases
2. Rename to `C:\tools\BokehDashboard.exe`
3. Copy the XML config from the repo:
   ```powershell
   copy C:\Users\main\temp_sensor\server\BokehDashboard.xml C:\tools\BokehDashboard.xml
   ```
4. Create logs directory and install:
   ```powershell
   mkdir C:\Users\main\temp_sensor\logs
   C:\tools\BokehDashboard.exe install
   C:\tools\BokehDashboard.exe start
   ```

## What the service runs

```
C:\Users\main\temp_sensor\.venv\Scripts\python.exe -m bokeh serve server/bokeh_app --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=139.184.163.16:80
```

Working directory: `C:\Users\main\temp_sensor`

Port 80 is forwarded to 8000 via netsh portproxy (configured separately).

## Managing the service

All commands require an admin PowerShell.

```powershell
C:\tools\BokehDashboard.exe status     # Check if running
C:\tools\BokehDashboard.exe start      # Start
C:\tools\BokehDashboard.exe stop       # Stop
C:\tools\BokehDashboard.exe restart    # Restart
```

Also visible in services.msc as "Temperature Dashboard (Bokeh)".

Standard Windows service commands also work:

```powershell
sc query BokehDashboard
net start BokehDashboard
net stop BokehDashboard
```

## Restart behavior

- Auto-starts on boot (AUTO_START)
- On crash: restarts after 10s, then 30s, then 60s for subsequent crashes
- Failure counter resets after 1 hour of stable running

## After updating code or dependencies

```powershell
cd C:\Users\main\temp_sensor
git pull
uv sync --group server
C:\tools\BokehDashboard.exe restart
```

If `server/BokehDashboard.xml` changed in the pull, also re-copy it:
```powershell
copy C:\Users\main\temp_sensor\server\BokehDashboard.xml C:\tools\BokehDashboard.xml
C:\tools\BokehDashboard.exe restart
```

## Logs

Logs rotate at 10MB, 5 files kept. Check errors with:

```powershell
type C:\Users\main\temp_sensor\logs\BokehDashboard.err.log
type C:\Users\main\temp_sensor\logs\BokehDashboard.out.log
```

## Uninstalling

```powershell
C:\tools\BokehDashboard.exe stop
C:\tools\BokehDashboard.exe uninstall
```

## Troubleshooting

If the service starts then immediately stops, check the err.log. Common causes:
- .venv missing or stale — run `uv sync --group server`
- `server/bokeh_app` directory missing — run `git pull`
- Port 8000 already in use — check with `netstat -ano | findstr :8000`
