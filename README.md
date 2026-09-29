# pve-goodies

Useful stuff for Proxmox VE 9: Nagios/Centreon checks that run on the PVE nodes, over SNMP (`extend`) or NRPE. Python 3, standard library only.

| Check | What it does |
|---|---|
| [check_pve_multipath](check_pve_multipath/) | Multipath health (iSCSI, FC, SAS). Learns a baseline of paths per LUN automatically and alerts on failed or missing paths, a missing LUN, or a changed active/enabled distribution (e.g. ALUA failover). |
| [check_pve_snapshots](check_pve_snapshots/) | Age of VM and LXC snapshots on the local node. WARNING/CRITICAL when a snapshot is older than the given number of hours. Slow: check it once an hour at most. |

Each folder has its own README with installation, sudoers rules, snmpd/NRPE setup and the Centreon configuration.

## License

GPL-3.0, see [LICENSE](LICENSE).
