"""Constants for the Remote SMART integration."""
from typing import Final

DOMAIN: Final = "smart_ssh"

# Config keys - Connection
CONF_TRANSPORT: Final = "transport"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_USERNAME: Final = "username"
CONF_AUTH_METHOD: Final = "auth_method"
CONF_PRIVATE_KEY: Final = "private_key"
CONF_PASSWORD: Final = "password"
CONF_HOST_KEY_POLICY: Final = "host_key_policy"
CONF_SUDO_MODE: Final = "sudo_mode"
CONF_SUDO_PASSWORD: Final = "sudo_password"
CONF_CONNECT_TIMEOUT: Final = "connect_timeout"
CONF_COMMAND_TIMEOUT: Final = "command_timeout"

# Config keys - Devices and Command
CONF_DEVICES: Final = "devices"
CONF_COMMAND_TEMPLATE: Final = "command_template"
CONF_PARSER_TYPE: Final = "parser_type"
CONF_DEVICE_ALIAS_COMMAND: Final = "device_alias_command"
CONF_DEVICE_ALIAS_FORMAT: Final = "device_alias_format"

# Config keys - SNMP
CONF_SNMP_VERSION: Final = "snmp_version"
CONF_SNMP_COMMUNITY: Final = "snmp_community"
CONF_SNMP_AUTH_PROTOCOL: Final = "snmp_auth_protocol"
CONF_SNMP_AUTH_KEY: Final = "snmp_auth_key"
CONF_SNMP_PRIV_PROTOCOL: Final = "snmp_priv_protocol"
CONF_SNMP_PRIV_KEY: Final = "snmp_priv_key"

# Config keys - Options
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_MAX_PARALLEL: Final = "max_parallel"
CONF_FAIL_MODE: Final = "fail_mode"

# Config keys - Thresholds (for drive_failing policy)
CONF_FAIL_PENDING_GT: Final = "fail_if_pending_gt"
CONF_FAIL_OFFLINE_UNC_GT: Final = "fail_if_offline_unc_gt"
CONF_FAIL_REPORTED_UNC_GT: Final = "fail_if_reported_unc_gt"
CONF_WARN_REALLOC_DELTA_GT: Final = "warn_if_realloc_delta_gt"

# Auth method options
AUTH_METHOD_KEY: Final = "private_key"
AUTH_METHOD_PASSWORD: Final = "password"

# Transport options
TRANSPORT_SSH: Final = "ssh"
TRANSPORT_SYNOLOGY_SNMP: Final = "synology_snmp"

# Host key policy options
HOST_KEY_POLICY_STRICT: Final = "strict"
HOST_KEY_POLICY_ACCEPT_NEW: Final = "accept_new"

# Sudo mode options
SUDO_MODE_NONE: Final = "none"
SUDO_MODE_NOPASSWD: Final = "sudo_no_password"
SUDO_MODE_PASSWORD: Final = "sudo_with_password"

# Parser type options
PARSER_SMARTCTL_JSON: Final = "smartctl_json"
PARSER_SMARTCTL_ATA_TEXT: Final = "smartctl_ata_text"

# Fail mode options
FAIL_MODE_UNAVAILABLE: Final = "unavailable"
FAIL_MODE_STALE: Final = "stale"

# Device alias format options
ALIAS_FORMAT_NONE: Final = "none"
ALIAS_FORMAT_SYNODISK: Final = "synodisk"
ALIAS_FORMAT_KEY_VALUE: Final = "key_value"
ALIAS_FORMAT_JSON: Final = "json"

# SNMP options
SNMP_VERSION_2C: Final = "2c"
SNMP_VERSION_3: Final = "3"
SNMP_AUTH_PROTOCOL_NONE: Final = "none"
SNMP_AUTH_PROTOCOL_MD5: Final = "hmac-md5"
SNMP_AUTH_PROTOCOL_SHA: Final = "hmac-sha"
SNMP_PRIV_PROTOCOL_NONE: Final = "none"
SNMP_PRIV_PROTOCOL_DES: Final = "des"
SNMP_PRIV_PROTOCOL_AES: Final = "aes-cfb-128"

# Defaults
DEFAULT_PORT: Final = 22
DEFAULT_SNMP_PORT: Final = 161
DEFAULT_CONNECT_TIMEOUT: Final = 10
DEFAULT_COMMAND_TIMEOUT: Final = 30
DEFAULT_SCAN_INTERVAL: Final = 60  # minutes
DEFAULT_MAX_PARALLEL: Final = 2
DEFAULT_FAIL_MODE: Final = FAIL_MODE_UNAVAILABLE
DEFAULT_COMMAND_TEMPLATE: Final = "smartctl -x -d sat {device}"
DEFAULT_SNMP_VERSION: Final = SNMP_VERSION_3
DEFAULT_SNMP_AUTH_PROTOCOL: Final = SNMP_AUTH_PROTOCOL_SHA
DEFAULT_SNMP_PRIV_PROTOCOL: Final = SNMP_PRIV_PROTOCOL_AES

# Default thresholds
DEFAULT_FAIL_PENDING_GT: Final = 0
DEFAULT_FAIL_OFFLINE_UNC_GT: Final = 0
DEFAULT_FAIL_REPORTED_UNC_GT: Final = 0
DEFAULT_WARN_REALLOC_DELTA_GT: Final = 0

# SMART attribute IDs (ATA)
SMART_ATTR_REALLOCATED_SECTORS: Final = 5
SMART_ATTR_REPORTED_UNCORRECT: Final = 187
SMART_ATTR_PENDING_SECTORS: Final = 197
SMART_ATTR_OFFLINE_UNCORRECTABLE: Final = 198
SMART_ATTR_UDMA_CRC_ERRORS: Final = 199
SMART_ATTR_POWER_ON_HOURS: Final = 9
SMART_ATTR_TEMPERATURE: Final = 194
SMART_ATTR_AIRFLOW_TEMP: Final = 190

# Metric keys (normalized names used in DriveReport)
METRIC_REALLOCATED_SECTORS: Final = "reallocated_sectors"
METRIC_REPORTED_UNCORRECT: Final = "reported_uncorrect"
METRIC_PENDING_SECTORS: Final = "pending_sectors"
METRIC_OFFLINE_UNCORRECTABLE: Final = "offline_uncorrectable"
METRIC_UDMA_CRC_ERRORS: Final = "udma_crc_errors"
METRIC_POWER_ON_HOURS: Final = "power_on_hours"
METRIC_TEMPERATURE_C: Final = "temperature_c"

# Critical metrics that get delta tracking
CRITICAL_METRICS: Final = [
    METRIC_REALLOCATED_SECTORS,
    METRIC_REPORTED_UNCORRECT,
    METRIC_PENDING_SECTORS,
    METRIC_OFFLINE_UNCORRECTABLE,
    METRIC_UDMA_CRC_ERRORS,
]

# All numeric metrics
ALL_METRICS: Final = CRITICAL_METRICS + [
    METRIC_POWER_ON_HOURS,
    METRIC_TEMPERATURE_C,
]

# Storage key for persistence
STORAGE_KEY: Final = f"{DOMAIN}_data"
STORAGE_VERSION: Final = 2  # v2: max delta tracking with baseline

# Service names
SERVICE_RESET_DELTAS: Final = "reset_deltas"

# Service attributes
ATTR_DEVICE_ID: Final = "device_id"
