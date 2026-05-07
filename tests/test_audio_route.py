"""AudioRoute resolver tests for WSJT-X DATA policy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock


def _radio(*, backend_id: str, data_mode_count: int = 1) -> AsyncMock:
    radio = AsyncMock()
    radio.backend_id = backend_id
    radio.profile = SimpleNamespace(data_mode_count=data_mode_count)
    return radio


def test_direct_lan_multi_data_resolves_data2_lan_policy() -> None:
    from rigplane.audio.route import (
        DataModePolicy,
        RadioTransport,
        TxAudioSource,
        resolve_audio_route,
        rigctld_wsjtx_policy,
    )

    route = resolve_audio_route(_radio(backend_id="rigplane", data_mode_count=3))

    assert route.radio_transport == RadioTransport.LAN
    assert route.tx_audio_source == TxAudioSource.LAN
    assert route.data_mode_policy == DataModePolicy.DATA2_LAN
    assert route.bridge_required is False
    assert rigctld_wsjtx_policy(route) == (2, 5)


def test_direct_lan_single_data_falls_back_to_legacy_policy() -> None:
    from rigplane.audio.route import (
        DataModePolicy,
        TxAudioSource,
        resolve_audio_route,
        rigctld_wsjtx_policy,
    )

    route = resolve_audio_route(_radio(backend_id="rigplane", data_mode_count=1))

    assert route.tx_audio_source == TxAudioSource.LAN
    assert route.data_mode_policy == DataModePolicy.LEGACY
    assert rigctld_wsjtx_policy(route) == (None, None)


def test_serial_usb_route_never_selects_data2_lan() -> None:
    from rigplane.audio.route import (
        DataModePolicy,
        RadioTransport,
        TxAudioSource,
        resolve_audio_route,
        rigctld_wsjtx_policy,
    )

    route = resolve_audio_route(_radio(backend_id="icom_serial", data_mode_count=3))

    assert route.radio_transport == RadioTransport.SERIAL
    assert route.tx_audio_source == TxAudioSource.USB
    assert route.data_mode_policy == DataModePolicy.DATA1_USB
    assert route.bridge_required is True
    assert rigctld_wsjtx_policy(route) == (None, None)


def test_unknown_route_does_not_change_data_source() -> None:
    from rigplane.audio.route import (
        DataModePolicy,
        TxAudioSource,
        resolve_audio_route,
        rigctld_wsjtx_policy,
    )

    route = resolve_audio_route(_radio(backend_id="unknown", data_mode_count=3))

    assert route.tx_audio_source == TxAudioSource.UNAVAILABLE
    assert route.data_mode_policy == DataModePolicy.LEGACY
    assert rigctld_wsjtx_policy(route) == (None, None)
