#!/usr/bin/env python3
"""
Nagios check: age of Proxmox VE snapshots (VMs and LXC containers, local node).
v1.2 2026-09-29
Yumaiia - https://github.com/Yumaiia/pve-goodies/

Run directly on the PVE host (uses pvesh locally, requires root or a user
with the appropriate PVE permissions).

Usage:
    check_pve_snapshots.py --warn-hours 24 --crit-hours 72 [--node NODE]
                           [--mode {nrpe,snmp}] [--cache-file FILE]
    check_pve_snapshots.py --read-cache --cache-file FILE [--cache-max-age SEC]

Modes:
    nrpe  summary + perfdata on line 1, list of guests on line 2 (default)
    snmp  single line (snmpd "extend" only exposes the first line via
          nsExtendOutput1Line)

The check is slow (one pvesh call per guest). snmpd blocks while an extend
command runs, so under SNMP refresh a cache file from cron (--cache-file)
and let snmpd only replay it (--read-cache).

NRPE example (/etc/nagios/nrpe.d/pve_snapshots.cfg, needs a matching sudoers rule):
    command[check_pve_snapshots]=/usr/bin/sudo -n /usr/bin/python3 /usr/local/bin/check_pve_snapshots.py --warn-hours 24 --crit-hours 72
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
STATUS_LABELS = {OK: "OK", WARNING: "WARNING", CRITICAL: "CRITICAL", UNKNOWN: "UNKNOWN"}


MAX_NAMES = 10  # guest names listed in the single-line (snmp) output
cache_file = None


def finish(status, text):
    """Print the result, store it in the cache file if requested, and exit."""
    print(text)
    if cache_file:
        try:
            d = os.path.dirname(cache_file) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".cache.")
            with os.fdopen(fd, "w") as f:
                json.dump({"status": status, "text": text, "ts": time.time()}, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, cache_file)
        except OSError as e:
            print(f"UNKNOWN - cannot write cache file {cache_file}: {e}", file=sys.stderr)
    sys.exit(status)


def replay_cache(path, max_age):
    try:
        with open(path) as f:
            data = json.load(f)
        status, text, ts = data["status"], data["text"], data["ts"]
    except FileNotFoundError:
        print(f"UNKNOWN - cache file not found: {path}")
        sys.exit(UNKNOWN)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"UNKNOWN - unreadable cache file {path}: {e}")
        sys.exit(UNKNOWN)
    age = time.time() - ts
    if age > max_age:
        print(f"UNKNOWN - cached result is {age / 60:.0f} min old (max {max_age / 60:g} min), "
              f"is the cron job running?")
        sys.exit(UNKNOWN)
    print(text)
    sys.exit(status)


def run_json(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except FileNotFoundError:
        finish(UNKNOWN, f"UNKNOWN - command not found: {cmd[0]}")
    except subprocess.CalledProcessError as e:
        finish(UNKNOWN, f"UNKNOWN - command failed {' '.join(cmd)}: "
                        f"{e.output.decode(errors='replace').strip()}")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        finish(UNKNOWN, f"UNKNOWN - invalid JSON output for: {' '.join(cmd)}")


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
    parser.add_argument("--warn-hours", type=float, help="WARNING threshold in hours")
    parser.add_argument("--crit-hours", type=float, help="CRITICAL threshold in hours")
    parser.add_argument("--node", default=None, help="PVE node name (default: local hostname)")
    parser.add_argument("--mode", choices=("nrpe", "snmp"), default="nrpe",
                        help="nrpe: summary + guest list on 2 lines; snmp: single line (default: nrpe)")
    parser.add_argument("--cache-file", default=None,
                        help="store the result in this file (with --read-cache: file to replay)")
    parser.add_argument("--read-cache", action="store_true",
                        help="replay the result stored in --cache-file instead of querying PVE")
    parser.add_argument("--cache-max-age", type=int, default=7200,
                        help="with --read-cache: UNKNOWN if the cached result is older than this many seconds (default: 7200)")
    args = parser.parse_args()

    if args.read_cache:
        if not args.cache_file:
            parser.error("--read-cache requires --cache-file")
        replay_cache(args.cache_file, args.cache_max_age)

    global cache_file
    cache_file = args.cache_file

    if args.warn_hours is None or args.crit_hours is None:
        parser.error("--warn-hours and --crit-hours are required")
    if args.crit_hours <= args.warn_hours:
        finish(UNKNOWN, f"UNKNOWN - crit-hours ({args.crit_hours}) must be > warn-hours ({args.warn_hours})")

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
    names = [name for _, name, _, _, _ in sorted(offenders, key=lambda x: -x[4])]

    if args.mode == "snmp":
        if len(names) > MAX_NAMES:
            names = names[:MAX_NAMES] + [f"+{len(names) - MAX_NAMES} more"]
        if names:
            summary += ": " + ", ".join(names)
        finish(status, f"{summary} | {perfdata}")

    text = f"{summary} | {perfdata}"
    if names:
        text += f"\nSnapshots older than {args.warn_hours:g} hours on " + ", ".join(names)
    finish(status, text)


if __name__ == "__main__":
    main()
