#!/bin/bash
# Install temperature sensor systemd service
# Auto-detects username and paths

set -e  # Exit on error

# Detect current user and home directory
CURRENT_USER=$(whoami)
HOME_DIR=$(eval echo ~$CURRENT_USER)
REPO_DIR="$HOME_DIR/temp_sensor"

echo "================================="
echo "Temperature Sensor Service Installer"
echo "================================="
echo ""
echo "Detected configuration:"
echo "  User: $CURRENT_USER"
echo "  Home: $HOME_DIR"
echo "  Repository: $REPO_DIR"
echo ""

# Check if we're in the right directory
if [ ! -f "$REPO_DIR/run_client.py" ]; then
    echo "ERROR: Repository not found at $REPO_DIR"
    echo "Please clone the repository to $REPO_DIR first"
    exit 1
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv not found in PATH"
    echo "Please install uv first: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

UV_PATH=$(which uv)
echo "  uv found: $UV_PATH"
echo ""

# Generate service file
SERVICE_FILE="/tmp/tempsens.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Temperature Sensor Client (DHT22)
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$REPO_DIR
Environment="PATH=$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Use uv to run the client with proper virtual environment
ExecStart=$UV_PATH run python run_client.py

# Restart on failure (sensor read errors, network issues, etc.)
Restart=on-failure
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tempsens

[Install]
WantedBy=multi-user.target
EOF

echo "Generated service file:"
echo "---"
cat "$SERVICE_FILE"
echo "---"
echo ""

# Install service (requires sudo)
echo "Installing service (requires sudo)..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/tempsens.service
sudo systemctl daemon-reload

echo "✓ Service installed"
echo ""

# Ask to enable and start
read -p "Enable service to start on boot? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl enable tempsens.service
    echo "✓ Service enabled (will start on boot)"
fi

echo ""
read -p "Start service now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo systemctl start tempsens.service
    echo "✓ Service started"
    echo ""
    echo "Checking status..."
    sleep 2
    sudo systemctl status tempsens.service --no-pager
fi

echo ""
echo "================================="
echo "Installation complete!"
echo "================================="
echo ""
echo "Useful commands:"
echo "  sudo systemctl status tempsens.service    # Check status"
echo "  sudo journalctl -u tempsens.service -f    # View live logs"
echo "  sudo systemctl restart tempsens.service   # Restart service"
echo "  sudo systemctl stop tempsens.service      # Stop service"
echo ""
