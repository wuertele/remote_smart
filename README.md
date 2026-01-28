# Remote SMART over SSH (Home Assistant Custom Integration)

Remote SMART over SSH is a Home Assistant custom integration that monitors disk
S.M.A.R.T. health by executing a user-configured command over SSH against a
user-configured set of storage devices.

It is intentionally **generic**:
- Works with Synology DSM, generic Linux hosts, macOS, or any SSH-accessible system
- Does not assume `/dev/sdX`, `/dev/sataX`, or any specific platform
- The command, device list, and parsing strategy are fully configurable

The recommended usage is with `smartctl -j` (JSON output), but text parsing and
regex-based fallbacks are supported.

---

## Features

- SSH-based polling of SMART data
- Configurable command template (`{device}` substitution)
- Explicit device lists or discovery commands
- Robust parsing (JSON preferred; text fallback)
- Per-drive sensors and binary sensors
- Delta tracking (detects *changes*, not just absolute values)
- Policy-based “drive failing” indicator
- Designed for large arrays and NAS environments

---

## Typical Use Cases

- Synology NAS via `/dev/sataX` with `-d sat`
- Linux servers with HBAs or SATA/NVMe disks
- macOS systems with Homebrew smartmontools
- Any remote host where `smartctl` can be executed

---

## Installation

### Manual
1. Copy `custom_components/smart_ssh/` into your Home Assistant `config/custom_components/`
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration**
4. Search for **Remote SMART over SSH**

### HACS
(Not yet published. Manual installation only.)

---

## Security Model (Important)

This integration executes commands over SSH.

Recommended best practices:
- Use a **dedicated user** on the remote host
- Prefer **key-based SSH authentication**
- If root privileges are required:
  - Use `sudo -n smartctl` with a **NOPASSWD rule limited to smartctl**
- Keep polling intervals conservative (default: 60 minutes)

The integration never logs passwords or private keys.

---

## Example Configurations

### Synology DSM
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
