"""Binary sensor platform for Remote SMART integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_FAIL_OFFLINE_UNC_GT,
    CONF_FAIL_PENDING_GT,
    CONF_FAIL_REPORTED_UNC_GT,
    CONF_WARN_REALLOC_DELTA_GT,
    DEFAULT_FAIL_OFFLINE_UNC_GT,
    DEFAULT_FAIL_PENDING_GT,
    DEFAULT_FAIL_REPORTED_UNC_GT,
    DEFAULT_WARN_REALLOC_DELTA_GT,
    DOMAIN,
    METRIC_OFFLINE_UNCORRECTABLE,
    METRIC_PENDING_SECTORS,
    METRIC_REALLOCATED_SECTORS,
    METRIC_REPORTED_UNCORRECT,
)
from .coordinator import SmartSSHCoordinator

if TYPE_CHECKING:
    from .parsers import DriveReport


@dataclass(frozen=True, kw_only=True)
class SmartBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a SMART binary sensor entity."""

    value_fn: Callable[[DriveReport, SmartSSHCoordinator, ConfigEntry], bool | None] | None = None
    attr_fn: Callable[[DriveReport, SmartSSHCoordinator, ConfigEntry], dict | None] | None = None


def _smart_overall_ok(report: DriveReport, coordinator: SmartSSHCoordinator, entry: ConfigEntry) -> bool | None:
    """Return True if SMART overall health is OK."""
    return report.smart_ok


def _has_pending_sectors(report: DriveReport, coordinator: SmartSSHCoordinator, entry: ConfigEntry) -> bool | None:
    """Return True if drive has pending sectors."""
    pending = report.metrics.get(METRIC_PENDING_SECTORS)
    if pending is None:
        return None
    return pending > 0


def _has_uncorrectable(report: DriveReport, coordinator: SmartSSHCoordinator, entry: ConfigEntry) -> bool | None:
    """Return True if drive has uncorrectable errors."""
    reported = report.metrics.get(METRIC_REPORTED_UNCORRECT)
    offline = report.metrics.get(METRIC_OFFLINE_UNCORRECTABLE)

    if reported is None and offline is None:
        return None

    return (reported or 0) > 0 or (offline or 0) > 0


def _drive_failing(report: DriveReport, coordinator: SmartSSHCoordinator, entry: ConfigEntry) -> bool | None:
    """Return True if drive is failing based on policy."""
    data = entry.data
    reasons: list[str] = []

    # Check pending sectors
    pending_threshold = data.get(CONF_FAIL_PENDING_GT, DEFAULT_FAIL_PENDING_GT)
    pending = report.metrics.get(METRIC_PENDING_SECTORS)
    if pending is not None and pending > pending_threshold:
        reasons.append(f"pending_sectors={pending}")

    # Check offline uncorrectable
    offline_threshold = data.get(CONF_FAIL_OFFLINE_UNC_GT, DEFAULT_FAIL_OFFLINE_UNC_GT)
    offline = report.metrics.get(METRIC_OFFLINE_UNCORRECTABLE)
    if offline is not None and offline > offline_threshold:
        reasons.append(f"offline_uncorrectable={offline}")

    # Check reported uncorrectable
    reported_threshold = data.get(CONF_FAIL_REPORTED_UNC_GT, DEFAULT_FAIL_REPORTED_UNC_GT)
    reported = report.metrics.get(METRIC_REPORTED_UNCORRECT)
    if reported is not None and reported > reported_threshold:
        reasons.append(f"reported_uncorrect={reported}")

    # Check reallocated sectors delta
    realloc_delta_threshold = data.get(CONF_WARN_REALLOC_DELTA_GT, DEFAULT_WARN_REALLOC_DELTA_GT)
    realloc_delta = coordinator.get_delta(report.unique_key, METRIC_REALLOCATED_SECTORS)
    if realloc_delta is not None and realloc_delta > realloc_delta_threshold:
        reasons.append(f"reallocated_sectors_delta={realloc_delta}")

    # Check SMART overall health
    if report.smart_ok is False:
        reasons.append("smart_status=FAILED")

    # Store reasons for attribute function
    report._failing_reasons = reasons  # type: ignore[attr-defined]

    return len(reasons) > 0


def _drive_failing_attrs(report: DriveReport, coordinator: SmartSSHCoordinator, entry: ConfigEntry) -> dict | None:
    """Return attributes for drive_failing sensor."""
    reasons = getattr(report, "_failing_reasons", [])

    return {
        "reasons": reasons,
        "policy_version": "1.0",
    }


BINARY_SENSOR_DESCRIPTIONS: tuple[SmartBinarySensorEntityDescription, ...] = (
    SmartBinarySensorEntityDescription(
        key="smart_overall_ok",
        translation_key="smart_overall_ok",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:check-circle",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda r, c, e: not _smart_overall_ok(r, c, e) if _smart_overall_ok(r, c, e) is not None else None,
    ),
    SmartBinarySensorEntityDescription(
        key="has_pending_sectors",
        translation_key="has_pending_sectors",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_has_pending_sectors,
    ),
    SmartBinarySensorEntityDescription(
        key="has_uncorrectable",
        translation_key="has_uncorrectable",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-octagon",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_has_uncorrectable,
    ),
    SmartBinarySensorEntityDescription(
        key="drive_failing",
        translation_key="drive_failing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:harddisk-remove",
        value_fn=_drive_failing,
        attr_fn=_drive_failing_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities from a config entry."""
    coordinator: SmartSSHCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SmartBinarySensor] = []

    # Wait for first data
    if coordinator.data:
        for unique_key, report in coordinator.data.items():
            for description in BINARY_SENSOR_DESCRIPTIONS:
                entities.append(
                    SmartBinarySensor(
                        coordinator=coordinator,
                        entry=entry,
                        description=description,
                        unique_key=unique_key,
                        report=report,
                    )
                )

    async_add_entities(entities)


class SmartBinarySensor(CoordinatorEntity[SmartSSHCoordinator], BinarySensorEntity):
    """Binary sensor entity for SMART status."""

    entity_description: SmartBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartSSHCoordinator,
        entry: ConfigEntry,
        description: SmartBinarySensorEntityDescription,
        unique_key: str,
        report: DriveReport,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)

        self.entity_description = description
        self._unique_key = unique_key
        self._entry = entry

        # Build unique ID
        self._attr_unique_id = f"{entry.entry_id}_{unique_key}_{description.key}"

        # Device info - link to same device as sensors
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{unique_key}")},
        )

    @property
    def is_on(self) -> bool | None:
        """Return binary sensor state."""
        if not self.coordinator.data:
            return None

        report = self.coordinator.data.get(self._unique_key)
        if not report:
            return None

        if self.entity_description.value_fn:
            return self.entity_description.value_fn(
                report, self.coordinator, self._entry
            )

        return None

    @property
    def extra_state_attributes(self) -> dict | None:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return None

        report = self.coordinator.data.get(self._unique_key)
        if not report:
            return None

        attrs = {
            "device": report.device,
            "serial": report.serial,
        }

        if self.entity_description.attr_fn:
            extra = self.entity_description.attr_fn(
                report, self.coordinator, self._entry
            )
            if extra:
                attrs.update(extra)

        return attrs

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False

        if not self.coordinator.data:
            return False

        return self._unique_key in self.coordinator.data
