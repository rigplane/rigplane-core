"""Tests for CoreRadio.update_credentials (MOR-252).

A rotated radio password must be honoured on the next full reconnect.
``soft_reconnect`` intentionally reuses the existing session token and
must NOT re-authenticate.
"""

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from rigplane.core.auth import build_login_packet as real_build_login_packet
from rigplane.runtime.radio import IcomRadio


class _AbortAfterLogin(Exception):
    """Sentinel raised to stop _connect_once right after login-packet build."""


class _FakeCtrlTransport:
    """Minimal control-transport stand-in for driving _connect_once to auth."""

    def __init__(self) -> None:
        self.my_id = 0x00010001
        self.remote_id = 0xDEADBEEF
        self._discard_data_packets = True
        self._udp_transport: object | None = object()

    async def connect(
        self, host: str, port: int, local_host: str | None = None
    ) -> None:
        pass

    async def reconnect(
        self, host: str, port: int, local_host: str | None = None
    ) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass


def test_update_credentials_mutates_stored_values() -> None:
    r = IcomRadio("192.168.1.100", username="old-user", password="old-pass")
    r.update_credentials(username="new-user", password="new-pass")
    assert r._username == "new-user"
    assert r._password == "new-pass"


def test_update_credentials_partial_update_keeps_other_field() -> None:
    r = IcomRadio("192.168.1.100", username="old-user", password="old-pass")
    r.update_credentials(password="new-pass")
    assert r._username == "old-user"
    assert r._password == "new-pass"
    r.update_credentials(username="new-user")
    assert r._username == "new-user"
    assert r._password == "new-pass"


def test_update_credentials_noop_when_no_args() -> None:
    r = IcomRadio("192.168.1.100", username="old-user", password="old-pass")
    r.update_credentials()
    assert r._username == "old-user"
    assert r._password == "old-pass"


@pytest.mark.asyncio
async def test_full_reconnect_builds_login_packet_with_new_password() -> None:
    r = IcomRadio("192.168.1.100", username="user", password="old-pass")
    r._ctrl_transport = _FakeCtrlTransport()  # type: ignore[assignment]
    r.update_credentials(password="new-pass")

    captured: dict[str, str] = {}

    def spy(username: str, password: str, **kwargs: Any) -> bytes:
        # Exercise the REAL builder so signature drift is caught.
        pkt = real_build_login_packet(username, password, **kwargs)
        assert len(pkt) == 0x80
        captured["username"] = username
        captured["password"] = password
        raise _AbortAfterLogin

    with patch("rigplane.runtime._control_phase.build_login_packet", side_effect=spy):
        with pytest.raises(_AbortAfterLogin):
            await r._control_phase._connect_once()

    assert captured == {"username": "user", "password": "new-pass"}


@pytest.mark.asyncio
async def test_soft_reconnect_reuses_token_and_does_not_reauth() -> None:
    r = IcomRadio("192.168.1.100", username="user", password="old-pass")
    r._ctrl_transport = _FakeCtrlTransport()  # type: ignore[assignment]
    r._token = 0x1234
    # Healthy CI-V transport with fresh data => soft_reconnect noop path.
    civ = MagicMock()
    civ._udp_transport = object()
    r._civ_transport = civ
    r._last_civ_data_received = time.monotonic()
    r.update_credentials(password="new-pass")

    with patch("rigplane.runtime._control_phase.build_login_packet") as mock_login:
        await r.soft_reconnect()

    mock_login.assert_not_called()
    assert r._token == 0x1234
