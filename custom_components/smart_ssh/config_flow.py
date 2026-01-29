"""Config flow for Remote SMART over SSH integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    ALIAS_FORMAT_JSON,
    ALIAS_FORMAT_KEY_VALUE,
    ALIAS_FORMAT_NONE,
    ALIAS_FORMAT_SYNODISK,
    AUTH_METHOD_KEY,
    AUTH_METHOD_PASSWORD,
    CONF_AUTH_METHOD,
    CONF_COMMAND_TEMPLATE,
    CONF_COMMAND_TIMEOUT,
    CONF_CONNECT_TIMEOUT,
    CONF_DEVICE_ALIAS_COMMAND,
    CONF_DEVICE_ALIAS_FORMAT,
    CONF_DEVICES,
    CONF_FAIL_MODE,
    CONF_FAIL_OFFLINE_UNC_GT,
    CONF_FAIL_PENDING_GT,
    CONF_FAIL_REPORTED_UNC_GT,
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
    CONF_USERNAME,
    CONF_WARN_REALLOC_DELTA_GT,
    DEFAULT_COMMAND_TEMPLATE,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_FAIL_MODE,
    DEFAULT_FAIL_OFFLINE_UNC_GT,
    DEFAULT_FAIL_PENDING_GT,
    DEFAULT_FAIL_REPORTED_UNC_GT,
    DEFAULT_MAX_PARALLEL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WARN_REALLOC_DELTA_GT,
    DOMAIN,
    FAIL_MODE_STALE,
    FAIL_MODE_UNAVAILABLE,
    HOST_KEY_POLICY_ACCEPT_NEW,
    HOST_KEY_POLICY_STRICT,
    PARSER_SMARTCTL_ATA_TEXT,
    PARSER_SMARTCTL_JSON,
    SUDO_MODE_NONE,
    SUDO_MODE_NOPASSWD,
    SUDO_MODE_PASSWORD,
)
from .ssh_client import SSHAuthError, SSHClient, SSHConnectionError, SSHError

_LOGGER = logging.getLogger(__name__)


class SmartSSHConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Remote SMART over SSH."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the connection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate SSH connection
            try:
                await self._test_connection(user_input)
                self._data.update(user_input)
                return await self.async_step_devices()
            except SSHAuthError:
                errors["base"] = "auth_failed"
            except SSHConnectionError as err:
                _LOGGER.debug("Connection error: %s", err)
                errors["base"] = "cannot_connect"
            except SSHError as err:
                _LOGGER.debug("SSH error: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=self._get_connection_schema(user_input),
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the devices and command step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Parse devices from textarea (one per line)
            devices_text = user_input.get(CONF_DEVICES, "")
            devices = [
                d.strip()
                for d in devices_text.strip().split("\n")
                if d.strip()
            ]

            if not devices:
                errors[CONF_DEVICES] = "no_devices"
            else:
                self._data[CONF_DEVICES] = devices
                self._data[CONF_COMMAND_TEMPLATE] = user_input[CONF_COMMAND_TEMPLATE]
                self._data[CONF_PARSER_TYPE] = user_input[CONF_PARSER_TYPE]
                self._data[CONF_DEVICE_ALIAS_COMMAND] = user_input.get(CONF_DEVICE_ALIAS_COMMAND, "")
                self._data[CONF_DEVICE_ALIAS_FORMAT] = user_input.get(CONF_DEVICE_ALIAS_FORMAT, ALIAS_FORMAT_NONE)

                # Test command on first device
                try:
                    await self._test_command(devices[0])
                    return await self.async_step_options()
                except SSHError as err:
                    _LOGGER.debug("Command test failed: %s", err)
                    errors["base"] = "command_failed"

        # Default devices text
        default_devices = ""
        if user_input and CONF_DEVICES in user_input:
            default_devices = user_input[CONF_DEVICES]

        return self.async_show_form(
            step_id="devices",
            data_schema=self._get_devices_schema(default_devices, user_input),
            errors=errors,
        )

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options step."""
        if user_input is not None:
            self._data.update(user_input)

            # Create unique title based on host
            host = self._data[CONF_HOST]
            title = f"SMART ({host})"

            return self.async_create_entry(title=title, data=self._data)

        return self.async_show_form(
            step_id="options",
            data_schema=self._get_options_schema(),
        )

    async def _test_connection(self, config: dict[str, Any]) -> None:
        """Test SSH connection with provided config."""
        client = SSHClient(
            host=config[CONF_HOST],
            port=config[CONF_PORT],
            username=config[CONF_USERNAME],
            auth_method=config[CONF_AUTH_METHOD],
            private_key=config.get(CONF_PRIVATE_KEY),
            password=config.get(CONF_PASSWORD),
            host_key_policy=config[CONF_HOST_KEY_POLICY],
            sudo_mode=config.get(CONF_SUDO_MODE, SUDO_MODE_NONE),
            sudo_password=config.get(CONF_SUDO_PASSWORD),
            connect_timeout=config.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
            command_timeout=config.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
        )

        try:
            if not await client.test_connection():
                raise SSHConnectionError("Connection test failed")
        finally:
            await client.disconnect()

    async def _test_command(self, device: str) -> None:
        """Test smartctl command on a device."""
        client = SSHClient(
            host=self._data[CONF_HOST],
            port=self._data[CONF_PORT],
            username=self._data[CONF_USERNAME],
            auth_method=self._data[CONF_AUTH_METHOD],
            private_key=self._data.get(CONF_PRIVATE_KEY),
            password=self._data.get(CONF_PASSWORD),
            host_key_policy=self._data[CONF_HOST_KEY_POLICY],
            sudo_mode=self._data.get(CONF_SUDO_MODE, SUDO_MODE_NONE),
            sudo_password=self._data.get(CONF_SUDO_PASSWORD),
            connect_timeout=self._data.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
            command_timeout=self._data.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
        )

        try:
            result = await client.execute_smartctl(
                self._data[CONF_COMMAND_TEMPLATE],
                device,
            )

            # Check for critical errors (bits 0-1)
            if result.exit_code & 0b00000011:
                raise SSHError(f"smartctl failed: {result.stderr or result.stdout}")

        finally:
            await client.disconnect()

    def _get_connection_schema(
        self, user_input: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build connection step schema."""
        user_input = user_input or {}

        return vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=user_input.get(CONF_HOST, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_PORT,
                    default=user_input.get(CONF_PORT, DEFAULT_PORT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=65535,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_USERNAME,
                    default=user_input.get(CONF_USERNAME, "root"),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_AUTH_METHOD,
                    default=user_input.get(CONF_AUTH_METHOD, AUTH_METHOD_KEY),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": AUTH_METHOD_KEY, "label": "Private Key"},
                            {"value": AUTH_METHOD_PASSWORD, "label": "Password"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_PRIVATE_KEY,
                    default=user_input.get(CONF_PRIVATE_KEY, ""),
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
                vol.Optional(
                    CONF_PASSWORD,
                    default=user_input.get(CONF_PASSWORD, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    CONF_HOST_KEY_POLICY,
                    default=user_input.get(CONF_HOST_KEY_POLICY, HOST_KEY_POLICY_ACCEPT_NEW),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": HOST_KEY_POLICY_STRICT, "label": "Strict (require known_hosts)"},
                            {"value": HOST_KEY_POLICY_ACCEPT_NEW, "label": "Accept New (trust on first use)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SUDO_MODE,
                    default=user_input.get(CONF_SUDO_MODE, SUDO_MODE_NONE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": SUDO_MODE_NONE, "label": "None (run as user)"},
                            {"value": SUDO_MODE_NOPASSWD, "label": "Sudo (no password)"},
                            {"value": SUDO_MODE_PASSWORD, "label": "Sudo (with password)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SUDO_PASSWORD,
                    default=user_input.get(CONF_SUDO_PASSWORD, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    CONF_CONNECT_TIMEOUT,
                    default=user_input.get(CONF_CONNECT_TIMEOUT, DEFAULT_CONNECT_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=60,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="seconds",
                    )
                ),
                vol.Required(
                    CONF_COMMAND_TIMEOUT,
                    default=user_input.get(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=10,
                        max=120,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="seconds",
                    )
                ),
            }
        )

    def _get_devices_schema(
        self, default_devices: str = "", user_input: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build devices step schema."""
        user_input = user_input or {}

        return vol.Schema(
            {
                vol.Required(
                    CONF_DEVICES,
                    default=default_devices,
                ): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
                vol.Required(
                    CONF_COMMAND_TEMPLATE,
                    default=user_input.get(CONF_COMMAND_TEMPLATE, DEFAULT_COMMAND_TEMPLATE),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(
                    CONF_PARSER_TYPE,
                    default=user_input.get(CONF_PARSER_TYPE, PARSER_SMARTCTL_ATA_TEXT),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": PARSER_SMARTCTL_JSON, "label": "JSON (smartctl -j)"},
                            {"value": PARSER_SMARTCTL_ATA_TEXT, "label": "ATA Text (legacy smartctl)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_DEVICE_ALIAS_COMMAND,
                    default=user_input.get(CONF_DEVICE_ALIAS_COMMAND, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Optional(
                    CONF_DEVICE_ALIAS_FORMAT,
                    default=user_input.get(CONF_DEVICE_ALIAS_FORMAT, ALIAS_FORMAT_NONE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": ALIAS_FORMAT_NONE, "label": "None (use device path)"},
                            {"value": ALIAS_FORMAT_SYNODISK, "label": "Synology (synodisk --enum)"},
                            {"value": ALIAS_FORMAT_KEY_VALUE, "label": "Key=Value (path=alias per line)"},
                            {"value": ALIAS_FORMAT_JSON, "label": "JSON ({path: alias, ...})"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    def _get_options_schema(
        self, user_input: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build options step schema."""
        user_input = user_input or {}

        return vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5,
                        max=1440,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="minutes",
                    )
                ),
                vol.Required(
                    CONF_MAX_PARALLEL,
                    default=user_input.get(CONF_MAX_PARALLEL, DEFAULT_MAX_PARALLEL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=6,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_FAIL_MODE,
                    default=user_input.get(CONF_FAIL_MODE, DEFAULT_FAIL_MODE),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": FAIL_MODE_UNAVAILABLE, "label": "Mark unavailable on failure"},
                            {"value": FAIL_MODE_STALE, "label": "Keep stale data on failure"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_FAIL_PENDING_GT,
                    default=user_input.get(CONF_FAIL_PENDING_GT, DEFAULT_FAIL_PENDING_GT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_FAIL_OFFLINE_UNC_GT,
                    default=user_input.get(CONF_FAIL_OFFLINE_UNC_GT, DEFAULT_FAIL_OFFLINE_UNC_GT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_FAIL_REPORTED_UNC_GT,
                    default=user_input.get(CONF_FAIL_REPORTED_UNC_GT, DEFAULT_FAIL_REPORTED_UNC_GT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_WARN_REALLOC_DELTA_GT,
                    default=user_input.get(CONF_WARN_REALLOC_DELTA_GT, DEFAULT_WARN_REALLOC_DELTA_GT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartSSHOptionsFlow(config_entry)


class SmartSSHOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Remote SMART over SSH."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Use current values from config entry
        data = self.config_entry.data

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5,
                            max=1440,
                            step=5,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="minutes",
                        )
                    ),
                    vol.Required(
                        CONF_MAX_PARALLEL,
                        default=data.get(CONF_MAX_PARALLEL, DEFAULT_MAX_PARALLEL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=6,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_FAIL_PENDING_GT,
                        default=data.get(CONF_FAIL_PENDING_GT, DEFAULT_FAIL_PENDING_GT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_FAIL_OFFLINE_UNC_GT,
                        default=data.get(CONF_FAIL_OFFLINE_UNC_GT, DEFAULT_FAIL_OFFLINE_UNC_GT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_FAIL_REPORTED_UNC_GT,
                        default=data.get(CONF_FAIL_REPORTED_UNC_GT, DEFAULT_FAIL_REPORTED_UNC_GT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_WARN_REALLOC_DELTA_GT,
                        default=data.get(CONF_WARN_REALLOC_DELTA_GT, DEFAULT_WARN_REALLOC_DELTA_GT),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
