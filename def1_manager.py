"""
def1_manager.py - Windows Physical Adapter /1 Route Overlay Manager
------------------------------------------------------------------
Manages the temporary addition and removal of 0.0.0.0/1 and 128.0.0.0/1
routes on the physical network interface to suppress native VPN default route.
"""

import sys
import subprocess
import argparse
import atexit
import signal
import time

def run_cmd(cmd):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def get_physical_gateway(interface_alias):
    ps_cmd = (
        f"Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
        f"Where-Object {{ $_.InterfaceAlias -eq '{interface_alias}' }} | "
        f"Select-Object -ExpandProperty NextHop"
    )
    code, stdout, stderr = run_cmd(f"powershell -NoProfile -Command \"{ps_cmd}\"")
    if code == 0 and stdout:
        return stdout.splitlines()[0].strip()
    return None

def add_def1_routes(interface_alias, gateway):
    print(f"[*] Adding /1 routes on interface '{interface_alias}' via gateway {gateway}...")
    cmd1 = f"route add 0.0.0.0 mask 128.0.0.0 {gateway} metric 1"
    cmd2 = f"route add 128.0.0.0 mask 128.0.0.0 {gateway} metric 1"
    run_cmd(cmd1)
    run_cmd(cmd2)
    print("[+] Successfully added 0.0.0.0/1 and 128.0.0.0/1 routes.")

def remove_def1_routes():
    print("[*] Removing /1 routes from routing table...")
    run_cmd("route delete 0.0.0.0 mask 128.0.0.0")
    run_cmd("route delete 128.0.0.0 mask 128.0.0.0")
    print("[+] Cleared /1 routes.")

def main():
    parser = argparse.ArgumentParser(description="Manage /1 overlay routes on physical interface.")
    parser.add_argument("--action", choices=["add", "remove", "watch"], default="watch",
                        help="Action to execute: add, remove, or watch with auto-cleanup on exit")
    parser.add_argument("--interface", default="WLAN", help="Physical interface alias (e.g. WLAN, Ethernet)")
    parser.add_argument("--gateway", help="Physical gateway IP (auto-detected if omitted)")
    args = parser.parse_args()

    if args.action == "remove":
        remove_def1_routes()
        return

    gateway = args.gateway
    if not gateway:
        gateway = get_physical_gateway(args.interface)
        if not gateway or gateway == "0.0.0.0":
            print(f"[-] Could not auto-detect gateway for interface '{args.interface}'. Please specify --gateway.")
            sys.exit(1)
        print(f"[+] Detected gateway for '{args.interface}': {gateway}")

    if args.action == "add":
        add_def1_routes(args.interface, gateway)
    elif args.action == "watch":
        add_def1_routes(args.interface, gateway)
        atexit.register(remove_def1_routes)
        def sig_handler(sig, frame):
            remove_def1_routes()
            sys.exit(0)
        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)
        print("[*] Running in watchdog mode. Press Ctrl+C to terminate and restore original routes.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            remove_def1_routes()

if __name__ == "__main__":
    main()
