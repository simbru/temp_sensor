#!/usr/bin/env python3
"""
Startup script for Raspberry Pi temperature sensor client.
Runs both the sensor logging process and FastAPI server concurrently.
"""
import logging
import logging.handlers
import multiprocessing
import pathlib
import sys
import time

from tempsens import io_funcs, sensor

# Configure logging
_project_root = pathlib.Path(__file__).parent

# Load config to check if file logging is enabled
config = io_funcs.fetch_config()
write_log_file = config["DEFAULT"].getboolean("write_log_file", True)  # Default: True

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Get root logger and configure it
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove any existing handlers to avoid duplicates
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

handlers = []

# File handler (optional, controlled by config)
if write_log_file:
    log_dir = _project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "client.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

# Console handler (always enabled for systemd journal)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
handlers.append(console_handler)

# Add all handlers
for handler in handlers:
    root_logger.addHandler(handler)

logger = logging.getLogger(__name__)
if write_log_file:
    logger.info(f"File logging enabled: {log_file}")
else:
    logger.info(f"File logging disabled (console/systemd only)")
logger.info(f"Client starting...")


def run_sensor_logger():
    """Run the sensor logging process."""
    print("Starting sensor logger...")
    sensor.run_tempsensor_test()


def run_api_server():
    """Run the FastAPI server."""
    import uvicorn
    from tempsens.api_server import app

    config = io_funcs.fetch_config()
    api_port = int(config["DEFAULT"].get("api_port", 5000))
    device_name = config["DEFAULT"].get("device_name", "Temperature Sensor")

    print(f"Starting API server for '{device_name}' on port {api_port}")
    uvicorn.run(app, host="0.0.0.0", port=api_port)


def run_lcd_display():
    """Run the LCD display loop (Enviro+ only)."""
    try:
        from tempsens.lcd_display import run_lcd_display_loop
        print("Starting LCD display...")
        run_lcd_display_loop(update_interval_s=1.0)
    except ImportError as e:
        print(f"LCD display not available (missing dependencies): {e}")
        print("Install with: uv sync --extra enviroplus")
        # Exit gracefully if LCD libraries not available
        return


def main():
    """Start sensor logger, API server, and optionally LCD display as separate processes."""
    print("=" * 60)
    print("Temperature Sensor Client")
    print("=" * 60)

    # Check if LCD display should be enabled
    config = io_funcs.fetch_config()
    enable_lcd = config["DEFAULT"].getboolean("enable_lcd_display", False)

    # Create processes
    logger_process = multiprocessing.Process(target=run_sensor_logger, name="SensorLogger")
    api_process = multiprocessing.Process(target=run_api_server, name="APIServer")
    lcd_process = None

    if enable_lcd:
        lcd_process = multiprocessing.Process(target=run_lcd_display, name="LCDDisplay")

    try:
        # Start all processes
        logger_process.start()
        time.sleep(1)  # Give logger a moment to initialize
        api_process.start()

        if lcd_process:
            time.sleep(0.5)
            lcd_process.start()
            print("\nAll processes started successfully (logger, API, LCD)!")
        else:
            print("\nBoth processes started successfully (logger, API)!")

        print("Press Ctrl+C to stop...")

        # Wait for processes and check for crashes
        processes = [logger_process, api_process]
        if lcd_process:
            processes.append(lcd_process)

        # Wait for any process to exit
        while all(p.is_alive() for p in processes):
            time.sleep(0.5)

        # If we reach here, one or more processes exited unexpectedly
        # Check exit codes
        if logger_process.exitcode is not None and logger_process.exitcode != 0:
            print(f"\n❌ ERROR: Sensor logger crashed with exit code {logger_process.exitcode}")
            sys.exit(1)
        if api_process.exitcode is not None and api_process.exitcode != 0:
            print(f"\n❌ ERROR: API server crashed with exit code {api_process.exitcode}")
            sys.exit(1)
        if lcd_process and lcd_process.exitcode is not None and lcd_process.exitcode != 0:
            print(f"\n❌ ERROR: LCD display crashed with exit code {lcd_process.exitcode}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        logger_process.terminate()
        api_process.terminate()
        if lcd_process:
            lcd_process.terminate()

        logger_process.join()
        api_process.join()
        if lcd_process:
            lcd_process.join()

        print("Shutdown complete.")
        sys.exit(0)


if __name__ == "__main__":
    main()
