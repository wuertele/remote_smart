"""Async SSH client for executing smartctl commands."""
from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

from .const import (
    AUTH_METHOD_KEY,
    AUTH_METHOD_PASSWORD,
    HOST_KEY_POLICY_ACCEPT_NEW,
    HOST_KEY_POLICY_STRICT,
    SUDO_MODE_NONE,
    SUDO_MODE_NOPASSWD,
    SUDO_MODE_PASSWORD,
)

if TYPE_CHECKING:
    from asyncssh import SSHClientConnection

_LOGGER = logging.getLogger(__name__)


class SSHError(Exception):
    """Base exception for SSH errors."""


class SSHConnectionError(SSHError):
    """Raised when SSH connection fails."""


class SSHAuthError(SSHError):
    """Raised when SSH authentication fails."""


class SSHCommandError(SSHError):
    """Raised when command execution fails."""

    def __init__(
        self,
        message: str,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Initialize SSHCommandError."""
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class SSHTimeoutError(SSHError):
    """Raised when SSH operation times out."""


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int


class SSHClient:
    """Async SSH client for remote command execution."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        auth_method: str,
        private_key: str | None = None,
        password: str | None = None,
        host_key_policy: str = HOST_KEY_POLICY_STRICT,
        sudo_mode: str = SUDO_MODE_NONE,
        sudo_password: str | None = None,
        connect_timeout: float = 10.0,
        command_timeout: float = 30.0,
    ) -> None:
        """Initialize SSH client.

        Args:
            host: Remote hostname or IP.
            port: SSH port.
            username: SSH username.
            auth_method: 'private_key' or 'password'.
            private_key: PEM-encoded private key (if auth_method is 'private_key').
            password: Password (if auth_method is 'password').
            host_key_policy: 'strict' or 'accept_new'.
            sudo_mode: 'none', 'sudo_no_password', or 'sudo_with_password'.
            sudo_password: Password for sudo (if sudo_mode is 'sudo_with_password').
            connect_timeout: Connection timeout in seconds.
            command_timeout: Default command timeout in seconds.
        """
        self.host = host
        self.port = port
        self.username = username
        self.auth_method = auth_method
        self.private_key = private_key
        self.password = password
        self.host_key_policy = host_key_policy
        self.sudo_mode = sudo_mode
        self.sudo_password = sudo_password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout

        self._connection: SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish SSH connection."""
        async with self._lock:
            if self._connection is not None:
                return

            try:
                options = self._build_connect_options()
                self._connection = await asyncio.wait_for(
                    asyncssh.connect(
                        self.host,
                        port=self.port,
                        username=self.username,
                        **options,
                    ),
                    timeout=self.connect_timeout,
                )
                _LOGGER.debug("SSH connection established to %s@%s", self.username, self.host)
            except asyncio.TimeoutError as err:
                raise SSHTimeoutError(
                    f"Connection to {self.host}:{self.port} timed out"
                ) from err
            except asyncssh.PermissionDenied as err:
                raise SSHAuthError(f"Authentication failed for {self.username}@{self.host}") from err
            except asyncssh.HostKeyNotVerifiable as err:
                raise SSHConnectionError(
                    f"Host key verification failed for {self.host}. "
                    "Add the host key to known_hosts or use 'accept_new' policy."
                ) from err
            except (OSError, asyncssh.Error) as err:
                raise SSHConnectionError(f"Failed to connect to {self.host}:{self.port}: {err}") from err

    def _build_connect_options(self) -> dict:
        """Build asyncssh connection options."""
        options: dict = {
            "known_hosts": None if self.host_key_policy == HOST_KEY_POLICY_ACCEPT_NEW else (),
        }

        if self.auth_method == AUTH_METHOD_KEY:
            if self.private_key:
                # Load key from string
                try:
                    options["client_keys"] = [asyncssh.import_private_key(self.private_key)]
                except asyncssh.KeyImportError as err:
                    raise SSHAuthError(f"Invalid private key: {err}") from err
        elif self.auth_method == AUTH_METHOD_PASSWORD:
            options["password"] = self.password
            options["client_keys"] = []  # Disable key auth

        return options

    async def disconnect(self) -> None:
        """Close SSH connection."""
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                await self._connection.wait_closed()
                self._connection = None
                _LOGGER.debug("SSH connection closed to %s", self.host)

    async def execute(
        self,
        command: str,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute a command over SSH.

        Args:
            command: Command to execute.
            timeout: Command timeout (uses default if not specified).

        Returns:
            CommandResult with stdout, stderr, and exit code.

        Raises:
            SSHConnectionError: If not connected.
            SSHTimeoutError: If command times out.
            SSHCommandError: If command fails.
        """
        if self._connection is None:
            await self.connect()

        if self._connection is None:
            raise SSHConnectionError("Not connected")

        timeout = timeout or self.command_timeout

        # Build the actual command with sudo handling
        actual_command = self._wrap_with_sudo(command)

        try:
            result = await asyncio.wait_for(
                self._connection.run(actual_command, check=False),
                timeout=timeout,
            )

            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_status or 0,
            )

        except asyncio.TimeoutError as err:
            raise SSHTimeoutError(f"Command timed out after {timeout}s: {command}") from err
        except asyncssh.ChannelOpenError as err:
            # Connection may have dropped
            self._connection = None
            raise SSHConnectionError(f"Channel error: {err}") from err

    def _wrap_with_sudo(self, command: str) -> str:
        """Wrap command with sudo if configured."""
        if self.sudo_mode == SUDO_MODE_NONE:
            return command

        if self.sudo_mode == SUDO_MODE_NOPASSWD:
            return f"sudo -n {command}"

        if self.sudo_mode == SUDO_MODE_PASSWORD:
            # Use printf to pipe password to sudo -S
            # Escape the password for shell
            escaped_pw = shlex.quote(self.sudo_password or "")
            return f"printf '%s\\n' {escaped_pw} | sudo -S {command}"

        return command

    async def execute_smartctl(
        self,
        command_template: str,
        device: str,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute smartctl command for a specific device.

        Args:
            command_template: Command template with {device} placeholder.
            device: Device path to substitute.
            timeout: Command timeout.

        Returns:
            CommandResult with stdout, stderr, and exit code.
        """
        # Safely quote the device to prevent injection
        safe_device = shlex.quote(device)
        command = command_template.replace("{device}", safe_device)

        result = await self.execute(command, timeout=timeout)

        # smartctl uses specific exit codes:
        # Bit 0: Command line parsing error
        # Bit 1: Device open failed
        # Bit 2: Some SMART or ATA command failed
        # Bit 3: SMART status check returned "DISK FAILING"
        # Bit 4: Prefail attributes <= threshold
        # Bit 5: Some attributes have been <= threshold at some time
        # Bit 6: Error log contains errors
        # Bit 7: Self-test log contains errors
        #
        # We only fail on bits 0-2 (critical errors), others are informational
        critical_bits = result.exit_code & 0b00000111
        if critical_bits != 0:
            _LOGGER.debug(
                "smartctl returned exit code %d for %s (critical bits: %d)",
                result.exit_code,
                device,
                critical_bits,
            )

        return result

    async def test_connection(self) -> bool:
        """Test SSH connection with a simple command.

        Returns:
            True if connection and command execution work.
        """
        try:
            await self.connect()
            # Use raw execute without sudo - just test basic SSH connectivity
            result = await self._execute_raw("echo ok", timeout=10)
            return result.exit_code == 0 and "ok" in result.stdout
        except SSHError:
            return False

    async def _execute_raw(
        self,
        command: str,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute a command without sudo wrapping.

        Used for connection testing where sudo may not be allowed for all commands.
        """
        if self._connection is None:
            await self.connect()

        if self._connection is None:
            raise SSHConnectionError("Not connected")

        timeout = timeout or self.command_timeout

        try:
            result = await asyncio.wait_for(
                self._connection.run(command, check=False),
                timeout=timeout,
            )

            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_status or 0,
            )

        except asyncio.TimeoutError as err:
            raise SSHTimeoutError(f"Command timed out after {timeout}s: {command}") from err
        except asyncssh.ChannelOpenError as err:
            self._connection = None
            raise SSHConnectionError(f"Channel error: {err}") from err

    async def __aenter__(self) -> SSHClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()
