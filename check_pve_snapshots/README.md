# pve-goodies
Useful stuff for Proxmox VE

## check_pve_snapshots.py 
Nagios / NRPE check for alerting on old snapshots on a PVE node
Rather slow, schedule only every hour or so
Example : check_pve_snapshots.py --warn-hours 24 --crit-hours 72
