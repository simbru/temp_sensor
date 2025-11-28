# Deployment Workflow Guide

This guide explains how to push code updates from your dev machine to both clients (Raspberry Pis) and server (lab server).

## Architecture Overview

```
┌─────────────────────┐
│   Dev Machine       │
│  (Windows/WSL)      │
│                     │
│  - Edit code        │
│  - Commit & push    │
│  - Run Ansible      │
└──────┬──────────────┘
       │
       │ git push
       ▼
┌─────────────────────┐
│   Git Repository    │
│   (GitHub/GitLab)   │
└──────┬──────────────┘
       │
       │ Ansible pulls from git
       │
       ├─────────────────┬─────────────────┬──────────────────┐
       ▼                 ▼                 ▼                  ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ Pi Client 1 │   │ Pi Client 2 │   │ Pi Client 3 │   │ Lab Server  │
│ Room 307    │   │ Perfusion   │   │ Incubator   │   │ (Dashboard) │
└─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
```

## Config File Strategy

### What's Tracked in Git

✅ **Templates** (tracked, safe to commit):
- `config.ini.template` - Client config template
- `server/config_server.ini.template` - Server config template
- `deployment/config_deployment.ini.template` - Deployment config template

✅ **Code and automation**:
- All Python/YAML files
- Ansible playbooks
- Deployment scripts

### What's Git-Ignored (Local Only)

❌ **Real configs** (gitignored, contain sensitive IPs):
- `config.ini` - Local client config (for dev testing)
- `server/config_server.ini` - Server config (auto-generated from deployment config)
- `deployment/config_deployment.ini` - **YOUR MAIN CONFIG** with real sensor IPs

❌ **Auto-generated files**:
- `deployment/inventory.ini` - Ansible inventory (generated from deployment config)

## One-Time Setup

### 1. Install Prerequisites

```bash
# On your dev machine
pip install ansible

# Set up SSH keys (do for each Pi)
ssh-copy-id pi@100.64.0.5
ssh-copy-id pi@100.64.0.6
# ... etc
```

### 2. Initialize Deployment Config

```bash
# Generate config files from templates
python deployment/setup_deployment.py
```

This creates:
- `deployment/config_deployment.ini` (gitignored)
- `server/config_server.ini` (gitignored)
- `config.ini` (gitignored, for local testing)

### 3. Edit Your Deployment Config

Edit `deployment/config_deployment.ini`:

```ini
[DEPLOYMENT]
git_repo = https://github.com/yourusername/temp_sensor.git  # YOUR REPO
git_version = main
ansible_user = pi

[SENSORS]
# Your actual production sensors with Headscale IPs
Room_307 = http://100.64.0.5:5000, 60
Perfusion_Sensor = http://100.64.0.6:5000, 1
Lab_Bench = http://100.64.0.7:5000, 60

[SERVER]
# Server connection (for remote deployment)
# Leave blank to deploy manually (git pull on server)
server_host = 139.184.163.16
server_user = your_username
server_path = /home/username/temp_sensor

# Dashboard settings
poll_interval_s = 2
dashboard_port = 8000
# ... other server settings
```

**Important:** This file stays local and is never committed!

**Note on server deployment:**
- If you fill in `server_host`, `server_user`, and `server_path`, you can deploy to the server remotely
- If you leave them blank, you'll need to manually update the server (SSH in and run `git pull`)

### 4. Sync Configs

```bash
# Copy sensor list from deployment config → server config
python deployment/sync_configs.py
```

This ensures `server/config_server.ini` matches your `deployment/config_deployment.ini`.

## Daily Workflow

### Making Code Changes

```bash
# 1. Edit code on dev machine
vim tempsens/sensor.py

# 2. Commit changes
git add .
git commit -m "Fix sensor reading bug"

# 3. Push to GitHub/GitLab
git push origin main

# 4. Deploy to everything (Pis + server)
python deployment/deploy_all.py

# Or deploy separately:
./deployment/deploy.sh              # Pis only
python deployment/deploy_server.py  # Server only
```

**Deploy to all** (`deploy_all.py`) will:
1. **Clients:** Generate Ansible inventory, pull code on all Pis, update deps, restart services
2. **Server:** Sync config, pull code on server, update dependencies

### Deploy to Specific Targets

```bash
# Only deploy to clients (Raspberry Pis)
python deployment/deploy_all.py --clients
# Or: ./deployment/deploy.sh

# Only deploy to server
python deployment/deploy_all.py --server
# Or: python deployment/deploy_server.py

# Deploy to specific Pi
./deployment/deploy.sh pi-room-307

# Deploy to multiple specific Pis
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml \
  --limit pi-room-307,pi-perfusion-sensor
```

### Manual Server Deployment

If you prefer to deploy to the server manually (without configuring SSH in deployment config):

```bash
# SSH to lab server
ssh user@139.184.163.16

# Pull latest code
cd ~/temp_sensor
git pull

# Sync server config (copies sensor list from deployment config)
python deployment/sync_configs.py

# Restart dashboard
pkill -f bokeh
uv run bokeh serve server/bokeh_app.py --port 8000 &
```

## Adding a New Sensor

### Step 1: Physical Setup (One-Time)

```bash
# SSH to new Pi
ssh pi@100.64.0.9

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone repo
git clone https://github.com/yourusername/temp_sensor.git ~/temp_sensor
cd ~/temp_sensor

# Test sensor hardware
~/.local/bin/uv sync --extra pi
~/.local/bin/uv run python -c "from tempsens.io_funcs import log_data; print(log_data())"
```

### Step 2: Add to Deployment Config

On your dev machine, edit `deployment/config_deployment.ini`:

```ini
[SENSORS]
# ... existing sensors ...
New_Lab_Bench = http://100.64.0.9:5000, 30
```

### Step 3: Deploy

```bash
# Sync deployment config → server config
python deployment/sync_configs.py

# Deploy to the new sensor
./deployment/deploy.sh pi-new-lab-bench

# Verify it's working
curl http://100.64.0.9:5000/status
```

Done! The new sensor is now:
- Running the latest code
- Set up as a systemd service
- Being polled by the dashboard

## Config Sync Workflow

Your deployment config is the **single source of truth**:

```
deployment/config_deployment.ini (YOU EDIT THIS)
    │
    ├─> python deployment/sync_configs.py
    │   └─> server/config_server.ini (auto-generated)
    │       └─> Used by dashboard server
    │
    └─> python deployment/generate_ansible_inventory.py
        └─> deployment/inventory.ini (auto-generated)
            └─> Used by Ansible to know which Pis to deploy to
```

**Rule of thumb:**
- **Editing sensor IPs/names?** → Edit `deployment/config_deployment.ini`, then run `sync_configs.py`
- **Deploying code?** → Just run `./deployment/deploy.sh` (it auto-generates inventory)

## Troubleshooting

### "No production sensors found"

You need to edit `deployment/config_deployment.ini` and add real sensor IPs (not localhost).

### "Permission denied (publickey)"

Set up SSH keys:
```bash
ssh-copy-id pi@100.64.0.x
```

### Changes not appearing on Pis

1. Did you commit and push? `git push origin main`
2. Did Ansible run successfully? Check for errors in output
3. Is the Pi actually pulling from git? SSH to Pi and check:
   ```bash
   cd ~/temp_sensor
   git status
   git log -1  # Should show your latest commit
   ```

### Server config out of sync

Run the sync script:
```bash
python deployment/sync_configs.py
```

This regenerates `server/config_server.ini` from your deployment config.

## Best Practices

### 1. Never Commit Real IPs

The `.gitignore` is configured to prevent this, but double-check:
```bash
git status
# Should NOT show config.ini, config_server.ini, or config_deployment.ini
```

### 2. Keep Templates Updated

If you change config structure, update the templates:
- `config.ini.template`
- `server/config_server.ini.template`
- `deployment/config_deployment.ini.template`

Then commit the templates to git.

### 3. Test Locally First

Before deploying to production Pis:
```bash
# Test with simulated sensors
uv run python run_client.py

# Verify API works
curl http://localhost:5000/status
```

### 4. Deploy During Low-Activity Times

Deployment restarts services, causing brief downtime (~5-10 seconds per sensor).

### 5. Use Git Tags for Releases

```bash
git tag -a v1.2.0 -m "Release 1.2.0: Added spike filtering"
git push origin v1.2.0

# Deploy specific version
ansible-playbook -i deployment/inventory.ini deployment/deploy.yml \
  -e "git_version=v1.2.0"
```

## Advanced: Multi-Environment Deployment

If you have dev/staging/production environments, create multiple deployment configs:

```
deployment/
  ├── config_deployment.ini.template
  ├── config_deployment_dev.ini      # Dev environment (gitignored)
  ├── config_deployment_staging.ini  # Staging (gitignored)
  └── config_deployment_prod.ini     # Production (gitignored)
```

Deploy to specific environment:
```bash
# Use different config for inventory generation
CONFIG=deployment/config_deployment_prod.ini python deployment/generate_ansible_inventory.py
./deployment/deploy.sh
```

## Summary

**To deploy updates:**
1. Edit code
2. `git commit && git push`
3. `./deployment/deploy.sh`

**To add a sensor:**
1. Initial Pi setup (manual)
2. Add to `deployment/config_deployment.ini`
3. Run `./deployment/deploy.sh pi-newsensor`

**To change sensor IPs:**
1. Edit `deployment/config_deployment.ini`
2. Run `python deployment/sync_configs.py`
3. Restart dashboard server

Your deployment config stays local, templates stay in git, everyone wins! 🎉
