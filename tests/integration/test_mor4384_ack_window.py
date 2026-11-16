"""MOR-4384 round 3: a read sink lives until its answer window closes.

The ACK-sink drain's 500 ms cutoff used to force-expire every open sink
with ``answer_window=0.0``, so inside the configured answer window (2 s in
production) a delayed answer found its sink gone and settled whatever
waiter had registered since. Through the real HTTP tuning path that
recreated the round-1 defect: a background read the radio refuses (the
bench IC-705 answers ``1C 01`` with a bare ``FA`` on every start) but whose
refusal arrives late rejects a ``set_freq`` the radio actually accepted —
the seat reports the tuning failed while the radio tuned.

The seam is real end to end: the simulated radio on UDP, the production
LAN backend, its commander and CI-V request tracker, the web command seat,
and ``POST /api/v1/commands`` on top.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio

pytestmark = [
    pytest.mark.mock_integration,
    pytest.mark.realtime(
        reason="the delayed refusal must land past the 500 ms drain cutoff "
        "but inside the 2 s answer window, on real UDP and HTTP sockets"
    ),
]

_MODEL = "IC-7610"
_FREQ = 14_074_000
_TUNER_READ = (0x1C, 0x01)
_SET_FREQ = 0x05
#: The tuner-status refusal must land past the drain cutoff (0.5 s after
#: the set starts, plus the web command queue's own dispatch latency) but
#: inside the answer window (2 s). It is scheduled from its read and then
#: re-anchored to the set's actual dispatch (always post-cutoff), so queue
#: latency cannot drop it inside the drain.
_FA_DELAY_S = 1.6
_FA_AFTER_SET_DISPATCH_S = 0.25


async def _wait_until(predicate, description: str, timeout_s: float = 10.0) -> None:
    try:
        async with asyncio.timeout(timeout_s):
            while not predicate():
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {description}") from None


class _DelayedAnswers:
    """Delay the radio's answers the way a radio that answers in order would.

    The refused tuner read's ``FA`` is held back past the drain cutoff. A
    real radio answers in command order, so nothing it was asked after that
    read can reach the wire before the ``FA`` does: every later reply (the
    poller's answers, the frequency SET's ``FB``) queues behind it in the
    order it was produced, and the queue is flushed in that order through
    the simulator's own outbound funnel. A delivery model that lets the
    poller's answers overtake the held ``FA`` is not a wire order, and the
    tracker rightly reads such an overtaking answer as proof the read's
    answer was lost.
    """

    def __init__(self, sim: MockIcomRadio) -> None:
        self._sim = sim
        self._wire: dict[str, Any] = {}
        self._armed = False
        self._first_read_delayed = False
        self._holding = False
        self._outbox: list[bytes] = []
        self._release_handle: Any = None
        self._dispatch = sim._dispatch_civ
        self._handle = sim._handle_civ_data

    def attach(self) -> None:
        sim = self._sim

        def handle(data, addr, sender_id, proto):
            self._wire.update(proto=proto, addr=addr, sender_id=sender_id)
            self._handle(data, addr, sender_id, proto)

        def dispatch(cmd: int, payload: bytes, from_addr: int):
            reply = self._dispatch(cmd, payload, from_addr)
            if reply is None or not self._armed:
                return reply
            if (
                cmd == _TUNER_READ[0]
                and payload == bytes([_TUNER_READ[1]])
                and not self._first_read_delayed
            ):
                self._first_read_delayed = True
                self._holding = True
                self._outbox.append(reply)
                self._schedule_release(_FA_DELAY_S)
                return None
            if not self._holding:
                return reply
            self._outbox.append(reply)
            if cmd == _SET_FREQ and payload:
                # The set's dispatch is always past the drain cutoff (the
                # cutoff gates the send), so re-anchor the release just
                # behind it: the refusal then provably lands while the
                # set's waiter is registered, not inside the drain.
                self._schedule_release(_FA_AFTER_SET_DISPATCH_S)
            return None

        sim._handle_civ_data = handle
        sim._dispatch_civ = dispatch

    def arm(self) -> None:
        self._armed = True

    def _schedule_release(self, delay: float) -> None:
        if self._release_handle is not None:
            self._release_handle.cancel()
        loop = asyncio.get_running_loop()
        self._release_handle = loop.call_later(delay, self._release)

    def _release(self) -> None:
        self._release_handle = None
        self._holding = False
        outbox, self._outbox = self._outbox, []
        sim = self._sim
        if not self._wire:
            return
        for reply in outbox:
            sim._send(
                self._wire["proto"],
                sim._wrap_civ(reply, self._wire["sender_id"]),
                self._wire["addr"],
                "civ",
            )


def _state_has_frequency(session: aiohttp.ClientSession, base: str):
    async def has_frequency() -> bool:
        async with session.get(base + "/api/v1/state") as response:
            if response.status != 200:
                return False
            state = await response.json()
            return state.get("main", {}).get("freqHz") is not None

    return has_frequency


async def test_delayed_read_refusal_does_not_fail_a_successful_http_tuning() -> None:
    """A ``set_freq`` the radio accepts reports success, not the read's FA.

    The refused tuner read's ``FA`` lands past the drain's 500 ms cutoff
    and inside the 2 s answer window (re-anchored just behind the set's
    own dispatch, which the cutoff gates), and the set's own ``FB``
    follows it. The read sink must still be claimable then, so the
    refusal retires the read it answers and the set settles on its own
    confirmation: the HTTP tuning reports success and the radio's frequency
    is the requested one.
    """
    profile = resolve_radio_profile(model=_MODEL)
    sim = MockIcomRadio(
        radio_addr=profile.civ_addr,
        single_owner=True,
        unsolicited_civ=False,
        enable_audio=False,
    )
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="ackwindow",
        password="ackwindow",
        timeout=2.0,
        model=_MODEL,
    )
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    delayed = _DelayedAnswers(sim)
    delayed.attach()
    try:
        await asyncio.wait_for(radio.connect(), 30)
        await web.start()
        port = web._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15.0)
        ) as session:
            await _wait_until(
                _state_has_frequency(session, base), "the poller to serve a frequency"
            )
            # From here the radio refuses the tuner-status read, and the
            # two scripted answers land past the drain cutoff.
            sim.refused_civ_commands.add(_TUNER_READ)
            delayed.arm()
            tuner_read = build_civ_frame(
                profile.civ_addr, CONTROLLER_ADDR, _TUNER_READ[0], sub=_TUNER_READ[1]
            )
            assert await radio._execute_civ_raw(tuner_read, wait_response=False) is None
            async with session.post(
                base + "/api/v1/commands",
                json={"id": "tune-1", "name": "set_freq", "params": {"freq": _FREQ}},
            ) as response:
                assert response.status == 200, await response.text()
                body = await response.json()
            assert body["ok"] is True, body
        assert sim._frequency == _FREQ, (
            f"the radio tuned to {sim._frequency} Hz, not {_FREQ} Hz"
        )
    finally:
        await web.stop()
        await radio.disconnect()
        await sim.stop()
