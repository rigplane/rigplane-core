"""Golden-test harness for ``CivRuntime._update_radio_state_from_frame``.

This is the **fence** for the upcoming Tier 3 dispatch-table refactor (#1257).
Fixtures are defined in ``tests/fixtures/civ_rx_frames.json``; each entry
declares a CI-V frame, optional host setup, and the expected ``RadioState``
mutations after the dispatch ladder runs. The test loops over every fixture
and asserts each ``expected_state`` field path against the resulting state
object.

Refs #1063 (Tier 3 wave 1). Parent: #1255. Fences #1257.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState
from rigplane.types import CivFrame

_FIXTURES_PATH = Path(__file__).parent / "fixtures" / "civ_rx_frames.json"


def _load_fixtures() -> list[dict[str, Any]]:
    with _FIXTURES_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise TypeError(f"Expected JSON list at {_FIXTURES_PATH}, got {type(data)}")
    return data


def _resolve_path(root: Any, path: str) -> Any:
    """Resolve a dotted path against an object/list/mapping, e.g.
    ``main.vfo_a.freq_hz`` or ``tx_band_edges.0.start_hz``.
    """
    cur: Any = root
    for part in path.split("."):
        if part.isdigit() and isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = getattr(cur, part)
    return cur


def _build_frame(spec: dict[str, Any]) -> CivFrame:
    sub = spec.get("sub")
    data_hex = spec.get("data", "")
    data = bytes.fromhex(data_hex) if data_hex else b""
    return CivFrame(
        to_addr=spec.get("to_addr", CONTROLLER_ADDR),
        from_addr=spec.get("from_addr", IC_7610_ADDR),
        command=spec["command"],
        sub=sub,
        data=data,
        receiver=spec.get("receiver"),
    )


@pytest.fixture
def radio() -> IcomRadio:
    transport = MockTransport()
    r = IcomRadio("192.168.1.100", model="IC-7610")
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    r._radio_state = RadioState()
    return r


_FIXTURES = _load_fixtures()


@pytest.mark.parametrize(
    "fixture",
    _FIXTURES,
    ids=[entry["name"] for entry in _FIXTURES],
)
def test_civ_rx_dispatch_golden(radio: IcomRadio, fixture: dict[str, Any]) -> None:
    """Each JSON fixture: build CivFrame → dispatch → assert expected state."""
    setup = fixture.get("host_setup") or {}
    if "vfo_slot_override" in setup:
        radio._vfo_slot_override = dict(setup["vfo_slot_override"])  # type: ignore[attr-defined]
    if setup.get("clear_profile"):
        radio._profile = None  # type: ignore[assignment]
    if "preexisting_tx_band_edge" in setup:
        from rigplane.radio_state import TxBandEdge

        spec = setup["preexisting_tx_band_edge"]
        radio._radio_state.tx_band_edges.append(
            TxBandEdge(start_hz=spec["start_hz"], end_hz=spec["end_hz"])
        )
    if "active" in setup:
        radio._radio_state.active = setup["active"]
    if "dual_watch" in setup:
        radio._radio_state.dual_watch = bool(setup["dual_watch"])

    frame = _build_frame(fixture["frame"])
    radio._civ_runtime._update_radio_state_from_frame(frame)

    rs = radio._radio_state
    assert rs is not None
    expected: dict[str, Any] = fixture["expected_state"]
    for path, want in expected.items():
        got = _resolve_path(rs, path)
        assert got == want, (
            f"fixture={fixture['name']} path={path}: expected {want!r}, got {got!r}"
        )


def test_fixtures_file_is_non_empty() -> None:
    """Sanity: the JSON file must declare at least 20 fixtures (issue acceptance)."""
    assert len(_FIXTURES) >= 20, (
        f"expected ≥20 fixtures, got {len(_FIXTURES)} — see #1256 acceptance criteria"
    )
