#!/usr/bin/env python3
"""check_pve_multipath.py : multipath monitoring (iSCSI/FC/SAS) against a learned baseline.
v1.0 2026-09-29
Yumaiia - https://github.com/Yumaiia/pve-goodies/

OK       : paths match the baseline
WARNING  : active/enabled distribution differs from the baseline
CRITICAL : failed path, missing paths, missing LUN
UNKNOWN  : missing prerequisite or collection error
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

OK, WARNING, CRITICAL, UNKNOWN = 0, 1, 2, 3
MULTIPATHD = "/usr/sbin/multipathd"      # must match the sudoers rule
LABELS = ("OK", "WARNING", "CRITICAL", "UNKNOWN")


def out(rc, msg, perf="", detail=()):
    print(f"{LABELS[rc]} - {msg}" + (f" | {perf}" if perf else ""))
    for line in detail:                                 # long output (following lines)
        print(line)
    sys.exit(rc)


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        out(UNKNOWN, f"unreadable baseline ({path}) : {e}")


def save_state(path, state):
    d = os.path.dirname(path) or "."
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".baseline.")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        out(UNKNOWN, f"cannot write baseline ({path}) : {e}")


def collect(mpd):
    if not shutil.which("sudo"):
        out(UNKNOWN, "sudo not found")
    if not (os.path.isfile(mpd) and os.access(mpd, os.X_OK)):
        out(UNKNOWN, f"multipathd not found ({mpd})")
    try:
        p = subprocess.run(["sudo", "-n", mpd, "show", "maps", "json"],
                           capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        out(UNKNOWN, "multipathd not responding (20 s timeout)")
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip().splitlines()
        out(UNKNOWN, "multipathd failed: " + (err[0] if err else f"code {p.returncode}"))
    try:
        maps = {}
        for m in json.loads(p.stdout).get("maps", []):
            c = {"ok": 0, "faulty": 0, "active": 0, "enabled": 0}
            for pg in m.get("path_groups", []):
                grp = "active" if pg.get("dm_st") == "active" else "enabled"
                for pa in pg.get("paths", []):
                    chk, dm = pa.get("chk_st"), pa.get("dm_st")
                    if chk == "ghost" or (dm == "active" and chk == "ready"):
                        c["ok"] += 1
                        c[grp] += 1
                    else:
                        c["faulty"] += 1
            maps[m["name"]] = c
        return maps
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        out(UNKNOWN, f"unreadable multipathd output : {e} / {p.stdout[:80]!r}")


def ref_from(c):
    return {"max": c["ok"], "active": c["active"], "enabled": c["enabled"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="/var/lib/check_pve_multipath/baseline.json")
    ap.add_argument("--mode", choices=("snmp", "nrpe"), default="snmp",
                    help="snmp: single detailed line (snmpd extend); "
                         "nrpe: short summary + one line per LUN (1024-byte limit)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--reset", action="store_true",
                   help="clear the baseline (relearned on next run)")
    g.add_argument("--forget", metavar="LUN", help="remove a LUN from the baseline")
    g.add_argument("--accept", metavar="LUN", nargs="?", const="*",
                   help="accept the current state as baseline (one LUN, or all if omitted)")
    a = ap.parse_args()

    if a.reset:
        save_state(a.state, {})
        out(OK, "baseline reset")

    state = load_state(a.state)

    if a.forget:
        state.pop(a.forget, None)
        save_state(a.state, state)
        out(OK, f"{a.forget} removed from baseline")

    maps = collect(MULTIPATHD)

    if a.accept:
        names = list(maps) if a.accept == "*" else [a.accept]
        for n in names:
            if n not in maps:
                out(UNKNOWN, f"LUN {n} unknown to multipathd")
            state[n] = ref_from(maps[n])
        save_state(a.state, state)
        out(OK, "current state accepted as baseline: " + ", ".join(names))

    if not maps:
        out(UNKNOWN, "no multipath LUN")

    rc, parts, perf, bad = OK, [], [], []
    for n in sorted(maps):
        c = maps[n]
        ref = state.setdefault(n, ref_from(c))          # first run: learning
        ref["max"] = max(ref["max"], c["ok"])           # total only ever learned upwards

        lun_rc = OK
        s = f"{n}: {c['ok']}/{ref['max']} ({c['active']}A+{c['enabled']}E)"
        if c["faulty"]:
            lun_rc = CRITICAL
            s += f" {c['faulty']} failed"
        if c["ok"] < ref["max"]:
            lun_rc = CRITICAL
            s += " MISSING PATHS"
        if (c["active"], c["enabled"]) != (ref["active"], ref["enabled"]):
            lun_rc = max(lun_rc, WARNING)
            s += f" distribution changed (baseline {ref['active']}A+{ref['enabled']}E)"
        rc = max(rc, lun_rc)
        parts.append(s)
        if lun_rc:
            bad.append(s)

        perf += [f"'{n}_ok'={c['ok']};;{ref['max']}:;0;{ref['max']}",
                 f"'{n}_faulty'={c['faulty']};;0;0;",
                 f"'{n}_active'={c['active']};;;0;",
                 f"'{n}_enabled'={c['enabled']};;;0;"]

    for n in sorted(set(state) - set(maps)):
        rc = CRITICAL
        parts.append(f"{n}: LUN MISSING")
        bad.append(parts[-1])

    save_state(a.state, state)
    if a.mode == "nrpe":
        summary = "; ".join(bad) if bad else f"{len(maps)} LUNs OK"
        out(rc, summary, " ".join(perf), parts)
    out(rc, "; ".join(parts), " ".join(perf))


if __name__ == "__main__":
    main()
