#!/usr/bin/env python3
"""
Startup script for Raspberry Pi temperature sensor client.
Runs both the sensor logging process and FastAPI server concurrently.
"""
import multiprocessing
import sys
import time

from tempsens import io_funcs, sensor


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

        # Wait for both processes
        logger_process.join()
        api_process.join()

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
