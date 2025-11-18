#Requires -RunAsAdministrator

# Install Temperature Dashboard as Windows Scheduled Task
# Much simpler than NSSM - uses built-in Task Scheduler

$TaskName = "TempDashboard"
$RepoPath = $PSScriptRoot
$LogPath = "$RepoPath\logs"
$Port = 8000
$Address = "127.0.0.1"

Write-Host "Installing Temperature Dashboard as Scheduled Task..." -ForegroundColor Cyan
Write-Host "Repository: $RepoPath" -ForegroundColor Yellow
Write-Host "Port: $Port" -ForegroundColor Yellow
Write-Host ""

# Create logs directory
New-Item -ItemType Directory -Path $LogPath -Force | Out-Null

# Find uv executable
try {
    $uvPath = (Get-Command uv).Source
    Write-Host "Found uv: $uvPath" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: uv not found. Install with:" -ForegroundColor Red
    Write-Host "  powershell -c 'irm https://astral.sh/uv/install.ps1 | iex'" -ForegroundColor Yellow
    exit 1
}

# Detect server IP
$ServerIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*" -and $_.IPAddress -notlike "169.254.*"} | Select-Object -First 1).IPAddress
if (-not $ServerIP) {
    $ServerIP = "localhost"
}
Write-Host "Server IP: $ServerIP" -ForegroundColor Green

# Create startup script
$StartScript = @"
Set-Location "$RepoPath"
`$env:PYTHONUNBUFFERED = "1"
& "$uvPath" run python -m bokeh serve server/bokeh_app.py ``
    --port $Port ``
    --address $Address ``
    --session-token-expiration 360000000 ``
    --allow-websocket-origin=localhost:$Port ``
    --allow-websocket-origin=${ServerIP}:80 ``
    *>> "$LogPath\dashboard.log"
"@

$StartScriptPath = "$RepoPath\start_dashboard.ps1"
$StartScript | Out-File -FilePath $StartScriptPath -Encoding UTF8

Write-Host "Created startup script: $StartScriptPath" -ForegroundColor Green

# Remove existing task if it exists
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create scheduled task
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScriptPath`""
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Temperature Sensor Dashboard - Multi-sensor monitoring" | Out-Null

Write-Host "Task registered successfully!" -ForegroundColor Green
Write-Host ""

# Start task now
$response = Read-Host "Start dashboard now? (y/n)"
if ($response -eq 'y' -or $response -eq 'Y') {
    Write-Host "Starting dashboard..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    Write-Host "Dashboard started! Open: http://localhost:$Port" -ForegroundColor Green
    Write-Host "With port forwarding: http://${ServerIP}" -ForegroundColor Green
}

Write-Host ""
Write-Host "Installation Complete!" -ForegroundColor Cyan
Write-Host ""
Write-Host "Management Commands:" -ForegroundColor Yellow
Write-Host "  Start:   Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Stop:    Stop-ScheduledTask -TaskName $TaskName"
Write-Host "  Status:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "  Logs:    Get-Content $LogPath\dashboard.log -Tail 50 -Wait"
Write-Host "  Remove:  Unregister-ScheduledTask -TaskName $TaskName"
Write-Host ""
