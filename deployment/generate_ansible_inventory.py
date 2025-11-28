#!/usr/bin/env python3
"""
Generate Ansible inventory from server/config_server.ini

This script parses the server config to extract sensor URLs and generates
an Ansible inventory file, avoiding config duplication.
"""

import configparser
import re
from pathlib import Path
from urllib.parse import urlparse


def parse_sensor_config(config_path: Path) -> dict[str, dict]:
    """Parse server config and extract sensor information."""
    config = configparser.ConfigParser()
    config.read(config_path)

    sensors = {}

    for name, value in config.items('SENSORS'):
        # Parse "url, poll_interval, username" or "url, poll_interval" or just "url"
        parts = [p.strip() for p in value.split(',')]
        url = parts[0]
        poll_interval = int(parts[1]) if len(parts) > 1 else None
        username = parts[2] if len(parts) > 2 else None

        # Parse URL to extract host and port
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or 5000

        # Skip localhost entries (dev/test sensors)
        if host in ('localhost', '127.0.0.1'):
            continue

        sensors[name] = {
            'host': host,
            'port': port,
            'url': url,
            'poll_interval': poll_interval,
            'username': username
        }

    return sensors


def generate_inventory(sensors: dict, output_path: Path,
                       ansible_user: str = 'pi',
                       python_interpreter: str = '/usr/bin/python3'):
    """Generate Ansible inventory file from sensor data."""

    lines = [
        "# Ansible Inventory for Temperature Sensors",
        "# Auto-generated from server/config_server.ini",
        "#",
        "# Usage: ansible-playbook -i deployment/inventory.ini deployment/deploy.yml",
        "",
        "[temperature_sensors]"
    ]

    # Add sensor entries
    for name, info in sensors.items():
        # Convert sensor name to valid hostname (replace spaces/underscores)
        hostname = f"pi-{name.lower().replace(' ', '-').replace('_', '-')}"

        # Build inventory line with optional per-host username
        inv_line = f"{hostname} ansible_host={info['host']} sensor_name=\"{name}\" sensor_port={info['port']}"
        if info.get('username'):
            inv_line += f" ansible_user={info['username']}"

        lines.append(inv_line)

    lines.extend([
        "",
        "[temperature_sensors:vars]",
        f"ansible_user={ansible_user}",
        f"ansible_python_interpreter={python_interpreter}",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no'",
        "",
        "# Sensor metadata (from config_server.ini)",
        "[temperature_sensors_metadata]"
    ])

    # Add metadata as comments for reference
    for name, info in sensors.items():
        lines.append(f"# {name}: {info['url']} (poll: {info['poll_interval'] or 'default'}s)")

    output_path.write_text('\n'.join(lines) + '\n')
    print(f"[OK] Generated inventory: {output_path}")
    print(f"[OK] Found {len(sensors)} production sensors")


def main():
    # Paths relative to repo root
    repo_root = Path(__file__).parent.parent
    deployment_config_path = repo_root / 'deployment' / 'config_deployment.ini'
    server_config_path = repo_root / 'server' / 'config_server.ini'
    output_path = repo_root / 'deployment' / 'inventory.ini'

    # Ensure deployment directory exists
    output_path.parent.mkdir(exist_ok=True)

    # Try deployment config first (preferred), fall back to server config
    if deployment_config_path.exists():
        config_path = deployment_config_path
        print(f"[OK] Using deployment config: {config_path}")
    elif server_config_path.exists():
        config_path = server_config_path
        print(f"[OK] Using server config: {config_path}")
    else:
        print("[ERROR] No config found!")
        print(f"  Create either:")
        print(f"    - {deployment_config_path} (recommended, git-ignored)")
        print(f"    - {server_config_path} (legacy)")
        return

    # Parse and generate
    sensors = parse_sensor_config(config_path)

    if not sensors:
        print("[WARNING] No production sensors found in config")
        print("  (localhost entries are skipped)")
        print(f"  Edit {config_path} and add production sensors")
        return

    generate_inventory(sensors, output_path)


if __name__ == '__main__':
    main()
