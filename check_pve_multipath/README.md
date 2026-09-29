# check_pve_multipath

Nagios/Centreon check for Linux multipath health (iSCSI, FC, SAS) on **Proxmox VE 9**. It is based on `multipathd show maps json` and uses an automatically learned baseline (no path count to declare).

## States

| State | Condition |
|---|---|
| UNKNOWN | `sudo` or `multipathd` not found, sudoers rule missing, multipathd not responding, unreadable output, baseline inaccessible |
| CRITICAL | failed path, usable paths < baseline, LUN from the baseline missing |
| WARNING | `active` / `enabled` path distribution differs from the baseline (ALUA failover, etc.) |
| OK | matches the baseline |

- The baseline total path count is learned on the first run, then only ever increases.
- The baseline active/enabled distribution is fixed on the first run; a WARNING persists until validated with `--accept`.
- `ghost` paths (ALUA standby) count as usable.

## Requirements

- Proxmox VE 9 (Debian 13), `python3` (standard library only), `multipath-tools`, `sudo`.

## Installation (on each node)

```bash
install -m 755 check_pve_multipath.py /usr/local/bin/check_pve_multipath.py
install -d -o Debian-snmp -g Debian-snmp /var/lib/check_pve_multipath
```

`/etc/sudoers.d/check_pve_multipath`:

```
Debian-snmp ALL=(root) NOPASSWD: /usr/sbin/multipathd show maps json
```

`/etc/snmp/snmpd.conf` (then `systemctl restart snmpd`):

```
extend check_pve_multipath /usr/local/bin/check_pve_multipath.py
```

### NRPE mode

`--mode nrpe` fits the output to NRPE's 1024-byte limit: the first line only details LUNs with an issue (or `N LUNs OK`) followed by the performance data, then one line per LUN. The default mode (`snmp`) remains a single line.

The plugin runs as the NRPE user (`nagios` on Debian): adjust the owner of `/var/lib/check_pve_multipath` and the sudoers rule accordingly.

`/etc/nagios/nrpe_local.cfg`:

```
command[check_pve_multipath]=/usr/local/bin/check_pve_multipath.py --mode nrpe
```

## First run

Run it when **all paths are present** (otherwise the baseline will be too low):

```bash
sudo -u Debian-snmp /usr/local/bin/check_pve_multipath.py
cat /var/lib/check_pve_multipath/baseline.json
```

## Baseline maintenance

```bash
sudo -u Debian-snmp /usr/local/bin/check_pve_multipath.py --accept <LUN>   # accept the current state of one LUN
sudo -u Debian-snmp /usr/local/bin/check_pve_multipath.py --accept         # accept all LUNs
sudo -u Debian-snmp /usr/local/bin/check_pve_multipath.py --forget <LUN>   # deliberately remove a LUN
sudo -u Debian-snmp /usr/local/bin/check_pve_multipath.py --reset          # relearn everything
```

## Centreon

Via SNMP (NET-SNMP-EXTEND-MIB, `.1.3.6.1.4.1.8072.1.3`):

- state: `nsExtendResult."check_pve_multipath"` (0/1/2/3)
- message: `nsExtendOutput1Line."check_pve_multipath"`

```bash
snmpwalk -v3 -l authPriv -u <user> -a SHA -A '<auth>' -x AES -X '<priv>' <ip> NET-SNMP-EXTEND-MIB::nsExtendObjects
```

Performance data per LUN: `<lun>_ok`, `<lun>_faulty`, `<lun>_active`, `<lun>_enabled`.

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
| Description | `PVE-Multipath` |
| Command | `App-Net-SNMP-Extend` |
| `EXTENDOID` macro | `.1.3.6.1.4.1.8072.1.3.2.3.1.1.19.99.104.101.99.107.95.112.118.101.95.109.117.108.116.105.112.97.116.104` |
| Check interval | 5 min |
| Max check attempts | 3 |

The OID is `nsExtendOutput1Line."check_pve_multipath"`, built from the name length (19) followed by the ASCII code of each character.

## Tests

Unit tests use simulated `multipathd` output and need no root access:

```bash
python3 -m unittest -v test_check_pve_multipath
```
