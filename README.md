# Remote SMART (Home Assistant Custom Integration)

Remote SMART is a Home Assistant custom integration that monitors disk
S.M.A.R.T. health from remote storage systems.

It supports two transports:
- **Synology SNMP** for DSM systems, using Synology's official disk, SMART, and storage I/O MIBs
- **SSH command polling** for generic hosts where `smartctl` must be executed remotely

The SSH mode is intentionally **generic**:
- Works with Synology DSM, generic Linux hosts, macOS, or any SSH-accessible system
- Does not assume `/dev/sdX`, `/dev/sataX`, or any specific platform
- The command, device list, and parsing strategy are fully configurable

The recommended usage is with `smartctl -j` (JSON output), but text parsing and
regex-based fallbacks are supported.

---

## Features

- Synology DSM SMART polling over SNMPv3 authPriv
- SSH-based polling of SMART data
- Configurable command template (`{device}` substitution)
- Explicit device lists or discovery commands
- Robust parsing (JSON preferred; text fallback)
- Per-drive sensors and binary sensors
- Max delta tracking (tracks maximum change since last reset)
- Reset button entity per drive and `smart_ssh.reset_deltas` service
- Policy-based “drive failing” indicator
- Designed for large arrays and NAS environments

---

## Typical Use Cases

- Synology DSM via SNMPv3 authPriv
- Synology NAS via SSH and `/dev/sataX` with `-d sat`
- Linux servers with HBAs or SATA/NVMe disks
- macOS systems with Homebrew smartmontools
- Any remote host where `smartctl` can be executed

---

## Installation

### Manual
1. Copy `custom_components/smart_ssh/` into your Home Assistant `config/custom_components/`
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration**
4. Search for **Remote SMART**

### HACS (Custom Repository)
1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/wuertele/remote_smart` with category **Integration**
4. Search for **Remote SMART** and install

---

## Security Model (Important)

Synology SNMP mode performs read-only SNMP table queries and does not execute
commands. Prefer SNMPv3 authPriv and restrict network access to Home Assistant.

SSH mode executes commands over SSH.

Recommended SSH best practices:
- Use a **dedicated user** on the remote host
- Prefer **key-based SSH authentication**
- If root privileges are required:
  - Use `sudo -n smartctl` with a **NOPASSWD rule limited to smartctl**
- Keep polling intervals conservative (default: 60 minutes)

The integration never logs passwords or private keys.

---

## Example Configurations

### Synology DSM over SNMP
Configure DSM through **Control Panel -> Terminal & SNMP -> SNMP** or the
`SYNO.Core.SNMP` WebAPI. Prefer SNMPv3 with authentication and privacy enabled.

The integration reads:
- `SYNOLOGY-SMART-MIB` for SMART attributes
- `SYNOLOGY-DISK-MIB` for disk model, name, temperature, and health status
- `SYNOLOGY-STORAGEIO-MIB` for device serial numbers

Do not rely on hand-editing `/etc/snmp/snmpd.conf` as the source of truth on
DSM. DSM owns the SNMP service configuration and can regenerate runtime files.

### Synology DSM over SSH
- Devices: `/dev/sata1` … `/dev/sata12`
- Command template:

    sudo smartctl -x -j -d sat {device}


### Linux
- Devices: `/dev/sda`, `/dev/sdb`, ...
- Command template:

    sudo smartctl -x -j {device}


### macOS
- Devices: `/dev/disk0`, `/dev/disk1`, ...
- Command template:

    sudo smartctl -x -j {device}


---

## Metrics Exposed (v1)

Per drive:
- Reallocated sectors
- Pending sectors
- Offline uncorrectable sectors
- Reported uncorrectable errors
- UDMA CRC errors
- Temperature
- Power-on hours
- SMART overall health

Binary indicators:
- SMART overall OK
- Has pending sectors
- Has uncorrectable errors
- Drive failing (policy-based aggregate)

---

## Status

This integration is under active development.
Expect breaking changes until the first tagged release.

See `plan.md` for full architecture and roadmap.
