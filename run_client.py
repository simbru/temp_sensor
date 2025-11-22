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


def main():
    """Start both sensor logger and API server as separate processes."""
    print("=" * 60)
    print("Temperature Sensor Client")
    print("=" * 60)

    # Create processes
    logger_process = multiprocessing.Process(target=run_sensor_logger, name="SensorLogger")
    api_process = multiprocessing.Process(target=run_api_server, name="APIServer")

    try:
        # Start both processes
        logger_process.start()
        time.sleep(1)  # Give logger a moment to initialize
        api_process.start()

        print("\nBoth processes started successfully!")
        print("Press Ctrl+C to stop...")

        # Wait for both processes and check for crashes
        logger_process.join()
        api_process.join()

        # If we reach here, one or both processes exited unexpectedly
        # Check exit codes and fail if either crashed
        if logger_process.exitcode != 0:
            print(f"\n❌ ERROR: Sensor logger crashed with exit code {logger_process.exitcode}")
            sys.exit(1)
        if api_process.exitcode != 0:
            print(f"\n❌ ERROR: API server crashed with exit code {api_process.exitcode}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        logger_process.terminate()
        api_process.terminate()
        logger_process.join()
        api_process.join()
        print("Shutdown complete.")
        sys.exit(0)


if __name__ == "__main__":
    main()
