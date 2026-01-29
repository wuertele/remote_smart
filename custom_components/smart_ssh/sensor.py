"""Sensor platform for Remote SMART over SSH integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CRITICAL_METRICS,
    DOMAIN,
    METRIC_OFFLINE_UNCORRECTABLE,
    METRIC_PENDING_SECTORS,
    METRIC_POWER_ON_HOURS,
    METRIC_REALLOCATED_SECTORS,
    METRIC_REPORTED_UNCORRECT,
    METRIC_TEMPERATURE_C,
    METRIC_UDMA_CRC_ERRORS,
)
from .coordinator import SmartSSHCoordinator

if TYPE_CHECKING:
    from .parsers import DriveReport


@dataclass(frozen=True, kw_only=True)
class SmartSensorEntityDescription(SensorEntityDescription):
    """Describes a SMART sensor entity."""

    metric_key: str | None = None
    is_delta: bool = False


SENSOR_DESCRIPTIONS: tuple[SmartSensorEntityDescription, ...] = (
    # Critical metrics
    SmartSensorEntityDescription(
        key="reallocated_sectors",
        translation_key="reallocated_sectors",
        metric_key=METRIC_REALLOCATED_SECTORS,
        icon="mdi:harddisk",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="reported_uncorrect",
        translation_key="reported_uncorrect",
        metric_key=METRIC_REPORTED_UNCORRECT,
        icon="mdi:alert-circle",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="pending_sectors",
        translation_key="pending_sectors",
        metric_key=METRIC_PENDING_SECTORS,
        icon="mdi:alert",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="offline_uncorrectable",
        translation_key="offline_uncorrectable",
        metric_key=METRIC_OFFLINE_UNCORRECTABLE,
        icon="mdi:alert-octagon",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="udma_crc_errors",
        translation_key="udma_crc_errors",
        metric_key=METRIC_UDMA_CRC_ERRORS,
        icon="mdi:cable-data",
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Temperature
    SmartSensorEntityDescription(
        key="temperature",
        translation_key="temperature",
        metric_key=METRIC_TEMPERATURE_C,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Power-on hours
    SmartSensorEntityDescription(
        key="power_on_hours",
        translation_key="power_on_hours",
        metric_key=METRIC_POWER_ON_HOURS,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Delta sensors
    SmartSensorEntityDescription(
        key="reallocated_sectors_delta",
        translation_key="reallocated_sectors_delta",
        metric_key=METRIC_REALLOCATED_SECTORS,
        is_delta=True,
        icon="mdi:delta",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="reported_uncorrect_delta",
        translation_key="reported_uncorrect_delta",
        metric_key=METRIC_REPORTED_UNCORRECT,
        is_delta=True,
        icon="mdi:delta",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="pending_sectors_delta",
        translation_key="pending_sectors_delta",
        metric_key=METRIC_PENDING_SECTORS,
        is_delta=True,
        icon="mdi:delta",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="offline_uncorrectable_delta",
        translation_key="offline_uncorrectable_delta",
        metric_key=METRIC_OFFLINE_UNCORRECTABLE,
        is_delta=True,
        icon="mdi:delta",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartSensorEntityDescription(
        key="udma_crc_errors_delta",
        translation_key="udma_crc_errors_delta",
        metric_key=METRIC_UDMA_CRC_ERRORS,
        is_delta=True,
        icon="mdi:delta",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    coordinator: SmartSSHCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SmartSensor] = []

    # Wait for first data
    if coordinator.data:
        for unique_key, report in coordinator.data.items():
            for description in SENSOR_DESCRIPTIONS:
                entities.append(
                    SmartSensor(
                        coordinator=coordinator,
                        entry=entry,
                        description=description,
                        unique_key=unique_key,
                        report=report,
                    )
                )

    async_add_entities(entities)


class SmartSensor(CoordinatorEntity[SmartSSHCoordinator], SensorEntity):
    """Sensor entity for SMART metrics."""

    entity_description: SmartSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartSSHCoordinator,
        entry: ConfigEntry,
        description: SmartSensorEntityDescription,
        unique_key: str,
        report: DriveReport,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

        self.entity_description = description
        self._unique_key = unique_key
        self._entry_id = entry.entry_id

        # Build unique ID
        self._attr_unique_id = f"{entry.entry_id}_{unique_key}_{description.key}"

        # Device info - create device per physical drive
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{unique_key}")},
            name=self._build_device_name(report, coordinator),
            manufacturer=report.manufacturer or self._infer_manufacturer(report.model),
            model=report.model,
            sw_version=report.firmware,
            via_device=(DOMAIN, entry.entry_id),
        )

    @staticmethod
    def _build_device_name(report: DriveReport, coordinator: SmartSSHCoordinator) -> str:
        """Build a friendly device name including device alias."""
        # Get device alias (e.g., "Disk 7" for Synology, or "/dev/sata1" as fallback)
        device_alias = coordinator.get_device_alias(report.device)

        if report.serial:
            return f"Disk {report.serial} ({device_alias})"
        return f"Disk ({device_alias})"

    @staticmethod
    def _infer_manufacturer(model: str | None) -> str | None:
        """Try to infer manufacturer from model name."""
        if not model:
            return None

        model_upper = model.upper()

        # Common manufacturer prefixes
        if model_upper.startswith(("ST", "SEAGATE")):
            return "Seagate"
        if model_upper.startswith(("WD", "WDC")):
            return "Western Digital"
        if model_upper.startswith("TOSHIBA"):
            return "Toshiba"
        if model_upper.startswith(("HGST", "HUS")):
            return "HGST"
        if model_upper.startswith("HITACHI"):
            return "Hitachi"
        if model_upper.startswith("SAMSUNG"):
            return "Samsung"
        if model_upper.startswith("INTEL"):
            return "Intel"
        if model_upper.startswith("CRUCIAL"):
            return "Crucial"
        if model_upper.startswith("KINGSTON"):
            return "Kingston"
        if model_upper.startswith("SANDISK"):
            return "SanDisk"

        return None

    @property
    def native_value(self) -> int | float | None:
        """Return the sensor value."""
        if not self.coordinator.data:
            return None

        report = self.coordinator.data.get(self._unique_key)
        if not report:
            return None

        description = self.entity_description

        if description.is_delta:
            # Get delta value from coordinator
            return self.coordinator.get_delta(
                self._unique_key, description.metric_key
            )

        if description.metric_key == METRIC_TEMPERATURE_C:
            return report.temperature_c

        if description.metric_key == METRIC_POWER_ON_HOURS:
            return report.power_on_hours

        # Get from metrics dict
        return report.metrics.get(description.metric_key)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return None

        report = self.coordinator.data.get(self._unique_key)
        if not report:
            return None

        attrs = {
            "device": report.device,
            "serial": report.serial,
            "last_update": report.timestamp.isoformat(),
        }

        if report.error:
            attrs["last_error"] = report.error

        return attrs

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.last_update_success:
            return False

        if not self.coordinator.data:
            return False

        return self._unique_key in self.coordinator.data
