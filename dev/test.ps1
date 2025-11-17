<#
.SYNOPSIS
    Quick test environment launcher for temperature sensor dashboard

.DESCRIPTION
    Sets up a complete test environment with simulated sensors and historical data.

    This script:
    - Cleans test databases for fresh state
    - Generates historical sensor data (configurable days)
    - Starts 3 simulated sensor clients
    - Starts Bokeh dashboard server
    - Opens browser to dashboard

.PARAMETER Days
    Number of days of historical data to generate (default: 7)
    Each day generates ~43,000 data points per sensor at 2-second intervals

.PARAMETER NoClean
    Keep existing server database cache (faster restart, skips backfill)

.PARAMETER NoBrowser
    Don't automatically open browser

.PARAMETER Port
    Dashboard server port (default: 5006)

.EXAMPLE
    .\dev\test.ps1
    # Full test with 7 days of historical data

.EXAMPLE
    .\dev\test.ps1 1
    # Quick test with 1 day (positional parameter)

.EXAMPLE
    .\dev\test.ps1 -Days 3
    # 3 days of data (named parameter)

.EXAMPLE
    .\dev\test.ps1 -NoClean
    # Keep server cache, skip backfill

.EXAMPLE
    .\dev\test.ps1 3 -NoClean -NoBrowser
    # 3 days, keep cache, no browser

.NOTES
    Requires: uv package manager and local-dev dependencies
    Install: uv sync --group local-dev
#>

param(
    [int]$Days = 7,
    [switch]$NoClean,
    [switch]$NoBrowser,
    [int]$Port = 5006
)

Write-Host ""
Write-Host "==================================================================================" -ForegroundColor Cyan
Write-Host "  TEMPERATURE SENSOR TEST ENVIRONMENT" -ForegroundColor Cyan
Write-Host "==================================================================================" -ForegroundColor Cyan
Write-Host ""

# Build command
$cmd = "uv run python dev/run_full_test_environment.py --days $Days --port $Port"

if ($NoClean) {
    $cmd += " --no-clean-server"
}

if ($NoBrowser) {
    $cmd += " --no-browser"
}

Write-Host "Running: $cmd" -ForegroundColor Yellow
Write-Host ""

# Execute
Invoke-Expression $cmd
