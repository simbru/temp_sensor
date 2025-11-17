#!/usr/bin/env bash
#
# Quick test environment launcher for temperature sensor dashboard
#
# DESCRIPTION:
#   Sets up a complete test environment with simulated sensors and historical data.
#
#   This script:
#   - Cleans test databases for fresh state
#   - Generates historical sensor data (configurable days)
#   - Starts 3 simulated sensor clients
#   - Starts Bokeh dashboard server
#   - Opens browser to dashboard
#
# USAGE:
#   ./dev/test.sh              # Full test with 7 days of historical data
#   ./dev/test.sh --days 1     # Quick test with 1 day
#   ./dev/test.sh --no-clean   # Keep server cache (faster restart)
#   ./dev/test.sh --days 3 --no-clean --no-browser
#
# OPTIONS:
#   --days N          Number of days of historical data (default: 7)
#                     Each day = ~43,000 data points per sensor at 2s intervals
#   --no-clean        Keep existing server database cache (skips backfill)
#   --no-browser      Don't automatically open browser
#   --port N          Dashboard server port (default: 5006)
#   -h, --help        Show help message
#
# EXAMPLES:
#   ./dev/test.sh                        # Full test (7 days)
#   ./dev/test.sh --days 1               # Quick test (1 day)
#   ./dev/test.sh --no-clean             # Keep cache
#   ./dev/test.sh --days 3 --no-browser  # 3 days, no browser
#
# REQUIREMENTS:
#   - uv package manager
#   - Local dev dependencies: uv sync --group local-dev
#

set -e  # Exit on error

# Default values
DAYS=7
NO_CLEAN=false
NO_BROWSER=false
PORT=5006

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --days)
            DAYS="$2"
            shift 2
            ;;
        --no-clean)
            NO_CLEAN=true
            shift
            ;;
        --no-browser)
            NO_BROWSER=true
            shift
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --days N          Number of days of historical data (default: 7)"
            echo "  --no-clean        Keep existing server cache"
            echo "  --no-browser      Don't open browser automatically"
            echo "  --port N          Dashboard port (default: 5006)"
            echo "  -h, --help        Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                    # Full test with 7 days"
            echo "  $0 --days 1           # Quick test with 1 day"
            echo "  $0 --no-clean         # Keep server cache"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo ""
echo "=================================================================================="
echo "  TEMPERATURE SENSOR TEST ENVIRONMENT"
echo "=================================================================================="
echo ""

# Build command
CMD="uv run python dev/run_full_test_environment.py --days $DAYS --port $PORT"

if [ "$NO_CLEAN" = true ]; then
    CMD="$CMD --no-clean-server"
fi

if [ "$NO_BROWSER" = true ]; then
    CMD="$CMD --no-browser"
fi

echo "Running: $CMD"
echo ""

# Execute
$CMD
