"""Diagnostics support for Remote SMART over SSH."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import (
    CONF_PASSWORD,
    CONF_PRIVATE_KEY,
    CONF_SUDO_PASSWORD,
    DOMAIN,
)
from .coordinator import SmartSSHCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_PRIVATE_KEY,
    CONF_SUDO_PASSWORD,
    "password",
    "private_key",
    "sudo_password",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SmartSSHCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Redact sensitive data from config
    config_data = async_redact_data(dict(entry.data), TO_REDACT)

    # Build drive reports summary
    drives: list[dict[str, Any]] = []
    if coordinator.data:
        for unique_key, report in coordinator.data.items():
            drives.append({
                "unique_key": unique_key,
                "device": report.device,
                "serial": report.serial,
                "model": report.model,
                "firmware": report.firmware,
                "protocol": report.protocol,
                "smart_ok": report.smart_ok,
                "temperature_c": report.temperature_c,
                "power_on_hours": report.power_on_hours,
                "metrics": report.metrics,
                "error": report.error,
                "timestamp": report.timestamp.isoformat() if report.timestamp else None,
            })

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": config_data,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
        "drives": drives,
    }
