#!/bin/bash
# Deploy to both clients (Raspberry Pis) and server (dashboard)
#
# Usage:
#   ./deployment/deploy_all.sh           # Deploy to all Pis and server
#   ./deployment/deploy_all.sh --clients # Deploy to Pis only
#   ./deployment/deploy_all.sh --server  # Deploy to server only

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEPLOY_CLIENTS=true
DEPLOY_SERVER=true

# Parse arguments
for arg in "$@"; do
    case $arg in
        --clients)
            DEPLOY_SERVER=false
            ;;
        --server)
            DEPLOY_CLIENTS=false
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --clients    Deploy to Raspberry Pi clients only"
            echo "  --server     Deploy to dashboard server only"
            echo "  (no args)    Deploy to both clients and server"
            echo ""
            echo "Examples:"
            echo "  $0                   # Deploy everything"
            echo "  $0 --clients         # Deploy to Pis only"
            echo "  $0 --server          # Deploy to server only"
            exit 0
            ;;
    esac
done

if [[ "$DEPLOY_CLIENTS" == true ]]; then
    echo "========================================"
    echo "Deploying to Raspberry Pi clients..."
    echo "========================================"
    "$SCRIPT_DIR/deploy.sh"
fi

if [[ "$DEPLOY_SERVER" == true ]]; then
    echo ""
    echo "========================================"
    echo "Deploying to dashboard server..."
    echo "========================================"
    "$SCRIPT_DIR/deploy_server.sh"
fi

echo ""
echo "========================================"
echo "Deployment complete!"
echo "========================================"
