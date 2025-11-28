# New Sensor Setup Checklist

Follow this checklist when adding a new Raspberry Pi sensor to the network.

## Hardware Setup

- [ ] Flash Raspberry Pi OS to SD card
- [ ] Connect DHT22 sensor to GPIO (data pin = GPIO 4 by default)
- [ ] Power on Pi and complete initial setup (keyboard, WiFi, timezone)
- [ ] Enable SSH: `sudo systemctl enable ssh && sudo systemctl start ssh`
- [ ] Update system: `sudo apt update && sudo apt upgrade -y`

## Network Setup (Headscale VPN)

- [ ] Install Tailscale client on Pi:
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  ```

- [ ] Connect to your Headscale server:
  ```bash
  sudo tailscale up --login-server=https://your-headscale-server.com
  ```

- [ ] Note the assigned Headscale IP (e.g., `100.64.0.x`)
  ```bash
  tailscale ip -4
  ```

- [ ] Verify connectivity from lab server:
  ```bash
  # From lab server
  ping 100.64.0.x
  ```

## SSH Key Setup

- [ ] Copy your SSH public key to the Pi (from dev machine):
  ```bash
  ssh-copy-id pi@100.64.0.x
  ```

- [ ] Test passwordless SSH:
  ```bash
  ssh pi@100.64.0.x whoami
  # Should print "pi" without asking for password
  ```

## Software Setup (Manual, One-Time)

- [ ] Install uv (Python package manager):
  ```bash
  ssh pi@100.64.0.x
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- [ ] Clone the repository:
  ```bash
  cd ~
  git clone https://github.com/yourusername/temp_sensor.git
  cd temp_sensor
  ```

- [ ] Install dependencies:
  ```bash
  ~/.local/bin/uv sync --extra pi
  ```

- [ ] Test sensor reading:
  ```bash
  ~/.local/bin/uv run python -c "from tempsens.io_funcs import log_data; print(log_data())"
  # Should print: {'timestamp': ..., 'temperature': 22.x, 'humidity': 45.x}
  ```

## Configuration

- [ ] Choose a unique device name (e.g., "Room 307", "Perfusion Sensor")

- [ ] Add sensor to `server/config_server.ini` on your dev machine:
  ```ini
  [SENSORS]
  # ... existing sensors ...
  Room_307 = http://100.64.0.x:5000, 60  # Poll every 60 seconds
  ```

- [ ] Commit and push the config change:
  ```bash
  git add server/config_server.ini
  git commit -m "Add Room 307 sensor"
  git push
  ```

## Ansible Deployment

- [ ] Generate Ansible inventory from updated config:
  ```bash
  # On your dev machine
  python deployment/generate_ansible_inventory.py
  ```

- [ ] Deploy to the new sensor:
  ```bash
  ./deployment/deploy.sh pi-room-307
  ```

  This will:
  - Pull the latest code
  - Install dependencies
  - Generate `config.ini` with device name
  - Create systemd service
  - Start the sensor

- [ ] Verify the sensor is running:
  ```bash
  ssh pi@100.64.0.x sudo systemctl status tempsensor
  ```

## Verification

- [ ] Check API endpoint from dev machine:
  ```bash
  curl http://100.64.0.x:5000/status
  # Should return JSON with sensor info
  ```

- [ ] Check latest data:
  ```bash
  curl http://100.64.0.x:5000/data/latest?limit=5
  # Should return recent readings
  ```

- [ ] Verify server dashboard can see the sensor:
  - Open dashboard: `http://your-server-ip:8000`
  - Select new sensor from dropdown
  - Should show temperature/humidity plots

- [ ] Check server logs (on lab server):
  ```bash
  grep "Room 307" logs/dashboard.log
  # Should show successful polling messages
  ```

## Troubleshooting

### Sensor not found
```bash
ssh pi@100.64.0.x
cd ~/temp_sensor
~/.local/bin/uv run python -c "from tempsens.io_funcs import sensor_found; print(sensor_found)"
# Should print: True
```

If `False`, check GPIO wiring or try different GPIO pin in code.

### Service keeps restarting
```bash
ssh pi@100.64.0.x
sudo journalctl -u tempsensor -n 50
# Look for error messages
```

### Dashboard not showing data
1. Check server can reach Pi: `curl http://100.64.0.x:5000/status`
2. Check `server/config_server.ini` has correct IP
3. Restart server dashboard (on lab server):
   ```bash
   pkill -f bokeh
   uv run bokeh serve server/bokeh_app.py --port 8000
   ```

## Maintenance

### Update sensor code
```bash
./deployment/deploy.sh pi-room-307
```

### View logs
```bash
ssh pi@100.64.0.x sudo journalctl -u tempsensor -f
```

### Restart sensor
```bash
ssh pi@100.64.0.x sudo systemctl restart tempsensor
```

### Check disk space (important for SD card longevity)
```bash
ssh pi@100.64.0.x df -h
# Watch /home/pi/temp_sensor/templog.db size
```

## Next Steps

- Consider setting up log rotation for `templog.db` if disk space is limited
- Add monitoring alerts if sensor goes offline
- Document sensor physical location and setup in a central location
