A Raspberry Pi temperature and humidity monitoring system with a real-time web dashboard. Reads data from a DHT22 sensor, logs to HDF5 files, and displays live graphs with configurable time windows and moving averages.

## Quick Start (Raspberry Pi)

### 1. Clone and Install
```bash
git clone <your-repo-url>
cd temp_sensor
uv sync --extra pi
```

### 2. Configure Device (Optional)
Edit the device name and logging interval in the dashboard UI after starting, or modify `config.ini` before running:
- `loginterval_s = 10` - Sensor read interval (10 seconds recommended for Pi Zero 2 W)
- Reduce to `5` if you need more frequent updates
- **Avoid going below 5 seconds on Pi Zero 2 W** to prevent performance issues

### 3. Run the Dashboard
```bash
uv run bokeh serve --show tempsens/dashboard/bokeh_app.py
```

The dashboard will automatically start temperature logging in the background.

### 4. Access the Dashboard
- **Local**: `http://localhost:5006`
- **Remote**: `http://<pi-ip-address>:5006`
- Set your device name and view the IP address in the "Device Information" section at the top

## Features

- **Real-time monitoring**: Live temperature and humidity graphs with moving averages
- **Device identification**: Set custom device names (e.g., "Room 397", "Turtle Tank") and view IP address
- **Flexible time windows**: View last 10 minutes to 1 week, or all historical data
- **Smart y-axis**: Auto-centers on current values with configurable deadbands to reduce visual jitter
- **Data export**: Download CSV files with device name included in filename and data
- **Cross-platform development**: Runs with simulated data on WSL/local for testing

### Dashboard Controls

- **Time Window**: Preset buttons (10 min, 1 hour, 3 hours, etc.) or custom intervals
- **Moving Average**: Adjust smoothing window (default: 10 samples)
- **Display Range**: Configure temperature/humidity window widths that persist across sessions
- **Raw Data Toggle**: Show/hide raw sensor readings alongside moving averages
- Temperature/humidity y-ranges are driven by configurable window widths: defaults come from `config.ini`, live tweaks persist to `settings.ini`
- Manual y-axis zoom updates the window controls; adjust the spinners to recentre on the moving-average values
- The axes auto recentre at most every `temperature_range_update_s` / `humidity_range_update_s` seconds (set in `config.ini`) with a small built-in deadband to avoid jitter

## Performance Notes

- **Pi Zero 2 W**: Works well with 10-second logging intervals (default)
- **Pi 3/4/5**: Can handle faster intervals (5 seconds or less) with ease
- Dashboard uses Bokeh server for rich interactivity - uses more resources than static plots but provides real-time updates
