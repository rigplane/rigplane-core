"""Golden-fixture test runner for the rigctld protocol layer.

Loads tests/golden/protocol_golden.json and for each fixture:
  1. parse_line(input)  → RigctldCommand   (or direct cmd from fixture)
  2. handler.execute(cmd) → RigctldResponse  (with AsyncMock radio per mock spec)
  3. format_response(cmd, resp, session) → bytes
  4. Assert exact wire bytes match expected_output (normal mode)
  5. Assert exact wire bytes match extended_output (extended mode, if present)

Fixture fields
--------------
input            : str  — raw text fed to parse_line (mutually exclusive with cmd)
cmd              : dict — direct RigctldCommand spec, bypasses parse_line
expect_parse_error : bool — if true, parse_line must raise ValueError
mock             : dict — radio method mocks:
                      key           → return_value (int/bool/float/list)
                      get_mode_info → [mode_int, filter_or_null]  (special)
                      "<method>__exc" → "ConnectionError"|"TimeoutError"
read_only        : bool — use handler with read_only=True config
expected_output  : str  — normal-mode wire string
extended_output  : str  — extended-mode wire string (optional)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from _caps import FULL_ICOM_CAPS
from rigplane.exceptions import ConnectionError as IcomConnectionError
from rigplane.exceptions import TimeoutError as IcomTimeoutError
from rigplane.radio_protocol import MetersCapable
from rigplane.profiles import resolve_radio_profile
from _tx_target_fixtures import observe_tx_target
from rigplane.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldCommand,
    RigctldConfig,
)
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import format_error, format_response, parse_line
from rigplane.types import Mode

# ---------------------------------------------------------------------------
# Load fixtures at module level so parametrize sees them at collection time.
# ---------------------------------------------------------------------------

_FIXTURES_PATH = Path(__file__).parent / "golden" / "protocol_golden.json"
_FIXTURES: list[dict] = json.loads(_FIXTURES_PATH.read_text())


# ---------------------------------------------------------------------------
# Mock-radio builder
# ---------------------------------------------------------------------------

_EXC_MAP: dict[str, Exception] = {
    "ConnectionError": IcomConnectionError("mock connection error"),
    "TimeoutError": IcomTimeoutError("mock timeout"),
}


class _MockRadio(SimpleNamespace):
    pass


MetersCapable.register(_MockRadio)


def _make_mock_radio(mock_spec: dict) -> _MockRadio:
    """Build an explicit mock radio from a fixture mock-spec dict.

    Keys ending in ``__exc`` set a side_effect instead of a return_value.
    The ``get_mode_info`` key expects a [mode_int, filter_or_null] list which
    is converted to the ``(Mode, int|None)`` tuple the handler expects.
    Level getters (get_s_meter, get_power, get_swr) are set as AsyncMock so
    await in the handler returns the value.
    """
    radio = _MockRadio()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.capabilities = set(FULL_ICOM_CAPS)
    # Sensible defaults so get_mode doesn't blow up when not explicitly mocked.
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.set_freq = AsyncMock(return_value=None)
    radio.get_mode_info = AsyncMock(return_value=(Mode.USB, None))
    radio.set_mode = AsyncMock(return_value=None)
    radio.get_data_mode = AsyncMock(return_value=False)
    radio.set_data_mode = AsyncMock(return_value=None)
    radio.set_ptt = AsyncMock(return_value=None)

    _async_methods = {"get_s_meter", "get_rf_power", "get_swr", "get_data_mode"}

    for key, value in mock_spec.items():
        if key.endswith("__exc"):
            method = key[:-5]
            exc = _EXC_MAP[value]
            setattr(radio, method, AsyncMock(side_effect=exc))
        elif key == "get_mode_info":
            mode_int, filt = value[0], value[1]
            setattr(radio, key, AsyncMock(return_value=(Mode(mode_int), filt)))
        elif key in _async_methods:
            setattr(radio, key, AsyncMock(return_value=value))
        else:
            setattr(radio, key, AsyncMock(return_value=value))

    return radio


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", _FIXTURES, ids=[f["id"] for f in _FIXTURES])
async def test_golden_protocol(fixture: dict) -> None:
    """Execute one golden fixture end-to-end through parse → handler → format."""
    mock_spec = fixture.get("mock", {})
    radio = _make_mock_radio(mock_spec)
    read_only = fixture.get("read_only", False)
    # cache_ttl=0.0 → cache always expired → deterministic radio calls in tests.
    config = RigctldConfig(read_only=read_only, cache_ttl=0.0)
    # Populate level cache when mock has level getters so get_level returns numeric (not RPRT -4)
    from rigplane.rigctld.state_cache import StateCache

    cache = StateCache()
    if "get_s_meter" in mock_spec:
        cache.update_s_meter(mock_spec["get_s_meter"])
    if "get_power" in mock_spec:
        cache.update_rf_power(float(mock_spec["get_power"]) / 255.0)
    if "get_swr" in mock_spec:
        # ``get_swr`` is contracted as a calibrated ratio (>= 1.0); the
        # rigctld handler now passes the value through. The fixture's
        # mock value is the calibrated ratio directly (issue #1173).
        cache.update_swr(float(mock_spec["get_swr"]))
    handler = RigctldHandler(radio, config)
    frequency = mock_spec.get("get_freq", 14_074_000)
    if type(frequency) is int and frequency > 0:
        observe_tx_target(handler._state_store, frequency)

    normal_session = ClientSession()
    extended_session = ClientSession(extended_mode=True)

    # ── Branch 1: parse error expected ──────────────────────────────────────
    if fixture.get("expect_parse_error", False):
        raw = fixture["input"].encode("ascii")
        with pytest.raises(ValueError):
            parse_line(raw)
        # Server translates parse errors to ENIMPL (-4).
        expected_wire = format_error(HamlibError.ENIMPL).decode()
        assert fixture["expected_output"] == expected_wire, (
            f"[{fixture['id']}] parse-error expected_output mismatch"
        )
        return

    # ── Branch 2: direct cmd dict (bypasses parse_line) ─────────────────────
    if "cmd" in fixture:
        cmd_data = fixture["cmd"]
        cmd = RigctldCommand(
            short_cmd=cmd_data["short_cmd"],
            long_cmd=cmd_data["long_cmd"],
            args=tuple(cmd_data.get("args", [])),
            is_set=cmd_data.get("is_set", False),
        )

    # ── Branch 3: normal input through parse_line ────────────────────────────
    else:
        raw = fixture["input"].encode("ascii")
        cmd = parse_line(raw)

    # Execute command through handler.
    resp = await handler.execute(cmd)

    # Normal mode assertion.
    normal_out = format_response(cmd, resp, normal_session).decode("ascii")
    assert normal_out == fixture["expected_output"], (
        f"[{fixture['id']}] normal mode mismatch\n"
        f"  got:      {normal_out!r}\n"
        f"  expected: {fixture['expected_output']!r}"
    )

    # Extended mode assertion (optional).
    if "extended_output" in fixture:
        extended_out = format_response(cmd, resp, extended_session).decode("ascii")
        assert extended_out == fixture["extended_output"], (
            f"[{fixture['id']}] extended mode mismatch\n"
            f"  got:      {extended_out!r}\n"
            f"  expected: {fixture['extended_output']!r}"
        )
