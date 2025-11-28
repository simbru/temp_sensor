#!/usr/bin/env python3
"""
Sync deployment config to server config.

This script copies sensor list and server settings from
deployment/config_deployment.ini → server/config_server.ini

This ensures you only maintain sensor IPs in one place (deployment config).

Usage:
    python deployment/sync_configs.py
"""

import configparser
from pathlib import Path


def sync_configs(deployment_config_path: Path, server_config_path: Path):
    """Sync deployment config to server config."""

    if not deployment_config_path.exists():
        print(f"[ERROR] Deployment config not found: {deployment_config_path}")
        print(f"  Run: python deployment/setup_deployment.py")
        return False

    # Read deployment config
    deployment_config = configparser.ConfigParser()
    deployment_config.read(deployment_config_path)

    # Create new server config
    server_config = configparser.ConfigParser()

    # Copy SERVER section
    if deployment_config.has_section('SERVER'):
        server_config.add_section('SERVER')
        for key, value in deployment_config.items('SERVER'):
            if key != 'DEFAULT':  # Skip DEFAULT section
                server_config.set('SERVER', key, value)
    else:
        print("[WARNING] No [SERVER] section in deployment config")

    # Copy SENSORS section
    if deployment_config.has_section('SENSORS'):
        server_config.add_section('SENSORS')
        for key, value in deployment_config.items('SENSORS'):
            if key != 'DEFAULT':  # Skip DEFAULT section
                server_config.set('SENSORS', key, value)
    else:
        print("[WARNING] No [SENSORS] section in deployment config")

    # Write to server config
    server_config_path.parent.mkdir(exist_ok=True)

    # Add header comment
    with open(server_config_path, 'w') as f:
        f.write("# Server Configuration for Multi-Sensor Temperature Monitoring Dashboard\n")
        f.write("#\n")
        f.write("# AUTO-GENERATED from deployment/config_deployment.ini\n")
        f.write("# DO NOT EDIT MANUALLY - Changes will be overwritten!\n")
        f.write("# Edit deployment/config_deployment.ini instead, then run:\n")
        f.write("#   python deployment/sync_configs.py\n")
        f.write("#\n\n")
        server_config.write(f)

    print(f"[OK] Synced deployment config → server config")
    print(f"     Source: {deployment_config_path}")
    print(f"     Target: {server_config_path}")

    # Count sensors
    sensor_count = len([k for k in deployment_config.options('SENSORS') if k != 'DEFAULT'])
    print(f"[OK] Synced {sensor_count} sensors")

    return True


def main():
    repo_root = Path(__file__).parent.parent
    deployment_config_path = repo_root / 'deployment' / 'config_deployment.ini'
    server_config_path = repo_root / 'server' / 'config_server.ini'

    print("=== Syncing Deployment Config to Server Config ===\n")

    if sync_configs(deployment_config_path, server_config_path):
        print("\nNext step:")
        print("  python deployment/generate_ansible_inventory.py")
    else:
        print("\nSetup needed:")
        print("  python deployment/setup_deployment.py")


if __name__ == '__main__':
    main()
