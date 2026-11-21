"""MOR-3624: the duplex offset through the production stack (API replay).

The unit tests in ``tests/test_mor3624_repeater_offset.py`` seed observations
by calling internals (``_update_state_cache_from_frame``) and stop the
dispatch coverage at ``prepare_command_intent`` with a mocked ``sendCommand``
on the frontend side, so they pass even if the new public-state projection
never publishes the offset. This module drives the whole stack instead and
injects no observation anywhere:

- a ``MockIcomRadio`` with an offset register (answers the ``0x0C`` read with
  the last ``0x0D`` write, NAKs malformed offset frames), real UDP sockets,
  and the production ``IcomRadio``;
- a ``WebServer`` started the way ``rigplane web`` starts it (its poller,
  freshness service and acquisition drain run unmodified);
- the offset command through the real command endpoint (HTTP and WebSocket),
  and every assertion reads the delivered ``GET /api/v1/state`` payload or
  the frames the simulated radio actually received.

Only the radio and the network are fake. Deleting the ``0x0C`` projection in
``src/rigplane/runtime/_civ_rx.py`` (the line that publishes the readback)
leaves this test timing out on the unchanged public state; deleting the
``set_repeater_offset`` dispatch line refuses the command instead.

Both declaring profiles run the roundtrip (IC-705 single-receiver,
IC-9700 MAIN); the IC-9700 SUB case pins the MOR-3591 routing refusal
(``receiver_not_selected``, nothing on the wire). The request/reply goldens
live in
``frontend/src/components-v2/wiring/__tests__/fixtures/mor3624-repeater-offset.seam.json``
and are shared with the frontend twins
(``semantic-repeater-wiring.component.test.ts``,
``mor2111-repeater-conformance.isolated.test.ts``,
``repeater-adapter.test.ts``): this module asserts that file against the
production builders, then drives the stack from it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from rigplane.commands import (
    get_freq_offset,
    parse_civ_frame,
    parse_freq_offset_response,
    set_freq_offset,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from test_web_server import _http_get
from test_web_split_writes import _request

REPO_ROOT = Path(__file__).resolve().parent.parent
SEAM_PATH = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "components-v2"
    / "wiring"
    / "__tests__"
    / "fixtures"
    / "mor3624-repeater-offset.seam.json"
)


_REALTIME = pytest.mark.realtime(
    reason="real UDP simulator and production poller timing"
)


def _seam() -> dict[str, Any]:
    return json.loads(SEAM_PATH.read_text(encoding="utf-8"))


def _offset_data(hz: int, to_addr: int, command_map: Any) -> bytes:
    """The ``0x0D`` payload the production builder puts on the wire."""
    frame = parse_civ_frame(set_freq_offset(hz, to_addr=to_addr, cmd_map=command_map))
    assert (frame.command, frame.sub) == (0x0D, None)
    return bytes(frame.data)


class _OffsetSim(MockIcomRadio):
    """A ``MockIcomRadio`` with a duplex-offset register (MOR-3624).

    The ``0x0C`` read answers the last accepted ``0x0D`` write (or the
    constructor's initial bytes); a malformed offset frame is NAKed with no
    state change. Everything else keeps the native behaviour.
    """

    def __init__(self, *args: Any, initial_reply: bytes, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self._offset_reply = bytes(initial_reply)

    def _dispatch_civ_native(
        self, cmd: int, payload: bytes, from_addr: int
    ) -> bytes | None:
        to, frm = from_addr, self._radio_addr
        if cmd == 0x0C and not payload:
            return self._civ_frame(to, frm, 0x0C, data=self._offset_reply)
        if cmd == 0x0D and payload:
            if parse_freq_offset_response(bytes(payload)) is None:
                return self._civ_nak(to, frm)
            self._offset_reply = bytes(payload)
            return self._civ_ack(to, frm)
        if cmd in (0x0C, 0x0D):
            return self._civ_nak(to, frm)
        return super()._dispatch_civ_native(cmd, payload, from_addr)


def _production_stack_for(
    model: str, *, initial_hz: int
) -> tuple[Callable[[], AsyncIterator[tuple[WebServer, _OffsetSim]]], int, Any]:
    """Build the stack factory plus the radio address and command map."""
    profile = resolve_radio_profile(model=model)
    command_map = profile.command_map
    assert command_map is not None
    to_addr = profile.civ_addr
    initial_reply = _offset_data(initial_hz, to_addr, command_map)

    @contextlib.asynccontextmanager
    async def _stack() -> AsyncIterator[tuple[WebServer, _OffsetSim]]:
        sim = _OffsetSim(
            radio_addr=to_addr,
            dual_receiver=profile.receiver_count > 1,
            capture_civ_frames=True,
            initial_reply=initial_reply,
        )
        if profile.expected_identity_ids:
            sim.model_id = bytes.fromhex(profile.expected_identity_ids[0])
        await sim.start()
        radio = IcomRadio(
            host="127.0.0.1",
            port=sim.control_port,
            username="mor3624",
            password="mor3624",
            timeout=1.0,
            model=model,
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

    return _stack, to_addr, command_map


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
            raise AssertionError(
                f"timed out after {deadline_s}s waiting for: {what}; "
                f"main={state.get('main', {}).get('repeaterOffsetHz')!r} "
                f"status={state.get('fieldStatus', {}).get('main.repeaterOffsetHz')!r}"
            )
        await asyncio.sleep(0.1)
        state = await _public_state(web)
    return state


def _offset_observed(state: dict[str, Any], hz: int) -> bool:
    status = state["fieldStatus"].get("main.repeaterOffsetHz", {})
    return (
        status.get("observed") is True
        and status.get("availability") == "available"
        and state["main"].get("repeaterOffsetHz") == hz
    )


def _sent_0d_payloads(sim: _OffsetSim, *, since: int = 0) -> list[bytes]:
    """Every ``0x0D`` payload the simulated radio actually received."""
    frames: list[CivFrame] = []
    for raw in sim.civ_frames_received[since:]:
        try:
            frames.append(parse_civ_frame(bytes(raw)))
        except Exception:
            continue
    return [bytes(f.data) for f in frames if f.command == 0x0D]


# Bounded waits. The slowest legitimate step is start-up acquisition over a
# bare simulator (unanswered menu reads cost the 1 s radio timeout each).
_STARTUP_DEADLINE_S = 30.0
_TRANSITION_DEADLINE_S = 15.0


def test_seam_matches_the_producer_built_documents() -> None:
    """The shared seam file is producer-built, not hand-written."""
    seam = _seam()
    for model in ("IC-705", "IC-9700"):
        profile = resolve_radio_profile(model=model)
        assert profile.command_map is not None
        to_addr = profile.civ_addr
        write = parse_civ_frame(
            set_freq_offset(
                seam["command"]["params"]["offset_hz"],
                to_addr=to_addr,
                cmd_map=profile.command_map,
            )
        )
        assert (write.command, write.sub) == (
            seam["wire"]["set0d"]["command"],
            None,
        )
        assert bytes(write.data).hex() == seam["wire"]["set0d"]["dataHex"]
        read = parse_civ_frame(
            get_freq_offset(to_addr=to_addr, cmd_map=profile.command_map)
        )
        assert (read.command, read.sub) == (
            seam["wire"]["readback0c"]["command"],
            None,
        )
        assert read.data == b""
    assert (
        parse_freq_offset_response(bytes.fromhex(seam["wire"]["readback0c"]["dataHex"]))
        == seam["command"]["params"]["offset_hz"]
    )
    assert (
        parse_freq_offset_response(bytes.fromhex(seam["wire"]["set0d"]["dataHex"]))
        == seam["command"]["params"]["offset_hz"]
    )
    assert seam["publicState"]["value"] == seam["command"]["params"]["offset_hz"]


@_REALTIME
@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_offset_write_reads_back_through_the_public_state(
    model: str, protocol: str
) -> None:
    """The real command endpoint moves the delivered offset leaf.

    Starts from the seam's confirmed offset, submits the seam's command, and
    requires the seam's ``0x0D`` payload on the wire plus the seam's public
    value (observed and available) in the delivered state.
    """
    seam = _seam()
    initial_hz = seam["offsetsHz"]["confirmed"]
    target_hz = seam["command"]["params"]["offset_hz"]
    stack_factory, _, _ = _production_stack_for(model, initial_hz=initial_hz)
    async with stack_factory() as (web, sim):
        if model == "IC-9700":
            # The plain 0x0C/0x0D reaches only the selected receiver
            # (MOR-3591): the cold poll must have established MAIN first.
            await _wait_for_state(
                web,
                lambda s: s.get("active") == "MAIN" and _offset_observed(s, initial_hz),
                deadline_s=_STARTUP_DEADLINE_S,
                what=f"{model} start-up acquisition with MAIN selected",
            )
        else:
            await _wait_for_state(
                web,
                lambda s: _offset_observed(s, initial_hz),
                deadline_s=_STARTUP_DEADLINE_S,
                what=f"{model} start-up acquisition of the offset",
            )

        before = len(sim.civ_frames_received)
        status, result = await _request(
            web, protocol, seam["command"]["name"], dict(seam["command"]["params"])
        )
        assert status == (200 if protocol == "http" else None), result
        assert result["ok"] is True, result

        state = await _wait_for_state(
            web,
            lambda s: _offset_observed(s, target_hz),
            deadline_s=_TRANSITION_DEADLINE_S,
            what=f"{model} public offset {target_hz} Hz after the write",
        )
        assert state["fieldStatus"][seam["publicState"]["leaf"]]["observed"] is True
        assert (
            state["fieldStatus"][seam["publicState"]["leaf"]]["availability"]
            == "available"
        )
        written = _sent_0d_payloads(sim, since=before)
        assert bytes.fromhex(seam["wire"]["set0d"]["dataHex"]) in written


@_REALTIME
@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_ic9700_sub_offset_is_refused_before_wire_traffic(
    protocol: str,
) -> None:
    """MOR-3591 receiver routing through the real endpoint: SUB is not the
    selected receiver, so the write is refused and nothing reaches the wire.
    """
    seam = _seam()
    initial_hz = seam["offsetsHz"]["confirmed"]
    stack_factory, _, _ = _production_stack_for("IC-9700", initial_hz=initial_hz)
    async with stack_factory() as (web, sim):
        await _wait_for_state(
            web,
            lambda s: s.get("active") == "MAIN" and _offset_observed(s, initial_hz),
            deadline_s=_STARTUP_DEADLINE_S,
            what="IC-9700 start-up acquisition with MAIN selected",
        )
        before = len(sim.civ_frames_received)
        status, result = await _request(
            web,
            protocol,
            "set_repeater_offset",
            {"offset_hz": seam["command"]["params"]["offset_hz"], "receiver": 1},
        )
        if protocol == "http":
            assert status == 409, result
        assert result["ok"] is False, result
        assert result["error"] == "receiver_not_selected", result
        # The background poller keeps reading while we assert: only the
        # refused write's own 0x0D must be absent, never any frame at all.
        assert _sent_0d_payloads(sim, since=before) == []
        state = await _public_state(web)
        assert _offset_observed(state, initial_hz)
