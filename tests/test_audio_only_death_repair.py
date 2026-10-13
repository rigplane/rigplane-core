"""MOR-3401 phase 1: characterize "audio-only death with CI-V alive is never repaired".

Characterization only — NO production change. Three tests against the real
``IcomRadio`` + ``MockIcomRadio(enable_audio=True)`` stack from
``scripts/soak/soak_driver.py::_start_simulator_stack`` (the same fixture
``tests/test_web_owner_reconnect_audio.py`` uses):

- ``test_audio_only_death_never_repaired`` — RED on main: killing only the
  client's audio leg never heals; RX frames do not reach the held
  subscription within the bound although CI-V answers throughout.
- ``test_manual_audio_rebuild_repairs_audio_only_death`` — GREEN on main
  positive control: the repair a fix would trigger
  (``CoreRadio._teardown_audio_transport`` →
  ``CoreRadio._ensure_audio_transport`` → ``AudioSession.reestablish``,
  the same path ``ControlPhaseRuntime._after_reconnect_parts`` runs)
  makes frames resume.
- ``test_detectors_observe_but_do_not_repair`` — GREEN on main: pins what
  the existing detectors did during the death (``rx_silent`` fires,
  ``audio_error_watchdog_loop`` does not act, nothing calls
  ``soft_reconnect``).

Client-side death model: the test closes the UNDERLYING asyncio UDP
endpoint of the client's audio transport
(``radio._audio_transport._udp_transport.close()``) and touches nothing
else. This models a dead client socket/FD: the simulator (like the real
IC-7610 per Lane A w31, which keeps streaming RX across short outages)
keeps sending, CI-V stays healthy, but no audio packet can reach the
client. A silent socket raises no UDP errors, so ``_udp_error_count``
stays low — exactly the gap ``audio_error_watchdog_loop`` leaves.
"""

from __future__ import annotations

import asyncio
import time

from rigplane.audio import AudioPacket
from rigplane.audio.session import AudioSessionEvent, AudioSessionState
from scripts.soak import soak_driver

#: Bound for "frames reach the subscription again" (the property a fix must give).
RESUME_BOUND_S = 10.0
#: Each test must stay well under ~30 s wall-clock.
_SANITY_FIRST_FRAME_S = 10.0
_CIV_CHECK_TIMEOUT_S = 5.0


async def _next_frame_or_none(sub: object, timeout_s: float) -> AudioPacket | None:
    """Next RX packet, or None on timeout (``asyncio.TimeoutError`` → None)."""
    try:
        return await sub.get(timeout=timeout_s)  # type: ignore[union-attr]
    except asyncio.TimeoutError:
        return None


def _kill_client_audio_only(radio: object) -> object:
    """Kill ONLY the client's audio leg; CI-V and control stay untouched.

    Closes the underlying asyncio datagram endpoint under the client's
    audio ``IcomTransport``. The transport object stays attached (so the
    audio-error watchdog still sees a transport, just one with no error
    storm), the simulator keeps streaming into the void, and CI-V keeps
    flowing — a client-side audio death.
    """
    audio_t = radio._audio_transport  # noqa: SLF001
    assert audio_t is not None, "no client audio transport to kill"
    udp = audio_t._udp_transport  # noqa: SLF001
    assert udp is not None, "client audio transport has no UDP endpoint"
    udp.close()
    return audio_t


async def _civ_answers(radio: object) -> int:
    """Wire-level frequency read (bypasses the state cache fallback)."""
    return await asyncio.wait_for(
        radio.get_freq(bypass_cache=True),  # type: ignore[union-attr]
        _CIV_CHECK_TIMEOUT_S,
    )


def _drain(sub: object) -> None:
    while True:
        try:
            sub.get_nowait()  # type: ignore[union-attr]
        except asyncio.QueueEmpty:
            break


async def test_audio_only_death_never_repaired() -> None:
    """RED on main: an audio-only death with live CI-V is never repaired."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        sim = stack.sim
        radio.audio_session.add_listener(events.append)

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None, (
            "no RX frame reached the subscriber before the audio death"
        )

        _drain(sub)
        sent_before = sim.audio_rx_frames_sent
        t_death = time.monotonic()
        _kill_client_audio_only(radio)

        # CI-V stayed healthy throughout: isolate the death to audio-only.
        freq = await _civ_answers(radio)
        assert freq > 0, "CI-V did not answer right after the audio death"

        packet = await _next_frame_or_none(sub, RESUME_BOUND_S)

        freq_after = await _civ_answers(radio)
        assert freq_after > 0, "CI-V stopped answering during the audio death"
        assert sim.audio_rx_frames_sent > sent_before, (
            "simulator stopped sending RX audio — the death was not client-side"
        )

        silent_at = [e.timestamp - t_death for e in events if e.reason == "rx_silent"]
        print(f"[MOR-3401] rx_silent delays after death: {silent_at}")
        print(f"[MOR-3401] session state at bound: {radio.audio_session.state}")

        # THE RED LINE (MOR-3401): on main nothing rebuilds the dead audio
        # leg, so no frame arrives within the bound.
        assert packet is not None, (
            "MOR-3401: RX frames never resumed on the held subscription within "
            f"{RESUME_BOUND_S}s after a client-side audio death while CI-V "
            "kept answering — the audio-only death is never repaired"
        )
        await sub.release()
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()


async def test_manual_audio_rebuild_repairs_audio_only_death() -> None:
    """GREEN positive control: the hand-driven repair restores RX frames."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        radio.audio_session.add_listener(events.append)

        sub = await radio.audio_session.subscribe_rx("web-audio")
        assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None, (
            "no RX frame reached the subscriber before the audio death"
        )

        _drain(sub)
        old_audio_t = _kill_client_audio_only(radio)

        # Let the liveness detector observe the death first (3.0 s timeout).
        for _ in range(60):
            if any(e.reason == "rx_silent" for e in events):
                break
            await asyncio.sleep(0.1)

        # The repair a fix would trigger — the same path
        # ``ControlPhaseRuntime._after_reconnect_parts`` runs for audio.
        await radio._teardown_audio_transport()  # noqa: SLF001
        await radio._ensure_audio_transport()  # noqa: SLF001
        await radio.audio_session.reestablish()
        assert radio._audio_transport is not old_audio_t  # noqa: SLF001

        _drain(sub)
        packet = await _next_frame_or_none(sub, RESUME_BOUND_S)
        assert packet is not None, (
            "manual audio rebuild (teardown → ensure → reestablish) did not "
            "restore RX frames — the positive control is broken"
        )
        assert await _civ_answers(radio) > 0, "CI-V stopped answering"
        await sub.release()
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()


async def test_detectors_observe_but_do_not_repair() -> None:
    """GREEN on main: existing detectors surface the death but repair nothing."""
    stack = await soak_driver._start_simulator_stack()
    events: list[AudioSessionEvent] = []
    try:
        radio = stack.radio
        sim = stack.sim
        radio.audio_session.add_listener(events.append)

        soft_calls: list[str] = []
        orig_soft = radio.soft_reconnect

        async def _spy() -> None:
            soft_calls.append("called")
            await orig_soft()

        radio.soft_reconnect = _spy  # type: ignore[method-assign]
        try:
            sub = await radio.audio_session.subscribe_rx("web-audio")
            assert await _next_frame_or_none(sub, _SANITY_FIRST_FRAME_S) is not None, (
                "no RX frame reached the subscriber before the audio death"
            )

            _drain(sub)
            sent_before = sim.audio_rx_frames_sent
            t_death = time.monotonic()
            audio_t = _kill_client_audio_only(radio)

            # Past the 3.0 s RX liveness timeout plus watchdog-cadence slack.
            await asyncio.sleep(6.0)

            silent = [e for e in events if e.reason == "rx_silent"]
            assert silent, "rx_silent never fired during the audio-only death"
            delay = silent[0].timestamp - t_death
            assert 2.9 <= delay < 10.0, f"rx_silent fired after {delay:.1f}s"
            assert radio.audio_session.state is AudioSessionState.RECOVERING, (
                f"session left RECOVERING without any repair: {radio.audio_session.state}"
            )
            assert audio_t._udp_error_count < 50, (  # noqa: SLF001
                "UDP error storm — audio_error_watchdog_loop territory, "
                "not a silent death"
            )
            assert radio._audio_transport is audio_t, (  # noqa: SLF001
                "audio transport was rebuilt with no repair caller"
            )
            assert not soft_calls, "soft_reconnect ran during healthy CI-V"
            assert sim.audio_rx_frames_sent > sent_before, (
                "simulator stopped sending — the death was not client-side"
            )
            assert await _civ_answers(radio) > 0, "CI-V stopped answering"
            assert await _next_frame_or_none(sub, 1.0) is None, (
                "frames arrived with no repair — the death was not sustained"
            )
            await sub.release()
        finally:
            radio.soft_reconnect = orig_soft  # type: ignore[method-assign]
    finally:
        radio.audio_session.remove_listener(events.append)
        await stack.aclose()
