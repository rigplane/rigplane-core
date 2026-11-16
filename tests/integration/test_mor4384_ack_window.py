"""MOR-4384: a delayed answer inside its window lands on its own request.

Every request is stamped when its frame leaves and an answer goes to the
oldest stamped request that can take it; nothing is presumed lost before
its own answer window (2 s in production) closes. Two delayed answers are
pinned through the real HTTP command path:

- a background read the radio refuses (the bench IC-705 answers ``1C 01``
  with a bare ``FA`` on every start) whose refusal arrives late must not
  reject a ``set_freq`` the radio accepted;
- a fire-and-forget write whose ``FB`` arrives 650 ms late must not report
  a following ``set_freq`` the radio refused as a success (T4).

The seam is real end to end: the simulated radio on UDP, the production
LAN backend, its commander and CI-V request tracker, the web command seat,
and ``POST /api/v1/commands`` on top.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable
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
        reason="the delayed answers must land hundreds of milliseconds late "
        "but inside the 2 s answer window, on real UDP and HTTP sockets"
    ),
]

_MODEL = "IC-7610"
_FREQ = 14_074_000
_REFUSED_FREQ = 7_074_000
_TUNER_READ = (0x1C, 0x01)
_AF_WRITE = (0x14, 0x01)
_SET_FREQ = 0x05
#: The tuner-status refusal lands 1.6 s after its read: well past any
#: scheduling slack, still inside the read's 2 s answer window.
_FA_DELAY_S = 1.6
#: T4: the fire-and-forget write's FB lands 650 ms after the write.
_FB_DELAY_S = 0.65
#: A held answer is never released before the set's own answer can queue
#: behind it, however late the web command queue dispatches the set.
_AFTER_SET_DISPATCH_S = 0.05


async def _wait_until(predicate, description: str, timeout_s: float = 10.0) -> None:
    try:
        async with asyncio.timeout(timeout_s):
            while True:
                ready = predicate()
                if inspect.isawaitable(ready):
                    ready = await ready
                if ready:
                    return
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {description}") from None


class _DelayedAnswers:
    """Delay the radio's answers the way a radio that answers in order would.

    The first answer to the ``held`` command (``(cmd, payload)`` match) is
    held back for ``hold_s``. A real radio answers in command order, so
    nothing it was asked after that command can reach the wire before the
    held answer does: every later reply (the poller's answers, the
    frequency SET's own answer) queues behind it in the order it was
    produced, and the queue is flushed in that order through the
    simulator's own outbound funnel. The release never comes before the
    SET was dispatched, so the SET's answer always queues behind the held
    one.
    """

    def __init__(
        self, sim: MockIcomRadio, held: Callable[[int, bytes], bool], hold_s: float
    ) -> None:
        self._sim = sim
        self._held = held
        self._hold_s = hold_s
        self._wire: dict[str, Any] = {}
        self._armed = False
        self._first_held = False
        self._holding = False
        self._outbox: list[bytes] = []
        self._release_at = 0.0
        self._release_handle: Any = None
        self.last_dispatch = 0.0
        self._dispatch = sim._dispatch_civ
        self._handle = sim._handle_civ_data

    def attach(self) -> None:
        sim = self._sim

        def handle(data, addr, sender_id, proto):
            self._wire.update(proto=proto, addr=addr, sender_id=sender_id)
            self._handle(data, addr, sender_id, proto)

        def dispatch(cmd: int, payload: bytes, from_addr: int):
            reply = self._dispatch(cmd, payload, from_addr)
            self.last_dispatch = time.monotonic()
            if reply is None or not self._armed:
                return reply
            if not self._first_held and self._held(cmd, payload):
                self._first_held = True
                self._holding = True
                self._outbox.append(reply)
                self._release_no_earlier_than(self._hold_s)
                return None
            if not self._holding:
                return reply
            self._outbox.append(reply)
            if cmd == _SET_FREQ and payload:
                self._release_no_earlier_than(_AFTER_SET_DISPATCH_S)
            return None

        sim._handle_civ_data = handle
        sim._dispatch_civ = dispatch

    def arm(self) -> None:
        self._armed = True

    def _release_no_earlier_than(self, delay: float) -> None:
        loop = asyncio.get_running_loop()
        release_at = max(self._release_at, loop.time() + delay)
        if release_at == self._release_at and self._release_handle is not None:
            return
        if self._release_handle is not None:
            self._release_handle.cancel()
        self._release_at = release_at
        self._release_handle = loop.call_at(release_at, self._release)

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


async def _tune_behind_held_answer(
    held: Callable[[int, bytes], bool],
    hold_s: float,
    background: Callable[[int], bytes],
    *,
    refused: tuple[int, int | None],
    freq: int = _FREQ,
) -> tuple[int, dict[str, Any], int]:
    """POST ``set_freq`` while the answer to a background frame is held.

    ``background(civ_addr)`` builds the fire-and-forget frame sent just
    before the set; the radio refuses ``refused``. Returns the HTTP status,
    the reply body and the frequency the simulated radio ended up on.
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
    delayed = _DelayedAnswers(sim, held, hold_s)
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
            # A radio that answers in order cannot answer the set before it
            # has answered everything it was asked earlier. The poller stays
            # out of the way for the setup (the public external-CAT-session
            # pause), then the queued set runs behind the held answer.
            radio.begin_external_cat_session()
            await _wait_until(
                lambda: time.monotonic() - delayed.last_dispatch > 0.4,
                "the poller to go quiet",
            )
            sim.refused_civ_commands.add(refused)
            delayed.arm()
            frame = background(profile.civ_addr)
            assert await radio._execute_civ_raw(frame, wait_response=False) is None
            post = asyncio.create_task(
                session.post(
                    base + "/api/v1/commands",
                    json={
                        "id": "tune-1",
                        "name": "set_freq",
                        "params": {"freq": freq},
                    },
                )
            )
            await asyncio.sleep(0.1)
            radio.end_external_cat_session()
            async with await post as response:
                return response.status, await response.json(), sim._frequency
    finally:
        await web.stop()
        await radio.disconnect()
        await sim.stop()


async def test_delayed_read_refusal_does_not_fail_a_successful_http_tuning() -> None:
    """A ``set_freq`` the radio accepts reports success, not the read's FA.

    The refused tuner read's ``FA`` lands 1.6 s late, inside the read's 2 s
    answer window and behind the set's dispatch, and the set's own ``FB``
    follows it. The read sink is the oldest request that can take the FA,
    so the refusal retires the read it answers and the set settles on its
    own confirmation: the HTTP tuning reports success and the radio's
    frequency is the requested one.
    """
    status, body, frequency = await _tune_behind_held_answer(
        lambda cmd, payload: (
            cmd == _TUNER_READ[0] and payload == bytes([_TUNER_READ[1]])
        ),
        _FA_DELAY_S,
        lambda addr: build_civ_frame(
            addr, CONTROLLER_ADDR, _TUNER_READ[0], sub=_TUNER_READ[1]
        ),
        refused=_TUNER_READ,
    )
    assert status == 200, body
    assert body["ok"] is True, body
    assert frequency == _FREQ, f"the radio tuned to {frequency} Hz, not {_FREQ} Hz"


async def test_t4_delayed_write_ack_does_not_hide_a_refused_http_tuning() -> None:
    """T4: a ``set_freq`` the radio refuses reports the refusal.

    A fire-and-forget AF-level write's ``FB`` lands 650 ms late (past the
    retired 500 ms drain cutoff, inside the 2 s window), and the refused
    set's ``FA`` follows it. The FB goes to the oldest write -- the AF
    write it answers -- and the FA to the set: the HTTP reply is the
    radio's refusal, and the radio's frequency is unchanged.
    """
    status, body, frequency = await _tune_behind_held_answer(
        lambda cmd, payload: (
            cmd == _AF_WRITE[0] and payload[:1] == bytes([_AF_WRITE[1]])
        ),
        _FB_DELAY_S,
        lambda addr: build_civ_frame(
            addr, CONTROLLER_ADDR, _AF_WRITE[0], sub=_AF_WRITE[1], data=b"\x01\x28"
        ),
        refused=(_SET_FREQ, None),
        freq=_REFUSED_FREQ,
    )
    assert status == 409, body
    assert body["ok"] is False, body
    assert body["error"] == "radio_nak", body
    assert frequency != _REFUSED_FREQ, "the radio applied the refused tuning"
