"""Parser for smartctl ATA text output."""
from __future__ import annotations

import re
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
    SMART_ATTR_AIRFLOW_TEMP,
    SMART_ATTR_OFFLINE_UNCORRECTABLE,
    SMART_ATTR_PENDING_SECTORS,
    SMART_ATTR_POWER_ON_HOURS,
    SMART_ATTR_REALLOCATED_SECTORS,
    SMART_ATTR_REPORTED_UNCORRECT,
    SMART_ATTR_TEMPERATURE,
    SMART_ATTR_UDMA_CRC_ERRORS,
)
from .base import DriveReport, ParseError, Parser

# Regex patterns for parsing smartctl text output
RE_DEVICE_MODEL = re.compile(r"^Device Model:\s+(.+)$", re.MULTILINE)
RE_MODEL_FAMILY = re.compile(r"^Model Family:\s+(.+)$", re.MULTILINE)
RE_SERIAL = re.compile(r"^Serial Number:\s+(.+)$", re.MULTILINE)
RE_FIRMWARE = re.compile(r"^Firmware Version:\s+(.+)$", re.MULTILINE)
RE_CAPACITY = re.compile(r"^User Capacity:\s+([\d,]+)\s+bytes", re.MULTILINE)
RE_SMART_HEALTH = re.compile(
    r"SMART overall-health self-assessment test result:\s+(\w+)", re.MULTILINE
)
RE_SELFTEST_STATUS = re.compile(
    r"^#\s*1\s+(\S+(?:\s+\S+)*?)\s+(Completed|Interrupted|Aborted|Fatal|Unknown)",
    re.MULTILINE,
)

# SMART attribute table line pattern
# ID# ATTRIBUTE_NAME FLAGS VALUE WORST THRESH FAIL RAW_VALUE
# Example:
#   5 Reallocated_Sector_Ct  PO--CK   100   100   010    -    0
RE_ATTR_LINE = re.compile(
    r"^\s*(\d+)\s+"  # ID
    r"(\S+)\s+"  # ATTRIBUTE_NAME
    r"(\S+)\s+"  # FLAGS
    r"(\d+)\s+"  # VALUE
    r"(\d+)\s+"  # WORST
    r"(\d+)\s+"  # THRESH
    r"(\S+)\s+"  # FAIL (- or FAILING_NOW or In_the_past)
    r"(.+)$",  # RAW_VALUE (may contain spaces)
    re.MULTILINE,
)

# Map SMART attribute IDs to metric names
ATTR_ID_TO_METRIC = {
    SMART_ATTR_REALLOCATED_SECTORS: METRIC_REALLOCATED_SECTORS,
    SMART_ATTR_REPORTED_UNCORRECT: METRIC_REPORTED_UNCORRECT,
    SMART_ATTR_PENDING_SECTORS: METRIC_PENDING_SECTORS,
    SMART_ATTR_OFFLINE_UNCORRECTABLE: METRIC_OFFLINE_UNCORRECTABLE,
    SMART_ATTR_UDMA_CRC_ERRORS: METRIC_UDMA_CRC_ERRORS,
    SMART_ATTR_POWER_ON_HOURS: METRIC_POWER_ON_HOURS,
}


class SmartctlAtaTextParser(Parser):
    """Parser for smartctl ATA text output (non-JSON)."""

    def parse(self, output: str, device: str, host: str) -> DriveReport:
        """Parse smartctl ATA text output.

        Args:
            output: Raw stdout from smartctl command.
            device: Device token (e.g., /dev/sata1).
            host: Remote host identifier for fallback unique_key.

        Returns:
            DriveReport with extracted data.

        Raises:
            ParseError: If parsing fails completely.
        """
        if not output or not output.strip():
            raise ParseError("Empty output from smartctl", output)

        # Check for common error indicators
        if "Unable to detect device type" in output:
            raise ParseError("smartctl unable to detect device type", output)
        if "No such device" in output:
            raise ParseError(f"Device {device} not found", output)

        # Extract device info
        serial = self._extract_match(RE_SERIAL, output)
        model = self._extract_match(RE_DEVICE_MODEL, output)
        if not model:
            model = self._extract_match(RE_MODEL_FAMILY, output)
        firmware = self._extract_match(RE_FIRMWARE, output)
        capacity = self._extract_capacity(output)

        # Extract SMART health
        smart_ok = self._extract_smart_health(output)

        # Parse SMART attributes
        attributes = self._parse_attributes(output)

        # Extract temperature (try ID 194 first, then ID 190)
        temperature = self._extract_temperature(attributes, output)

        # Extract power-on hours
        power_on_hours = attributes.get(SMART_ATTR_POWER_ON_HOURS)

        # Build metrics dict
        metrics: dict[str, int | float | None] = {}
        for attr_id, metric_name in ATTR_ID_TO_METRIC.items():
            if attr_id in attributes:
                metrics[metric_name] = attributes[attr_id]

        # Extract self-test status
        selftest_status = self._extract_selftest_status(output)

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
            protocol="ata",
            smart_ok=smart_ok,
            temperature_c=temperature,
            power_on_hours=power_on_hours,
            metrics=metrics,
            selftest_status=selftest_status,
            raw=self._truncate_raw(attributes),
        )

    @staticmethod
    def _extract_match(pattern: re.Pattern, text: str) -> str | None:
        """Extract first capture group from regex match."""
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _extract_capacity(text: str) -> int | None:
        """Extract capacity in bytes."""
        match = RE_CAPACITY.search(text)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_smart_health(text: str) -> bool | None:
        """Extract SMART overall health status."""
        match = RE_SMART_HEALTH.search(text)
        if match:
            status = match.group(1).upper()
            if status == "PASSED":
                return True
            if status == "FAILED":
                return False
        return None

    @staticmethod
    def _parse_attributes(text: str) -> dict[int, int]:
        """Parse SMART attributes table into dict of ID -> raw value."""
        attributes: dict[int, int] = {}

        for match in RE_ATTR_LINE.finditer(text):
            try:
                attr_id = int(match.group(1))
                raw_value_str = match.group(8).strip()

                # Raw value may have additional info in parentheses
                # e.g., "37 (0 14 0 0 0)" or "37 (Min/Max 22/40)"
                # We want just the first number
                raw_parts = raw_value_str.split()
                if raw_parts:
                    raw_value = int(raw_parts[0])
                    attributes[attr_id] = raw_value
            except (ValueError, IndexError):
                continue

        return attributes

    @staticmethod
    def _extract_temperature(attributes: dict[int, int], text: str) -> float | None:
        """Extract temperature from attributes or SCT section."""
        # Try SMART attribute 194 (Temperature_Celsius)
        if SMART_ATTR_TEMPERATURE in attributes:
            return float(attributes[SMART_ATTR_TEMPERATURE])

        # Try SMART attribute 190 (Airflow_Temperature_Cel)
        if SMART_ATTR_AIRFLOW_TEMP in attributes:
            return float(attributes[SMART_ATTR_AIRFLOW_TEMP])

        # Try SCT Temperature section
        # "Current Temperature:                    37 Celsius"
        sct_match = re.search(r"Current Temperature:\s+(\d+)\s+Celsius", text)
        if sct_match:
            try:
                return float(sct_match.group(1))
            except ValueError:
                pass

        return None

    @staticmethod
    def _extract_selftest_status(text: str) -> str | None:
        """Extract last self-test status."""
        match = RE_SELFTEST_STATUS.search(text)
        if match:
            test_type = match.group(1)
            status = match.group(2)
            return f"{test_type}: {status}"
        return None

    @staticmethod
    def _truncate_raw(data: Any, max_size: int = 1000) -> dict | None:
        """Truncate raw data for diagnostics."""
        if not data:
            return None
        # Just store attribute IDs and values for diagnostics
        return {"attributes": dict(data)}
