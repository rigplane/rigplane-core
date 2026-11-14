"""MOR-4436 T1 journey: production LAN client vs the wfview-mode simulator.

One end-to-end pass of the recorded Xiegu G7250 "wfview" network mode
(MOR-4436): the production LAN client built from the real G7250 profile
(``rigs/g7250.toml``) through the normal ``create_radio`` /
``LanBackendConfig`` path, against ``MockIcomRadio`` with the wfview
knobs on (``radio_addr=0xA0``, ``substream_discovery="ping"``,
``write_confirmation="echo"``, ``lan_civ_echo=False``,
``ignored_civ_commands={(0x05, None)}``, ``model_id=b"\\xa0"`` — the
recorded ``19 00`` answer): connect, initial frequency read, frequency
write, read-back on the client and on the simulator, RX audio frames at
the client's audio callback, clean disconnect. Bounded entirely by the
virtual clock; no real sleeps.

Every await that talks to the radio runs through :func:`_bounded`, which
drives the virtual clock until the step finishes or a virtual deadline
passes — a missing reply (e.g. pre-L1/L2) fails fast with a clear
``AssertionError`` instead of hanging on a frozen clock.
``@pytest.mark.timeout`` stays only as a last-resort guard.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from rigplane.backends.config import LanBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock

from rigplane_testkit.icom import MockIcomRadio

T = TypeVar("T")

_XFAIL_REASON = (
    "MOR-4436: L2 (SET confirmed by echo) is on main (#4573); still needs "
    "L1 (sub-stream ping discovery, core/transport.py) and L3 (G7250 LAN "
    "profile, rigs/g7250.toml); remove this mark when both are on main"
)


async def _bounded(
    clock: VirtualClock,
    step: str,
    coro: Coroutine[Any, Any, T],
    budget_s: float,
    granule_s: float = 0.1,
) -> T:
    """Drive ``clock`` until ``coro`` finishes or ``budget_s`` virtual seconds pass.

    The coroutine runs as a task while the clock advances in ``granule_s``
    steps (each :meth:`VirtualClock.elapse` fires every timer on the way and
    settles loop callbacks, so real localhost I/O interleaves with timer
    progress). Awaiting the task directly would freeze on a missing reply:
    its internal timeouts run on this clock and nothing else moves it. On
    success the task's own result (or exception) is returned (or raised);
    only an exhausted budget raises
    ``AssertionError("<step> did not finish within N virtual s")``. Budget
    exhaustion — or an :meth:`VirtualClock.elapse` failure — cancels the step
    task and awaits it (suppressing ``CancelledError``) before raising, so no
    radio operation keeps running into teardown.
    """
    task = asyncio.create_task(coro)
    spent = 0.0
    try:
        while not task.done():
            if spent >= budget_s:
                break
            await clock.elapse(min(granule_s, budget_s - spent))
            spent += granule_s
        if task.done():
            return task.result()
    except BaseException:  # noqa: BLE001 — must free the orphaned step task
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    raise AssertionError(f"{step} did not finish within {budget_s:g} virtual s")


@pytest.mark.xfail(strict=True, reason=_XFAIL_REASON)
@pytest.mark.timeout(120)
async def test_g7250_wfview_lan_journey(
    virtual_clock: VirtualClock,
) -> None:
    """The G7250 LAN journey over the wfview-mode simulator (MOR-4436)."""
    sim = MockIcomRadio(
        radio_addr=0xA0,
        substream_discovery="ping",
        write_confirmation="echo",
        lan_civ_echo=False,
        ignored_civ_commands={(0x05, None)},
        enable_audio=True,
    )
    sim.model_id = b"\xa0"  # recorded 19 00 answer (MOR-4436)
    radio: IcomRadio | None = None
    cleanup_error: BaseException | None = None
    try:
        await sim.start()
        radio = create_radio(
            LanBackendConfig(
                host="127.0.0.1",
                port=sim.control_port,
                username="testuser",
                password="testpass",
                # No radio_addr: it must come from the G7250 profile (L3),
                # as in a real user's config.
                model="G7250",
            )
        )
        assert isinstance(radio, IcomRadio)

        await _bounded(virtual_clock, "connect", radio.connect(), 60.0)
        assert radio.connected

        initial = await _bounded(
            virtual_clock, "get_freq", radio.get_freq(bypass_cache=True), 30.0
        )
        assert initial == sim._frequency  # noqa: SLF001

        await _bounded(virtual_clock, "set_freq", radio.set_freq(7_138_000), 30.0)
        read_back = await _bounded(
            virtual_clock,
            "read-back get_freq",
            radio.get_freq(bypass_cache=True),
            30.0,
        )
        assert read_back == 7_138_000
        assert sim._frequency == 7_138_000  # noqa: SLF001

        sub = await _bounded(
            virtual_clock,
            "subscribe_rx",
            radio.audio_session.subscribe_rx("mor4436-journey"),
            30.0,
        )
        try:
            await virtual_clock.elapse(2.0)
            frames = 0
            while True:
                try:
                    packet = sub.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if packet is None:
                    break
                frames += 1
            assert frames > 0, "no RX audio reached the client callback"
        finally:
            await _bounded(virtual_clock, "sub.release", sub.release(), 10.0)

        await _bounded(virtual_clock, "disconnect", radio.disconnect(), 10.0)
        assert not radio.connected
    finally:
        # Never leak a radio (LeakedRadioError at teardown): disconnect any
        # instantiated radio, connected or only partially so — disconnect()
        # is idempotent. A cleanup disconnect that fails must not mask the
        # original failure, so it is re-raised only when the journey passed.
        if radio is not None:
            try:
                await _bounded(virtual_clock, "disconnect", radio.disconnect(), 10.0)
            except BaseException as exc:  # noqa: BLE001
                cleanup_error = exc
        await sim.stop()
    if cleanup_error is not None:
        raise cleanup_error
