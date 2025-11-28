#!/usr/bin/env python3
"""
Deploy to lab server (dashboard)

This script updates the server dashboard with latest code and config.

Usage:
    python deployment/deploy_server.py           # Deploy to remote server
    python deployment/deploy_server.py --local   # Deploy locally (testing)
"""

import configparser
import subprocess
import sys
from pathlib import Path


def read_config(config_path: Path, section: str, key: str, default: str = "") -> str:
    """Read config value."""
    config = configparser.ConfigParser()
    config.read(config_path)
    return config.get(section, key, fallback=default)


def deploy_local(repo_root: Path, script_dir: Path):
    """Deploy to local machine (dev/testing)."""
    print("==> Deploying to local server (current machine)")

    # Sync configs
    print("==> Syncing deployment config to server config...")
    subprocess.run(
        [sys.executable, str(script_dir / "sync_configs.py")],
        check=True
    )

    print("\n==> Server config updated at: server/config_server.ini")
    print("\nTo start the dashboard:")
    print("  uv run bokeh serve server/bokeh_app.py --port 8000")


def deploy_remote(repo_root: Path, script_dir: Path, config_path: Path):
    """Deploy to remote server via SSH."""
    print("==> Reading deployment config...")

    server_host = read_config(config_path, "SERVER", "server_host")
    server_user = read_config(config_path, "SERVER", "server_user")
    server_path = read_config(config_path, "SERVER", "server_path")

    if not all([server_host, server_user, server_path]):
        print("Error: Server deployment info not configured\n")
        print("Edit deployment/config_deployment.ini and set:")
        print("  [SERVER]")
        print("  server_host = 139.184.163.16")
        print("  server_user = your_username")
        print("  server_path = /path/to/temp_sensor\n")
        print("Or use --local to deploy to current machine:")
        print("  python deployment/deploy_server.py --local")
        sys.exit(1)

    print(f"==> Deploying to server: {server_user}@{server_host}:{server_path}")

    # Sync configs locally first
    print("==> Syncing deployment config to server config...")
    subprocess.run(
        [sys.executable, str(script_dir / "sync_configs.py")],
        check=True
    )

    # Copy server config to remote
    print("==> Copying server config to remote server...")
    server_config = repo_root / "server" / "config_server.ini"
    remote_dest = f"{server_user}@{server_host}:{server_path}/server/"

    subprocess.run(
        ["scp", str(server_config), remote_dest],
        check=True
    )

    # SSH to server and update
    print("==> Updating code on server...")
    ssh_commands = f"""
        cd {server_path}
        git pull
        uv sync

        echo "==> Server updated successfully"
        echo ""
        echo "To restart the dashboard:"
        echo "  pkill -f bokeh"
        echo "  uv run bokeh serve server/bokeh_app.py --port 8000 &"
    """

    subprocess.run(
        ["ssh", f"{server_user}@{server_host}", ssh_commands],
        check=True
    )

    print("==> Server deployment complete!")


def main():
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    config_path = script_dir / "config_deployment.ini"

    # Check if config exists
    if not config_path.exists():
        print(f"Error: Deployment config not found: {config_path}")
        print("\nRun setup first:")
        print("  python deployment/setup_deployment.py")
        sys.exit(1)

    # Parse command line args
    local_mode = "--local" in sys.argv

    if local_mode:
        deploy_local(repo_root, script_dir)
    else:
        deploy_remote(repo_root, script_dir, config_path)


if __name__ == '__main__':
    main()
