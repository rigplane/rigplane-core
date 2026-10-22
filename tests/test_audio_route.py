"""AudioRoute resolver tests for WSJT-X DATA policy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from rigplane.profiles import get_radio_profile
from rigplane.types import AudioCodec


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


def test_mk2_lan_route_selects_declared_data1_lan_input() -> None:
    from rigplane.audio.route import resolve_audio_route, rigctld_wsjtx_policy
    from rigplane.radio import IcomRadio

    route = resolve_audio_route(IcomRadio("192.0.2.1", model="IC-7300MK2"))

    assert route.data_mode_policy.value == "data1_lan"
    assert rigctld_wsjtx_policy(route) == (1, 5)


def test_single_data_lan_requires_declared_supported_lan_input() -> None:
    from rigplane.audio.route import resolve_audio_route, rigctld_wsjtx_policy

    for model in ("IC-705", "IC-9700", "IC-7300"):
        radio = SimpleNamespace(
            backend_id="rigplane",
            profile=get_radio_profile(model),
            supports_command=lambda _name: True,
        )
        assert rigctld_wsjtx_policy(resolve_audio_route(radio)) == (None, None)
    radio = SimpleNamespace(
        backend_id="rigplane",
        profile=get_radio_profile("IC-7300MK2"),
        supports_command=lambda _name: False,
    )
    assert rigctld_wsjtx_policy(resolve_audio_route(radio)) == (None, None)


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


def test_ic7610_lan_stream_request_uses_profile_audio_policy() -> None:
    from rigplane.audio.route import AudioConfigSource, resolve_lan_audio_stream_request

    request = resolve_lan_audio_stream_request(
        profile=get_radio_profile("IC-7610"),
        requested_rx_codec=AudioCodec.PCM_2CH_16BIT,
        requested_sample_rate_hz=48000,
    )

    assert request.rx_codec == AudioCodec.PCM_2CH_16BIT
    assert request.tx_codec == AudioCodec.PCM_1CH_16BIT
    assert request.rx_sample_rate_hz == 48000
    assert request.tx_sample_rate_hz == 48000
    assert request.rx_channels == 2
    assert request.tx_channels == 1
    assert request.rx_codec_source == AudioConfigSource.PROFILE_DEFAULT
    assert request.tx_codec_source == AudioConfigSource.PROFILE_DEFAULT
    assert request.rx_sample_rate_source == AudioConfigSource.PROFILE_CODEC_DEFAULT
    assert request.tx_sample_rate_source == AudioConfigSource.PROFILE_CODEC_DEFAULT


def test_ic705_and_ic9700_lan_stream_requests_use_profile_tx_codec_only() -> None:
    from rigplane.audio.route import AudioConfigSource, resolve_lan_audio_stream_request

    for model in ("IC-705", "IC-9700"):
        request = resolve_lan_audio_stream_request(
            profile=get_radio_profile(model),
            requested_rx_codec=AudioCodec.PCM_2CH_16BIT,
            requested_sample_rate_hz=48000,
        )

        assert request.rx_codec == AudioCodec.PCM_1CH_16BIT
        assert request.tx_codec == AudioCodec.PCM_1CH_16BIT
        assert request.rx_sample_rate_hz == 48000
        assert request.tx_sample_rate_hz == 48000
        assert request.rx_channels == 1
        assert request.tx_channels == 1
        assert request.rx_codec_source == AudioConfigSource.PROFILE_DEFAULT
        assert request.tx_codec_source == AudioConfigSource.PROFILE_DEFAULT
        assert request.rx_sample_rate_source == AudioConfigSource.GLOBAL_DEFAULT
        assert request.tx_sample_rate_source == AudioConfigSource.GLOBAL_DEFAULT


def test_lan_stream_request_honors_explicit_overrides() -> None:
    from rigplane.audio.route import AudioConfigSource, resolve_lan_audio_stream_request

    request = resolve_lan_audio_stream_request(
        profile=get_radio_profile("IC-7610"),
        requested_rx_codec=AudioCodec.OPUS_1CH,
        requested_sample_rate_hz=48000,
        rx_codec_explicit=True,
        sample_rate_explicit=True,
    )

    assert request.rx_codec == AudioCodec.OPUS_1CH
    assert request.tx_codec == AudioCodec.PCM_1CH_16BIT
    assert request.rx_sample_rate_hz == 48000
    assert request.tx_sample_rate_hz == 48000
    assert request.rx_channels == 1
    assert request.rx_codec_source == AudioConfigSource.EXPLICIT
    assert request.rx_sample_rate_source == AudioConfigSource.EXPLICIT
    assert request.tx_sample_rate_source == AudioConfigSource.EXPLICIT


def test_lan_stream_request_preserves_non_audited_profile_defaults() -> None:
    from rigplane.audio.route import AudioConfigSource, resolve_lan_audio_stream_request

    request = resolve_lan_audio_stream_request(
        profile=get_radio_profile("IC-7300"),
        requested_rx_codec=AudioCodec.PCM_2CH_16BIT,
        requested_sample_rate_hz=48000,
    )

    assert request.rx_codec == AudioCodec.PCM_1CH_16BIT
    assert request.tx_codec == AudioCodec.PCM_1CH_16BIT
    assert request.rx_sample_rate_hz == 48000
    assert request.tx_sample_rate_hz == 48000
    assert request.rx_codec_source == AudioConfigSource.PROFILE_DEFAULT
    assert request.rx_sample_rate_source == AudioConfigSource.GLOBAL_DEFAULT
