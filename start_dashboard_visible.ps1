# Start Temperature Dashboard in visible console window
# Use this for debugging or when you want to see live logs

$RepoPath = $PSScriptRoot

Write-Host "Starting Temperature Dashboard..." -ForegroundColor Cyan
Write-Host "Repository: $RepoPath" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

Set-Location $RepoPath

# Start with visible output
uv run python -m bokeh serve server/bokeh_app.py `
    --port 8000 `
    --address 127.0.0.1 `
    --session-token-expiration 360000000 `
    --allow-websocket-origin=localhost:8000 `
    --allow-websocket-origin=139.184.163.16:80
