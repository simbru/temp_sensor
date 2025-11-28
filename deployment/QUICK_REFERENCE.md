# Deployment Quick Reference

## Daily Commands

```bash
# Make changes, commit, push
git add .
git commit -m "Your message"
git push

# Deploy everything (clients + server)
python deployment/deploy_all.py

# Or deploy separately
./deployment/deploy.sh              # Clients only
python deployment/deploy_server.py  # Server only
```

## Deployment Options

### Deploy to Everything
```bash
python deployment/deploy_all.py
```

### Deploy to Clients Only (Raspberry Pis)
```bash
python deployment/deploy_all.py --clients
# Or: ./deployment/deploy.sh
```

### Deploy to Server Only
```bash
python deployment/deploy_all.py --server
# Or: python deployment/deploy_server.py
```

### Deploy to Specific Pi
```bash
./deployment/deploy.sh pi-room-307
```

### Deploy Locally (Testing)
```bash
python deployment/deploy_server.py --local
```

## Configuration Files

### Main Config (Edit This)
```
deployment/config_deployment.ini  (gitignored)
```

Contains:
- Git repo URL
- Sensor IPs and poll intervals
- Server connection info
- Dashboard settings

### Generated Configs (Auto-Generated)
```
server/config_server.ini           (gitignored, auto-generated)
deployment/inventory.ini           (gitignored, auto-generated)
```

Don't edit these - they're generated from `deployment/config_deployment.ini`.

## Sync Commands

```bash
# Sync deployment config → server config
python deployment/sync_configs.py

# Generate Ansible inventory
python deployment/generate_ansible_inventory.py
```

## Adding a New Sensor

```bash
# 1. Physical setup (manual on Pi)
ssh pi@100.64.0.9
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> ~/temp_sensor

# 2. Add to deployment config (on dev machine)
# Edit: deployment/config_deployment.ini
#   New_Sensor = http://100.64.0.9:5000, 30

# 3. Deploy
python deployment/sync_configs.py
./deployment/deploy.sh pi-new-sensor
```

## Troubleshooting

### Check What Would Change (Dry Run)
```bash
./deployment/deploy.sh --check
```

### View Sensor Status
```bash
curl http://100.64.0.5:5000/status
```

### Check Service on Pi
```bash
ssh pi@100.64.0.5
sudo systemctl status tempsensor
sudo journalctl -u tempsensor -f
```

### Manually Restart Pi Service
```bash
ssh pi@100.64.0.5
sudo systemctl restart tempsensor
```

### Check Git Status on Pi
```bash
ssh pi@100.64.0.5
cd ~/temp_sensor
git status
git log -1
```

## File Structure

```
deployment/
├── config_deployment.ini      ← YOUR MAIN CONFIG (gitignored)
├── sync_configs.py            ← Sync deployment → server config
├── generate_ansible_inventory.py  ← Generate inventory from config
├── deploy.sh                  ← Deploy to clients (Pis)
├── deploy_server.py           ← Deploy to server
└── deploy_all.py              ← Deploy to everything

config.ini                     ← Local client config (gitignored)
server/config_server.ini       ← Server config (auto-generated, gitignored)
```

## What's Gitignored vs Tracked

### Gitignored (Local Only)
- `deployment/config_deployment.ini` - Your config with real IPs
- `server/config_server.ini` - Auto-generated
- `deployment/inventory.ini` - Auto-generated
- `config.ini` - Local testing config

### Tracked in Git
- All `.template` files
- All scripts (`*.py`, `*.sh`, `*.yml`)
- Documentation

## SSH Setup (One-Time)

```bash
# Copy SSH key to each Pi
ssh-copy-id pi@100.64.0.5
ssh-copy-id pi@100.64.0.6
# etc.

# Copy SSH key to server (optional, for remote deployment)
ssh-copy-id user@139.184.163.16
```

## Configuration Examples

### deployment/config_deployment.ini
```ini
[DEPLOYMENT]
git_repo = https://github.com/yourusername/temp_sensor.git
git_version = main

[SENSORS]
Room_307 = http://100.64.0.5:5000, 60
Perfusion = http://100.64.0.6:5000, 1

[SERVER]
server_host = 139.184.163.16   # Leave blank for manual deployment
server_user = your_username
server_path = /path/to/temp_sensor
poll_interval_s = 2
```

## Common Workflows

### Update Code Everywhere
```bash
git commit -am "Your changes"
git push
python deployment/deploy_all.py
```

### Update Just One Pi
```bash
git commit -am "Your changes"
git push
./deployment/deploy.sh pi-room-307
```

### Change Sensor Poll Intervals
```bash
# Edit deployment/config_deployment.ini
# Change: Room_307 = http://100.64.0.5:5000, 60  → 30

python deployment/sync_configs.py
python deployment/deploy_server.py  # Update server with new poll interval
```

### Test Locally Before Deploying
```bash
# Test with simulated sensors
uv run python run_client.py

# Check API
curl http://localhost:5000/status
```
