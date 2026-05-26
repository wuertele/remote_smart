"""Button platform for Remote SMART integration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartSSHCoordinator

if TYPE_CHECKING:
    from .parsers import DriveReport


BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="reset_deltas",
    translation_key="reset_deltas",
    icon="mdi:restart",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator: SmartSSHCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ResetDeltasButton] = []

    if coordinator.data:
        for unique_key, report in coordinator.data.items():
            entities.append(
                ResetDeltasButton(
                    coordinator=coordinator,
                    entry=entry,
                    unique_key=unique_key,
                    report=report,
                )
            )

    async_add_entities(entities)


class ResetDeltasButton(CoordinatorEntity[SmartSSHCoordinator], ButtonEntity):
    """Button to reset delta counters for a drive."""

    entity_description = BUTTON_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartSSHCoordinator,
        entry: ConfigEntry,
        unique_key: str,
        report: DriveReport,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)

        self._unique_key = unique_key

        # Build unique ID
        self._attr_unique_id = f"{entry.entry_id}_{unique_key}_reset_deltas"

        # Device info - link to same device as sensors
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{unique_key}")},
        )

    async def async_press(self) -> None:
        """Handle button press - reset delta counters for this device."""
        await self.coordinator.reset_deltas(self._unique_key)
