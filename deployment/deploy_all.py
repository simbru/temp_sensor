#!/usr/bin/env python3
"""
Deploy to both clients (Raspberry Pis) and server (dashboard)

Usage:
    python deployment/deploy_all.py           # Deploy to all Pis and server
    python deployment/deploy_all.py --clients # Deploy to Pis only
    python deployment/deploy_all.py --server  # Deploy to server only
"""

import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).parent

    deploy_clients = True
    deploy_server = True

    # Parse arguments
    if "--clients" in sys.argv:
        deploy_server = False
    elif "--server" in sys.argv:
        deploy_clients = False
    elif "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: python deployment/deploy_all.py [OPTIONS]\n")
        print("Options:")
        print("  --clients    Deploy to Raspberry Pi clients only")
        print("  --server     Deploy to dashboard server only")
        print("  (no args)    Deploy to both clients and server\n")
        print("Examples:")
        print("  python deployment/deploy_all.py           # Deploy everything")
        print("  python deployment/deploy_all.py --clients # Deploy to Pis only")
        print("  python deployment/deploy_all.py --server  # Deploy to server only")
        sys.exit(0)

    if deploy_clients:
        print("=" * 50)
        print("Deploying to Raspberry Pi clients...")
        print("=" * 50)
        subprocess.run([str(script_dir / "deploy.sh")], check=True)

    if deploy_server:
        print("\n" + "=" * 50)
        print("Deploying to dashboard server...")
        print("=" * 50)
        subprocess.run([sys.executable, str(script_dir / "deploy_server.py")], check=True)

    print("\n" + "=" * 50)
    print("Deployment complete!")
    print("=" * 50)


if __name__ == '__main__':
    main()
