"""MOR-4223: the SQL tag's squelch level over the real local API.

The SQL tag reads the receiver ``squelch`` leaf. These tests start the real
local web stack the way ``rigplane web`` does (production startup and routing;
the far end is the simulated radio, the only fake), set the squelch level
through the real ``POST /api/v1/commands`` path (``set_squelch``), let the
production poller read it back, and look at what a client sees:

- over ``GET /api/v1/state``;
- over the ``/api/v1/ws`` stream, both the full state a new client receives
  and the delta a connected client receives when the level changes, in the
  default rendering and as leaf deltas (``?state_delta=2``, MOR-4206).

The delivered ``squelch`` value and its ``fieldStatus`` row must agree with
the golden payloads in ``state-wire-goldens.json`` (``polledSquelchRaised`` /
``Zero`` / ``Unanswered`` and the leaf stream). The browser spec
``frontend/src/components-v2/layout/__tests__/state-wire-goldens.sql-tag.isolated.test.ts``
replays those same goldens through the real client, view-model adapter,
``VfoSurface`` and ``MobileRadioLayout``, so the rendered tag is tied to what
the wire really carries: tag text for a level above 0, an empty slot at 0 and
while the read is unanswered. ``scripts/gen_state_wire_goldens.py`` writes the
goldens and ``tests/web/test_state_wire_goldens.py`` keeps them current.

Mutations that turn these red: drop ``squelch`` from a profile's
``polling_only`` or its getter (the level never arrives), or stop projecting
``squelch`` into the public state (``web/runtime_helpers.py``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from rigplane.web._delta_encoder import apply_delta
from scripts.soak import soak_driver

_GOLDEN_PATH = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/lib/transport/__tests__/fixtures/state-wire-goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(_GOLDEN_PATH.read_text("utf-8"))
_MODELS = ["IC-705", "IC-7300"]
# CI-V 0x14 0x03: the polled squelch-level read. The simulator answers it with
# the level it holds, or refuses it (NAK) on request.
_SQUELCH_LEVEL = (0x14, 0x03)
_RAISED = 128
# What a row says about the observation, without its provenance: a level set
# by command and read back by the poller carries a different source than a
# polled reply alone, and the timing keys vary run to run.
_ROW_KEYS = ("storePath", "observed", "availability", "freshness")
_DEADLINE_S = 30.0
_POLL_STEP_S = 0.1

pytestmark = pytest.mark.realtime(
    reason="real HTTP and WebSocket sockets and the production poller on real time"
)


def _slice(main: dict[str, Any], field_status: dict[str, Any]) -> dict[str, Any]:
    """What the tag depends on: the squelch value and its status row."""
    row = field_status["main.squelch"]
    return {
        "squelch": main.get("squelch"),
        "row": {key: row[key] for key in _ROW_KEYS},
    }


def _golden_slice(model: str, case: str) -> dict[str, Any]:
    data = _GOLDENS[model][case]["data"]
    return _slice(data["main"], data["fieldStatus"])


async def _wait_until(predicate: Callable[[], Any], what: str) -> None:
    try:
        async with asyncio.timeout(_DEADLINE_S):
            while not await _resolve(predicate()):
                await asyncio.sleep(_POLL_STEP_S)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


async def _resolve(value: Any) -> Any:
    return await value if asyncio.iscoroutine(value) else value


@asynccontextmanager
async def _stack(
    model: str, *, refused: tuple[tuple[int, int | None], ...] = ()
) -> AsyncIterator[tuple[Any, aiohttp.ClientSession, str]]:
    stack = await soak_driver._start_simulator_stack(
        model=model, enable_audio=False, refused_civ_commands=refused
    )
    base = f"http://127.0.0.1:{stack.web_port}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_DEADLINE_S)
        ) as session:
            yield stack, session, base
    finally:
        await stack.aclose()


async def _get_state(session: aiohttp.ClientSession, base: str) -> dict[str, Any]:
    async with session.get(base + "/api/v1/state") as response:
        assert response.status == 200
        return await response.json()  # type: ignore[no-any-return]


async def _set_squelch(session: aiohttp.ClientSession, base: str, level: int) -> None:
    """Set the squelch level through the real HTTP command path."""
    body = {"id": f"sql-{level}", "name": "set_squelch", "params": {"level": level}}
    async with session.post(base + "/api/v1/commands", json=body) as response:
        assert response.status == 200, await response.text()
        assert (await response.json())["ok"] is True


async def _wait_for_http_level(
    session: aiohttp.ClientSession, base: str, raised: bool
) -> dict[str, Any]:
    """Poll the real HTTP state until it publishes a level above 0 (or 0)."""
    seen: list[dict[str, Any]] = []

    async def published() -> bool:
        seen[:] = [await _get_state(session, base)]
        level = seen[0]["main"].get("squelch")
        return level is not None and (level > 0) is raised

    await _wait_until(published, f"GET /api/v1/state to publish raised={raised}")
    return seen[0]


async def _first_ws_full_state(
    session: aiohttp.ClientSession, base: str
) -> dict[str, Any]:
    """The full state a newly connected WebSocket client receives."""
    async with session.ws_connect(base.replace("http", "ws") + "/api/v1/ws") as ws:
        async with asyncio.timeout(_DEADLINE_S):
            while True:
                message = json.loads((await ws.receive()).data)
                if message["type"] == "state_update":
                    assert message["data"]["type"] == "full"
                    return message["data"]["data"]  # type: ignore[no-any-return]


async def _next_state_envelope(ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    async with asyncio.timeout(_DEADLINE_S):
        while True:
            message = json.loads((await ws.receive()).data)
            if message["type"] == "state_update":
                return message["data"]  # type: ignore[no-any-return]


async def _await_level(
    ws: aiohttp.ClientWebSocketResponse, held: dict[str, Any], raised: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply stream frames to ``held`` until ``main.squelch`` is above 0 (or 0).

    Returns the frame that made it so and the reconstructed state after it.
    """
    state = held
    while True:
        envelope = await _next_state_envelope(ws)
        state = apply_delta(state, envelope)
        level = state["main"].get("squelch")
        if level is not None and (level > 0) is raised:
            return envelope, state


def _assert_live_delta(
    golden: dict[str, Any], baseline: dict[str, Any], envelope: dict[str, Any]
) -> None:
    """Hold one live delta to the committed golden's envelope contract."""
    assert envelope["type"] == "delta" == golden["type"]
    assert envelope.get("deltaVersion") == golden.get("deltaVersion")
    assert envelope["stateContractVersion"] == golden["stateContractVersion"]
    # The delta belongs to the baseline's stream and is newer than it.
    assert envelope["providerGeneration"] == baseline["providerGeneration"]
    assert envelope["stateRevision"] > baseline["stateRevision"]
    assert envelope["observationSeq"] > baseline["observationSeq"]


@pytest.mark.parametrize("model", _MODELS)
async def test_squelch_level_set_over_the_api_reaches_http_and_ws(model: str) -> None:
    """Level 0, then raised through the command API, then 0 again (MOR-4223).

    The simulator holds the squelch fully open at connect. The level is set
    with ``POST /api/v1/commands``; the production poller's 0x14 0x03 reads
    carry it to the client as the tag's source.
    """
    async with _stack(model) as (_stack_, session, base):
        zero = await _wait_for_http_level(session, base, False)
        assert _slice(zero["main"], zero["fieldStatus"]) == _golden_slice(
            model, "polledSquelchZero"
        )
        ws_zero = await _first_ws_full_state(session, base)
        assert _slice(ws_zero["main"], ws_zero["fieldStatus"]) == _golden_slice(
            model, "polledSquelchZero"
        )

        ws_url = base.replace("http", "ws") + "/api/v1/ws"
        async with (
            session.ws_connect(ws_url) as ws,
            session.ws_connect(ws_url + "?state_delta=2") as leaf_ws,
        ):
            baseline = await _next_state_envelope(ws)
            assert baseline["type"] == "full"
            assert _slice(
                baseline["data"]["main"], baseline["data"]["fieldStatus"]
            ) == _golden_slice(model, "polledSquelchZero")
            golden_baseline, golden_leaf = _GOLDENS[model][
                "polledSquelchLeafStreamRaised"
            ]
            leaf_baseline = await _next_state_envelope(leaf_ws)
            assert leaf_baseline["type"] == "full" == golden_baseline["type"]
            assert leaf_baseline["data"]["main"]["squelch"] == 0

            await _set_squelch(session, base, _RAISED)

            raised_delta, held = await _await_level(ws, baseline["data"], True)
            _assert_live_delta(
                _GOLDENS[model]["polledSquelchDeltaRaised"], baseline, raised_delta
            )
            assert _slice(held["main"], held["fieldStatus"]) == _golden_slice(
                model, "polledSquelchRaised"
            )
            leaf_delta, leaf_held = await _await_level(
                leaf_ws, leaf_baseline["data"], True
            )
            _assert_live_delta(golden_leaf, leaf_baseline, leaf_delta)
            assert ["main", "squelch"] in [list(path) for path, _ in leaf_delta["set"]]
            assert _slice(leaf_held["main"], leaf_held["fieldStatus"]) == _golden_slice(
                model, "polledSquelchRaised"
            )

            raised = await _wait_for_http_level(session, base, True)
            assert _slice(raised["main"], raised["fieldStatus"]) == _golden_slice(
                model, "polledSquelchRaised"
            )
            ws_raised = await _first_ws_full_state(session, base)
            assert _slice(ws_raised["main"], ws_raised["fieldStatus"]) == _golden_slice(
                model, "polledSquelchRaised"
            )

            await _set_squelch(session, base, 0)
            zero_delta, held = await _await_level(ws, held, False)
            _assert_live_delta(
                _GOLDENS[model]["polledSquelchDeltaZero"], raised_delta, zero_delta
            )
            assert _slice(held["main"], held["fieldStatus"]) == _golden_slice(
                model, "polledSquelchZero"
            )

            rezeroed = await _wait_for_http_level(session, base, False)
            assert _slice(rezeroed["main"], rezeroed["fieldStatus"]) == _golden_slice(
                model, "polledSquelchZero"
            )


@pytest.mark.parametrize("model", _MODELS)
async def test_squelch_level_the_radio_never_answers_stays_unobserved(
    model: str,
) -> None:
    """A radio that refuses the read leaves the level absent, never ``0``.

    The simulator refuses (NAK) every 0x14 0x03 read from the first one on.
    The test waits for several of the poller's reads to have been refused, so
    the absence is the result of polling, not of polling not having started.
    """
    async with _stack(model, refused=(_SQUELCH_LEVEL,)) as (stack, session, base):
        await _wait_until(
            lambda: stack.sim.refused_count >= 2, "two refused squelch-level polls"
        )

        state = await _get_state(session, base)
        assert state["main"].get("squelch") is None
        assert _slice(state["main"], state["fieldStatus"]) == _golden_slice(
            model, "polledSquelchUnanswered"
        )
        ws_state = await _first_ws_full_state(session, base)
        assert ws_state["main"].get("squelch") is None
        assert _slice(ws_state["main"], ws_state["fieldStatus"]) == _golden_slice(
            model, "polledSquelchUnanswered"
        )


def test_the_goldens_cover_the_radios_exercised_here() -> None:
    for model in _MODELS:
        for case in (
            "polledSquelchRaised",
            "polledSquelchZero",
            "polledSquelchUnanswered",
            "polledSquelchDeltaRaised",
            "polledSquelchDeltaZero",
            "polledSquelchLeafStreamRaised",
        ):
            assert case in _GOLDENS[model], f"{model}: no {case} golden"
