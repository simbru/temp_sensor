#!/usr/bin/env python3
"""
Run multiple test sensor clients concurrently for testing the multi-sensor dashboard.
Each client runs with its own config file and port.
"""
import multiprocessing
import sys
import time
import os

# Override the config path for each instance
def run_client_instance(config_file: str, instance_name: str):
    """Run a single client instance with a specific config file."""
    # Set environment variable so io_funcs uses this config
    os.environ['TEMPSENS_CONFIG'] = config_file

    # Import after setting env var so it picks up the right config
    from tempsens import io_funcs, sensor
    import uvicorn
    from tempsens.api_server import app

    print(f"[{instance_name}] Starting with config: {config_file}")

    # Start sensor logger in a subprocess
    sensor_process = multiprocessing.Process(
        target=sensor.run_tempsensor_test,
        name=f"SensorLogger-{instance_name}"
    )
    sensor_process.start()

    # Get port from config
    config = io_funcs.fetch_config(config_file)
    api_port = int(config["DEFAULT"].get("api_port", 5000))
    device_name = config["DEFAULT"].get("device_name", "Unknown")

    print(f"[{instance_name}] Starting API server for '{device_name}' on port {api_port}")

    # Run API server
    try:
        uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")
    finally:
        sensor_process.terminate()
        sensor_process.join()


def main():
    """Start all test client instances."""
    print("=" * 80)
    print("Starting 3 Test Sensor Clients")
    print("=" * 80)

    # Define test instances
    instances = [
        ("config_test1.ini", "Test-1"),
        ("config_test2.ini", "Test-2"),
        ("config_test3.ini", "Test-3"),
    ]

    # Start each instance in its own process
    processes = []
    for config_file, instance_name in instances:
        p = multiprocessing.Process(
            target=run_client_instance,
            args=(config_file, instance_name),
            name=instance_name
        )
        p.start()
        processes.append(p)
        time.sleep(1)  # Stagger startup slightly

    print("\n" + "=" * 80)
    print("All 3 test clients started!")
    print("  - Test Sensor 1 on http://localhost:5001")
    print("  - Test Sensor 2 on http://localhost:5002")
    print("  - Test Sensor 3 on http://localhost:5003")
    print("Press Ctrl+C to stop all clients...")
    print("=" * 80 + "\n")

    try:
        # Wait for all processes
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n\nShutting down all test clients...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join()
        print("All clients stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
