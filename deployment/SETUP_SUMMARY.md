# Deployment System Setup Summary

## What Was Created

This deployment system allows you to push code updates from your dev machine to both Raspberry Pi clients and the lab server using Ansible.

### File Structure

```
deployment/
├── README.md                           # Technical reference
├── WORKFLOW.md                         # Daily workflow guide ⭐ READ THIS FIRST
├── NEW_SENSOR_SETUP.md                 # Checklist for adding new sensors
├── SETUP_SUMMARY.md                    # This file
│
├── config_deployment.ini.template      # Template (tracked in git)
├── config_deployment.ini               # YOUR CONFIG (gitignored) ⭐
│
├── setup_deployment.py                 # One-time setup script
├── sync_configs.py                     # Sync deployment config → server config
├── generate_ansible_inventory.py       # Generate Ansible inventory
├── deploy.sh                           # Main deployment script
├── deploy.yml                          # Ansible playbook
│
└── templates/
    ├── config.ini.j2                   # Client config template (for Pis)
    └── tempsensor.service.j2           # Systemd service template

config.ini.template                     # Client config template (tracked)
config.ini                              # Local client config (gitignored)

server/
├── config_server.ini.template          # Server config template (tracked)
└── config_server.ini                   # Auto-generated (gitignored)
```

### What's Tracked in Git vs Gitignored

**Tracked (safe to commit):**
- ✅ All `.template` files
- ✅ All Python/YAML/shell scripts
- ✅ Documentation (README, WORKFLOW, etc.)

**Gitignored (contains sensitive IPs):**
- ❌ `deployment/config_deployment.ini` - YOUR MAIN CONFIG
- ❌ `server/config_server.ini` - Auto-generated from deployment config
- ❌ `config.ini` - Local client config for testing
- ❌ `deployment/inventory.ini` - Auto-generated Ansible inventory

## How It Works

### Single Source of Truth

You edit **one file** with all your sensor IPs:

```
deployment/config_deployment.ini  (YOU EDIT THIS)
```

Everything else is auto-generated from this:

```
deployment/config_deployment.ini
    │
    ├─> sync_configs.py
    │   └─> server/config_server.ini
    │       └─> Used by dashboard server
    │
    └─> generate_ansible_inventory.py
        └─> deployment/inventory.ini
            └─> Used by Ansible deployments
```

### Deployment Flow

```
1. Edit code on dev machine
2. git commit && git push
3. ./deployment/deploy.sh
   │
   ├─> Generates Ansible inventory from your deployment config
   ├─> Ansible connects to each Pi via SSH
   ├─> Each Pi pulls latest code from GitHub
   ├─> Dependencies updated with uv
   ├─> Services restarted
   └─> Health checks verify everything works
```

## Quick Reference

### First-Time Setup (Run Once)

```bash
# 1. Install Ansible
pip install ansible

# 2. Set up SSH keys to all Pis
ssh-copy-id pi@100.64.0.5
ssh-copy-id pi@100.64.0.6
# ... etc

# 3. Initialize config files
python deployment/setup_deployment.py

# 4. Edit deployment config
# Open deployment/config_deployment.ini in your editor
# - Add your git repo URL
# - Add all sensor IPs (Headscale addresses)

# 5. Sync to server config
python deployment/sync_configs.py
```

### Daily Workflow

```bash
# Make changes, commit, deploy
git add .
git commit -m "Your changes"
git push
./deployment/deploy.sh
```

### Adding a New Sensor

```bash
# 1. Do initial Pi setup (manual)
ssh pi@100.64.0.9
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> ~/temp_sensor

# 2. Add to deployment config (on dev machine)
# Edit deployment/config_deployment.ini:
#   New_Sensor = http://100.64.0.9:5000, 30

# 3. Deploy
python deployment/sync_configs.py
./deployment/deploy.sh pi-new-sensor
```

## Key Benefits

### 1. No Config Duplication
- Sensor IPs defined once in `deployment/config_deployment.ini`
- All other configs auto-generated from this

### 2. Security
- Real IPs never committed to git (gitignored)
- Templates in git are safe to share publicly
- Each developer has their own local deployment config

### 3. Easy Multi-Sensor Deployment
```bash
# Update all sensors with one command
./deployment/deploy.sh

# Or specific sensors
./deployment/deploy.sh pi-room-307
```

### 4. Automated Everything
- Pulls latest code from git
- Updates dependencies
- Generates correct per-sensor configs
- Restarts services
- Verifies health
- All in ~30 seconds per sensor

## Troubleshooting

### "No production sensors found"

Your `deployment/config_deployment.ini` only has localhost test entries.

**Fix:** Edit the file and add real Headscale IPs:
```ini
[SENSORS]
Room_307 = http://100.64.0.5:5000, 60
```

### "Permission denied (publickey)"

SSH keys not set up to that Pi.

**Fix:**
```bash
ssh-copy-id pi@100.64.0.x
```

### "Could not read deployment/config_deployment.ini"

Config file doesn't exist.

**Fix:**
```bash
python deployment/setup_deployment.py
# Then edit deployment/config_deployment.ini
```

### Server config out of sync

You edited `deployment/config_deployment.ini` but forgot to sync.

**Fix:**
```bash
python deployment/sync_configs.py
```

### Changes not deploying to Pis

1. Did you push to GitHub? `git push origin main`
2. Did deployment succeed? Check Ansible output for errors
3. Is the Pi actually running your code? SSH and check:
   ```bash
   ssh pi@100.64.0.x
   cd ~/temp_sensor
   git log -1  # Should show your latest commit
   ```

## Next Steps

1. **Read [WORKFLOW.md](WORKFLOW.md)** for detailed workflow guide
2. **Configure your deployment:** Edit `deployment/config_deployment.ini`
3. **Test deployment:** Run `./deployment/deploy.sh --check` (dry run)
4. **Deploy for real:** Run `./deployment/deploy.sh`

## Environment-Specific Configs (Advanced)

If you need separate dev/staging/prod environments:

```bash
# Create environment-specific configs
cp deployment/config_deployment.ini deployment/config_prod.ini
cp deployment/config_deployment.ini deployment/config_dev.ini

# Edit each with different sensor IPs

# Deploy to specific environment
CONFIG=deployment/config_prod.ini python deployment/generate_ansible_inventory.py
./deployment/deploy.sh
```

All environment configs are gitignored, so they stay local.

## Support

Questions? Check these docs:
- [WORKFLOW.md](WORKFLOW.md) - Detailed workflows
- [README.md](README.md) - Command reference
- [NEW_SENSOR_SETUP.md](NEW_SENSOR_SETUP.md) - Adding sensors

Issues? File a bug report or check git history for this deployment system.
