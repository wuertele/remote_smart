"""Data coordinator for Remote SMART integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ALIAS_FORMAT_JSON,
    ALIAS_FORMAT_KEY_VALUE,
    ALIAS_FORMAT_NONE,
    ALIAS_FORMAT_SYNODISK,
    CONF_AUTH_METHOD,
    CONF_COMMAND_TEMPLATE,
    CONF_COMMAND_TIMEOUT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEVICE_ALIAS_COMMAND,
    CONF_DEVICE_ALIAS_FORMAT,
    CONF_DEVICES,
    CONF_FAIL_MODE,
    CONF_HOST,
    CONF_HOST_KEY_POLICY,
    CONF_MAX_PARALLEL,
    CONF_PARSER_TYPE,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_PRIVATE_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SUDO_MODE,
    CONF_SUDO_PASSWORD,
    CONF_SNMP_AUTH_KEY,
    CONF_SNMP_AUTH_PROTOCOL,
    CONF_SNMP_COMMUNITY,
    CONF_SNMP_PRIV_KEY,
    CONF_SNMP_PRIV_PROTOCOL,
    CONF_SNMP_VERSION,
    CONF_TRANSPORT,
    CONF_USERNAME,
    CRITICAL_METRICS,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_FAIL_MODE,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SNMP_AUTH_PROTOCOL,
    DEFAULT_SNMP_PRIV_PROTOCOL,
    DEFAULT_SNMP_VERSION,
    DOMAIN,
    FAIL_MODE_STALE,
    PARSER_SMARTCTL_ATA_TEXT,
    PARSER_SMARTCTL_JSON,
    STORAGE_KEY,
    STORAGE_VERSION,
    TRANSPORT_SSH,
    TRANSPORT_SYNOLOGY_SNMP,
)
from .parsers import DriveReport, ParseError, SmartctlAtaTextParser, SmartctlJsonParser
from .snmp_client import SNMPError, SynologySNMPClient
from .ssh_client import SSHClient, SSHError

if TYPE_CHECKING:
    from .parsers.base import Parser

_LOGGER = logging.getLogger(__name__)


class SmartSSHCoordinator(DataUpdateCoordinator[dict[str, DriveReport]]):
    """Coordinator for fetching remote SMART data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self._ssh_client: SSHClient | None = None
        self._parser: Parser = self._create_parser()
        self._semaphore: asyncio.Semaphore | None = None
        self._store = Store[dict[str, Any]](
            hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}"
        )
        # Baseline metrics are the values at last reset (used for delta computation)
        self._baseline_metrics: dict[str, dict[str, int | float | None]] = {}
        # Max deltas track the maximum delta seen since baseline
        self._max_deltas: dict[str, dict[str, int | float | None]] = {}
        self._last_reports: dict[str, DriveReport] = {}
        self._device_aliases: dict[str, str] = {}
        self._aliases_loaded: bool = False
        self._stored_data_loaded: bool = False

        scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data[CONF_HOST]}",
            update_interval=timedelta(minutes=scan_interval),
        )

    def _create_parser(self) -> Parser:
        """Create parser based on config."""
        parser_type = self.entry.data.get(CONF_PARSER_TYPE, PARSER_SMARTCTL_ATA_TEXT)

        if parser_type == PARSER_SMARTCTL_JSON:
            return SmartctlJsonParser()
        return SmartctlAtaTextParser()

    def _create_ssh_client(self) -> SSHClient:
        """Create SSH client from config."""
        data = self.entry.data

        return SSHClient(
            host=data[CONF_HOST],
            port=data[CONF_PORT],
            username=data[CONF_USERNAME],
            auth_method=data[CONF_AUTH_METHOD],
            private_key=data.get(CONF_PRIVATE_KEY),
            password=data.get(CONF_PASSWORD),
            host_key_policy=data[CONF_HOST_KEY_POLICY],
            sudo_mode=data.get(CONF_SUDO_MODE, "none"),
            sudo_password=data.get(CONF_SUDO_PASSWORD),
            connect_timeout=data.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
            command_timeout=data.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
        )

    async def _async_update_data(self) -> dict[str, DriveReport]:
        """Fetch SMART data from all configured devices."""
        if self.entry.data.get(CONF_TRANSPORT, TRANSPORT_SSH) == TRANSPORT_SYNOLOGY_SNMP:
            return await self._async_update_snmp_data()

        # Load stored data (baseline metrics and max deltas) on first run
        if not self._stored_data_loaded:
            await self._load_stored_data()
            self._stored_data_loaded = True

        data = self.entry.data
        devices = data.get(CONF_DEVICES, [])
        max_parallel = data.get(CONF_MAX_PARALLEL, DEFAULT_MAX_PARALLEL)
        fail_mode = data.get(CONF_FAIL_MODE, DEFAULT_FAIL_MODE)

        if not devices:
            raise UpdateFailed("No devices configured")

        # Initialize semaphore for concurrency control
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max_parallel)

        # Create SSH client for this update cycle
        self._ssh_client = self._create_ssh_client()

        try:
            await self._ssh_client.connect()

            # Load device aliases on first run
            if not self._aliases_loaded:
                await self._load_device_aliases()
                self._aliases_loaded = True

            # Fetch data from all devices concurrently (with semaphore)
            tasks = [
                self._fetch_device_data(device)
                for device in devices
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            reports: dict[str, DriveReport] = {}
            errors: list[str] = []

            for device, result in zip(devices, results):
                if isinstance(result, Exception):
                    _LOGGER.warning("Failed to fetch SMART data for %s: %s", device, result)
                    errors.append(f"{device}: {result}")

                    # Handle fail mode
                    if fail_mode == FAIL_MODE_STALE and device in self._last_reports:
                        # Keep stale data
                        report = self._last_reports[device]
                        report.error = str(result)
                        reports[report.unique_key] = report
                else:
                    reports[result.unique_key] = result
                    self._last_reports[device] = result

            if not reports:
                raise UpdateFailed(f"Failed to fetch SMART data: {'; '.join(errors)}")

            # Compute deltas
            self._compute_deltas(reports)

            # Store current metrics for next delta computation
            await self._store_metrics(reports)

            return reports

        except SSHError as err:
            raise UpdateFailed(f"SSH connection failed: {err}") from err
        finally:
            if self._ssh_client:
                await self._ssh_client.disconnect()
                self._ssh_client = None

    async def _async_update_snmp_data(self) -> dict[str, DriveReport]:
        """Fetch SMART data from Synology SNMP MIBs."""
        if not self._stored_data_loaded:
            await self._load_stored_data()
            self._stored_data_loaded = True

        data = self.entry.data
        fail_mode = data.get(CONF_FAIL_MODE, DEFAULT_FAIL_MODE)

        client = SynologySNMPClient(
            host=data[CONF_HOST],
            port=data[CONF_PORT],
            version=data.get(CONF_SNMP_VERSION, DEFAULT_SNMP_VERSION),
            community=data.get(CONF_SNMP_COMMUNITY),
            username=data.get(CONF_USERNAME),
            auth_key=data.get(CONF_SNMP_AUTH_KEY),
            auth_protocol=data.get(CONF_SNMP_AUTH_PROTOCOL, DEFAULT_SNMP_AUTH_PROTOCOL),
            priv_key=data.get(CONF_SNMP_PRIV_KEY),
            priv_protocol=data.get(CONF_SNMP_PRIV_PROTOCOL, DEFAULT_SNMP_PRIV_PROTOCOL),
            timeout=data.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
        )

        try:
            reports = await client.fetch_reports()
        except SNMPError as err:
            if fail_mode == FAIL_MODE_STALE and self._last_reports:
                stale_reports: dict[str, DriveReport] = {}
                for report in self._last_reports.values():
                    report.error = str(err)
                    stale_reports[report.unique_key] = report
                return stale_reports
            raise UpdateFailed(f"SNMP update failed: {err}") from err
        finally:
            client.close()

        self._device_aliases = {
            report.device: str(report.raw.get("disk_alias"))
            for report in reports.values()
            if report.raw and report.raw.get("disk_alias")
        }
        self._aliases_loaded = True
        self._last_reports = {report.device: report for report in reports.values()}

        self._compute_deltas(reports)
        await self._store_metrics(reports)

        return reports

    async def _fetch_device_data(self, device: str) -> DriveReport:
        """Fetch SMART data for a single device."""
        if self._semaphore is None or self._ssh_client is None:
            raise RuntimeError("Coordinator not initialized")

        async with self._semaphore:
            command_template = self.entry.data[CONF_COMMAND_TEMPLATE]
            host = self.entry.data[CONF_HOST]

            try:
                result = await self._ssh_client.execute_smartctl(
                    command_template, device
                )

                # Parse the output
                report = self._parser.parse(result.stdout, device, host)

                # Add stderr to error if present and non-empty
                if result.stderr and result.stderr.strip():
                    report.error = result.stderr.strip()[:200]

                return report

            except ParseError as err:
                _LOGGER.warning("Parse error for %s: %s", device, err)
                raise
            except SSHError as err:
                _LOGGER.warning("SSH error for %s: %s", device, err)
                raise

    def _compute_deltas(self, reports: dict[str, DriveReport]) -> None:
        """Compute max metric deltas from baseline values.

        Deltas represent the maximum change observed since the baseline was set.
        The baseline is set on first run or when reset_deltas() is called.
        """
        for unique_key, report in reports.items():
            baseline = self._baseline_metrics.get(unique_key, {})
            current_max = self._max_deltas.get(unique_key, {})
            new_max: dict[str, int | float | None] = {}

            # If no baseline exists for this drive, set it now
            if not baseline:
                self._baseline_metrics[unique_key] = dict(report.metrics)
                self._max_deltas[unique_key] = {m: 0 for m in CRITICAL_METRICS}
                continue

            for metric_name in CRITICAL_METRICS:
                current = report.metrics.get(metric_name)
                baseline_value = baseline.get(metric_name)

                if current is None or baseline_value is None:
                    # Can't compute delta
                    new_max[metric_name] = current_max.get(metric_name)
                elif current < baseline_value:
                    # Counter decreased (drive swap or reset) - reset baseline
                    _LOGGER.info(
                        "Metric %s decreased for %s (%s -> %s), resetting baseline",
                        metric_name,
                        unique_key,
                        baseline_value,
                        current,
                    )
                    # Update baseline to current value, reset max delta to 0
                    self._baseline_metrics[unique_key][metric_name] = current
                    new_max[metric_name] = 0
                else:
                    # Compute delta from baseline and track maximum
                    delta_from_baseline = current - baseline_value
                    prev_max = current_max.get(metric_name, 0) or 0
                    new_max[metric_name] = max(delta_from_baseline, prev_max)

            self._max_deltas[unique_key] = new_max

    async def _load_stored_data(self) -> None:
        """Load previously stored baseline metrics and max deltas from disk."""
        try:
            stored = await self._store.async_load()
            if stored:
                # Handle v2 format (baseline + max_deltas)
                if "baseline_metrics" in stored:
                    self._baseline_metrics = stored["baseline_metrics"]
                    self._max_deltas = stored.get("max_deltas", {})
                    _LOGGER.debug(
                        "Loaded stored baseline for %d drives",
                        len(self._baseline_metrics),
                    )
                # Handle v1 format (previous_metrics only) - migrate to v2
                elif "metrics" in stored:
                    self._baseline_metrics = stored["metrics"]
                    self._max_deltas = {}
                    _LOGGER.info(
                        "Migrated v1 stored data for %d drives",
                        len(self._baseline_metrics),
                    )
        except Exception as err:
            _LOGGER.warning("Failed to load stored data: %s", err)
            self._baseline_metrics = {}
            self._max_deltas = {}

    async def _store_metrics(self, reports: dict[str, DriveReport]) -> None:
        """Store baseline metrics and max deltas for persistence."""
        try:
            await self._store.async_save({
                "baseline_metrics": self._baseline_metrics,
                "max_deltas": self._max_deltas,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as err:
            _LOGGER.warning("Failed to store metrics: %s", err)

    def get_delta(self, unique_key: str, metric_name: str) -> int | float | None:
        """Get max delta value for a metric since baseline."""
        if unique_key not in self._max_deltas:
            return None
        return self._max_deltas[unique_key].get(metric_name)

    async def reset_deltas(self, unique_key: str | None = None) -> None:
        """Reset delta counters by setting baseline to current values.

        Args:
            unique_key: If provided, reset only this device. Otherwise reset all.
        """
        if not self.data:
            _LOGGER.warning("Cannot reset deltas: no data available")
            return

        if unique_key:
            # Reset single device
            if unique_key in self.data:
                report = self.data[unique_key]
                self._baseline_metrics[unique_key] = dict(report.metrics)
                self._max_deltas[unique_key] = {m: 0 for m in CRITICAL_METRICS}
                _LOGGER.info("Reset delta baseline for %s", unique_key)
            else:
                _LOGGER.warning("Cannot reset deltas: device %s not found", unique_key)
                return
        else:
            # Reset all devices
            for key, report in self.data.items():
                self._baseline_metrics[key] = dict(report.metrics)
                self._max_deltas[key] = {m: 0 for m in CRITICAL_METRICS}
            _LOGGER.info("Reset delta baseline for all %d devices", len(self.data))

        # Persist the new baseline
        await self._store_metrics(self.data)

        # Trigger entity updates
        self.async_set_updated_data(self.data)

    async def async_shutdown(self) -> None:
        """Shutdown the coordinator."""
        if self._ssh_client:
            await self._ssh_client.disconnect()
            self._ssh_client = None

    @property
    def host(self) -> str:
        """Return the configured host."""
        return self.entry.data[CONF_HOST]

    @property
    def device_unique_keys(self) -> list[str]:
        """Return list of all device unique keys."""
        if not self.data:
            return []
        return list(self.data.keys())

    def get_device_alias(self, device: str) -> str:
        """Get friendly alias for a device, or the device path if no alias."""
        return self._device_aliases.get(device, device)

    async def _load_device_aliases(self) -> None:
        """Load device aliases from configured command."""
        data = self.entry.data
        alias_command = data.get(CONF_DEVICE_ALIAS_COMMAND, "")
        alias_format = data.get(CONF_DEVICE_ALIAS_FORMAT, ALIAS_FORMAT_NONE)

        if not alias_command or alias_format == ALIAS_FORMAT_NONE:
            _LOGGER.debug("No device alias command configured")
            return

        if self._ssh_client is None:
            _LOGGER.warning("SSH client not connected, cannot load aliases")
            return

        try:
            # Run the alias command (without sudo wrapping)
            result = await self._ssh_client._execute_raw(alias_command, timeout=30)

            if result.exit_code != 0:
                _LOGGER.warning(
                    "Device alias command failed with exit code %d: %s",
                    result.exit_code,
                    result.stderr,
                )
                return

            # Parse the output based on format
            self._device_aliases = self._parse_alias_output(
                result.stdout, alias_format
            )

            _LOGGER.info(
                "Loaded %d device aliases: %s",
                len(self._device_aliases),
                self._device_aliases,
            )

        except SSHError as err:
            _LOGGER.warning("Failed to load device aliases: %s", err)

    def _parse_alias_output(self, output: str, format_type: str) -> dict[str, str]:
        """Parse alias command output into device->alias mapping."""
        aliases: dict[str, str] = {}

        if format_type == ALIAS_FORMAT_SYNODISK:
            aliases = self._parse_synodisk_output(output)
        elif format_type == ALIAS_FORMAT_KEY_VALUE:
            aliases = self._parse_key_value_output(output)
        elif format_type == ALIAS_FORMAT_JSON:
            aliases = self._parse_json_output(output)

        return aliases

    @staticmethod
    def _parse_synodisk_output(output: str) -> dict[str, str]:
        """Parse Synology synodisk --enum output.

        Expected format:
        ************ Disk Info ***************
        >> Disk id: 7
        >> Slot id: -1
        >> Disk path: /dev/sata1
        >> Disk model: OOS12000G
        ...
        """
        import re

        aliases: dict[str, str] = {}
        current_disk_id: str | None = None
        current_path: str | None = None

        for line in output.split("\n"):
            line = line.strip()

            # Match ">> Disk id: N"
            disk_id_match = re.match(r">>\s*Disk id:\s*(\d+)", line)
            if disk_id_match:
                # Save previous disk if we have both id and path
                if current_disk_id and current_path:
                    aliases[current_path] = f"Disk {current_disk_id}"

                current_disk_id = disk_id_match.group(1)
                current_path = None
                continue

            # Match ">> Disk path: /dev/..."
            path_match = re.match(r">>\s*Disk path:\s*(/\S+)", line)
            if path_match:
                current_path = path_match.group(1)

        # Don't forget the last disk
        if current_disk_id and current_path:
            aliases[current_path] = f"Disk {current_disk_id}"

        return aliases

    @staticmethod
    def _parse_key_value_output(output: str) -> dict[str, str]:
        """Parse key=value or key:value format (one per line).

        Expected format:
        /dev/sda=Disk 1
        /dev/sdb:Disk 2
        """
        import re

        aliases: dict[str, str] = {}

        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Try key=value or key:value
            match = re.match(r"^(\S+)[=:](.+)$", line)
            if match:
                device = match.group(1)
                alias = match.group(2).strip()
                aliases[device] = alias

        return aliases

    @staticmethod
    def _parse_json_output(output: str) -> dict[str, str]:
        """Parse JSON object mapping device paths to aliases.

        Expected format:
        {"/dev/sda": "Disk 1", "/dev/sdb": "Disk 2"}
        """
        import json

        try:
            data = json.loads(output)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError as err:
            _LOGGER.warning("Failed to parse JSON aliases: %s", err)

        return {}
