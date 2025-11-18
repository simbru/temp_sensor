#!/usr/bin/env python3
"""
Complete test environment setup script.

This script:
1. Cleans all test databases (client and server)
2. Generates historical data for test sensors
3. Starts test sensor clients (simulated data)
4. Starts Bokeh dashboard server
5. Opens browser to dashboard

Use this to ensure testing in a "data-heavy" environment with known good state.
"""
import argparse
import multiprocessing
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def clean_databases(clean_server: bool = True, clean_clients: bool = True):
    """Remove all test database files to start fresh."""
    print("=" * 80)
    print("STEP 1: Cleaning databases")
    print("=" * 80)

    deleted_files = []

    if clean_clients:
        # Client databases
        client_patterns = [
            "dev/templog_test*.db",
            "dev/templog_test*.db-wal",
            "dev/templog_test*.db-shm",
            "dev/templog_test*.meta.json"
        ]

        for pattern in client_patterns:
            for file_path in PROJECT_ROOT.glob(pattern):
                try:
                    file_path.unlink()
                    deleted_files.append(str(file_path.relative_to(PROJECT_ROOT)))
                except Exception as e:
                    print(f"  Warning: Could not delete {file_path}: {e}")

    if clean_server:
        # Server database
        server_patterns = [
            "sensor_data.db",
            "sensor_data.db-wal",
            "sensor_data.db-shm"
        ]

        for pattern in server_patterns:
            for file_path in PROJECT_ROOT.glob(pattern):
                try:
                    file_path.unlink()
                    deleted_files.append(str(file_path.relative_to(PROJECT_ROOT)))
                except Exception as e:
                    print(f"  Warning: Could not delete {file_path}: {e}")

    if deleted_files:
        print(f"  ✓ Deleted {len(deleted_files)} file(s):")
        for f in deleted_files:
            print(f"    - {f}")
    else:
        print("  ✓ No existing databases found (clean slate)")
    print()


def generate_historical_data(days: int):
    """Generate historical data for test sensors."""
    print("=" * 80)
    print(f"STEP 2: Generating {days} days of historical data")
    print("=" * 80)

    # Import here to avoid issues with environment setup
    from dev.run_test_clients import generate_historical_data as gen_data, get_metadata_path

    instances = [
        ("dev/config_test1.ini", "Test-1"),
        ("dev/config_test2.ini", "Test-2"),
        ("dev/config_test3.ini", "Test-3"),
    ]

    for config_file, instance_name in instances:
        try:
            gen_data(config_file, instance_name, days=days)
        except Exception as e:
            print(f"\n[{instance_name}] ERROR during historical data generation:")
            print(f"  Exception type: {type(e).__name__}")
            print(f"  Exception message: {str(e)}")
            import traceback
            print("\nFull traceback:")
            traceback.print_exc()
            print()
            raise  # Re-raise to stop execution
        print()


def start_test_clients():
    """Start test sensor clients using subprocess (not multiprocessing)."""
    print("=" * 80)
    print("STEP 3: Starting test sensor clients")
    print("=" * 80)

    # Use subprocess instead of multiprocessing to avoid daemon issues
    client_script = PROJECT_ROOT / "dev" / "run_test_clients.py"

    # Start clients in background using subprocess
    process = subprocess.Popen(
        [sys.executable, str(client_script), "--no-historical"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )

    # Read output until we see the "All clients started" message
    for line in process.stdout:
        print(f"  {line.rstrip()}")
        if "All 3 test clients started successfully!" in line or "Press Ctrl+C" in line:
            break

    print("  ✓ Test Sensor 1: http://localhost:5001")
    print("  ✓ Test Sensor 2: http://localhost:5002")
    print("  ✓ Test Sensor 3: http://localhost:5003")
    print()

    return process


def start_bokeh_server(port: int = 5006, open_browser: bool = True):
    """Start Bokeh dashboard server."""
    print("=" * 80)
    print("STEP 4: Starting Bokeh dashboard server")
    print("=" * 80)

    # Wait a moment for clients to fully initialize
    print("  Waiting 3 seconds for clients to initialize...")
    time.sleep(3)

    # Build bokeh command
    bokeh_cmd = [
        sys.executable, "-m", "bokeh", "serve",
        "server/bokeh_app.py",
        "--port", str(port),
        "--session-token-expiration", "360000000"  # Long session timeout
    ]

    if open_browser:
        bokeh_cmd.append("--show")

    print(f"  Starting server on http://localhost:{port}")
    print(f"  Command: {' '.join(bokeh_cmd)}")
    print()

    # Start bokeh server (this blocks)
    try:
        subprocess.run(bokeh_cmd, cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        print("\n\nShutting down Bokeh server...")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Complete test environment setup for temperature sensor dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full test with 7 days of data
  python dev/run_full_test_environment.py --days 7

  # Quick test with 1 day of data
  python dev/run_full_test_environment.py --days 1

  # Skip historical data generation (use existing)
  python dev/run_full_test_environment.py --no-historical

  # Only clean and regenerate client data (keep server cache)
  python dev/run_full_test_environment.py --no-clean-server --days 3
        """
    )

    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of historical data to generate (default: 7)"
    )

    parser.add_argument(
        "--no-historical",
        action="store_true",
        help="Skip historical data generation (start with empty/existing databases)"
    )

    parser.add_argument(
        "--no-clean-server",
        action="store_true",
        help="Don't clean server database (keeps cached data)"
    )

    parser.add_argument(
        "--no-clean-clients",
        action="store_true",
        help="Don't clean client databases (keeps existing client data)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=5006,
        help="Port for Bokeh dashboard server (default: 5006)"
    )

    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't automatically open browser"
    )

    args = parser.parse_args()

    print("\n")
    print("=" * 80)
    print("TEMPERATURE SENSOR TEST ENVIRONMENT SETUP")
    print("=" * 80)
    print()
    print("Configuration:")
    print(f"  - Historical days: {args.days if not args.no_historical else 'None (using existing)'}")
    print(f"  - Clean server DB: {not args.no_clean_server}")
    print(f"  - Clean client DBs: {not args.no_clean_clients}")
    print(f"  - Dashboard port: {args.port}")
    print(f"  - Open browser: {not args.no_browser}")
    print()

    try:
        # Step 1: Clean databases
        clean_databases(
            clean_server=not args.no_clean_server,
            clean_clients=not args.no_clean_clients
        )

        # Step 2: Generate historical data
        if not args.no_historical:
            generate_historical_data(args.days)
        else:
            print("=" * 80)
            print("STEP 2: Skipping historical data generation")
            print("=" * 80)
            print()

        # Step 3: Start test clients
        client_process = start_test_clients()

        # Step 4: Start Bokeh server (this blocks until Ctrl+C)
        print("=" * 80)
        print("ENVIRONMENT READY")
        print("=" * 80)
        print()
        print("Test environment is now running:")
        print(f"  - Dashboard: http://localhost:{args.port}")
        print("  - 3 test sensors with simulated data")
        if not args.no_historical:
            print(f"  - {args.days} days of historical data per sensor")
        print()
        print("Press Ctrl+C to shut down everything...")
        print("=" * 80)
        print()

        try:
            start_bokeh_server(port=args.port, open_browser=not args.no_browser)
        finally:
            # Cleanup client process
            print("\n\nShutting down test clients...")
            client_process.terminate()
            client_process.wait(timeout=5)

    except KeyboardInterrupt:
        print("\n\nShutting down test environment...")
        if 'client_process' in locals():
            client_process.terminate()
            client_process.wait(timeout=5)
    finally:
        print("All processes stopped.")
        sys.exit(0)


if __name__ == "__main__":
    # Required for multiprocessing on Windows
    multiprocessing.freeze_support()
    main()
