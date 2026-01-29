"""Parser for smartctl JSON output."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..const import (
    METRIC_OFFLINE_UNCORRECTABLE,
    METRIC_PENDING_SECTORS,
    METRIC_POWER_ON_HOURS,
    METRIC_REALLOCATED_SECTORS,
    METRIC_REPORTED_UNCORRECT,
    METRIC_TEMPERATURE_C,
    METRIC_UDMA_CRC_ERRORS,
    SMART_ATTR_OFFLINE_UNCORRECTABLE,
    SMART_ATTR_PENDING_SECTORS,
    SMART_ATTR_POWER_ON_HOURS,
    SMART_ATTR_REALLOCATED_SECTORS,
    SMART_ATTR_REPORTED_UNCORRECT,
    SMART_ATTR_UDMA_CRC_ERRORS,
)
from .base import DriveReport, ParseError, Parser

_LOGGER = logging.getLogger(__name__)

# Map SMART attribute IDs to metric names
ATTR_ID_TO_METRIC = {
    SMART_ATTR_REALLOCATED_SECTORS: METRIC_REALLOCATED_SECTORS,
    SMART_ATTR_REPORTED_UNCORRECT: METRIC_REPORTED_UNCORRECT,
    SMART_ATTR_PENDING_SECTORS: METRIC_PENDING_SECTORS,
    SMART_ATTR_OFFLINE_UNCORRECTABLE: METRIC_OFFLINE_UNCORRECTABLE,
    SMART_ATTR_UDMA_CRC_ERRORS: METRIC_UDMA_CRC_ERRORS,
}


class SmartctlJsonParser(Parser):
    """Parser for smartctl JSON output (-j flag)."""

    def parse(self, output: str, device: str, host: str) -> DriveReport:
        """Parse smartctl JSON output.

        Args:
            output: Raw stdout from smartctl -j command.
            device: Device token (e.g., /dev/sata1).
            host: Remote host identifier for fallback unique_key.

        Returns:
            DriveReport with extracted data.

        Raises:
            ParseError: If parsing fails completely.
        """
        if not output or not output.strip():
            raise ParseError("Empty output from smartctl", output)

        try:
            data = json.loads(output)
        except json.JSONDecodeError as err:
            raise ParseError(f"Invalid JSON: {err}", output[:500]) from err

        # Check for smartctl error in JSON
        if "smartctl" in data:
            smartctl_info = data["smartctl"]
            exit_status = smartctl_info.get("exit_status", 0)
            # Critical error bits (0-2) indicate serious problems
            if exit_status & 0b00000011:
                messages = smartctl_info.get("messages", [])
                error_msg = "; ".join(m.get("string", "") for m in messages if m.get("severity") == "error")
                if error_msg:
                    raise ParseError(f"smartctl error: {error_msg}", None)

        # Determine protocol (ATA vs NVMe)
        protocol = self._detect_protocol(data)

        # Extract device info
        serial = data.get("serial_number")
        model = data.get("model_name") or data.get("model_family")
        firmware = data.get("firmware_version")
        capacity = self._extract_capacity(data)

        # Extract SMART health
        smart_ok = self._extract_smart_health(data)

        # Extract temperature
        temperature = self._extract_temperature(data, protocol)

        # Extract power-on hours
        power_on_hours = self._extract_power_on_hours(data, protocol)

        # Extract metrics based on protocol
        if protocol == "nvme":
            metrics = self._extract_nvme_metrics(data)
        else:
            metrics = self._extract_ata_metrics(data)

        # Extract self-test status
        selftest_status = self._extract_selftest_status(data)

        # Build unique key
        unique_key = self._make_unique_key(serial, host, device)

        return DriveReport(
            device=device,
            unique_key=unique_key,
            timestamp=datetime.now(timezone.utc),
            serial=serial,
            model=model,
            firmware=firmware,
            capacity_bytes=capacity,
            protocol=protocol,
            smart_ok=smart_ok,
            temperature_c=temperature,
            power_on_hours=power_on_hours,
            metrics=metrics,
            selftest_status=selftest_status,
            raw=self._truncate_raw(data),
        )

    @staticmethod
    def _detect_protocol(data: dict) -> str:
        """Detect drive protocol from JSON data."""
        device_info = data.get("device", {})
        protocol = device_info.get("protocol", "").lower()
        if protocol in ("nvme", "ata"):
            return protocol

        # Check for NVMe-specific fields
        if "nvme_smart_health_information_log" in data:
            return "nvme"

        # Check for ATA-specific fields
        if "ata_smart_attributes" in data:
            return "ata"

        return "unknown"

    @staticmethod
    def _extract_capacity(data: dict) -> int | None:
        """Extract capacity in bytes."""
        user_capacity = data.get("user_capacity", {})
        if isinstance(user_capacity, dict):
            return user_capacity.get("bytes")
        return None

    @staticmethod
    def _extract_smart_health(data: dict) -> bool | None:
        """Extract SMART overall health status."""
        smart_status = data.get("smart_status", {})
        if isinstance(smart_status, dict):
            return smart_status.get("passed")
        return None

    @staticmethod
    def _extract_temperature(data: dict, protocol: str) -> float | None:
        """Extract current temperature."""
        if protocol == "nvme":
            nvme_health = data.get("nvme_smart_health_information_log", {})
            temp = nvme_health.get("temperature")
            if temp is not None:
                return float(temp)
        else:
            # ATA: try temperature object first
            temp_obj = data.get("temperature", {})
            if isinstance(temp_obj, dict):
                current = temp_obj.get("current")
                if current is not None:
                    return float(current)

        return None

    @staticmethod
    def _extract_power_on_hours(data: dict, protocol: str) -> int | None:
        """Extract power-on hours."""
        if protocol == "nvme":
            nvme_health = data.get("nvme_smart_health_information_log", {})
            hours = nvme_health.get("power_on_hours")
            if hours is not None:
                return int(hours)
        else:
            # ATA: try power_on_time object
            power_on = data.get("power_on_time", {})
            if isinstance(power_on, dict):
                hours = power_on.get("hours")
                if hours is not None:
                    return int(hours)

        return None

    @staticmethod
    def _extract_ata_metrics(data: dict) -> dict[str, int | float | None]:
        """Extract critical metrics from ATA SMART attributes."""
        metrics: dict[str, int | float | None] = {}

        ata_attrs = data.get("ata_smart_attributes", {})
        table = ata_attrs.get("table", [])

        for attr in table:
            attr_id = attr.get("id")
            if attr_id in ATTR_ID_TO_METRIC:
                metric_name = ATTR_ID_TO_METRIC[attr_id]
                raw = attr.get("raw", {})

                # Raw value can be in different formats
                if isinstance(raw, dict):
                    value = raw.get("value")
                elif isinstance(raw, int):
                    value = raw
                else:
                    continue

                if value is not None:
                    metrics[metric_name] = int(value)

        return metrics

    @staticmethod
    def _extract_nvme_metrics(data: dict) -> dict[str, int | float | None]:
        """Extract metrics from NVMe health information."""
        metrics: dict[str, int | float | None] = {}

        nvme_health = data.get("nvme_smart_health_information_log", {})

        # NVMe doesn't have the same attributes as ATA
        # Map available fields to closest equivalents
        media_errors = nvme_health.get("media_errors")
        if media_errors is not None:
            # Map media errors to offline_uncorrectable as closest equivalent
            metrics[METRIC_OFFLINE_UNCORRECTABLE] = int(media_errors)

        # NVMe critical warning byte (if non-zero, indicates problems)
        critical_warning = nvme_health.get("critical_warning")
        if critical_warning is not None and critical_warning != 0:
            _LOGGER.warning("NVMe critical warning: %d", critical_warning)

        return metrics

    @staticmethod
    def _extract_selftest_status(data: dict) -> str | None:
        """Extract last self-test status."""
        # ATA self-test log
        selftest_log = data.get("ata_smart_self_test_log", {})
        standard = selftest_log.get("standard", {})
        table = standard.get("table", [])

        if table:
            last_test = table[0]
            test_type = last_test.get("type", {}).get("string", "Unknown")
            status = last_test.get("status", {}).get("string", "Unknown")
            return f"{test_type}: {status}"

        return None

    @staticmethod
    def _truncate_raw(data: dict, max_keys: int = 10) -> dict | None:
        """Truncate raw data for diagnostics."""
        if not data:
            return None

        # Only keep essential keys for diagnostics
        keep_keys = [
            "serial_number",
            "model_name",
            "firmware_version",
            "smart_status",
            "temperature",
            "power_on_time",
        ]

        return {k: data[k] for k in keep_keys if k in data}
