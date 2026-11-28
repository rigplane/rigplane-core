"""Reported squelch gates LIVE while the scope and audio bridge keep PCM."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bus import AudioBus
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.ic705.serial import Ic705SerialRadio
from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.radio_protocol import AudioCapable
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.types import AudioCodec, CivFrame, Mode
from rigplane.profiles import get_radio_profile
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane.web.handlers.audio import AudioBroadcaster
from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE

MAIN_DCD = FieldPath.receiver("main", "operator_toggles", "dcd")
MAIN_MODE = FieldPath.active("main", "freq_mode", "mode")
ACTIVE = FieldPath.global_("slow_state", "active")
DUAL = FieldPath.global_("tx_state", "dual_watch")
PCM = b"\x01\x02" * 960


def _observe(store: StateStore, path: FieldPath, value: object, at: float) -> None:
    store.apply(Observation(path, value, SourceMetadata("poll_response", "test"), at))


@pytest.mark.timeout(5)
@pytest.mark.parametrize("model", ["FTX-1", "FT-991A"])
async def test_live_squelch_leaves_scope_and_audio_bridge_open(
    model: str, virtual_clock: VirtualClock
) -> None:
    profile = get_radio_profile(model)
    store = StateStore()
    now = virtual_clock.now
    _observe(store, ACTIVE, "MAIN", now)
    _observe(store, DUAL, False, now)
    _observe(store, MAIN_MODE, "FM", now)

    device = AudioDeviceId(1)
    backend = FakeAudioBackend([AudioDeviceInfo(device, "Test RX", 1, 0)])
    source = backend.open_rx(device, sample_rate=48000, channels=1)
    radio = MagicMock(spec=AudioCapable)
    radio.profile = profile
    radio.capabilities = {"audio"}
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = 48000
    bus = AudioBus(radio)
    radio.audio_bus = bus
    broadcaster = AudioBroadcaster(radio, state_store=store)
    scope: list[bytes] = []
    broadcaster.set_pcm_tap(scope.append)
    bridge = bus.subscribe(name="audio-bridge")
    await bridge.start()
    live = await broadcaster.subscribe(preferred_rx_codec=AUDIO_CODEC_PCM16)
    await source.start(
        lambda frame: bus._on_opus_packet(
            AudioPacket(ident=0x0080, send_seq=1, data=frame)
        )
    )

    async def check_pcm(expected: bytes) -> None:
        source.inject_frame(PCM)
        await virtual_clock.elapse(0)
        assert live.get_nowait()[AUDIO_HEADER_SIZE:] == expected
        bridge_frame = bridge.get_nowait()
        assert bridge_frame is not None and bridge_frame.data == PCM
        assert scope[-1] == PCM

    def report_squelch(value: bool) -> None:
        store.apply(
            Observation(
                MAIN_DCD,
                value,
                SourceMetadata("poll_response", "test"),
                virtual_clock.now,
                max_age=1.0,
            )
        )

    try:
        await check_pcm(PCM)  # unknown
        report_squelch(False)
        await check_pcm(PCM if model == "FT-991A" else bytes(len(PCM)))
        report_squelch(True)
        await check_pcm(PCM)
        report_squelch(False)
        _observe(store, MAIN_MODE, "USB", now)
        await check_pcm(PCM)
        _observe(store, MAIN_MODE, "CW", now)
        await check_pcm(PCM)
        _observe(store, MAIN_MODE, "FM", now)
        if model == "FTX-1":
            _observe(store, ACTIVE, "SUB", now)
            await check_pcm(PCM)
            _observe(store, ACTIVE, "MAIN", now)
            _observe(store, DUAL, True, now)
            await check_pcm(PCM)
            _observe(store, DUAL, False, now)
        await virtual_clock.elapse(1.1)
        store.mark_stale_due(now=virtual_clock.now)
        await check_pcm(PCM)
    finally:
        await source.stop()
        broadcaster.set_pcm_tap(None)
        await broadcaster.unsubscribe(live)
        await bridge.aclose()


@pytest.mark.timeout(5)
@pytest.mark.parametrize("data_mode", [0, 1], ids=["voice", "data"])
async def test_ic705_usb_live_squelch(
    data_mode: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = AudioDeviceInfo(AudioDeviceId(1), "USB Audio CODEC", 2, 1)
    backend = FakeAudioBackend([device])
    radio = Ic705SerialRadio(
        device="/dev/null",
        audio_driver=UsbAudioDriver(rx_device=device.name, backend=backend),
    )
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    resolve = acquisition_query_resolver_for_profile(radio.profile)
    squelch_query = resolve(MAIN_DCD)
    mode_query = resolve(MAIN_MODE)
    assert squelch_query is not None
    assert mode_query is not None

    def receive(command: int, sub: int | None, data: bytes) -> None:
        radio._civ_runtime._update_state_cache_from_frame(
            CivFrame(0xE0, radio.profile.civ_addr, command, sub, data)
        )

    def report_mode(mode: Mode) -> None:
        receive(
            mode_query.command,
            mode_query.sub,
            mode_query.data + bytes((mode, data_mode, 1)),
        )

    def report_squelch(open_: bool) -> None:
        receive(squelch_query.command, squelch_query.sub, bytes((open_,)))

    # The IC-705 can retain DATA when changing the base mode from USB to FM.
    report_mode(Mode.USB)
    report_mode(Mode.FM)
    assert radio.state_store.field("receiver.0.active.freq_mode.mode").value == "FM"
    data = radio.state_store.field("receiver.0.active.freq_mode.data_mode")
    assert data.value == data_mode

    broadcaster = AudioBroadcaster(radio, state_store=radio.state_store)
    live = await broadcaster.subscribe(preferred_rx_codec=AUDIO_CODEC_PCM16)
    try:
        assert len(backend.rx_streams) == 1
        bridge = await radio.audio_session.subscribe_rx("audio-bridge")

        async def check_pcm(expected: bytes) -> None:
            backend.rx_streams[0].inject_frame(PCM)
            assert (await live.get())[AUDIO_HEADER_SIZE:] == expected
            bridge_frame = bridge.get_nowait()
            assert bridge_frame is not None and bridge_frame.data == PCM

        try:
            report_squelch(True)
            await check_pcm(PCM)
            report_squelch(False)
            dcd = radio.state_store.field("receiver.0.operator_toggles.dcd")
            assert dcd.value is False
            await check_pcm(bytes(len(PCM)))
            report_squelch(True)
            await check_pcm(PCM)
            report_squelch(False)
            report_mode(Mode.USB)
            await check_pcm(PCM)
        finally:
            await bridge.release()
    finally:
        await broadcaster.unsubscribe(live)


@pytest.mark.timeout(5)
@pytest.mark.parametrize("bit,expected", [(0, False), (1, True), (2, None)])
async def test_yaesu_rx_poll_reads_profile_squelch(
    bit: int,
    expected: bool | None,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    monkeypatch.setattr(YaesuCatRadio, "connected", True)
    query = AsyncMock(return_value=f"RI0000000{bit}")
    monkeypatch.setattr(radio._transport, "query", query)
    monkeypatch.setattr(radio, "read_s_meter", AsyncMock(return_value=0))
    observations = await YaesuObservationAdapter.from_radio(
        radio, clock=virtual_clock
    ).poll_rx_meters()
    query.assert_awaited_once_with("RI0;")
    assert [item.value for item in observations if item.path == MAIN_DCD] == (
        [] if expected is None else [expected]
    )


@pytest.mark.parametrize(
    "model", ["IC-705", "IC-7300", "IC-7300MK2", "IC-7610", "IC-905", "IC-9700"]
)
def test_icom_squelch_read_resolves_and_decodes(model: str) -> None:
    radio = IcomRadio("192.0.2.1", model=model)
    profile = radio.profile
    assert profile.state_acquisition is not None
    assert profile.state_acquisition.capability_for(MAIN_DCD).can_poll
    query = acquisition_query_resolver_for_profile(profile)(MAIN_DCD)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x15, 0x01, b"")
    for raw in (b"", b"\x02", b"\x00\x01"):
        frame = CivFrame(0xE0, profile.civ_addr, query.command, query.sub, raw)
        radio._civ_runtime._update_state_cache_from_frame(frame)
        with pytest.raises(KeyError):
            radio.state_store.field("receiver.0.operator_toggles.dcd")
    for raw, expected in [(b"\x00", False), (b"\x01", True)]:
        frame = CivFrame(0xE0, profile.civ_addr, query.command, query.sub, raw)
        radio._civ_runtime._update_state_cache_from_frame(frame)
        field = radio.state_store.field("receiver.0.operator_toggles.dcd")
        assert field.value is expected
