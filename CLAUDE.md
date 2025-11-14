# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi temperature and humidity monitoring system that:
1. Reads data from a DHT22 sensor (via `adafruit_dht`)
2. Logs measurements to an HDF5 file (`templog.h5`)
3. Displays real-time data via a Bokeh server dashboard

The system is designed to run continuously on a Raspberry Pi but can also run in development mode (WSL/local) using simulated sensor data.

## Project Structure

```
tempsens/                    # Main package directory
├── __init__.py             # Package initialization
├── io_funcs.py             # Data I/O and sensor reading
├── sensor.py               # Background logging process
└── dashboard/              # Bokeh dashboard
    ├── __init__.py
    └── bokeh_app.py        # Bokeh Server entry point

config.ini                   # Configuration file
templog.h5                   # HDF5 data file (generated)
```

## Cross-Platform Development

**Critical**: The `adafruit_dht` package only builds on Raspberry Pi hardware. The codebase handles this with:

- **Optional dependency group**: `adafruit_dht` is in `[project.optional-dependencies]` under the `pi` group
- **Graceful import handling**: `tempsens/io_funcs.py:11-19` catches ImportError and sets `sensor_found = False`, falling back to `simulate_tempsens()`

**On development machines (WSL/local)**:
```bash
uv sync                             # Install dependencies (no hardware libs)
uv run python -m tempsens.sensor    # Run with simulated data
```

**On Raspberry Pi**:
```bash
uv sync --extra pi                  # Install dependencies including adafruit_dht
uv run python -m tempsens.sensor    # Run with real sensor
```

## Architecture

### Core Components

**`tempsens/io_funcs.py`**: Central data I/O module
- Configuration management (via `config.ini`)
- Sensor reading (real DHT22 or simulated data)
- HDF5 file operations with thread-safe locking
- Scheduled data logging using `sched` module
- Global `sensor_found` flag determines real vs simulated sensor

**`tempsens/sensor.py`**: Background logging process
- Runs continuous temperature/humidity logging loop
- Uses PID file (`tempsens_running.pid`) to prevent duplicate processes
- `run_tempsensor_test()`: Direct execution mode
- `tempsensor_subprocess()`: Spawns subprocess with PID tracking
- `end_tempsensor()`: Cleanup via `atexit` registration

**`tempsens/dashboard/bokeh_app.py`**: Interactive Bokeh dashboard
    - Launches sensor as subprocess on startup
    - Renders temperature and humidity plots with moving averages
    - Provides controls for window presets, custom ranges, smoothing, downloads

### Data Flow

1. `sensor.py` schedules `io_funcs.log_data()` at intervals
2. `log_data()` reads sensor (or simulates) and calls `write_data_hdf5()`
3. `write_data_hdf5()` appends to HDF5 file with file locking
4. Dashboard's `reactive.file_reader()` detects changes and calls `fetch_log_data()`
5. `fetch_log_data()` reads HDF5 and updates Bokeh `ColumnDataSource`

### Configuration

`config.ini` contains:
- `loginterval_s`: Seconds between sensor reads
- `outputfile`: Path to HDF5 data file
- Other parameters loaded via `fetch_config()`

## Common Commands

**Install dependencies (development)**:
```bash
uv sync
```

**Install dependencies (Raspberry Pi)**:
```bash
uv sync --extra pi
```

**Run temperature logging only**:
```bash
uv run python -m tempsens.sensor
```

**Run dashboard (launches logging automatically)**:
```bash
uv run bokeh serve --show tempsens/dashboard/bokeh_app.py
```
The dashboard will open at `http://localhost:5006`

## HDF5 File Locking

The codebase uses manual file locking (`threading.Lock()`) because HDF5's built-in locking can cause issues:
- `file_lock` in `io_funcs.py:24` protects concurrent read/write
- Environment variable `HDF5_USE_FILE_LOCKING = 'FALSE'` set in `sensor.py`
- All HDF5 operations use `locking=False` parameter and rely on `file_lock` instead

## Important Notes

- The dashboard spawns the sensor as a subprocess - check for existing PID file to avoid duplicates
- Sensor reads can fail (even on real hardware) - code handles `RuntimeError` and retries
- HDF5 datasets use resizable arrays (`maxshape=(None,)`) for continuous appending
- Time data stored as string in HDF5, converted to `np.datetime64` on read
- All imports within the package use relative imports (e.g., `from .. import io_funcs`)
