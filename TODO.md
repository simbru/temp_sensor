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
- ✅ **Got SSH tunnel working for cross-subnet dashboard access** 🎉

**Key Findings:**
- Server and Pis can communicate directly via campus IPs (139.184.x.x) without Headscale
- Cross-subnet dashboard access blocked by university network (need IT help OR SSH tunnel)
- SSH tunnel command: `ssh -L 8000:127.0.0.1:8000 main@139.184.163.16`
- Port 8000 chosen (instead of 5006) for consistency
- Note: Headscale runs on server, and Pi is already connected to Headscale. Issue is: How to serve dashboard to office PCs with current IT setup? If we can avoid Headscale, that does make things easier.  

**What's Left:**
- Test with real DHT22 sensor hardware on Pi
- Set up systemd services for production
- Contact IT about subnet access (optional if SSH tunnel is acceptable)

---

## IT Request - Network Access Issue

**Summary for Scott (IT):**
We have a temperature monitoring server at `139.184.163.16` that needs to be accessible from office PCs on subnet `139.184.160.x`. The server runs a web dashboard on port 8000. Currently:

- ✅ Server can communicate with Raspberry Pi sensors across subnets (API polling works)
- ✅ Windows Firewall rule added for port 8000 inbound traffic
- ❌ Cannot access dashboard from different subnet (`139.184.160.232` → `139.184.163.16:8000` times out)

**Request:** Either allow inbound connections to `139.184.163.16:8000` from `139.184.160.x` subnet, OR move server to `139.184.160.x` subnet so office PCs can access it.

**Alternative:** We can use Headscale VPN for access if network changes aren't feasible.

## Deployment Testing (Monday)
- [x] Verify server can poll Pi API over campus network (✅ confirmed working via campus IPs)
- [x] SSH tunnel working for cross-subnet dashboard access (✅ `ssh -L 8000:127.0.0.1:8000 main@139.184.163.16`)
- [ ] Test dashboard with real sensor data from Pi
- [ ] Test Raspberry Pi client with actual DHT22 sensor hardware
- [ ] Verify CSV download functionality works with real data
- [ ] Test systemd service for auto-start on both Pi and server

## Documentation
- [ ] Update README with Windows Firewall rule for port 8000 (if needed)
- [ ] Add troubleshooting section for cross-subnet access issues

## Nice to Have
- [ ] Set up systemd services on both Pi and server for auto-start on boot
- [ ] Test CSV download functionality with real data
