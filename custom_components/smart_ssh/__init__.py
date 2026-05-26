"""Remote SMART integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_HOST,
    CONF_TRANSPORT,
    DOMAIN,
    SERVICE_RESET_DELTAS,
    TRANSPORT_SYNOLOGY_SNMP,
)
from .coordinator import SmartSSHCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

SERVICE_RESET_DELTAS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DEVICE_ID): str,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Remote SMART component."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_reset_deltas(call: ServiceCall) -> None:
        """Handle reset_deltas service call."""
        device_id = call.data.get(ATTR_DEVICE_ID)

        if device_id:
            # Find the coordinator and unique_key for this device
            device_registry = dr.async_get(hass)
            device = device_registry.async_get(device_id)

            if not device:
                _LOGGER.error("Device %s not found", device_id)
                return

            # Find the config entry and unique_key from device identifiers
            for identifier in device.identifiers:
                if identifier[0] == DOMAIN:
                    # Identifier format: (DOMAIN, "{entry_id}_{unique_key}")
                    parts = identifier[1].split("_", 1)
                    if len(parts) == 2:
                        entry_id, unique_key = parts
                        coordinator = hass.data[DOMAIN].get(entry_id)
                        if coordinator:
                            await coordinator.reset_deltas(unique_key)
                            return

            _LOGGER.error("Could not find coordinator for device %s", device_id)
        else:
            # Reset all devices in all coordinators
            for coordinator in hass.data[DOMAIN].values():
                if isinstance(coordinator, SmartSSHCoordinator):
                    await coordinator.reset_deltas()

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_DELTAS,
        handle_reset_deltas,
        schema=SERVICE_RESET_DELTAS_SCHEMA,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Remote SMART from a config entry."""
    is_snmp = entry.data.get(CONF_TRANSPORT) == TRANSPORT_SYNOLOGY_SNMP
    gateway_name = "SMART SNMP" if is_snmp else "SMART SSH"
    gateway_model = "Synology SNMP Agent" if is_snmp else "SSH Gateway"
    gateway_manufacturer = "Synology" if is_snmp else "Remote SMART"

    # Create the hub device so child devices can reference it via via_device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"{gateway_name} ({entry.data[CONF_HOST]})",
        manufacturer=gateway_manufacturer,
        model=gateway_model,
    )

    coordinator = SmartSSHCoordinator(hass, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: SmartSSHCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
