# TODO

## Session Summary (2025-11-15)

**What We Accomplished:**
- ✅ Deployed server on Windows WSL2 (Ubuntu)
- ✅ Deployed Raspberry Pi client with simulated sensor data
- ✅ Confirmed Pi ↔ Server communication works over campus network (no Headscale needed!)
- ✅ Set up CSV download functionality for multi-sensor dashboard
- ✅ Resolved `uv` dependency group issues (client vs server groups)
- ✅ Fixed `adafruit_dht` Windows build issues (switched to CircuitPython version)
- ✅ Configured Windows Firewall for port 8000
- ✅ **Got port 80 cross-subnet dashboard access working!** 🎉

**Key Findings:**
- Server and Pis can communicate directly via campus IPs (139.184.x.x) without Headscale
- University network blocks port 8000 between subnets, but **port 80 works!**
- Used Windows `netsh` port proxy: port 80 → WSL2 IP:8000
- Dashboard now accessible at: `http://139.184.163.16/bokeh_app` (no SSH tunnel needed!)

**Critical Discovery - WSL2 Networking:**
- WSL2 runs in separate virtualized network with its own IP (e.g., `172.22.87.2`)
- Windows `127.0.0.1` ≠ WSL2 `127.0.0.1`
- Port forwarding must use WSL2's actual IP: `netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8000 connectaddress=172.22.87.2`
- **PROBLEM**: WSL2 IP can change on Windows reboot!

**What's Left:**
- ~~Fix WSL2 IP change issue~~ ✅ **SOLVED: Running natively on Windows instead!**
- ✅ **DHT22 sensor now working!** (needed RPi.GPIO dependency)
- Set up auto-start services (systemd on Pi, Windows service on server)
- Polish sensor logging (occasional bad reads are normal)

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
- [x] Cross-subnet dashboard access working via port 80 (✅ `http://139.184.163.16/bokeh_app`)
- [x] Test Raspberry Pi client with actual DHT22 sensor hardware (✅ Reading 23.1°C, 53.4% humidity)
- [ ] Test dashboard with real sensor data from Pi (run client API + verify server polls it)
- [ ] Verify CSV download functionality works with real data
- [ ] Set up auto-start services (systemd on Pi, Windows service/task on server)
- [ ] Test recovery after Windows reboot (WSL IP change)

## Key Discovery - RPi.GPIO Dependency
**Problem:** `adafruit-circuitpython-dht` failed to import on Pi, falling back to simulation mode
**Cause:** Missing `RPi.GPIO` system library (required by `adafruit-blinka` for GPIO access)
**Solution:** Added `RPi.GPIO` to `pi-hardware` optional dependencies in `pyproject.toml`
**Status:** ✅ Fixed - sensor now reading real data

## Documentation
- [ ] Update README with port 80 setup and WSL IP issue
- [ ] Document Windows startup script for auto-updating WSL IP
- [ ] Add troubleshooting section for WSL2 networking

## Future Improvements
- [ ] Decide on Path A (WSL + auto-update script) vs Path B (native Windows)
- [ ] If Path B: Investigate making server runnable on Windows without WSL
- [ ] Set up proper Windows service for production
