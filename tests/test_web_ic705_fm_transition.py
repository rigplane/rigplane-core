"""MOR-3630: the IC-705 FM transition through the production stack.

The unit tests in ``tests/test_web_runtime_helpers.py`` and
``tests/test_acquisition_scheduler.py`` seed ``"FM"`` into a store and call
helpers directly, so they pass even if the production transition never
observes the mode or never reacquires the controls. This test drives the
whole stack instead and injects no observation anywhere:

- a recorded IC-705 personality on the LAN simulator (the recorded answer
  table of ``tests/fixtures/ic705``), which also models the one radio fact
  under test: in FM it NAKs the PBT, manual-notch-width, filter-shape,
  filter-width and AGC-time reads (the bench probe,
  ``tests/fixtures/ic705/README.md``);
- the production ``IcomRadio`` over real UDP sockets, and a ``WebServer``
  started the way ``rigplane web`` starts it (its poller, freshness service
  and acquisition drain run unmodified);
- the mode changes through the real command endpoint (HTTP and WebSocket),
  and every assertion reads the delivered ``GET /api/v1/state`` payload or
  the frames the simulated radio actually received.

Only the radio and the network are fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from rigplane.runtime.radio import IcomRadio
from rigplane.types import Mode
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from test_web_server import _http_get
from test_web_split_writes import _request
from testkit.ic705_fakes import ic705_lan_fake

# (public leaf, wire command, wire sub) of the reads the radio refuses in FM.
_FM_NAKED_READS: tuple[tuple[str, int, int], ...] = (
    ("pbtInner", 0x14, 0x07),
    ("pbtOuter", 0x14, 0x08),
    ("manualNotchWidth", 0x16, 0x57),
    ("filterShape", 0x16, 0x56),
)
_FM_NAKED_CODES = frozenset(
    (command, sub)
    for command, sub in (
        *((command, sub) for _, command, sub in _FM_NAKED_READS),
        (0x1A, 0x03),  # filter width
        (0x1A, 0x04),  # AGC time constant
    )
)
_FILTER_SHAPE_READ = (0x16, 0x56)

#: Bounded waits. The slowest legitimate step is a PBT/notch-width cadence
#: poll (5 s in ``rigs/ic705.toml``) after the mode returns to USB.
_STARTUP_DEADLINE_S = 20.0
_TRANSITION_DEADLINE_S = 12.0
#: How long FM is observed for stray reads: longer than the slowest
#: contradicted cadence (5 s) plus one adaptive re-prime window (5 s).
_FM_WINDOW_S = 11.0
#: A read already handed to the CI-V queue when the mode was observed may
#: still reach the wire shortly after; later than this it is a violation.
_IN_FLIGHT_GRACE_S = 1.0


class _Ic705FmRadio(MockIcomRadio):
    """The recorded IC-705 plus its FM refusals, with a timed wire log."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        # (monotonic time, command, sub) of every read the radio received
        # for a code it refuses in FM, whatever mode it was in at the time.
        self.guarded_reads: list[tuple[float, int, int]] = []

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        if len(payload) == 1 and (cmd, payload[0]) in _FM_NAKED_CODES:
            self.guarded_reads.append((time.monotonic(), cmd, payload[0]))
            if self._mode == Mode.FM:
                self.refused_count += 1
                return self._civ_nak(from_addr, self._radio_addr)
        return super()._dispatch_civ(cmd, payload, from_addr)


@contextlib.asynccontextmanager
async def _production_stack() -> AsyncIterator[tuple[WebServer, _Ic705FmRadio]]:
    sim = ic705_lan_fake(cls=_Ic705FmRadio)
    assert isinstance(sim, _Ic705FmRadio)
    sim._mode = int(Mode.USB)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="fm-transition",
        password="fm-transition",
        timeout=1.0,
        model="IC-705",
    )
    web: WebServer | None = None
    try:
        await radio.connect()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        await web.start()
        yield web, sim
    finally:
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


async def _public_state(web: WebServer) -> dict[str, Any]:
    status, _, body = await _http_get("127.0.0.1", web.port, "/api/v1/state")
    assert status == 200
    return json.loads(body)


async def _wait_for_state(
    web: WebServer,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    deadline_s: float,
    what: str,
) -> dict[str, Any]:
    """Poll the delivered state until ``predicate`` holds, else fail loudly."""
    deadline = time.monotonic() + deadline_s
    state = await _public_state(web)
    while not predicate(state):
        if time.monotonic() >= deadline:
            seen = {
                leaf: (
                    state["main"][leaf],
                    state["fieldStatus"][f"main.{leaf}"]["availability"],
                )
                for leaf, _, _ in _FM_NAKED_READS
            }
            raise AssertionError(
                f"timed out after {deadline_s}s waiting for: {what}; last state: "
                f"mode={state['main']['mode']!r} controls={seen}"
            )
        await asyncio.sleep(0.1)
        state = await _public_state(web)
    return state


def _controls_available(state: dict[str, Any]) -> bool:
    status = state["fieldStatus"]
    return all(
        status[f"main.{leaf}"]["observed"] is True
        and status[f"main.{leaf}"]["availability"] == "available"
        and state["main"][leaf] is not None
        for leaf, _, _ in _FM_NAKED_READS
    )


def _controls_unavailable(state: dict[str, Any]) -> bool:
    status = state["fieldStatus"]
    return all(
        status[f"main.{leaf}"]["observed"] is False
        and status[f"main.{leaf}"]["availability"] == "unavailable"
        and state["main"][leaf] is None
        for leaf, _, _ in _FM_NAKED_READS
    )


async def _select_mode(web: WebServer, protocol: str, mode: str) -> None:
    status, result = await _request(web, protocol, "set_mode", {"mode": mode})
    assert status == (200 if protocol == "http" else None)
    assert result["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_ic705_fm_transition_stops_the_reads_and_reacquires_in_usb(
    protocol: str,
) -> None:
    """USB -> FM -> USB through the command endpoint, with no injected state.

    1. Start-up acquisition fills the four controls (the radio answers the
       reads from its recorded table) and the filter-shape read reaches the
       wire.
    2. FM is selected through the real endpoint. The production readback
       observes the mode, the freshness tick clears the controls, and for
       longer than the slowest contradicted cadence no read the radio
       refuses in FM reaches the wire.
    3. USB is selected again. Production cadence and priming reacquire the
       controls from the radio's replies, to the values read in step 1.
    """

    async with _production_stack() as (web, sim):
        state = await _wait_for_state(
            web,
            lambda s: s["main"]["mode"] == "USB" and _controls_available(s),
            deadline_s=_STARTUP_DEADLINE_S,
            what="start-up acquisition of the four controls in USB",
        )
        usb_values = {leaf: state["main"][leaf] for leaf, _, _ in _FM_NAKED_READS}
        assert any(
            (command, sub) == _FILTER_SHAPE_READ
            for _, command, sub in sim.guarded_reads
        ), "positive control: the filter-shape read must reach the wire in USB"
        assert sim.refused_count == 0

        await _select_mode(web, protocol, "FM")
        await _wait_for_state(
            web,
            lambda s: s["main"]["mode"] == "FM" and _controls_unavailable(s),
            deadline_s=_TRANSITION_DEADLINE_S,
            what="FM observed and the four controls cleared",
        )
        fm_observed_at = time.monotonic()

        # Observe FM for longer than the slowest contradicted cadence; the
        # state must stay cleared throughout.
        window_end = fm_observed_at + _FM_WINDOW_S
        while time.monotonic() < window_end:
            state = await _public_state(web)
            assert state["main"]["mode"] == "FM"
            assert _controls_unavailable(state)
            await asyncio.sleep(0.25)
        stray = [
            (command, sub)
            for at, command, sub in sim.guarded_reads
            if at > fm_observed_at + _IN_FLIGHT_GRACE_S
            and (command, sub) in {(c, s) for _, c, s in _FM_NAKED_READS}
        ]
        assert stray == [], f"reads the radio refuses in FM reached the wire: {stray}"

        await _select_mode(web, protocol, "USB")
        state = await _wait_for_state(
            web,
            lambda s: s["main"]["mode"] == "USB" and _controls_available(s),
            deadline_s=_TRANSITION_DEADLINE_S,
            what="USB observed and the four controls reacquired",
        )
        assert {leaf: state["main"][leaf] for leaf, _, _ in _FM_NAKED_READS} == (
            usb_values
        )
