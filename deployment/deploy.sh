#!/bin/bash
# Convenience script for deploying to temperature sensors
#
# Usage:
#   ./deployment/deploy.sh              # Deploy to all sensors
#   ./deployment/deploy.sh pi-room-307  # Deploy to specific sensor
#   ./deployment/deploy.sh --check      # Dry run (check mode)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INVENTORY="$SCRIPT_DIR/inventory.ini"
PLAYBOOK="$SCRIPT_DIR/deploy.yml"

# Check if Ansible is installed
if ! command -v ansible-playbook &> /dev/null; then
    echo "Error: ansible-playbook not found"
    echo "Install with: pip install ansible"
    exit 1
fi

# Generate fresh inventory from server config
echo "==> Generating Ansible inventory from server/config_server.ini..."
python3 "$SCRIPT_DIR/generate_ansible_inventory.py"

# Parse arguments
EXTRA_ARGS=()
if [[ $# -gt 0 ]]; then
    case "$1" in
        --check)
            EXTRA_ARGS+=(--check --diff)
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS] [SENSOR]"
            echo ""
            echo "Options:"
            echo "  --check    Dry run (show what would change)"
            echo "  --help     Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                 Deploy to all sensors"
            echo "  $0 pi-room-307     Deploy to specific sensor"
            echo "  $0 --check         Show what would change"
            exit 0
            ;;
        *)
            EXTRA_ARGS+=(--limit "$1")
            ;;
    esac
fi

# Run Ansible playbook
echo "==> Running Ansible playbook..."
ansible-playbook -i "$INVENTORY" "$PLAYBOOK" "${EXTRA_ARGS[@]}"

echo "==> Deployment complete!"
