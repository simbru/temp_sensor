#!/bin/bash
# Deploy to lab server (dashboard)
#
# Usage:
#   ./deployment/deploy_server.sh          # Deploy to server configured in deployment config
#   ./deployment/deploy_server.sh --local  # Deploy locally (for testing)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOYMENT_CONFIG="$SCRIPT_DIR/config_deployment.ini"

# Parse command line args
LOCAL_MODE=false
if [[ "$1" == "--local" ]]; then
    LOCAL_MODE=true
fi

# Function to read config values
read_config() {
    local section=$1
    local key=$2
    python3 -c "import configparser; c=configparser.ConfigParser(); c.read('$DEPLOYMENT_CONFIG'); print(c.get('$section', '$key', fallback=''))"
}

if [[ "$LOCAL_MODE" == true ]]; then
    echo "==> Deploying to local server (current machine)"

    # Sync configs
    echo "==> Syncing deployment config to server config..."
    python3 "$SCRIPT_DIR/sync_configs.py"

    echo "==> Server config updated at: server/config_server.ini"
    echo ""
    echo "To start the dashboard:"
    echo "  uv run bokeh serve server/bokeh_app.py --port 8000"

else
    # Remote deployment
    echo "==> Reading deployment config..."

    SERVER_HOST=$(read_config SERVER server_host)
    SERVER_USER=$(read_config SERVER server_user)
    SERVER_PATH=$(read_config SERVER server_path)

    if [[ -z "$SERVER_HOST" || -z "$SERVER_USER" || -z "$SERVER_PATH" ]]; then
        echo "Error: Server deployment info not configured"
        echo ""
        echo "Edit deployment/config_deployment.ini and set:"
        echo "  [SERVER]"
        echo "  server_host = 139.184.163.16"
        echo "  server_user = your_username"
        echo "  server_path = /path/to/temp_sensor"
        echo ""
        echo "Or use --local to deploy to current machine:"
        echo "  ./deployment/deploy_server.sh --local"
        exit 1
    fi

    echo "==> Deploying to server: $SERVER_USER@$SERVER_HOST:$SERVER_PATH"

    # Sync configs locally first
    echo "==> Syncing deployment config to server config..."
    python3 "$SCRIPT_DIR/sync_configs.py"

    # Copy server config to remote
    echo "==> Copying server config to remote server..."
    scp "$REPO_ROOT/server/config_server.ini" "$SERVER_USER@$SERVER_HOST:$SERVER_PATH/server/"

    # SSH to server and update
    echo "==> Updating code on server..."
    ssh "$SERVER_USER@$SERVER_HOST" << EOF
        cd $SERVER_PATH
        git pull
        uv sync

        echo "==> Server updated successfully"
        echo ""
        echo "To restart the dashboard:"
        echo "  pkill -f bokeh"
        echo "  uv run bokeh serve server/bokeh_app.py --port 8000 &"
EOF

    echo "==> Server deployment complete!"
fi
