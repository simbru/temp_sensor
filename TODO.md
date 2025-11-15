# TODO

## Session Summary (2025-11-15)

**What We Accomplished:**
- ✅ Deployed server on Windows (natively, no WSL!)
- ✅ Deployed Raspberry Pi client with real DHT22 sensor
- ✅ Confirmed Pi ↔ Server communication works over campus network (no Headscale needed!)
- ✅ Set up CSV download functionality for multi-sensor dashboard
- ✅ Resolved `uv` dependency group issues (client vs server groups)
- ✅ Fixed `adafruit_dht` Windows build issues (switched to CircuitPython version)
- ✅ Fixed missing RPi.GPIO dependency for sensor hardware
- ✅ Configured Windows Firewall for port 80
- ✅ **Got port 80 cross-subnet dashboard access working!** 🎉
- ✅ **Comprehensive code cleanup** - removed ~100 lines of dead code
- ✅ **Improved sensor logging** - cleaner retry messages, less noise
- ✅ **Config file cleanup** - removed 8 unused parameters
- ✅ **Better project structure** - moved test files to dev/ folder

**Key Findings:**
- Server and Pis can communicate directly via campus IPs (139.184.x.x) without Headscale
- University network blocks port 8000 between subnets, but **port 80 works!**
- Server runs natively on Windows using `--address 127.0.0.1` (no WSL complexity!)
- Windows `netsh` port proxy: port 80 → localhost:8000 (never changes!)
- Dashboard accessible at: `http://<SERVER_IP>/bokeh_app` from any campus PC

**Code Quality Improvements (2025-11-15):**
- Removed unused functions: `tempsensor_subprocess()`, `cleanup()`
- Consolidated duplicate code: `safe_float()` defined once in api_server.py
- Fixed inconsistencies: HDF5 locking now uses `locking=False` everywhere
- Cleaned config files: removed 8 unused parameters (temperature_min_c, margins, windows, ranges)
- Improved logging: sensor retries now show clear, informative messages
- Better test setup: fixed import path issues in dev/run_test_clients.py

**What's Left:**
- Set up auto-start services (systemd on Pi, Windows Task Scheduler on server)
- Long-term stability testing (currently running...)

**Major Breakthrough:**
- ✅ Server now runs **natively on Windows** (no WSL needed!)
- ✅ Port forwarding simplified: `127.0.0.1` instead of dynamic WSL IP
- ✅ Cross-subnet access working via port 80
- ✅ Setup survives Windows reboots without manual intervention

---

## WSL2 Dynamic IP Issue - Two-Pronged Approach

### Problem
The server dashboard runs in WSL2, which requires Windows port forwarding (port 80 → WSL IP:8000). However, WSL2's IP address can change after Windows reboots, breaking the port proxy.

### Solution Path A: Auto-Update WSL IP on Startup (Keep WSL)
**Pros:** Works with current setup, keeps server-only dependencies separate
**Cons:** Requires Windows startup script, WSL adds complexity

**Implementation:**
1. Create PowerShell script to detect WSL IP and update port proxy
2. Add script to Windows Task Scheduler (run at startup)
3. Script example:
```powershell
# Get WSL IP
$wslIP = wsl hostname -I
$wslIP = $wslIP.Trim()

# Update port proxy
netsh interface portproxy delete v4tov4 listenport=80 listenaddress=0.0.0.0
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=$wslIP
```

### Solution Path B: Run Server Natively on Windows (Remove WSL Dependency)
**Pros:** Simpler deployment, no WSL networking issues, native Windows service
**Cons:** Need to handle `adafruit-circuitpython-dht` platform detection

**Current Blocker:**
- `adafruit-circuitpython-dht` checks `/proc/cpuinfo` on import (Linux-specific)
- Fails on native Windows even though server doesn't need sensor hardware
- Issue location: Package imports fail before `sensor_found` fallback can trigger

**Implementation Options:**
1. **Mock `/proc/cpuinfo`**: Patch library to skip platform check on import
2. **Lazy import**: Defer `adafruit_dht` import until actually reading sensor (server never does)
3. **Separate packages**: Split sensor code into `tempsens-client` (with hardware deps) and `tempsens-server` (no hardware deps)
4. **Conditional import with try/except**: Already done in `io_funcs.py:11-19`, but may need to apply to server imports too

**Action Items:**
- [ ] Investigate where exactly `adafruit-circuitpython-dht` import fails on Windows
- [ ] Test if server code can run without importing sensor modules
- [ ] Consider refactoring imports to be fully conditional

## Native Windows Setup (CURRENT - RECOMMENDED)

**Server Command:**
```cmd
uv run python -m bokeh serve server/bokeh_app.py --port 8000 --address 127.0.0.1 --allow-websocket-origin=localhost:8000 --allow-websocket-origin=139.184.163.16:80
```

**Windows Port Forwarding (PowerShell as Admin - ONE TIME SETUP):**
```powershell
# Port 80 → 8000 forwarding (uses localhost, never changes!)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=127.0.0.1

# Verify
netsh interface portproxy show all

# Firewall rule (if not already added)
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any
```

**Access Dashboard:**
- From office PC: `http://139.184.163.16/bokeh_app`
- From server: `http://localhost:8000/bokeh_app`

**Benefits over WSL2:**
- No dynamic IP issues
- Simpler networking
- Survives reboots automatically
- Easier to set up as Windows service

---

## Legacy WSL2 Setup (NOT RECOMMENDED - DEPRECATED)

**Server Command (in WSL2):**
```bash
uv run python -m bokeh serve server/bokeh_app.py \
  --port 8000 \
  --address 0.0.0.0 \
  --allow-websocket-origin=139.184.163.16:80 \
  --allow-websocket-origin=139.184.163.16:8000 \
  --allow-websocket-origin=localhost:8000
```

**Windows Port Forwarding (PowerShell as Admin):**
```powershell
# Get WSL IP first: wsl hostname -I
# Then set up port proxy (replace 172.22.87.2 with actual WSL IP)
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=172.22.87.2

# Add firewall rule
New-NetFirewallRule -DisplayName "Temperature Dashboard HTTP" -Direction Inbound -Protocol TCP -LocalPort 80 -Action Allow -Profile Any
```

**Access Dashboard:**
- From office PC: `http://139.184.163.16/bokeh_app`

---

## Deployment Testing
- [x] Verify server can poll Pi API over campus network (✅ confirmed working via campus IPs)
- [x] Cross-subnet dashboard access working via port 80 (✅ accessible from office PC)
- [x] Test Raspberry Pi client with actual DHT22 sensor hardware (✅ Real sensor working)
- [x] Test dashboard with real sensor data from Pi (✅ Server polling and displaying data)
- [x] Verify CSV download functionality works with real data (✅ Working)
- [x] Code cleanup and optimization (✅ Removed ~100 lines of dead code)
- [ ] **Long-term stability test** (Currently running - let run for several hours)
- [ ] **Set up auto-start services** (NEXT PRIORITY)
  - [ ] Raspberry Pi: systemd service for client
  - [ ] Windows Server: Task Scheduler for dashboard
- [ ] Test recovery after Windows reboot (port forwarding persistence)

## Key Discovery - RPi.GPIO Dependency
**Problem:** `adafruit-circuitpython-dht` failed to import on Pi, falling back to simulation mode
**Cause:** Missing `RPi.GPIO` system library (required by `adafruit-blinka` for GPIO access)
**Solution:** Added `RPi.GPIO` to `pi-hardware` optional dependencies in `pyproject.toml`
**Status:** ✅ Fixed - sensor now reading real data

## Next Steps - Auto-Start Services

### Raspberry Pi (systemd)
README already has instructions at lines 110-140. Need to:
1. Create `/etc/systemd/system/tempsensor.service`
2. Enable and start the service
3. Verify it survives reboot

### Windows Server (Task Scheduler)
README has batch file example at lines 142-150. Need to:
1. Create `start_dashboard.bat` with proper server IP in --allow-websocket-origin
2. Set up Task Scheduler job to run at startup
3. Configure job to run even if user not logged in
4. Test reboot persistence

## Documentation
- [x] README updated with native Windows deployment (lines 85-151)
- [x] WSL2 networking documented as "Alternative" deployment (lines 331-333)
- [x] Development & Testing section added (lines 252-296)
- [x] Troubleshooting section comprehensive (lines 185-216)
- [ ] Add auto-start service setup walkthrough (can reference existing README sections)
