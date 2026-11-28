"""MOR-3256: a TX audio marker through the real WS stack into the sink.

The production stack of ``test_tx_under_fault_matrix._stand_up_stack`` (real
``WebServer`` + ``RigctldServer`` + managed TX authority + ``IcomRadio`` over
the ``MockIcomRadio`` LAN simulator, built by ``test_multi_session_tx_lanes._Arena``
with ``capture_tx_audio=True``) is keyed over ``/api/v1/ws``, and a marker tone
with planted silence gaps (``rigplane_testkit/audio_markers.py``) is streamed
as 20 ms PCM16 frames over ``/api/v1/audio`` — while the simulator's RX byte
ramp runs on the same audio port (the RX-leak condition). The captured sink
payload must analyze clean: right frequency and level, gaps at their offsets,
no dropout, no RX leak; the sink's ``ptt`` flags and frame numbering pin the
gating (PTT off -> frames stop).

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

# Make tests/ importable from tests/integration/.
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.mock_integration

from rigplane.audio.lan_stream import AudioStream  # noqa: E402
from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.runtime import radio_reconnect  # noqa: E402
from rigplane.runtime.tx_interlock import RfState  # noqa: E402
from rigplane.testing.timing import VirtualClock  # noqa: E402
from rigplane.web.protocol import (  # noqa: E402
    AUDIO_CODEC_PCM16,
    MSG_TYPE_AUDIO_RX,
    MSG_TYPE_AUDIO_TX,
    encode_audio_frame,
)

from rigplane_testkit.audio_markers import (  # noqa: E402
    MarkerReport,
    MarkerSpec,
    analyze_marker_pcm16,
    check_marker,
    render_marker_pcm16,
)
from rigplane_testkit.icom.simulator_audio import TxAudioFormat  # noqa: E402

from _perf_helpers import fast_connect  # noqa: E402
from test_multi_session_tx_lanes import _Arena  # noqa: E402
from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEEPALIVE_HOLD_S,
    _KEY_DEADLINE_S,
    _WATCHDOG_TIMEOUT_S,
    _wait_ptt,
    _wait_rf,
    _ws_recv_frame,
    _WsKeyClient,
)

from rigplane_testkit.icom import MockIcomRadio  # noqa: E402

_FRAME_MS = 20  # one WS TX frame per 20 ms of PCM, paced in real time

# Latency bounds, measured on the Mac mini and set at ~4x the observed
# values, with a 250 ms floor for loaded runners (observed: WS frame sent -> sink 0.004 s; simulator PTT-on ->
# first sink frame 0.372 s — the latter is mostly the test's own
# observe-then-send path; both numbers are in the MOR-3256 PR text).
#
# The WS -> sink bound reads in wall seconds but is load-independent anyway
# (MOR-4214): the keyed stream below is paced by a virtual clock, so no wall
# time passes between the paced sends — the measured interval is pure
# pipeline callback depth (a few loop turns), which a loaded host cannot
# stretch the way it stretched the old wall-paced sleeps (0.348 s).
_WS_TO_SINK_BOUND_S = 0.25
_PTT_ON_TO_SINK_BOUND_S = 1.5
# Virtual seconds a first frame that has not arrived by the end of the stream
# is still waited for; far above the bound, so a late arrival is measured.
_FIRST_FRAME_GRACE_S = 2.0
# Quantum for exact virtual-time comparisons: whole microseconds. Both stamps
# come from the same clock under one quantum, so the difference is exact —
# float error (~1e-14 s here) is seven orders of magnitude below 1 us, while
# a frame one pacing step early is 20 000 us short. No epsilon is widened.
_US_PER_S = 1_000_000


async def _send_pcm_virtual(
    mic: _WsKeyClient,
    pcm: bytes,
    sample_rate: int,
    clock: VirtualClock,
    sim: MockIcomRadio,
) -> tuple[float, float]:
    """Stream ``pcm`` as 20 ms TX frames paced by virtual time (MOR-4214).

    Identical to :func:`_send_pcm` except the pacing: wall-clock sleeps would
    let a loaded host deschedule the loop between the send and the sink
    delivery (the 0.348 s flake); ``clock.elapse`` advances frame-times with
    no wall exposure while still settling the real socket callbacks each
    step.

    Returns ``(sent_first, arrived_first)``: when the first frame left the
    client and when it was first seen in the sink, BOTH read on the virtual
    clock. A forwarding delay in the pipeline (a timer the stack sleeps on)
    moves the virtual clock and is therefore measured; a descheduled host
    does not move it and is not. The simulator's own ``received_at`` is a
    real ``time.monotonic()`` stamp and must not be mixed with these.
    """
    frame_bytes = sample_rate * 2 * _FRAME_MS // 1000
    step_s = _FRAME_MS / 1000
    sent_first: float | None = None
    arrived_first: float | None = None
    seq = 0

    def note_arrival() -> None:
        nonlocal arrived_first
        if arrived_first is None and sim.audio_tx_frames:
            arrived_first = clock()

    for offset in range(0, len(pcm), frame_bytes):
        chunk = pcm[offset : offset + frame_bytes]
        if sent_first is None:
            sent_first = clock()
        await mic.send_binary(
            encode_audio_frame(
                MSG_TYPE_AUDIO_TX,
                AUDIO_CODEC_PCM16,
                seq,
                sample_rate // 100,
                1,
                _FRAME_MS,
                chunk,
            )
        )
        seq = (seq + 1) & 0xFFFF
        await clock.elapse(step_s)
        note_arrival()
    # A first frame still in flight when the stream ends: keep stepping
    # virtual time (bounded) so a late arrival is stamped late, not lost.
    for _ in range(int(_FIRST_FRAME_GRACE_S / step_s)):
        if arrived_first is not None:
            break
        await clock.elapse(step_s)
        note_arrival()
    assert sent_first is not None
    return sent_first, arrived_first if arrived_first is not None else float("inf")


def _assert_forwarding_latency(sent_first: float, arrived_first: float) -> None:
    """The WS -> sink bound, on one clock (MOR-4214)."""
    assert arrived_first - sent_first <= _WS_TO_SINK_BOUND_S, (
        f"first TX frame took {arrived_first - sent_first:.3f} s (virtual) "
        f"from the WS to the sink, bound {_WS_TO_SINK_BOUND_S} s"
    )


async def _cancel_session_watchdog(arena: _Arena) -> None:
    """Stop the audio-session watchdog after a virtual-clock stream (MOR-4214).

    The virtual clock ran ahead of real time, so once it is uninstalled every
    pending loop timer is that late in real seconds and the watchdog would
    trip mid-teardown. The audio evidence is already secured by then.
    """
    watchdog = arena.stack.radio.audio_session._watchdog_task  # noqa: SLF001
    if watchdog is not None and not watchdog.done():
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)


async def _open_tx_mic(
    arena: _Arena, key: str | None
) -> tuple[_WsKeyClient, dict[str, object]]:
    """Open ``/api/v1/audio`` and arm TX; return the mic and the
    ``audio_tx_format`` reply (the negotiated TX sample rate and codec)."""
    mic = await arena.ws(key, "/api/v1/audio")
    await mic.send({"type": "audio_start", "direction": "tx"})
    reply = await mic._until(lambda message: message.get("type") == "audio_tx_format")  # noqa: SLF001
    return mic, reply


async def _collect_rx_audio(mic: _WsKeyClient, frames: list[bytes]) -> None:
    """Read binary RX frames off the mic socket until cancelled; keep the
    MSG_TYPE_AUDIO_RX payloads in arrival order."""
    while True:
        opcode, payload = await _ws_recv_frame(mic._reader, timeout=10.0)  # noqa: SLF001
        if opcode == 0x2 and payload[:1] == bytes([MSG_TYPE_AUDIO_RX]):
            frames.append(payload)


async def _send_pcm(mic: _WsKeyClient, pcm: bytes, sample_rate: int) -> float:
    """Stream ``pcm`` as 20 ms MSG_TYPE_AUDIO_TX PCM16 frames paced in real
    time; return the monotonic time the first nonzero-payload frame went out."""
    frame_bytes = sample_rate * 2 * _FRAME_MS // 1000
    first_nonzero_at = 0.0
    start = time.monotonic()
    seq = 0
    for index, offset in enumerate(range(0, len(pcm), frame_bytes)):
        chunk = pcm[offset : offset + frame_bytes]
        if not first_nonzero_at and any(chunk):
            first_nonzero_at = time.monotonic()
        await mic.send_binary(
            encode_audio_frame(
                MSG_TYPE_AUDIO_TX,
                AUDIO_CODEC_PCM16,
                seq,
                sample_rate // 100,
                1,
                _FRAME_MS,
                chunk,
            )
        )
        seq = (seq + 1) & 0xFFFF
        await asyncio.sleep(
            max(0.0, start + (index + 1) * _FRAME_MS / 1000 - time.monotonic())
        )
    return first_nonzero_at


async def _wait_sink_bytes(sim: MockIcomRadio, payload_bytes: int) -> None:
    """Wait until the sink holds ``payload_bytes`` captured payload bytes.

    The radio's TX path chunks each WS frame to MAX_AUDIO_PAYLOAD (1364
    bytes; ``src/rigplane/audio/lan_stream.py``: ``push_tx``), so the sink
    frame count is not the WS frame count — the byte count is the exact
    one. Delivery of the last datagrams takes a loop tick; a fixed sleep
    would hope.
    """
    end = time.monotonic() + _GATE_TIMEOUT_S
    while sum(frame.payload_len for frame in sim.audio_tx_frames) < payload_bytes:
        assert time.monotonic() < end, (
            f"TX sink stuck at "
            f"{sum(frame.payload_len for frame in sim.audio_tx_frames)}"
            f"/{payload_bytes} bytes"
        )
        await asyncio.sleep(0.02)


async def _assert_session_held(sim: MockIcomRadio) -> None:
    """The radio session must have lasted the whole keyed window (MOR-4331).

    A loop stall longer than the simulator's keepalive hold expires the
    session: its timer is due the moment the loop runs again. One tick lets
    that timer fire, so a release is observed here instead of surfacing later
    as a reset TX audio socket, depending on how recovery races the test.
    """
    await asyncio.sleep(0.05)
    assert sim.released_count == 0, (
        f"radio session released while keyed: {sim.last_release_reason}"
    )


@pytest.fixture(
    params=[0.0, _KEEPALIVE_HOLD_S + 0.3], ids=["analysis_fast", "analysis_stalls_loop"]
)
def analysis_stall_s(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> float:
    """MOR-4331: how long ``analyze_marker_pcm16`` blocks the thread it runs on.

    The real analysis is CPU-heavy (a golden-section search of ~60 least-squares
    fits) and holds the event loop for over ``_KEEPALIVE_HOLD_S`` on a loaded
    runner. A plain ``time.sleep`` ahead of the real call reproduces that
    deterministically: if the analysis runs while the radio is keyed inside the
    arena, the simulator (same loop) misses its keepalive window, drops the
    session and the TX audio socket resets.
    """
    stall_s: float = request.param
    real_analyze = analyze_marker_pcm16

    def stalling_analyze(pcm: bytes, spec: MarkerSpec) -> MarkerReport:
        time.sleep(stall_s)
        return real_analyze(pcm, spec)

    if stall_s:
        monkeypatch.setattr(
            sys.modules[__name__], "analyze_marker_pcm16", stalling_analyze
        )
    return stall_s


async def test_marker_streamed_while_keyed_lands_clean_in_the_sink(
    tmp_path: Path, analysis_stall_s: float
) -> None:
    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        sim = arena.sim
        session = await arena.session()
        mic, tx_format = await _open_tx_mic(arena, session.key)
        # RX runs on the same mic for the whole test, started through the
        # real WS audio API: the marker must survive ACTIVE RX decoding on
        # this connection (the leak condition), not merely an emitting
        # simulator.
        await mic.send({"type": "audio_start", "direction": "rx"})
        rx_ack = await mic._until(lambda message: message.get("type") == "audio_format")  # noqa: SLF001
        assert isinstance(rx_ack.get("sample_rate"), int) and rx_ack["sample_rate"] > 0
        rate = tx_format["sample_rate"]
        assert isinstance(rate, int) and rate > 0, tx_format
        spec = MarkerSpec(sample_rate=rate)
        pcm = render_marker_pcm16(spec)

        # The RX-leak claim needs RX audio OBSERVED AT THIS CLIENT while
        # the marker streams — a simulator-side emission counter proves
        # nothing about the client's RX path.
        rx_frames: list[bytes] = []
        rx_collector = asyncio.create_task(_collect_rx_audio(mic, rx_frames))

        # PTT-gated: with nobody keyed, the WS gate drops every TX frame.
        probe_frames = 5
        await _send_pcm(mic, pcm[: probe_frames * rate * 2 * _FRAME_MS // 1000], rate)
        await asyncio.sleep(0.3)
        assert not sim.audio_tx_frames

        assert await session.primary.key("marker")
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        # The audio gate opens on the observed TX; wait for it explicitly.
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        t_ptt_on = next(t for t, on in reversed(sim.ptt_transitions) if on)

        rx_before = len(rx_frames)
        # The keyed stream is paced by the virtual clock (MOR-4214):
        # installed here — after everything the stack handshake needs,
        # removed right after the stream — it advances frame-times with
        # no wall exposure while settling the real socket callbacks each
        # step, so the WS -> sink latency below measures only pipeline
        # depth. The stack's session machinery (login handshakes, ping
        # keepalive, control watchdog) stays on real time throughout:
        # full virtualization races those handshakes and skews the hold.
        loop = asyncio.get_running_loop()
        clock = VirtualClock(start=loop.time())
        clock.install(loop)
        try:
            sent_first, arrived_first = await _send_pcm_virtual(
                mic, pcm, rate, clock, sim
            )
        finally:
            clock.uninstall(loop)
        await _wait_sink_bytes(sim, len(pcm))
        # RX audio must keep flowing at THIS client for the whole TX
        # stream: the captured marker is exactly the tone (asserted
        # below), so contamination from the active RX path would fail it.
        end = time.monotonic() + _GATE_TIMEOUT_S
        while len(rx_frames) == rx_before:
            assert time.monotonic() < end, (
                "no RX audio frames reached the client during the TX stream"
            )
            await asyncio.sleep(0.02)
        rx_collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await rx_collector

        frames = sim.audio_tx_frames
        captured = sim.audio_tx_pcm()
        assert len(captured) == len(pcm)  # exactly the marker, nothing else
        assert all(frame.ptt for frame in frames)
        seqs = [frame.inner_seq for frame in frames]
        assert all((b - a) & 0xFFFF == 1 for a, b in zip(seqs, seqs[1:]))

        first = frames[0]
        _assert_forwarding_latency(sent_first, arrived_first)
        # Real-clock stamps against real-clock stamps only (both are the
        # simulator's and the test's own ``time.monotonic()``).
        assert first.received_at - t_ptt_on <= _PTT_ON_TO_SINK_BOUND_S
        # Defuse the uninstall shift (MOR-4214): the clock advanced ~3 s
        # past real time, so every pending loop timer fires that late in
        # real seconds — including the RX burst loop, which would stall
        # the stream for ~3 s and trip the session watchdog mid-teardown.
        # All audio evidence is secured, so cancel the watchdog now; the
        # teardown below releases the demand anyway.
        await _cancel_session_watchdog(arena)
        await _assert_session_held(sim)

        # PTT off -> frames stop. The audio gate follows the OBSERVED RF
        # state (the poller must read RX first, as in
        # test_multi_session_tx_lanes path4), so the freeze is pinned from
        # the observed RX, not from the unkey reply.
        await arena.unkey(session)
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        frozen = len(sim.audio_tx_frames)
        await _send_pcm(mic, pcm[:rate], rate)  # 0.5 s more tone
        assert len(sim.audio_tx_frames) == frozen

    # The analysis is CPU-heavy and blocks the event loop the simulator and the
    # client share: it runs after the arena is torn down, never while keyed
    # (MOR-4331). Only the cheap capture happens inside.
    problems = check_marker(analyze_marker_pcm16(captured, spec), spec)
    assert problems == [], problems


async def test_marker_check_catches_a_wrong_tone(
    tmp_path: Path, analysis_stall_s: float
) -> None:
    """The harness is not vacuous: the same pipeline fed a tone 200 Hz off
    the spec must make ``check_marker`` report the frequency."""
    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        session = await arena.session()
        mic, tx_format = await _open_tx_mic(arena, session.key)
        rate = tx_format["sample_rate"]
        assert isinstance(rate, int) and rate > 0, tx_format
        spec = MarkerSpec(duration_s=0.5, gaps=(), sample_rate=rate)
        sent = render_marker_pcm16(replace(spec, freq_hz=spec.freq_hz + 200.0))

        assert await session.primary.key("broken")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        await _send_pcm(mic, sent, rate)
        await _wait_sink_bytes(arena.sim, len(sent))

        captured = arena.sim.audio_tx_pcm()
        await _assert_session_held(arena.sim)
        await arena.unkey(session)

    # Analysis after the arena (MOR-4331): it blocks the shared event loop.
    problems = check_marker(analyze_marker_pcm16(captured, spec), spec)
    assert any("frequency" in problem for problem in problems)


async def test_forwarding_latency_check_rejects_a_delayed_first_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control (MOR-4214): the WS -> sink bound is not vacuous on the
    virtual clock. Only the FIRST forwarded frame is held 0.3 s (> the 0.25 s
    bound) by a timer inside the stack; every later frame is prompt. The
    latency assertion must reject it. A real-clock measurement would not: the
    virtual sleep completes in microseconds of real time.

    The clock starts at 0, not at ``loop.time()``: this control measures the
    delay EXACTLY at the bound, and at a wall-derived base (~44.9k s, double
    spacing ~7e-12 s) the paced 20 ms steps are not multiples of that spacing,
    so the accumulated arrival stamp can land a hair below ``delay_s``
    (observed on quick: 0.3 - 1.3e-11) and the check flakes on the wall-clock
    base — wall time through the back door. From 0 ``sent_first`` is exactly
    0.0 and the injected sleep timer is due at ``delay_s`` itself, but the
    arrival stamp still comes out of the clock's advance arithmetic (the
    elapse steps accumulate 20 ms paces, then move to the timer deadline as
    ``prev + (deadline - prev)``), so it can read a hair below ``delay_s``
    (observed: 0.3 - 2e-14). The assertion below therefore compares whole
    microseconds — both stamps from the same clock under one quantum —
    instead of floats with an epsilon: every true value here is a whole
    number of microseconds (0.3 s, 20 ms paces), float error is ~1e-14 s,
    and a frame one pacing step early is 20 000 us short, so the quantized
    comparison is exact and still rejects an early frame. Timers the stack
    scheduled before the install sit ~44.9k s in this clock's future and do
    not fire during the ~0.3 s window; nothing in the window waits on one.
    """
    delay_s = _WS_TO_SINK_BOUND_S + 0.05
    real_push_tx = AudioStream.push_tx
    delayed = False

    async def delay_first_push(self: AudioStream, audio_data: bytes) -> None:
        nonlocal delayed
        if not delayed:
            delayed = True
            await asyncio.sleep(delay_s)
        await real_push_tx(self, audio_data)

    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        session = await arena.session()
        mic, tx_format = await _open_tx_mic(arena, session.key)
        rate = tx_format["sample_rate"]
        assert isinstance(rate, int) and rate > 0, tx_format
        pcm = render_marker_pcm16(MarkerSpec(sample_rate=rate))
        prefix = pcm[: 25 * rate * 2 * _FRAME_MS // 1000]
        assert await session.primary.key("late-first-frame")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)

        monkeypatch.setattr(AudioStream, "push_tx", delay_first_push)
        loop = asyncio.get_running_loop()
        clock = VirtualClock(start=0.0)  # exact at the bound; see the docstring
        clock.install(loop)
        try:
            sent_first, arrived_first = await _send_pcm_virtual(
                mic, prefix, rate, clock, arena.sim
            )
        finally:
            clock.uninstall(loop)
        assert delayed, "the delay hook never saw a forwarded frame"
        # Exact integer comparison, no epsilon: both stamps read the same
        # virtual clock under one microsecond quantum, so float pacing error
        # (~1e-14 s, observed delay_s - 2e-14 against a 0.3 s delay) cannot
        # flip the verdict, while a frame a full pacing step (20 ms) early
        # is 20 000 us short and still fails.
        sent_us = int(round(sent_first * _US_PER_S))
        arrived_us = int(round(arrived_first * _US_PER_S))
        delay_us = int(round(delay_s * _US_PER_S))
        assert arrived_us - sent_us >= delay_us, (
            f"first TX frame arrived {arrived_us - sent_us} us after the send, "
            f"injected delay {delay_us} us"
        )
        with pytest.raises(AssertionError, match="virtual"):
            _assert_forwarding_latency(sent_first, arrived_first)
        await _cancel_session_watchdog(arena)
        await arena.unkey(session)


class _StallRightAfterCheck:
    """``radio_reconnect``'s ``asyncio`` with one change: right after a
    watchdog interval sleep returns (the check about to run sees activity as
    of now), the event loop is blocked for ``stall_s`` once armed.

    That is the worst phase of the real hazard — a loop stall that starts
    just after a check, before any ping reply could be counted — made
    deterministic instead of a 1-in-20 race.
    """

    def __init__(self, interval_s: float, stall_s: float) -> None:
        self._interval_s = interval_s
        self._stall_s = stall_s
        self.armed = False
        self.stalled = False

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)

    async def sleep(self, delay: float, *args: object) -> None:
        await asyncio.sleep(delay, *args)
        if self.armed and not self.stalled and delay == self._interval_s:
            self.stalled = True
            asyncio.get_running_loop().call_soon(time.sleep, self._stall_s)


async def test_event_loop_stall_longer_than_the_watchdog_keeps_the_keyed_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MOR-3868: the simulator shares the client's event loop, so a stall of
    that loop (a CPU-starved CI runner: logged as "Watchdog: no activity for
    1.7s") silences both ends at once. The control watchdog (1.5 s in this
    stack) must not read that as a dead link and drop a keyed session — the
    CI symptom was the unkey failing with "the previous transmission is still
    being released" while the reconnect released the key.
    """
    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        radio = arena.stack.radio
        stall_s = 1.7
        assert _WATCHDOG_TIMEOUT_S < stall_s < _KEEPALIVE_HOLD_S
        stall = _StallRightAfterCheck(radio.WATCHDOG_CHECK_INTERVAL, stall_s)
        monkeypatch.setattr(radio_reconnect, "asyncio", stall)

        session = await arena.session()
        assert await session.primary.key("stall")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)

        stall.armed = True
        end = time.monotonic() + _GATE_TIMEOUT_S
        while not stall.stalled:
            assert time.monotonic() < end, "the watchdog never checked"
            await asyncio.sleep(0.05)
        await asyncio.sleep(stall_s + 1.0)  # the stall, then two more checks

        assert radio._reconnect_task is None  # noqa: SLF001
        await arena.unkey(session)
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)


async def test_real_client_negotiated_tx_format_is_learned() -> None:
    """A real Core ``IcomRadio(audio_sample_rate=16000)`` against the
    simulator: the accepted conninfo teaches the sink the negotiated TX
    format, and streamed TX frames snapshot it — the label source of the
    ``/audio/tx[.wav]`` capture routes."""
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=10.0,
        enable_audio=True,
        capture_tx_audio=True,
    )
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        audio_sample_rate=16000,
    )
    try:
        with fast_connect():
            await radio.connect()
        learned = TxAudioFormat(codec=0x04, sample_rate=16000)
        assert sim.audio_tx_format == learned

        await radio.start_tx()
        await radio.push_tx(b"\x01\x00" * 320)  # 20 ms of 16 kHz mono s16le
        end = time.monotonic() + _GATE_TIMEOUT_S
        while not sim.audio_tx_frames:
            assert time.monotonic() < end, "TX frame never reached the sink"
            await asyncio.sleep(0.02)
        assert sim.audio_tx_frames[0].tx_format == learned
        assert sim.tx_capture_format() == learned
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()
