"""A2 RED: TCI adapter TX audio path (MOR-3821, spec 8.3/10.3)."""

from __future__ import annotations

import asyncio
import struct

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from test_managed_tx_authority import FakeConfigStore, FakeLane

from tci_adapter_stub import (
    StubCommandQueue,
    StubCommandService,
    StubIcomRadio,
    StubProfile,
    build_store,
    make_handler,
)


def _loaded_store():  # type: ignore[no-untyped-def]
    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 128,
        }
    )


def _tx_radio(**kwargs):  # type: ignore[no-untyped-def]
    """StubIcomRadio with a PCM TX audio surface (stub transport + session)."""
    from rigplane.audio.bus import AudioBus
    from rigplane.audio.session import AudioSession

    from rigplane.core.types import AudioCodec

    kwargs.setdefault(
        "profile",
        StubProfile(modes=("AM", "LSB", "USB", "CW", "FM"), data_mode_count=3),
    )
    radio = StubIcomRadio(**kwargs)
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = 48000
    radio.pushed_tx: list[bytes] = []

    async def _start_rx(callback, jitter_depth=5):  # type: ignore[no-untyped-def]
        pass

    async def _stop_rx() -> None:
        pass

    async def _start_tx() -> None:
        pass

    async def _stop_tx() -> None:
        pass

    async def _push_tx(data: bytes) -> None:
        radio.pushed_tx.append(bytes(data))  # type: ignore[attr-defined]

    radio.start_rx = _start_rx  # type: ignore[attr-defined]
    radio.stop_rx = _stop_rx  # type: ignore[attr-defined]
    radio.start_tx = _start_tx  # type: ignore[attr-defined]
    radio.stop_tx = _stop_tx  # type: ignore[attr-defined]
    radio.push_tx = _push_tx  # type: ignore[attr-defined]
    radio.audio_session = AudioSession(radio, bus=AudioBus(radio))
    return radio


def _seam(radio=None, store=None, **kwargs):  # type: ignore[no-untyped-def]
    radio = radio if radio is not None else _tx_radio()
    store = store if store is not None else _loaded_store()
    lane = kwargs.pop("lane", FakeLane())
    authority = ManagedTxAuthority(
        lane,
        FakeConfigStore(kwargs.pop("tot_seconds", 10.0)),
        TxAbortFence(),
        provider_generation=7,
        attempt_timeout_seconds=kwargs.pop("attempt_timeout_seconds", 1.0),
        retry_delay_seconds=kwargs.pop("retry_delay_seconds", 2.0),
    )
    queue: StubCommandQueue = kwargs.pop("queue", StubCommandQueue())
    handler = make_handler(
        radio=radio,
        store=store,
        authority=authority,
        queue=queue,
        service=StubCommandService(),
        **kwargs,
    )
    return handler, authority, lane, queue


async def _program_digu(handler):  # type: ignore[no-untyped-def]
    handler._radio.observe_store = handler._store  # noqa: SLF001
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,DIGU;",)


class TestTxRouteFacts:
    def test_pcm_route_accepted(self) -> None:
        from rigplane.audio.route import tx_audio_facts

        from rigplane.core.types import AudioCodec

        facts = tx_audio_facts(_tx_radio(), has_mod_input_routing=True)
        assert facts.codec is AudioCodec.PCM_1CH_16BIT
        assert facts.route_accepted is True
        assert facts.required_mod_input == 5

    def test_opus_tx_codec_not_pcm(self) -> None:
        from rigplane.audio.route import tx_audio_facts
        from rigplane.tci_adapter.audio import tx_route_ready

        from rigplane.core.types import AudioCodec

        radio = _tx_radio()
        radio.audio_tx_codec = AudioCodec.OPUS_1CH
        facts = tx_audio_facts(radio, has_mod_input_routing=True)
        assert facts.codec is AudioCodec.OPUS_1CH
        assert tx_route_ready(radio) is None

    def test_no_route_not_accepted(self) -> None:
        from rigplane.audio.route import tx_audio_facts

        radio = _tx_radio()
        radio.backend_id = "unknown"
        radio.audio_session = None  # type: ignore[attr-defined]
        facts = tx_audio_facts(radio, has_mod_input_routing=True)
        assert facts.route_accepted is False


class TestTrxTciKey:
    async def test_key_tci_accepted_with_pcm_route(self) -> None:
        handler, authority, _lane, _queue = _seam()
        await _program_digu(handler)
        outcome = await handler.on_command("trx", ("0", "true", "tci"))
        assert outcome.to_sender == ("trx:0,true;",)
        assert handler.tx_session is not None
        snapshot = await authority.snapshot()
        assert snapshot.state.intent.kind is ManagedTxIntentKind.PTT
        await handler.on_command("trx", ("0", "false"))
        assert handler.tx_session is None
        await authority.close()

    async def test_key_tci_selects_input_before_ptt(self) -> None:
        handler, authority, lane, _queue = _seam()
        outcome = await handler.on_command("trx", ("0", "true", "tci"))
        assert outcome.to_sender == ("trx:0,true;",)
        assert handler._radio.mod_inputs.get(2) == 5  # noqa: SLF001
        assert [e.operation.name for e in lane.effects] == ["PTT_ON"]
        await handler.on_command("trx", ("0", "false"))
        await authority.close()

    async def test_key_tci_refused_for_opus_codec(self) -> None:
        from rigplane.core.types import AudioCodec

        radio = _tx_radio()
        radio.audio_tx_codec = AudioCodec.OPUS_1CH
        handler, authority, lane, _queue = _seam(radio=radio)
        await _program_digu(handler)
        outcome = await handler.on_command("trx", ("0", "true", "tci"))
        assert outcome.to_sender == ("trx:0,false;",)
        assert [e.operation.name for e in lane.effects] == []
        await authority.close()

    async def test_key_tci_refused_in_cw(self) -> None:
        handler, authority, _lane, _queue = _seam(retry_delay_seconds=0.05)
        handler._radio.observe_store = handler._store  # noqa: SLF001
        outcome = await handler.on_command("modulation", ("0", "CW"))
        assert outcome.to_sender == ("modulation:0,CW;",)
        refused = await handler.on_command("trx", ("0", "true", "tci"))
        assert refused.to_sender == ("trx:0,false;",)
        await asyncio.sleep(0.5)
        await authority.close()

    async def test_key_tci_before_on_failure_no_ptt(self) -> None:
        handler, authority, lane, _queue = _seam(retry_delay_seconds=0.05)
        await _program_digu(handler)
        handler._radio.fail_on.add("set_data2_mod_input")  # noqa: SLF001
        outcome = await handler.on_command("trx", ("0", "true", "tci"))
        assert outcome.to_sender == ("trx:0,false;",)
        assert [e.operation.name for e in lane.effects] == []
        await asyncio.sleep(0.5)
        await authority.close()

    async def test_key_other_source_still_refused(self) -> None:
        handler, authority, _lane, _queue = _seam()
        await _program_digu(handler)
        outcome = await handler.on_command("trx", ("0", "true", "mic1"))
        assert outcome.to_sender == ("trx:0,false;",)
        await authority.close()

    async def test_unkey_releases_lease_and_flushes(self) -> None:
        handler, authority, _lane, _queue = _seam()
        await _program_digu(handler)
        await handler.on_command("trx", ("0", "true", "tci"))
        lease = handler.tx_session.lease
        assert lease.released is False
        await handler.on_command("trx", ("0", "false"))
        assert lease.released is True
        assert handler.tx_session is None
        await authority.close()

    async def test_disconnect_while_keyed_releases(self) -> None:
        handler, authority, _lane, _queue = _seam()
        await _program_digu(handler)
        await handler.on_command("trx", ("0", "true", "tci"))
        lease = handler.tx_session.lease
        await handler.on_disconnect()
        assert lease.released is True
        snapshot = await authority.snapshot()
        assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
        await authority.close()


class TestChrono:
    def test_interval_mono_and_stereo(self) -> None:
        from rigplane.tci_adapter.audio import chrono_interval

        assert chrono_interval(2048, 1, 48000) == pytest.approx(2048 / 48000)
        assert chrono_interval(2048, 2, 48000) == pytest.approx(2048 / (2 * 48000))

    async def test_chrono_frames_mono_and_stereo(self) -> None:
        from rigplane.tci.frames import StreamType, unpack_header
        from rigplane.tci_adapter.audio import TciAudioSettings, TciTxSession

        seen: list[bytes] = []
        now = [0.0]
        session = TciTxSession(
            radio=_tx_radio(),
            owner="tci-client-1",
            authority=None,
            lease=None,
            settings=TciAudioSettings(samplerate=48000, samples=2048),
            iq=False,
            emit=seen.append,
            clock=lambda: now[0],
        )
        await session.emit_chrono()
        now[0] = 2048 / 48000
        await session.emit_chrono()
        assert len(seen) == 2
        first = unpack_header(seen[0][:64])
        assert first.stream_type is StreamType.TX_CHRONO
        assert first.length == 2048
        assert first.channels == 1
        assert first.sample_rate == 48000
        assert session.chrono_times == [0.0, pytest.approx(2048 / 48000)]


class TestConvertTxFrame:
    def test_stereo_digu_uses_channel_zero(self) -> None:
        from rigplane.tci_adapter.audio import convert_tx_frame

        from rigplane.tci.frames import SampleType

        left = [1000, 2000, 3000, 4000]
        right = [-1000, -2000, -3000, -4000]
        interleaved = struct.pack(
            "<8h", *[v for pair in zip(left, right) for v in pair]
        )
        out = convert_tx_frame(
            interleaved,
            channels=2,
            sample_type=SampleType.INT16,
            iq=True,
            from_rate=48000,
            to_rate=48000,
        )
        assert struct.unpack("<4h", out) == tuple(left)

    def test_stereo_non_digu_averages(self) -> None:
        from rigplane.tci_adapter.audio import convert_tx_frame

        from rigplane.tci.frames import SampleType

        interleaved = struct.pack("<4h", 1000, 3000, -1000, -3000)
        out = convert_tx_frame(
            interleaved,
            channels=2,
            sample_type=SampleType.INT16,
            iq=False,
            from_rate=48000,
            to_rate=48000,
        )
        assert struct.unpack("<2h", out) == (2000, -2000)

    def test_nan_inf_become_silence(self) -> None:
        import array

        from rigplane.tci_adapter.audio import convert_tx_frame

        from rigplane.tci.frames import SampleType

        payload = array.array("f", [float("nan"), float("inf"), 0.5, -0.5]).tobytes()
        out = convert_tx_frame(
            payload,
            channels=1,
            sample_type=SampleType.FLOAT32,
            iq=False,
            from_rate=8000,
            to_rate=8000,
        )
        assert struct.unpack("<4h", out) == (0, 0, 16384, -16384)

    def test_resample_ratio(self) -> None:
        from rigplane.tci_adapter.audio import convert_tx_frame

        from rigplane.tci.frames import SampleType

        payload = struct.pack("<48h", *([1000] * 48))
        out = convert_tx_frame(
            payload,
            channels=1,
            sample_type=SampleType.INT16,
            iq=False,
            from_rate=48000,
            to_rate=8000,
        )
        assert len(out) == 8 * 2


class TestPlayout:
    async def test_continuous_playout_and_underrun_silence(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings, TciTxSession

        pushed: list[bytes] = []

        class _Lease:
            async def push(self, data: bytes, *, admission=None) -> None:  # type: ignore[no-untyped-def]
                if admission is not None:
                    async with admission() as admitted:
                        if admitted is not True:
                            return
                pushed.append(bytes(data))

        class _Authority:
            def __init__(self) -> None:
                self.holder: str | None = "tci-client-1"

            def tx_holder_nowait(self) -> str | None:
                return self.holder

            def owned_tx_submission(self, owner: str):  # type: ignore[no-untyped-def]
                holder = self.holder

                class _Ctx:
                    async def __aenter__(self) -> bool:
                        return holder == owner

                    async def __aexit__(self, *exc: object) -> bool:
                        return False

                return _Ctx()

        authority = _Authority()
        session = TciTxSession(
            radio=_tx_radio(),
            owner="tci-client-1",
            authority=authority,
            lease=_Lease(),
            settings=TciAudioSettings(samplerate=8000, samples=256),
            iq=False,
            emit=lambda frame: None,
        )
        session.buffering_ms_override = 50
        session.feed_pcm(struct.pack("<960h", *([1000] * 960)))
        await session.tick()
        assert len(pushed) == 1
        assert len(pushed[0]) == 48000 * 20 // 1000 * 2
        assert pushed[0] == struct.pack("<960h", *([1000] * 960))
        await session.tick()
        assert session.underruns >= 1
        assert pushed[-1] == b"\x00" * (48000 * 20 // 1000 * 2)
        await session.stop()

    async def test_overrun_drops_oldest(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings, TciTxSession

        session = TciTxSession(
            radio=_tx_radio(),
            owner="tci-client-1",
            authority=None,
            lease=None,
            settings=TciAudioSettings(samplerate=8000, samples=256),
            iq=False,
            emit=lambda frame: None,
        )
        session.buffering_ms_override = 50
        session.feed_pcm(b"\x01\x00" * 30000)
        assert session.overruns > 0
        await session.stop()

    async def test_handoff_nothing_of_a_reaches_b(self) -> None:
        from rigplane.tci_adapter.audio import TciAudioSettings, TciTxSession

        pushed: list[bytes] = []

        class _Lease:
            async def push(self, data: bytes, *, admission=None) -> None:  # type: ignore[no-untyped-def]
                if admission is not None:
                    async with admission() as admitted:
                        if admitted is not True:
                            return
                pushed.append(bytes(data))

        class _Authority:
            def __init__(self) -> None:
                self.holder: str | None = "tci-client-1"

            def tx_holder_nowait(self) -> str | None:
                return self.holder

            def owned_tx_submission(self, owner: str):  # type: ignore[no-untyped-def]
                holder = self.holder

                class _Ctx:
                    async def __aenter__(self) -> bool:
                        return holder == owner

                    async def __aexit__(self, *exc: object) -> bool:
                        return False

                return _Ctx()

        authority = _Authority()
        session = TciTxSession(
            radio=_tx_radio(),
            owner="tci-client-1",
            authority=authority,
            lease=_Lease(),
            settings=TciAudioSettings(samplerate=8000, samples=256),
            iq=False,
            emit=lambda frame: None,
        )
        session.buffering_ms_override = 50
        session.feed_pcm(struct.pack("<800h", *([1000] * 800)))
        authority.holder = "tci-client-2"
        await session.tick()
        assert pushed == []
        assert session.dropped_not_owner == 1
        await session.stop()


class TestBinaryTxFrames:
    async def test_keyed_frame_pushes_s16le(self) -> None:
        from rigplane.tci.frames import (
            SampleType,
            StreamHeader,
            StreamType,
            pack_header,
        )

        handler, authority, _lane, _queue = _seam()
        await _program_digu(handler)
        assert (await handler.on_command("trx", ("0", "true", "tci"))).to_sender == (
            "trx:0,true;",
        )
        payload = struct.pack("<2048h", *([1000] * 2048))
        header = pack_header(
            StreamHeader(
                receiver=0,
                sample_rate=48000,
                sample_type=SampleType.INT16,
                codec=0,
                crc=0,
                length=2048,
                stream_type=StreamType.TX_AUDIO,
                channels=1,
                count=1,
            )
        )
        for _ in range(3):
            await handler.on_binary(header + payload)
        await asyncio.sleep(0.5)
        assert handler._radio.pushed_tx  # noqa: SLF001
        assert all(
            len(frame) == 48000 * 20 // 1000 * 2 for frame in handler._radio.pushed_tx
        )  # noqa: SLF001
        await handler.on_command("trx", ("0", "false"))
        await authority.close()

    async def test_unkeyed_frame_dropped_never_keys(self) -> None:
        from rigplane.tci.frames import (
            SampleType,
            StreamHeader,
            StreamType,
            pack_header,
        )

        handler, authority, lane, _queue = _seam()
        await _program_digu(handler)
        payload = struct.pack("<4h", 1000, 1000, 1000, 1000)
        header = pack_header(
            StreamHeader(
                receiver=0,
                sample_rate=48000,
                sample_type=SampleType.INT16,
                codec=0,
                crc=0,
                length=4,
                stream_type=StreamType.TX_AUDIO,
                channels=1,
                count=1,
            )
        )
        await handler.on_binary(header + payload)
        assert handler._radio.pushed_tx == []  # noqa: SLF001
        assert [e.operation.name for e in lane.effects] == []
        snapshot = await authority.snapshot()
        assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
        await authority.close()
