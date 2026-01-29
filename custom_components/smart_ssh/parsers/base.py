"""Base parser classes and data models."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ParseError(Exception):
    """Raised when parsing fails completely."""

    def __init__(self, message: str, raw_output: str | None = None) -> None:
        """Initialize ParseError."""
        super().__init__(message)
        self.raw_output = raw_output


@dataclass
class DriveReport:
    """Normalized per-drive SMART report."""

    device: str
    """Configured device token (e.g., /dev/sata1)."""

    unique_key: str
    """Unique identifier: serial number if available, else host:device."""

    timestamp: datetime
    """When this report was generated."""

    serial: str | None = None
    """Drive serial number."""

    model: str | None = None
    """Drive model name."""

    manufacturer: str | None = None
    """Drive manufacturer (if known)."""

    firmware: str | None = None
    """Firmware version."""

    capacity_bytes: int | None = None
    """Drive capacity in bytes."""

    protocol: str = "unknown"
    """Protocol: 'ata', 'nvme', or 'unknown'."""

    smart_ok: bool | None = None
    """SMART overall health status (True=PASSED, False=FAILED, None=unknown)."""

    temperature_c: float | None = None
    """Current temperature in Celsius."""

    power_on_hours: int | None = None
    """Total power-on hours."""

    metrics: dict[str, int | float | None] = field(default_factory=dict)
    """Normalized critical metrics (reallocated_sectors, pending_sectors, etc.)."""

    selftest_status: str | None = None
    """Last self-test status string."""

    error: str | None = None
    """Error message if parsing partially failed."""

    raw: dict[str, Any] | None = None
    """Raw parsed data for diagnostics (truncated)."""


class Parser(ABC):
    """Abstract base class for smartctl output parsers."""

    @abstractmethod
    def parse(self, output: str, device: str, host: str) -> DriveReport:
        """Parse smartctl output into a DriveReport.

        Args:
            output: Raw stdout from smartctl command.
            device: Device token (e.g., /dev/sata1).
            host: Remote host identifier for fallback unique_key.

        Returns:
            DriveReport with extracted data.

        Raises:
            ParseError: If parsing fails completely.
        """

    @staticmethod
    def _make_unique_key(serial: str | None, host: str, device: str) -> str:
        """Generate unique key from serial or host:device fallback."""
        if serial:
            return serial
        return f"{host}:{device}"
