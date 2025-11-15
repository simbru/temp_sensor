# TODO

## IT Request - Network Access Issue

**Summary for Scott (IT):**
We have a temperature monitoring server at `139.184.163.16` that needs to be accessible from office PCs on subnet `139.184.160.x`. The server runs a web dashboard on port 8000. Currently:

- ✅ Server can communicate with Raspberry Pi sensors across subnets (API polling works)
- ✅ Windows Firewall rule added for port 8000 inbound traffic
- ❌ Cannot access dashboard from different subnet (`139.184.160.232` → `139.184.163.16:8000` times out)

**Request:** Either allow inbound connections to `139.184.163.16:8000` from `139.184.160.x` subnet, OR move server to `139.184.160.x` subnet so office PCs can access it.

**Alternative:** We can use Headscale VPN for access if network changes aren't feasible.

## Deployment Testing (Monday)
- [ ] Test server dashboard access after IT resolution
- [ ] Test Raspberry Pi client with actual DHT22 sensor
- [ ] Verify server can poll Pi API over campus network (confirmed working)

## Documentation
- [ ] Update README with Windows Firewall rule for port 8000 (if needed)
- [ ] Add troubleshooting section for cross-subnet access issues

## Nice to Have
- [ ] Set up systemd services on both Pi and server for auto-start on boot
- [ ] Test CSV download functionality with real data
