# Deployment System for Temperature Sensors

This directory contains automation for deploying updates to both Raspberry Pi clients and the dashboard server.

## Quick Links

- **[WORKFLOW.md](WORKFLOW.md)** - Complete workflow guide (START HERE!)
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Command cheat sheet
- **[NEW_SENSOR_SETUP.md](NEW_SENSOR_SETUP.md)** - Checklist for adding new sensors
- This README - Technical reference and detailed documentation

## Key Feature: Single Source of Truth

**The sensor inventory is auto-generated from `deployment/config_deployment.ini`** (git-ignored), avoiding config duplication and keeping sensitive IPs out of git:

```ini
# deployment/config_deployment.ini (git-ignored, YOUR MAIN CONFIG)
[DEPLOYMENT]
git_repo = https://github.com/you/temp_sensor.git

[SENSORS]
Room_307 = http://100.64.0.5:5000, 60
Perfusion_Sensor = http://100.64.0.6:5000, 1
```

↓ **`python deployment/sync_configs.py`** ↓

```ini
# server/config_server.ini (git-ignored, auto-generated)
[SENSORS]
Room_307 = http://100.64.0.5:5000, 60
Perfusion_Sensor = http://100.64.0.6:5000, 1
```

↓ **`python deployment/generate_ansible_inventory.py`** ↓

```ini
# deployment/inventory.ini (git-ignored, auto-generated)
[temperature_sensors]
pi-room-307 ansible_host=100.64.0.5 sensor_name="Room 307" sensor_port=5000
pi-perfusion-sensor ansible_host=100.64.0.6 sensor_name="Perfusion Sensor" sensor_port=5000
```

## Quick Start

### 1. One-Time Setup

```bash
# Install Ansible
pip install ansible

# Set up SSH keys to each Pi
ssh-copy-id pi@100.64.0.5
ssh-copy-id pi@100.64.0.6
# ... etc

# Initialize deployment config
python deployment/setup_deployment.py

# Edit deployment config with your sensor IPs
# Edit: deployment/config_deployment.ini
#   - Set git_repo URL
#   - Add sensor IPs (Headscale addresses)

# Sync configs
python deployment/sync_configs.py
```

### 2. Daily Workflow

```bash
# Make code changes, commit, and push
git add .
git commit -m "Your changes"
git push origin main

# Deploy to all sensors
./deployment/deploy.sh

# Or deploy to specific sensor
./deployment/deploy.sh pi-room-307

# Or dry run (see what would change)
./deployment/deploy.sh --check
```

## What Gets Deployed

The playbook automatically:

1. ✓ Pulls latest code from git
2. ✓ Updates Python dependencies with `uv sync --extra pi`
3. ✓ Generates `config.ini` from sensor metadata (device name, port)
4. ✓ Creates/updates systemd service
5. ✓ Restarts sensor service
6. ✓ Verifies API health

## Adding a New Sensor

### Step 1: Initial Pi Setup (manual, one-time)

```bash
# SSH to new Pi
ssh pi@100.64.0.x

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone repo
git clone <your-repo-url> ~/temp_sensor

# Exit back to dev machine
exit
```

### Step 2: Add to Server Config

Edit `server/config_server.ini`:

```ini
[SENSORS]
# ... existing sensors ...
New_Lab_Bench = http://100.64.0.9:5000, 30
```

### Step 3: Deploy

```bash
# Inventory is auto-generated from server config
./deployment/deploy.sh pi-new-lab-bench
```

That's it! The sensor is now:
- Running the latest code
- Configured with correct device name
- Set up as a systemd service
- Being polled by the server dashboard

## Manual Ansible Commands

If you prefer direct `ansible-playbook` commands:

```bash
# Generate inventory first
python3 deployment/generate_ansible_inventory.py

# Deploy to all
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml

# Deploy to specific sensor
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml --limit pi-room-307

# Check mode (dry run)
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml --check --diff

# View sensor health
ansible temperature_sensors -i deployment/inventory.ini -m uri -a "url=http://localhost:5000/status return_content=yes"
```

## File Structure

```
deployment/
├── README.md                           # This file
├── deploy.sh                           # Convenience deployment script
├── deploy.yml                          # Ansible playbook
├── generate_ansible_inventory.py       # Generates inventory from server config
├── inventory.ini                       # Auto-generated (git-ignored)
└── templates/
    ├── config.ini.j2                  # Template for sensor config.ini
    └── tempsensor.service.j2          # Template for systemd service
```

## Troubleshooting

### "Permission denied (publickey)"

You need to set up SSH keys:

```bash
ssh-copy-id pi@100.64.0.x
```

### "No production sensors found"

Your `server/config_server.ini` only has localhost entries. Add production sensors:

```ini
[SENSORS]
Room_307 = http://100.64.0.5:5000
```

### View systemd logs on Pi

```bash
ssh pi@100.64.0.5
sudo journalctl -u tempsensor -f
```

### Manually restart service

```bash
ansible temperature_sensors -i deployment/inventory.ini -b -m systemd -a "name=tempsensor state=restarted"
```

## Advanced Usage

### Deploy Specific Git Version

```bash
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml -e "git_version=v1.2.0"
```

### Skip Service Restart

```bash
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml --skip-tags restart
```

### Run Custom Command on All Sensors

```bash
# Check disk space
ansible temperature_sensors -i deployment/inventory.ini -a "df -h"

# Check Python version
ansible temperature_sensors -i deployment/inventory.ini -a "/home/pi/.local/bin/uv --version"
```

## Configuration Precedence

1. **Server Config** (`server/config_server.ini`): Source of truth for sensor URLs and names
2. **Ansible Template** (`templates/config.ini.j2`): Default sensor settings (intervals, thresholds)
3. **Generated Config** (`config.ini` on each Pi): Auto-generated from above

To change sensor-specific settings (e.g., faster polling for perfusion sensor), edit the template or add host-specific variables.
