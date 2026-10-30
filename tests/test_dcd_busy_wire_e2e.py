"""MOR-3630: the BUSY lamp's squelch state over the real local API.

The lamp reads the receiver ``dcd`` leaf, and on a polling-only link the only
source of that leaf is the production poller asking the radio for its SQL
status (CI-V 0x15 0x01). These tests start the real local web stack the way
``rigplane web`` does (production startup and routing; the far end is the
simulated radio, the only fake), let that poller read the squelch as open,
then closed, then never answered, and look at what a client sees:

- over ``GET /api/v1/state``;
- over the ``/api/v1/ws`` stream, both the full state a new client receives
  and the delta a connected client receives when the squelch changes.

The delivered ``dcd`` value and ``fieldStatus`` rows must equal the golden
payloads in ``state-wire-goldens.json`` (``polledDcdOpen``/``Closed``/
``Unanswered``). The live deltas are held to the whole delta contract
against ``polledDcdDeltaOpen``/``Closed``: the envelope fields (type,
revisions, state-contract version, provider generation) and the ``dcd``
value, alias and ``fieldStatus`` rows, and the state a client reconstructs
from baseline plus delta. The browser spec
``frontend/src/components-v2/layout/__tests__/state-wire-goldens.busy-lamp.isolated.test.ts``
replays those same goldens through the real client, view-model adapter,
``VfoSurface`` and ``MobileRadioLayout``, so the lamp is tied to what the
wire really carries. ``scripts/gen_state_wire_goldens.py`` writes the goldens
and ``tests/web/test_state_wire_goldens.py`` keeps them current.

Mutations that turn these red: drop the ``"dcd"`` row from
``runtime/_state_queries.py::_RECEIVER_TOGGLE_GETTERS`` or the path from a
profile's ``polling_only`` (nothing polls, the leaf never arrives), or stop
projecting ``dcd`` into the public state (``web/runtime_helpers.py``).
"""

from __future__ import annotations

import asyncio
import copy
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
# CI-V 0x15 0x01: the polled squelch-status read. The simulator answers it
# with the squelch it was told to hold, or refuses it (NAK) on request.
_SQL_STATUS = (0x15, 0x01)
_DCD_FIELD_ROWS = ("main.dcd", "main.sMeterSqlOpen")
# Rows that vary run to run: when the sample was taken and the age budget
# counting down from it. Everything else in a row must equal the golden.
_VOLATILE_ROW_KEYS = frozenset({"lastObservedMonotonic", "maxAge"})
_DEADLINE_S = 30.0
_POLL_STEP_S = 0.1


def _dcd_slice(main: dict[str, Any], field_status: dict[str, Any]) -> dict[str, Any]:
    """What the lamp depends on: the dcd value, its alias and their rows."""
    return {
        "main": {key: main.get(key) for key in ("dcd", "sMeterSqlOpen")},
        "fieldStatus": {
            row: {
                key: value
                for key, value in field_status[row].items()
                if key not in _VOLATILE_ROW_KEYS
            }
            for row in _DCD_FIELD_ROWS
        },
    }


def _golden_slice(model: str, case: str) -> dict[str, Any]:
    data = _GOLDENS[model][case]["data"]
    return _dcd_slice(data["main"], data["fieldStatus"])


def _http_slice(state: dict[str, Any]) -> dict[str, Any]:
    return _dcd_slice(state["main"], state["fieldStatus"])


def _assert_delta_matches_golden(
    golden: dict[str, Any],
    baseline: dict[str, Any],
    envelope: dict[str, Any],
    reconstructed: dict[str, Any],
    expected_state: dict[str, Any],
) -> None:
    """Hold one live squelch-change delta to the committed delta golden.

    ``golden`` is the ``polledDcdDelta*`` envelope, ``baseline`` the full
    envelope the client held before, ``envelope`` the live frame,
    ``reconstructed`` the client state after applying it, and
    ``expected_state`` the ``polledDcd*`` full golden of the new squelch.
    """
    assert envelope["type"] == "delta" == golden["type"]
    # No extra envelope fields (``removed`` appears only when something was).
    assert set(envelope) - {"removed"} == set(golden)
    assert envelope["stateContractVersion"] == golden["stateContractVersion"]
    assert envelope["providerGeneration"] == golden["providerGeneration"]
    assert envelope["providerGeneration"] == baseline["providerGeneration"]
    # The delta is a newer state of the same stream, with its freshness
    # counters, not a replay of the baseline.
    assert envelope["stateRevision"] > baseline["stateRevision"]
    assert envelope["observationSeq"] > baseline["observationSeq"]
    assert envelope["freshnessRevision"] >= baseline["freshnessRevision"]
    removed = envelope.get("removed", [])
    assert "main" not in removed
    assert "fieldStatus" not in removed
    changed = envelope["changed"]
    assert "main" in changed
    assert "fieldStatus" in changed
    for row in _DCD_FIELD_ROWS:
        assert row in changed["fieldStatus"], f"the delta carries no {row} status row"
    # The dcd value, its alias and their status rows travel together in the
    # delta itself, equal to the golden delta's.
    assert _dcd_slice(changed["main"], changed["fieldStatus"]) == _dcd_slice(
        golden["changed"]["main"], golden["changed"]["fieldStatus"]
    )
    # And the client that applied it holds the full golden state.
    assert (
        _dcd_slice(reconstructed["main"], reconstructed["fieldStatus"])
        == expected_state
    )


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


async def _wait_for_http_dcd(
    session: aiohttp.ClientSession, base: str, expected: bool
) -> dict[str, Any]:
    """Poll the real HTTP state until it publishes ``dcd == expected``."""
    seen: list[dict[str, Any]] = []

    async def published() -> bool:
        seen[:] = [await _get_state(session, base)]
        return seen[0]["main"].get("dcd") is expected

    await _wait_until(published, f"GET /api/v1/state to publish dcd={expected}")
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


async def _await_dcd_delta(
    ws: aiohttp.ClientWebSocketResponse,
    held: dict[str, Any],
    expected: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply stream frames to ``held`` until ``main.dcd`` reads ``expected``.

    Returns the frame that made it so and the reconstructed state after it.
    """
    state = held
    while True:
        envelope = await _next_state_envelope(ws)
        state = apply_delta(state, envelope)
        if state["main"].get("dcd") is expected:
            return envelope, state


@pytest.mark.parametrize("model", _MODELS)
async def test_polled_open_then_closed_squelch_reaches_http_and_ws(model: str) -> None:
    """Open then closed, both delivered over HTTP and over WebSocket (MOR-3630).

    The simulator holds the squelch closed (idle) at connect; the test opens
    it and closes it again. Only the production poller's 0x15 0x01 reads
    carry the change to the client.
    """
    async with _stack(model) as (stack, session, base):
        closed = await _wait_for_http_dcd(session, base, False)
        assert _http_slice(closed) == _golden_slice(model, "polledDcdClosed")
        ws_closed = await _first_ws_full_state(session, base)
        assert _dcd_slice(ws_closed["main"], ws_closed["fieldStatus"]) == _golden_slice(
            model, "polledDcdClosed"
        )

        # A client already connected holds the full baseline, then sees each
        # change arrive as a delta on the stream.
        async with session.ws_connect(base.replace("http", "ws") + "/api/v1/ws") as ws:
            baseline = await _next_state_envelope(ws)
            assert baseline["type"] == "full"
            assert _dcd_slice(
                baseline["data"]["main"], baseline["data"]["fieldStatus"]
            ) == _golden_slice(model, "polledDcdClosed")

            stack.sim.set_squelch_open(True)
            open_delta, held = await _await_dcd_delta(ws, baseline["data"], True)
            _assert_delta_matches_golden(
                _GOLDENS[model]["polledDcdDeltaOpen"],
                baseline,
                open_delta,
                held,
                _golden_slice(model, "polledDcdOpen"),
            )

            stack.sim.set_squelch_open(False)
            closed_delta, held = await _await_dcd_delta(ws, held, False)
            _assert_delta_matches_golden(
                _GOLDENS[model]["polledDcdDeltaClosed"],
                open_delta,
                closed_delta,
                held,
                _golden_slice(model, "polledDcdClosed"),
            )

        opened = await _wait_for_http_dcd(session, base, True)
        assert _http_slice(opened) == _golden_slice(model, "polledDcdOpen")
        ws_opened = await _first_ws_full_state(session, base)
        assert _dcd_slice(ws_opened["main"], ws_opened["fieldStatus"]) == _golden_slice(
            model, "polledDcdOpen"
        )

        stack.sim.set_squelch_open(False)
        reclosed = await _wait_for_http_dcd(session, base, False)
        assert _http_slice(reclosed) == _golden_slice(model, "polledDcdClosed")


@pytest.mark.parametrize("model", _MODELS)
async def test_squelch_status_the_radio_never_answers_stays_unobserved(
    model: str,
) -> None:
    """A radio that refuses the poll leaves dcd absent, never ``false`` (MOR-3630).

    The simulator refuses (NAK) every 0x15 0x01 read from the first one on.
    The test waits for several of the poller's reads to have been refused, so
    the absence is the result of polling, not of polling not having started.
    """
    async with _stack(model, refused=(_SQL_STATUS,)) as (stack, session, base):
        await _wait_until(
            lambda: stack.sim.refused_count >= 2, "two refused squelch-status polls"
        )

        state = await _get_state(session, base)
        assert state["main"].get("dcd") is None
        assert _http_slice(state) == _golden_slice(model, "polledDcdUnanswered")
        ws_state = await _first_ws_full_state(session, base)
        assert ws_state["main"].get("dcd") is None
        assert _dcd_slice(ws_state["main"], ws_state["fieldStatus"]) == _golden_slice(
            model, "polledDcdUnanswered"
        )


def test_the_goldens_cover_the_radios_exercised_here() -> None:
    for model in _MODELS:
        for case in (
            "polledDcdOpen",
            "polledDcdClosed",
            "polledDcdUnanswered",
            "polledDcdDeltaOpen",
            "polledDcdDeltaClosed",
        ):
            assert case in _GOLDENS[model], f"{model}: no {case} golden"


def _live_like_delta(model: str, case: str) -> dict[str, Any]:
    """A frame as the stream delivers it: the golden delta, volatile keys aside."""
    return copy.deepcopy(_GOLDENS[model][case])  # type: ignore[no-any-return]


def _check_golden_delta(
    model: str, case: str, expected_case: str, envelope: dict[str, Any]
) -> None:
    baseline = copy.deepcopy(_GOLDENS[model]["polledDcdClosed"])
    baseline["stateRevision"] -= 1
    baseline["observationSeq"] -= 1
    reconstructed = apply_delta(copy.deepcopy(baseline["data"]), envelope)
    _assert_delta_matches_golden(
        _GOLDENS[model][case],
        baseline,
        envelope,
        reconstructed,
        _golden_slice(model, expected_case),
    )


@pytest.mark.parametrize("model", _MODELS)
def test_a_delta_equal_to_the_golden_passes_the_delta_contract(model: str) -> None:
    _check_golden_delta(
        model,
        "polledDcdDeltaOpen",
        "polledDcdOpen",
        _live_like_delta(model, "polledDcdDeltaOpen"),
    )


def _drop_status_row(envelope: dict[str, Any]) -> None:
    del envelope["changed"]["fieldStatus"]["main.dcd"]


def _mark_dcd_unobserved(envelope: dict[str, Any]) -> None:
    row = envelope["changed"]["fieldStatus"]["main.dcd"]
    row["observed"] = False
    row["availability"] = "missing"


def _mark_dcd_stale(envelope: dict[str, Any]) -> None:
    envelope["changed"]["fieldStatus"]["main.dcd"]["freshness"] = "stale"


def _wrong_value(envelope: dict[str, Any]) -> None:
    envelope["changed"]["main"]["dcd"] = False


def _wrong_generation(envelope: dict[str, Any]) -> None:
    envelope["providerGeneration"] += 1


def _extra_envelope_field(envelope: dict[str, Any]) -> None:
    envelope["unexpected"] = True


@pytest.mark.parametrize(
    "corrupt",
    [
        _drop_status_row,
        _mark_dcd_unobserved,
        _mark_dcd_stale,
        _wrong_value,
        _wrong_generation,
        _extra_envelope_field,
    ],
)
@pytest.mark.parametrize("model", _MODELS)
def test_a_corrupted_live_delta_fails_the_delta_contract(
    model: str, corrupt: Callable[[dict[str, Any]], None]
) -> None:
    """The delta check is not satisfied by ``main.dcd=True`` alone (MOR-3630).

    ``_mark_dcd_unobserved`` is the case the real adapter must never produce:
    the value arrives but its status row says nothing was observed, so the
    lamp would be dark while the value reads open.
    """
    envelope = _live_like_delta(model, "polledDcdDeltaOpen")
    corrupt(envelope)

    with pytest.raises(AssertionError):
        _check_golden_delta(model, "polledDcdDeltaOpen", "polledDcdOpen", envelope)
