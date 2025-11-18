# Install Temperature Dashboard as Windows Service
# Requires administrator privileges

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Temperature Dashboard Service Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$ServiceName = "TempDashboard"
$ServiceDisplayName = "Temperature Sensor Dashboard"
$ServiceDescription = "Multi-sensor temperature and humidity monitoring dashboard"
$RepoPath = $PSScriptRoot  # Directory where this script is located
$Port = 5006
$Address = "0.0.0.0"  # Listen on all interfaces

Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Repository: $RepoPath"
Write-Host "  Service Name: $ServiceName"
Write-Host "  Port: $Port"
Write-Host "  Address: $Address"
Write-Host ""

# Check if running as administrator
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: This script must be run as Administrator" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Red
    exit 1
}

# Check if repo exists
if (-not (Test-Path "$RepoPath\server\bokeh_app.py")) {
    Write-Host "ERROR: Dashboard app not found at $RepoPath\server\bokeh_app.py" -ForegroundColor Red
    exit 1
}

# Check if uv is installed
try {
    $uvVersion = uv --version
    Write-Host "✓ Found uv: $uvVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: uv not found. Please install uv first:" -ForegroundColor Red
    Write-Host "  powershell -c 'irm https://astral.sh/uv/install.ps1 | iex'" -ForegroundColor Yellow
    exit 1
}

# Check if NSSM is installed
$nssmPath = $null
$nssmLocations = @(
    "$env:ProgramFiles\nssm\nssm.exe",
    "$env:ProgramFiles\nssm\win64\nssm.exe",
    "${env:ProgramFiles(x86)}\nssm\nssm.exe",
    "${env:ProgramFiles(x86)}\nssm\win64\nssm.exe",
    "$env:LOCALAPPDATA\nssm\nssm.exe",
    "C:\nssm\nssm.exe"
)

foreach ($location in $nssmLocations) {
    if (Test-Path $location) {
        $nssmPath = $location
        break
    }
}

if (-not $nssmPath) {
    Write-Host "NSSM (Non-Sucking Service Manager) not found." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Would you like to download and install NSSM? (Required)" -ForegroundColor Yellow
    $response = Read-Host "Download NSSM? (y/n)"

    if ($response -eq 'y' -or $response -eq 'Y') {
        Write-Host "Downloading NSSM..." -ForegroundColor Cyan

        $nssmZip = "$env:TEMP\nssm.zip"
        $nssmExtract = "$env:TEMP\nssm"
        $nssmInstall = "$env:ProgramFiles\nssm"

        try {
            # Download NSSM
            Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $nssmZip -ErrorAction Stop

            # Extract
            Expand-Archive -Path $nssmZip -DestinationPath $nssmExtract -Force -ErrorAction Stop

            # Install (copy to Program Files)
            New-Item -ItemType Directory -Path $nssmInstall -Force -ErrorAction Stop | Out-Null
            Copy-Item "$nssmExtract\nssm-2.24\win64\nssm.exe" -Destination "$nssmInstall\nssm.exe" -Force -ErrorAction Stop

            $nssmPath = "$nssmInstall\nssm.exe"

            Write-Host "✓ NSSM installed to $nssmPath" -ForegroundColor Green

            # Cleanup
            Remove-Item $nssmZip -Force -ErrorAction SilentlyContinue
            Remove-Item $nssmExtract -Recurse -Force -ErrorAction SilentlyContinue
        }
        catch {
            Write-Host "ERROR: Failed to download/install NSSM: $_" -ForegroundColor Red
            Write-Host "Please download manually from: https://nssm.cc/download" -ForegroundColor Yellow
            exit 1
        }
    }
    else {
        Write-Host "ERROR: NSSM is required to install the service" -ForegroundColor Red
        Write-Host "Download from: https://nssm.cc/download" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "✓ Found NSSM: $nssmPath" -ForegroundColor Green
Write-Host ""

# Check if service already exists
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Service '$ServiceName' already exists." -ForegroundColor Yellow
    $response = Read-Host "Remove existing service and reinstall? (y/n)"

    if ($response -eq 'y' -or $response -eq 'Y') {
        Write-Host "Stopping service..." -ForegroundColor Cyan
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2

        Write-Host "Removing service..." -ForegroundColor Cyan
        & $nssmPath remove $ServiceName confirm
        Start-Sleep -Seconds 2
    }
    else {
        Write-Host "Installation cancelled." -ForegroundColor Yellow
        exit 0
    }
}

# Get server IP for allow-websocket-origin
$serverIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*" -and $_.IPAddress -notlike "169.254.*"} | Select-Object -First 1).IPAddress
if (-not $serverIp) {
    $serverIp = "localhost"
}

# Build bokeh command
$uvPath = (Get-Command uv).Source
$bokehArgs = @(
    "run",
    "python", "-m", "bokeh", "serve",
    "server/bokeh_app.py",
    "--port", $Port,
    "--address", $Address,
    "--allow-websocket-origin=${serverIp}:80",
    "--allow-websocket-origin=${serverIp}:${Port}",
    "--allow-websocket-origin=localhost:${Port}",
    "--session-token-expiration", "360000000"
)

Write-Host "Installing service..." -ForegroundColor Cyan
Write-Host "  Command: $uvPath $($bokehArgs -join ' ')" -ForegroundColor Gray

# Install service
& $nssmPath install $ServiceName $uvPath $bokehArgs

# Configure service
& $nssmPath set $ServiceName AppDirectory $RepoPath
& $nssmPath set $ServiceName DisplayName $ServiceDisplayName
& $nssmPath set $ServiceName Description $ServiceDescription
& $nssmPath set $ServiceName Start SERVICE_AUTO_START
& $nssmPath set $ServiceName AppStdout "$RepoPath\logs\dashboard.log"
& $nssmPath set $ServiceName AppStderr "$RepoPath\logs\dashboard-error.log"
& $nssmPath set $ServiceName AppRotateFiles 1
& $nssmPath set $ServiceName AppRotateBytes 10485760  # 10MB

# Create logs directory
New-Item -ItemType Directory -Path "$RepoPath\logs" -Force | Out-Null

Write-Host "✓ Service installed" -ForegroundColor Green
Write-Host ""

# Start service
$response = Read-Host "Start service now? (y/n)"
if ($response -eq 'y' -or $response -eq 'Y') {
    Write-Host "Starting service..." -ForegroundColor Cyan
    Start-Service -Name $ServiceName
    Start-Sleep -Seconds 3

    $service = Get-Service -Name $ServiceName
    if ($service.Status -eq 'Running') {
        Write-Host "✓ Service started successfully" -ForegroundColor Green
        Write-Host ""
        Write-Host "Dashboard available at:" -ForegroundColor Cyan
        Write-Host "  http://localhost:${Port}" -ForegroundColor Yellow
        Write-Host "  http://${serverIp}:${Port}" -ForegroundColor Yellow
    }
    else {
        Write-Host "✗ Service failed to start. Check logs at:" -ForegroundColor Red
        Write-Host "  $RepoPath\logs\dashboard-error.log" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Installation Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service Management Commands:" -ForegroundColor Yellow
Write-Host "  Start-Service -Name $ServiceName" -ForegroundColor Gray
Write-Host "  Stop-Service -Name $ServiceName" -ForegroundColor Gray
Write-Host "  Restart-Service -Name $ServiceName" -ForegroundColor Gray
Write-Host "  Get-Service -Name $ServiceName" -ForegroundColor Gray
Write-Host ""
Write-Host "View Logs:" -ForegroundColor Yellow
Write-Host "  Get-Content $RepoPath\logs\dashboard.log -Tail 50 -Wait" -ForegroundColor Gray
Write-Host ""
Write-Host "Uninstall Service:" -ForegroundColor Yellow
Write-Host "  Stop-Service -Name $ServiceName" -ForegroundColor Gray
Write-Host ('  & "' + $nssmPath + '" remove ' + $ServiceName + ' confirm') -ForegroundColor Gray
Write-Host ""
