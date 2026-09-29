# check_pve_snapshots

Nagios/Centreon check for old snapshots of VMs and LXC containers on a **Proxmox VE 9** node. It queries the local node with `pvesh`.

## States

| State | Condition |
|---|---|
| UNKNOWN | `pvesh` missing or failing, invalid JSON, invalid thresholds, (cache mode) cached result missing or too old |
| CRITICAL | at least one snapshot older than `--crit-hours` |
| WARNING | at least one snapshot older than `--warn-hours` |
| OK | no snapshot older than `--warn-hours` |

Performance data: `guest_offenders`, `snap_warn`, `snap_crit`.

## Requirements

- Proxmox VE 9 (Debian 13), `python3` (standard library only).
- Must run as root (`pvesh` on the local node).
- Slow: one `pvesh` call per guest. Do not run it more than once an hour or so.

## Options

| Option | Description |
|---|---|
| `--warn-hours H` | WARNING threshold in hours |
| `--crit-hours H` | CRITICAL threshold in hours (> warn) |
| `--node NAME` | PVE node name (default: local hostname) |
| `--mode nrpe\|snmp` | `nrpe` (default): summary and perfdata on line 1, guest list on line 2. `snmp`: single line, at most 10 guest names |
| `--cache-file FILE` | store the result in `FILE` (with `--read-cache`: file to replay) |
| `--read-cache` | replay the stored result instead of querying PVE |
| `--cache-max-age SEC` | with `--read-cache`: UNKNOWN if the result is older (default 7200) |

## Installation (on each node)

```bash
install -m 755 check_pve_snapshots.py /usr/local/bin/check_pve_snapshots.py
```

### NRPE

NRPE runs as `nagios`, which cannot use `pvesh`, so the check runs through sudo. The sudoers rule allows exactly one command line (arguments included), so `nagios` cannot run the script with anything else. The script must belong to root and not be writable by others (`install` above does this).

`/etc/sudoers.d/check_pve_snapshots` (edit with `visudo -f`):

```
nagios ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/bin/check_pve_snapshots.py --warn-hours 24 --crit-hours 72
```

`/etc/nagios/nrpe.d/pve_snapshots.cfg`:

```
command[check_pve_snapshots]=/usr/bin/sudo -n /usr/bin/python3 /usr/local/bin/check_pve_snapshots.py --warn-hours 24 --crit-hours 72
```

To use other thresholds, change them in both files. Mind NRPE's timeout on nodes with many guests.

### SNMP

snmpd blocks while an `extend` command runs, and the check is slow, so the query is done by a cron job and snmpd only replays the stored result (no root needed).

`/etc/cron.d/check_pve_snapshots` (hourly):

```
7 * * * * root /usr/local/bin/check_pve_snapshots.py --mode snmp --warn-hours 24 --crit-hours 72 --cache-file /var/lib/check_pve_snapshots/result.json >/dev/null
```

`/etc/snmp/snmpd.conf` (then `systemctl restart snmpd`):

```
extend check_pve_snapshots /usr/local/bin/check_pve_snapshots.py --read-cache --cache-file /var/lib/check_pve_snapshots/result.json
```

The result goes UNKNOWN if it is older than `--cache-max-age` (default 2 h), which also alerts you when the cron job stops working. Run the cron command once by hand to create the first result.

## Centreon

Via SNMP (NET-SNMP-EXTEND-MIB, `.1.3.6.1.4.1.8072.1.3`):

- state: `nsExtendResult."check_pve_snapshots"` (0/1/2/3)
- message: `nsExtendOutput1Line."check_pve_snapshots"`

```bash
snmpwalk -v3 -l authPriv -u <user> -a SHA -A '<auth>' -x AES -X '<priv>' <ip> NET-SNMP-EXTEND-MIB::nsExtendObjects
```

### Check command

**Configuration > Commands > Checks > Add**

- Name: `App-Net-SNMP-Extend`
- Command line:
  ```
  $CENTREONPLUGINS$/centreon_generic_snmp.pl --plugin=apps::protocols::snmp::plugin --mode=string-value --hostname=$HOSTADDRESS$ --snmp-version='$_HOSTSNMPVERSION$' $_HOSTSNMPEXTRAOPTIONS$ --oid='$_SERVICEEXTENDOID$' --warning-regexp='^WARNING' --critical-regexp='^(CRITICAL|UNKNOWN)' --format-ok='%{value}' --format-warning='%{value}' --format-critical='%{value}'
  ```
- Type: Check, then declare the `EXTENDOID` macro (type Service).

The check writes its state at the start of its output line (`OK - `, `WARNING - `, ...), so the regular expressions recover the state and the full message is displayed. The command can be shared with other extend-based checks.

### Service

**Configuration > Services > Services by host > Add**

| Field | Value |
|---|---|
| Description | `PVE-Snapshots` |
| Command | `App-Net-SNMP-Extend` |
| `EXTENDOID` macro | `.1.3.6.1.4.1.8072.1.3.2.3.1.1.19.99.104.101.99.107.95.112.118.101.95.115.110.97.112.115.104.111.116.115` |
| Check interval | 5 min (it only replays the cache) |
| Max check attempts | 3 |

The OID is `nsExtendOutput1Line."check_pve_snapshots"`, built from the name length (19) followed by the ASCII code of each character.
