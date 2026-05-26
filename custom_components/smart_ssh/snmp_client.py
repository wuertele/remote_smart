"""Synology SNMP client for collecting disk SMART reports."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any

from pyasn1.type.univ import OctetString
from pysnmp.error import PySnmpError
import pysnmp.hlapi.v3arch.asyncio as hlapi
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    Udp6TransportTarget,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd,
    is_end_of_mib,
)

from .const import (
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
    SNMP_AUTH_PROTOCOL_MD5,
    SNMP_AUTH_PROTOCOL_NONE,
    SNMP_AUTH_PROTOCOL_SHA,
    SNMP_PRIV_PROTOCOL_AES,
    SNMP_PRIV_PROTOCOL_DES,
    SNMP_PRIV_PROTOCOL_NONE,
    SNMP_VERSION_3,
)
from .parsers import DriveReport

_LOGGER = logging.getLogger(__name__)

SYNOLOGY_DISK_BASE = "1.3.6.1.4.1.6574.2.1.1"
SYNOLOGY_SMART_BASE = "1.3.6.1.4.1.6574.5.1.1"
SYNOLOGY_STORAGE_IO_BASE = "1.3.6.1.4.1.6574.101.1.1"

DISK_COL_ID = 2
DISK_COL_MODEL = 3
DISK_COL_TYPE = 4
DISK_COL_STATUS = 5
DISK_COL_TEMPERATURE = 6
DISK_COL_BAD_SECTOR = 9
DISK_COL_NAME = 12
DISK_COL_HEALTH_STATUS = 13

SMART_COL_DEVICE = 2
SMART_COL_ATTR_NAME = 3
SMART_COL_ATTR_ID = 4
SMART_COL_RAW = 8
SMART_COL_STATUS = 9
SMART_COL_RAW64 = 10

STORAGE_IO_COL_DEVICE = 2
STORAGE_IO_COL_SERIAL = 14

ATTR_ID_TO_METRIC = {
    SMART_ATTR_REALLOCATED_SECTORS: METRIC_REALLOCATED_SECTORS,
    SMART_ATTR_REPORTED_UNCORRECT: METRIC_REPORTED_UNCORRECT,
    SMART_ATTR_PENDING_SECTORS: METRIC_PENDING_SECTORS,
    SMART_ATTR_OFFLINE_UNCORRECTABLE: METRIC_OFFLINE_UNCORRECTABLE,
    SMART_ATTR_UDMA_CRC_ERRORS: METRIC_UDMA_CRC_ERRORS,
    SMART_ATTR_POWER_ON_HOURS: METRIC_POWER_ON_HOURS,
}

AUTH_PROTOCOLS = {
    SNMP_AUTH_PROTOCOL_NONE: hlapi.usmNoAuthProtocol,
    SNMP_AUTH_PROTOCOL_MD5: hlapi.usmHMACMD5AuthProtocol,
    SNMP_AUTH_PROTOCOL_SHA: hlapi.usmHMACSHAAuthProtocol,
}

PRIV_PROTOCOLS = {
    SNMP_PRIV_PROTOCOL_NONE: hlapi.usmNoPrivProtocol,
    SNMP_PRIV_PROTOCOL_DES: hlapi.usmDESPrivProtocol,
    SNMP_PRIV_PROTOCOL_AES: hlapi.usmAesCfb128Protocol,
}


class SNMPError(Exception):
    """Base exception for SNMP errors."""


class SNMPConnectionError(SNMPError):
    """Raised when SNMP connection setup fails."""


class SNMPCommandError(SNMPError):
    """Raised when an SNMP query fails."""


@dataclass(slots=True)
class SmartAttr:
    """Single SMART attribute row from the Synology SMART MIB."""

    name: str | None = None
    attr_id: int | None = None
    raw: int | None = None
    raw64: int | None = None
    status: str | None = None

    @property
    def raw_value(self) -> int | None:
        """Return the best raw value for this SMART attribute."""
        if self.raw64 is not None:
            return self.raw64
        return self.raw


@dataclass(slots=True)
class DiskInfo:
    """Single disk row from the Synology Disk MIB."""

    alias: str | None = None
    model: str | None = None
    disk_type: str | None = None
    status: int | None = None
    temperature_c: int | None = None
    bad_sector_count: int | None = None
    name: str | None = None
    health_status: int | None = None


@dataclass(slots=True)
class StorageInfo:
    """Single device row from the Synology StorageIO MIB."""

    device: str | None = None
    serial: str | None = None


@dataclass(slots=True)
class DeviceSmartData:
    """SMART rows grouped by Linux device path."""

    device: str
    attrs: dict[int, SmartAttr] = field(default_factory=dict)


class SynologySNMPClient:
    """Collect Synology disk SMART data over SNMP."""

    def __init__(
        self,
        host: str,
        port: int,
        version: str,
        community: str | None = None,
        username: str | None = None,
        auth_key: str | None = None,
        auth_protocol: str = SNMP_AUTH_PROTOCOL_NONE,
        priv_key: str | None = None,
        priv_protocol: str = SNMP_PRIV_PROTOCOL_NONE,
        timeout: float = 10.0,
    ) -> None:
        """Initialize the SNMP client."""
        self.host = host
        self.port = int(port)
        self.version = version
        self.community = community or "public"
        self.username = username or ""
        self.auth_key = auth_key or None
        self.auth_protocol = auth_protocol
        self.priv_key = priv_key or None
        self.priv_protocol = priv_protocol
        self.timeout = float(timeout)

        self._engine = SnmpEngine()
        self._target: UdpTransportTarget | Udp6TransportTarget | None = None

    async def fetch_reports(self) -> dict[str, DriveReport]:
        """Fetch and normalize all Synology disk reports."""
        await self._ensure_target()

        smart_rows = await self._walk(SYNOLOGY_SMART_BASE)
        disk_rows = await self._walk(SYNOLOGY_DISK_BASE)
        storage_rows = await self._walk(SYNOLOGY_STORAGE_IO_BASE)

        smart_devices = self._parse_smart_rows(smart_rows)
        disk_by_device = self._parse_disk_rows(disk_rows, list(smart_devices))
        storage_by_device = self._parse_storage_rows(storage_rows)

        reports: dict[str, DriveReport] = {}
        for device, smart_data in smart_devices.items():
            disk_info = disk_by_device.get(device, DiskInfo())
            storage_info = storage_by_device.get(device, StorageInfo())
            report = self._build_report(device, smart_data, disk_info, storage_info)
            reports[report.unique_key] = report

        if not reports:
            raise SNMPCommandError("Synology SNMP returned no disk reports")

        return reports

    async def test_connection(self) -> bool:
        """Return True if the Synology Disk MIB responds."""
        try:
            rows = await self._walk(f"{SYNOLOGY_DISK_BASE}.{DISK_COL_ID}")
        except SNMPError:
            return False
        return bool(rows)

    def close(self) -> None:
        """Close PySNMP transport resources."""
        if self._engine.transport_dispatcher:
            self._engine.close_dispatcher()
        self._target = None

    async def _ensure_target(self) -> None:
        """Create the UDP transport target."""
        if self._target is not None:
            return

        try:
            self._target = await UdpTransportTarget.create(
                (self.host, self.port),
                timeout=self.timeout,
            )
        except PySnmpError:
            try:
                self._target = await Udp6TransportTarget.create(
                    (self.host, self.port),
                    timeout=self.timeout,
                )
            except PySnmpError as err:
                raise SNMPConnectionError(f"Invalid SNMP host {self.host}: {err}") from err

    def _auth_data(self) -> CommunityData | UsmUserData:
        """Build PySNMP authentication data."""
        if self.version == SNMP_VERSION_3:
            if not self.username:
                raise SNMPConnectionError("SNMPv3 username is required")
            if self.auth_protocol not in AUTH_PROTOCOLS:
                raise SNMPConnectionError(f"Unsupported SNMP auth protocol: {self.auth_protocol}")
            if self.priv_protocol not in PRIV_PROTOCOLS:
                raise SNMPConnectionError(f"Unsupported SNMP privacy protocol: {self.priv_protocol}")
            if self.auth_protocol != SNMP_AUTH_PROTOCOL_NONE and not self.auth_key:
                raise SNMPConnectionError("SNMPv3 auth key is required")
            if self.priv_protocol != SNMP_PRIV_PROTOCOL_NONE and not self.priv_key:
                raise SNMPConnectionError("SNMPv3 privacy key is required")

            return UsmUserData(
                self.username,
                authKey=self.auth_key,
                privKey=self.priv_key,
                authProtocol=AUTH_PROTOCOLS[self.auth_protocol],
                privProtocol=PRIV_PROTOCOLS[self.priv_protocol],
            )

        return CommunityData(self.community, mpModel=1)

    async def _walk(self, base_oid: str) -> list[tuple[str, Any]]:
        """Walk an SNMP subtree and return OID/value pairs."""
        await self._ensure_target()
        if self._target is None:
            raise SNMPConnectionError("SNMP target was not created")

        auth_data = self._auth_data()
        context = ContextData()
        var_binds: tuple[ObjectType, ...] = (
            ObjectType(ObjectIdentity(base_oid)),
        )
        rows: list[tuple[str, Any]] = []
        last_oid: tuple[int, ...] = ()

        while True:
            result = await bulk_cmd(
                self._engine,
                auth_data,
                self._target,
                context,
                0,
                50,
                *var_binds,
            )
            error_indication, error_status, error_index, returned_binds = result

            if error_indication:
                raise SNMPCommandError(str(error_indication))
            if error_status:
                raise SNMPCommandError(
                    f"{error_status.prettyPrint()} at "
                    f"{error_index and returned_binds[int(error_index) - 1][0] or '?'}"
                )

            if not returned_binds:
                break

            next_var_binds: list[ObjectType] = []
            for var_bind in returned_binds:
                oid = str(var_bind[0])
                oid_tuple = self._oid_tuple(oid)
                if not oid.startswith(f"{base_oid}."):
                    return rows
                if oid_tuple <= last_oid:
                    return rows

                rows.append((oid, var_bind[1]))
                last_oid = oid_tuple
                next_var_binds.append(ObjectType(ObjectIdentity(oid)))

            if is_end_of_mib(returned_binds):
                break

            var_binds = tuple(next_var_binds[-1:])

        return rows

    @staticmethod
    def _oid_tuple(oid: str) -> tuple[int, ...]:
        """Return an OID as a numeric tuple for lexical comparison."""
        return tuple(int(part) for part in oid.split("."))

    @staticmethod
    def _parse_column_oid(base_oid: str, oid: str) -> tuple[int, int]:
        """Return (column, row_index) from a Synology table OID."""
        suffix = oid.removeprefix(f"{base_oid}.")
        column, row_index = suffix.split(".", 1)
        return int(column), int(row_index)

    def _parse_smart_rows(
        self,
        rows: list[tuple[str, Any]],
    ) -> dict[str, DeviceSmartData]:
        """Parse the Synology SMART MIB rows."""
        row_data: dict[int, dict[int, Any]] = {}
        for oid, value in rows:
            column, row_index = self._parse_column_oid(SYNOLOGY_SMART_BASE, oid)
            row_data.setdefault(row_index, {})[column] = value

        devices: dict[str, DeviceSmartData] = {}
        for row_index in sorted(row_data):
            row = row_data[row_index]
            device = self._decode_text(row.get(SMART_COL_DEVICE))
            attr_id = self._decode_int(row.get(SMART_COL_ATTR_ID))
            if not device or attr_id is None:
                continue

            smart_data = devices.setdefault(device, DeviceSmartData(device=device))
            smart_data.attrs[attr_id] = SmartAttr(
                name=self._decode_text(row.get(SMART_COL_ATTR_NAME)),
                attr_id=attr_id,
                raw=self._decode_int(row.get(SMART_COL_RAW)),
                raw64=self._decode_int(row.get(SMART_COL_RAW64)),
                status=self._decode_text(row.get(SMART_COL_STATUS)),
            )

        return devices

    def _parse_disk_rows(
        self,
        rows: list[tuple[str, Any]],
        device_order: list[str],
    ) -> dict[str, DiskInfo]:
        """Parse the Synology Disk MIB rows and map them to SMART devices.

        Synology's Disk MIB does not expose the Linux device path. On DSM 7, the
        Disk MIB row order matches the SMART MIB's first-seen device order, which
        is the same ordering reported by ``synodisk --enum``.
        """
        row_data: dict[int, dict[int, Any]] = {}
        for oid, value in rows:
            column, row_index = self._parse_column_oid(SYNOLOGY_DISK_BASE, oid)
            row_data.setdefault(row_index, {})[column] = value

        disk_by_device: dict[str, DiskInfo] = {}
        for ordinal, row_index in enumerate(sorted(row_data)):
            if ordinal >= len(device_order):
                break
            row = row_data[row_index]
            device = device_order[ordinal]
            disk_by_device[device] = DiskInfo(
                alias=self._decode_text(row.get(DISK_COL_ID)),
                model=self._decode_text(row.get(DISK_COL_MODEL)),
                disk_type=self._decode_text(row.get(DISK_COL_TYPE)),
                status=self._decode_int(row.get(DISK_COL_STATUS)),
                temperature_c=self._decode_int(row.get(DISK_COL_TEMPERATURE)),
                bad_sector_count=self._decode_int(row.get(DISK_COL_BAD_SECTOR)),
                name=self._decode_text(row.get(DISK_COL_NAME)),
                health_status=self._decode_int(row.get(DISK_COL_HEALTH_STATUS)),
            )

        return disk_by_device

    def _parse_storage_rows(
        self,
        rows: list[tuple[str, Any]],
    ) -> dict[str, StorageInfo]:
        """Parse the Synology StorageIO MIB rows."""
        row_data: dict[int, dict[int, Any]] = {}
        for oid, value in rows:
            column, row_index = self._parse_column_oid(SYNOLOGY_STORAGE_IO_BASE, oid)
            row_data.setdefault(row_index, {})[column] = value

        storage_by_device: dict[str, StorageInfo] = {}
        for row in row_data.values():
            device_name = self._decode_text(row.get(STORAGE_IO_COL_DEVICE))
            if not device_name:
                continue
            device = f"/dev/{device_name}"
            storage_by_device[device] = StorageInfo(
                device=device,
                serial=self._decode_text(row.get(STORAGE_IO_COL_SERIAL)),
            )

        return storage_by_device

    def _build_report(
        self,
        device: str,
        smart_data: DeviceSmartData,
        disk_info: DiskInfo,
        storage_info: StorageInfo,
    ) -> DriveReport:
        """Build a normalized DriveReport from Synology SNMP rows."""
        metrics: dict[str, int | float | None] = {}
        for attr_id, metric_name in ATTR_ID_TO_METRIC.items():
            if attr := smart_data.attrs.get(attr_id):
                metrics[metric_name] = attr.raw_value

        temperature = self._extract_temperature(smart_data, disk_info)
        if temperature is not None:
            metrics[METRIC_TEMPERATURE_C] = temperature

        smart_ok = None
        if disk_info.health_status is not None:
            smart_ok = disk_info.health_status == 1

        serial = storage_info.serial
        unique_key = serial or f"{self.host}:{device}"

        return DriveReport(
            device=device,
            unique_key=unique_key,
            timestamp=datetime.now(timezone.utc),
            serial=serial,
            model=disk_info.model,
            firmware=None,
            capacity_bytes=None,
            protocol=(disk_info.disk_type or "unknown").lower(),
            smart_ok=smart_ok,
            temperature_c=temperature,
            power_on_hours=metrics.get(METRIC_POWER_ON_HOURS),
            metrics=metrics,
            selftest_status=None,
            raw={
                "disk_alias": disk_info.alias,
                "disk_name": disk_info.name,
                "disk_status": disk_info.status,
                "disk_health_status": disk_info.health_status,
                "attributes": {
                    attr_id: attr.raw_value
                    for attr_id, attr in smart_data.attrs.items()
                },
            },
        )

    @staticmethod
    def _extract_temperature(
        smart_data: DeviceSmartData,
        disk_info: DiskInfo,
    ) -> float | None:
        """Extract current temperature in Celsius."""
        if disk_info.temperature_c is not None:
            return float(disk_info.temperature_c)

        for attr_id in (SMART_ATTR_TEMPERATURE, SMART_ATTR_AIRFLOW_TEMP):
            if attr := smart_data.attrs.get(attr_id):
                if attr.raw_value is not None:
                    return float(attr.raw_value)

        return None

    @staticmethod
    def _decode_int(value: Any) -> int | None:
        """Decode a PySNMP integer-like value."""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decode_text(value: Any) -> str | None:
        """Decode a PySNMP text-like value."""
        if value is None:
            return None
        if isinstance(value, OctetString):
            try:
                return value.asOctets().decode("utf-8").strip()
            except UnicodeDecodeError:
                return value.prettyPrint().strip()
        return str(value.prettyPrint() if hasattr(value, "prettyPrint") else value).strip()
