#!/usr/bin/env python3
"""
Nagios check: age of Proxmox VE snapshots (VMs and LXC containers, local node).

Run directly on the PVE host (uses pvesh locally, requires root or a user
with the appropriate PVE permissions).

Usage:
    check_pve_snapshots.py --warn-hours 24 --crit-hours 72 [--node NODE]

NRPE example (/etc/nagios/nrpe.d/pve_snapshots.cfg):
    command[check_pve_snapshots]=/usr/bin/python3 /usr/local/bin/check_pve_snapshots.py --warn-hours 24 --crit-hours 72
"""

import argparse
import json
import socket
import subprocess
import sys
import time

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
STATUS_LABELS = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}


def run_json(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        print(f"UNKNOWN - command not found: {cmd[0]}")
        sys.exit(UNKNOWN)
    except subprocess.CalledProcessError as e:
        print(f"UNKNOWN - command failed {' '.join(cmd)}: {e.output.decode(errors='replace').strip()}")
        sys.exit(UNKNOWN)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        print(f"UNKNOWN - invalid JSON output for: {' '.join(cmd)}")
        sys.exit(UNKNOWN)


def get_local_node():
    return socket.gethostname().split(".")[0]


def get_local_guests(node):
    guests = []
    for kind in ("qemu", "lxc"):
        data = run_json(["pvesh", "get", f"/nodes/{node}/{kind}", "--output-format", "json"])
        for guest in data:
            guests.append({
                "vmid": guest["vmid"],
                "name": guest.get("name", f"{kind}{guest['vmid']}"),
                "type": kind,
            })
    return guests


def get_snapshots(node, kind, vmid):
    data = run_json(["pvesh", "get", f"/nodes/{node}/{kind}/{vmid}/snapshot", "--output-format", "json"])
    snaps = []
    for snap in data:
        if snap.get("name") == "current" or "snaptime" not in snap:
            continue
        snaps.append({"name": snap["name"], "snaptime": snap["snaptime"]})
    return snaps


def main():
    parser = argparse.ArgumentParser(description="Nagios check - age of Proxmox VE VM snapshots")
    parser.add_argument("--warn-hours", type=float, required=True, help="WARNING threshold in hours")
    parser.add_argument("--crit-hours", type=float, required=True, help="CRITICAL threshold in hours")
    parser.add_argument("--node", default=None, help="PVE node name (default: local hostname)")
    args = parser.parse_args()

    if args.crit_hours <= args.warn_hours:
        print(f"UNKNOWN - crit-hours ({args.crit_hours}) must be > warn-hours ({args.warn_hours})")
        sys.exit(UNKNOWN)

    node = args.node or get_local_node()
    guests = get_local_guests(node)

    now = time.time()
    offenders = []  # (vmid, name, nb_warn, nb_crit, oldest_hours)
    total_warn = 0
    total_crit = 0

    for guest in guests:
        snaps = get_snapshots(node, guest["type"], guest["vmid"])
        nb_warn = nb_crit = 0
        oldest_hours = 0.0
        for snap in snaps:
            age_hours = (now - snap["snaptime"]) / 3600.0
            if age_hours >= args.crit_hours:
                nb_crit += 1
            elif age_hours >= args.warn_hours:
                nb_warn += 1
            oldest_hours = max(oldest_hours, age_hours)

        if nb_warn or nb_crit:
            offenders.append((guest["vmid"], guest["name"], nb_warn, nb_crit, oldest_hours))
            total_warn += nb_warn
            total_crit += nb_crit

    if total_crit:
        status = CRITICAL
    elif total_warn:
        status = WARNING
    else:
        status = OK

    nb_offenders = len(offenders)
    if status == OK:
        summary = f"SNAPSHOTS OK - {len(guests)} guest(s) checked, no snapshot older than {args.warn_hours:g}h"
    else:
        summary = (
            f"SNAPSHOTS {STATUS_LABELS[status]} - {nb_offenders} guest(s) with snapshot(s) too old "
            f"({total_crit} crit, {total_warn} warn)"
        )

    perfdata = f"guest_offenders={nb_offenders};;;; snap_warn={total_warn};;;; snap_crit={total_crit};;;;"
    print(f"{summary} | {perfdata}")

    if offenders:
        names = [name for _, name, _, _, _ in sorted(offenders, key=lambda x: -x[4])]
        print(f"Snapshots older than {args.warn_hours:g} hours on " + ", ".join(names))

    sys.exit(status)


if __name__ == "__main__":
    main()
