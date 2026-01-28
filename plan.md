# Remote SMART over SSH (Home Assistant Custom Integration) — Plan

## Summary
This repository implements a Home Assistant custom integration that monitors disk SMART health by executing a user-configured command over SSH against a user-configured set of drive identifiers (e.g., `/dev/sda`, `/dev/sata1`, `/dev/disk0`). The integration is generic: it does not assume Synology, Linux, or macOS. Users configure:
- how to connect (SSH)
- which devices to query (explicit list or discovery command)
- what command to run per device (template with `{device}`)
- how to parse output (smartctl JSON preferred; text parser fallback; regex escape hatch)

The integration exposes per-drive entities (sensors + binary sensors) and supports trend/delta tracking for critical counters.

---

## Goals
- Generic across hosts accessible by SSH (Synology DSM, Linux, macOS, etc.)
- Configurable command + configurable device list (no hardcoding `/dev/sataX`)
- Robust parsing (prefer `smartctl -j` JSON; text parsing when JSON not available)
- Expose the critical SMART counters and “policy” health states suitable for automations
- Safe execution (timeouts, limited concurrency, stable unique IDs)
- Diagnostics-friendly (surface parse/command errors without spamming logs)

Non-goals (v1)
- Native non-SSH transports (SNMP, IPMI, vendor APIs)
- Automatic OS detection or automatic `-d` flag selection (user config supplies this)
- Full SMART attribute coverage for all vendors (critical set only; extend later)
- Disk discovery across exotic HBAs without user help

---

## Critical metrics to monitor
These are the “array risk” indicators, and form the v1 entity set:

### ATA/SATA (smartctl attributes)
- Reallocated sectors (SMART ID 5)        -> `reallocated_sectors`
- Reported uncorrectable (SMART ID 187)   -> `reported_uncorrect`
- Current pending sectors (SMART ID 197)  -> `pending_sectors`
- Offline uncorrectable (SMART ID 198)    -> `offline_uncorrectable`
- UDMA CRC errors (SMART ID 199)          -> `udma_crc_errors` (signals cabling/backplane issues)
Also:
- Temperature (C)                         -> `temperature_c` (if available)
- Power-on hours                          -> `power_on_hours`
- SMART overall health (PASSED/FAILED)    -> `smart_ok`
- Self-test status / last test            -> `selftest_status` (optional v1 if easy)

### NVMe
For NVMe devices, expose the closest equivalents:
- critical warning / overall health
- temperature
- media errors
- power-on hours
(Exact keys depend on JSON output; start with JSON parsing.)

---

## UX / Configuration Model (Config Flow)
The config flow should capture four categories: Connection, Targets, Command+Parser, Polling/Behavior.

### Step 1: Connection
Fields:
- `host` (string)
- `port` (int, default 22)
- `username` (string)
- Authentication method:
  - `auth_type`: enum `private_key` | `password`
  - `private_key` (string, PEM) if `private_key`
  - `password` (string, secure) if `password`
- Host key policy:
  - `known_hosts_policy`: enum `strict` | `accept_new` (default `strict`)
- Privilege elevation:
  - `sudo_mode`: enum `none` | `sudo_no_password` | `sudo_with_password`
  - `sudo_password` (secure string) if `sudo_with_password`
- Timeouts:
  - `connect_timeout` seconds (default 10)
  - `command_timeout` seconds (default 20)

Validation:
- Attempt SSH connection
- Run a lightweight command (e.g., `echo ok`) to validate credentials

### Step 2: Targets (device list)
Support two modes:

#### Mode A: Explicit list
- `devices`: list of strings (examples: `/dev/sda`, `/dev/sata1`, `/dev/disk0`, `disk0`, etc.)

#### Mode B: Discovery command
- `discovery_command`: string executed over SSH
  - Must print one device token per line
- Optional:
  - `discovery_regex`: regex filter applied to lines (keep matches)
  - `discovery_refresh_interval_hours`: int (0 disables; default 0)

Notes:
- v1 may ship with explicit list only; discovery can be v1.1.

### Step 3: Command template and parser
- `command_template`: string; must include `{device}`
  - Examples:
    - `smartctl -x -j {device}`
    - `smartctl -x -j -d sat {device}`
    - `sudo smartctl -x -j -d sat {device}`
    - `smartctl -x {device}` (text)
- `parser_type`: enum
  - `smartctl_json` (preferred)
  - `smartctl_ata_text` (fallback)
  - `smartctl_nvme_text` (fallback)
  - `regex_map` (escape hatch)

If `regex_map`, user supplies:
- `regex_metrics`: mapping metric_name -> regex with one capture group
  - Example metric names: `reallocated_sectors`, `pending_sectors`, etc.

### Step 4: Polling and performance
- `scan_interval_minutes` (default 60)
- `max_parallel` (default 2; range 1..6)
- `per_device_timeout_seconds` (default inherits from command_timeout)
- Failure behavior:
  - `fail_mode`: enum `unavailable` | `stale` (default `unavailable`)
    - `unavailable`: set entities unavailable on failure
    - `stale`: keep last state but expose `last_error` attribute

---

## Integration internals

### Home Assistant patterns used
- Use `DataUpdateCoordinator` to manage periodic polling and shared IO.
- Entities read from coordinator data.
- Use HA `Store` to persist previous sample values for delta computation.

### Data model
Normalized per-drive report returned by parser:

`DriveReport` (conceptual):
- `device`: str (configured device token)
- `unique_key`: str (prefer serial; else host+device)
- `serial`: Optional[str]
- `model`: Optional[str]
- `manufacturer`: Optional[str]
- `protocol`: "ata"|"nvme"|"unknown"
- `smart_ok`: Optional[bool]
- `temperature_c`: Optional[float]
- `power_on_hours`: Optional[int]
- `metrics`: dict[str, int|float|str]   (normalized metrics)
- `selftest_status`: Optional[str]
- `timestamp`: datetime
- `raw`: Optional[dict] (only for diagnostics; avoid huge blobs)

Coordinator data shape:
- `dict[unique_key, DriveReport]`

### Unique ID strategy
- Primary: drive serial number (from smartctl output)
- Fallback: `<host>:<device>` if serial unavailable
- Ensure entity unique_ids include the config entry id + drive unique_key + metric name

### SSH execution layer
Implement an async SSH runner:
- Library: `asyncssh` (recommended)
- Features:
  - Connect once per coordinator update, reuse session for all commands in that tick
  - Support strict host key checking and accept-new mode
  - Per-command timeout and output capture (stdout/stderr/exit code)

Sudo support:
- none: run command as-is
- sudo_no_password: prefix with `sudo -n`
- sudo_with_password: pipe password to sudo (avoid logging sensitive data)
  - Example: `printf '%s\n' "$PW" | sudo -S <cmd>`
  - Keep password only in memory; never log the command with password

Command templating:
- Replace `{device}` with a safely-quoted device token.
- Avoid shell injection:
  - Prefer running `sh -lc` with strict quoting OR build a command list if supported.
  - At minimum, wrap the device token in single quotes and escape embedded quotes.

Concurrency:
- Use a semaphore for `max_parallel`
- Default low concurrency to avoid stressing NAS backplane / spinning disks.

### Parsers

#### Preferred: `smartctl_json`
- Requires user command to include `-j` (JSON).
- Parse JSON into normalized metrics.
- Map ATA vs NVMe formats into the critical set.

JSON mapping (approximate; refine with real samples):
- Serial: `serial_number`
- Model: `model_name` or `model_family`
- Smart OK: `smart_status.passed`
- Temperature:
  - ATA: `temperature.current` or `temperature.current_celsius` (varies)
  - NVMe: `nvme_smart_health_information_log.temperature`
- Power-on hours:
  - ATA: `power_on_time.hours`
  - NVMe: `nvme_smart_health_information_log.power_on_hours`
- ATA critical attributes:
  - Use `ata_smart_attributes.table[]` entries by `id`:
    - 5, 187, 197, 198, 199 -> raw values
  - Note: raw format may include `raw.value`

#### Fallback: `smartctl_ata_text`
- Parse classic attribute table lines:
  - Identify the "ID# ATTRIBUTE_NAME ..." table
  - Extract IDs 5/187/197/198/199 and RAW_VALUE
- Also parse:
  - overall health line: contains "PASSED" / "FAILED"
  - temperature if present ("Current Drive Temperature" or "Temperature_Celsius" attribute)
  - power-on hours if present

#### Fallback: `smartctl_nvme_text`
- Parse “SMART/Health Information” block:
  - critical warning
  - temperature
  - power on hours
  - media and data integrity errors
- Normalize to closest metrics (document exact mapping in code comments)

#### Escape hatch: `regex_map`
- User provides regexes for each metric.
- Parser runs each regex; missing match -> metric absent.
- Intended for odd platforms or custom wrapper scripts.

Parser contract:
- Must never throw uncaught exceptions to coordinator; return partial data with error fields.
- If parse fails completely: raise a controlled `ParseError` so coordinator can mark that drive unavailable and include `last_error`.

---

## Entities and HA exposure

### Device registry
Create an HA Device per physical disk when serial is known.
If serial unknown, device per configured device token.

Device info:
- name: `Disk <serial>` or `Disk <device>`
- identifiers: `(domain, config_entry_id, serial_or_device)`
- manufacturer/model if known
- Suggested `via_device`: represent the remote host as a "hub" device (optional v1; can be added later)

### Sensors (per drive)
Core numeric sensors:
- `reallocated_sectors`
- `reported_uncorrect`
- `pending_sectors`
- `offline_uncorrectable`
- `udma_crc_errors`
- `temperature_c`
- `power_on_hours`

Optional sensors (diagnostic):
- `last_update` (timestamp)
- `last_selftest_status` (string)

### Delta sensors (per drive)
Compute deltas per poll using stored previous values:
- `reallocated_sectors_delta`
- `reported_uncorrect_delta`
- `pending_sectors_delta`
- `offline_uncorrectable_delta`
- `udma_crc_errors_delta`

Delta behavior:
- If previous sample missing -> delta = 0 or `unknown` (choose `unknown` for first sample)
- If counter decreases (e.g., after drive swap) -> treat as reset; delta = 0 and update baseline.

### Binary sensors (per drive)
- `smart_overall_ok` (True/False/Unknown)
- `has_pending_sectors` (pending > 0)
- `has_uncorrectable` (reported_uncorrect > 0 OR offline_uncorrectable > 0)
- `drive_failing` (policy aggregate; see below)

### Policy aggregate: `drive_failing`
Provide a configurable policy:
Default policy:
- failing if:
  - pending_sectors > 0
  - OR offline_uncorrectable > 0
  - OR reported_uncorrect > 0
  - OR reallocated_sectors_delta > 0 (optionally warn vs fail)
Expose:
- `drive_failing` binary sensor
- attributes:
  - `reasons`: list[str]
  - `policy_version`: str
Policy configuration (v1 minimal):
- thresholds:
  - `fail_if_pending_gt` (default 0)
  - `fail_if_offline_unc_gt` (default 0)
  - `fail_if_reported_unc_gt` (default 0)
  - `warn_if_realloc_delta_gt` (default 0)
Can be an “Options” flow in HA.

---

## Persistence and state handling

### Store
Use `homeassistant.helpers.storage.Store` to persist:
- per-drive last metrics snapshot
- timestamp
- optional rolling history of last N samples (N small, e.g., 10) to support trend detection later

### Failure modes
- Per-device failure:
  - mark sensors `unavailable` or keep stale per `fail_mode`
  - record `last_error` in entity attributes or a diagnostics entity
- Connection failure:
  - all drives unavailable/stale
  - coordinator logs a single warning per cycle (rate-limited)

---

## Performance and safety considerations
- Default poll interval: 60 minutes
- Default max_parallel: 2
- Timeouts:
  - connect timeout: 10s
  - command timeout per device: 20s
- Avoid waking spun-down drives unnecessarily:
  - users can set larger intervals
  - consider an option to skip temperature if it causes wakeups (v2)
- Log hygiene:
  - do not log full stdout routinely
  - cap diagnostics raw output length if included

Security:
- Encourage dedicated remote user with least privilege.
- Prefer `sudo -n` with NOPASSWD restricted to `smartctl`.
- Strict host key checking by default.

---

## Repo structure (custom component)
Recommended structure:

custom_components/smart_ssh/
init.py
manifest.json
const.py
config_flow.py
coordinator.py
ssh_client.py
parsers/
init.py
smartctl_json.py
smartctl_ata_text.py
smartctl_nvme_text.py
regex_map.py
sensor.py
binary_sensor.py
diagnostics.py
translations/en.json
strings.json


### Key modules
- `const.py`: domain, default intervals, supported metrics constants
- `ssh_client.py`: async SSH wrapper + sudo handling + timeouts
- `coordinator.py`: enumerates devices, runs commands, parses results, stores deltas
- `parsers/*`: parsing implementations returning normalized DriveReport
- `sensor.py` / `binary_sensor.py`: entity definitions and mapping to metrics
- `diagnostics.py`: HA diagnostics support (redact secrets)

---

## Testing strategy

### Unit tests
- Parser unit tests with fixture outputs:
  - smartctl JSON ATA
  - smartctl JSON NVMe
  - smartctl text ATA
  - smartctl text NVMe (if supported)
- Validate extraction of critical IDs and correct normalization.

### Integration tests (manual)
Provide a `docs/examples.md` (optional) with known-good config examples:
- Synology: `smartctl -x -j -d sat /dev/sata1`
- Linux: `smartctl -x -j /dev/sda`
- macOS: `smartctl -x -j /dev/disk0` (if supported)

### Error path tests
- command non-zero exit codes
- timeouts
- parse failures
- SSH authentication failure

---

## Milestones

### M0: Repo bootstrapping
- Add `manifest.json`, basic module skeleton
- Add minimal config flow storing host/user/auth and explicit device list
- Add coordinator tick returning placeholder data

### M1: SSH execution and JSON parsing (v1 core)
- Implement async SSH runner and per-device command execution
- Implement `smartctl_json` parser extracting:
  - serial/model
  - smart_ok
  - temperature
  - power_on_hours
  - 5/187/197/198/199 for ATA
  - basic NVMe health equivalents
- Expose sensors for critical metrics per drive

### M2: Delta tracking
- Add Store persistence
- Add delta sensors and reset handling

### M3: Binary sensors and policy
- Add `smart_overall_ok`, `has_pending_sectors`, `has_uncorrectable`, `drive_failing`
- Add Options flow to configure thresholds

### M4: Text parser fallback
- Implement `smartctl_ata_text` (and nvme text if practical)
- Allow per-entry parser selection

### M5: Discovery mode (optional)
- Add discovery command + regex filter + refresh schedule

### M6: Packaging + documentation
- README with setup examples and security guidance
- Diagnostics and troubleshooting section

---

## Example configurations

### Synology (SAT required)
- devices: `/dev/sata1`..`/dev/sata12`
- command_template: `sudo smartctl -x -j -d sat {device}`
- parser_type: `smartctl_json`

### Linux
- devices: `/dev/sda`, `/dev/sdb`, ...
- command_template: `sudo smartctl -x -j {device}`
- parser_type: `smartctl_json`

### macOS (if smartctl present)
- devices: `/dev/disk0`, `/dev/disk1`, ...
- command_template: `sudo smartctl -x -j {device}`
- parser_type: `smartctl_json` or fallback text

---

## Notes for implementers (Claude Code)
- Prefer JSON parsing (`smartctl -j`) to avoid vendor-specific text quirks.
- When mapping JSON, be defensive: fields vary by smartctl version and device type.
- Keep secrets out of logs and diagnostics (host/user ok; passwords/keys never).
- Keep coordinator update bounded; do not spawn 12 parallel commands by default.
- Treat missing metrics as unknown, not zero.
- Ensure entity unique_ids remain stable if drive order changes (serial-based).
