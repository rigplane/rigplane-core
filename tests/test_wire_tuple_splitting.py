"""One splitting rule for CI-V wire tuples.

``commands/_frame.py`` used to have two independent rules for where a
sub-command byte lives: ``decode_wire_tuple`` (splitting a declared
``[commands]`` tuple) always treated ``wire[1]`` as ``sub`` positionally,
while ``parse_civ_frame`` (splitting a received frame's payload) put the
byte in ``.sub`` only for a command in ``_COMMANDS_WITH_SUB`` and left it
in ``.data`` otherwise. The two disagreed for every declared row with two
or more elements whose first byte is outside that set -- measured here
(at ``df7b1788``) as 51 of 1161 declared rows across the six CI-V
profiles (16 distinct names). This count moves as ``rigs/*.toml`` gains
or loses rows; what stays fixed is which 16 names disagree, since that
depends only on each name's first wire byte, not on how many other rows
exist.

Both functions now go through the single predicate
``command_carries_sub``, this file pins two properties of that fix:

- ``decode_wire_tuple(wire)`` agrees with what ``parse_civ_frame`` assigns
  to a frame built from that same wire, for every declared row on every
  shipped CI-V profile (``test_decode_wire_tuple_matches_parse_civ_frame``
  below -- this is also this change's red-proof: reverting
  ``decode_wire_tuple`` to its pre-fix positional rule makes this test
  fail on exactly the 51 rows enumerated above, and pass everywhere else).
- moving a byte from ``sub`` to the front of ``prefix`` (or back) changes
  nothing about the bytes ``_build_from_map`` puts on the wire, for every
  declared row (``test_wire_tuple_split_is_byte_neutral`` below).
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable

import pytest

from rigplane.commands._frame import (
    build_civ_frame,
    decode_wire_tuple,
    parse_civ_frame,
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_profile_command_binding import _civ_rig_configs  # noqa: E402

_TO_ADDR = 0xE0
_FROM_ADDR = 0x94


def _old_decode_wire_tuple(wire: tuple[int, ...]) -> tuple[int, int | None, bytes]:
    """The pre-fix rule: ``wire[1]`` is ``sub`` positionally, regardless of
    ``command_carries_sub``. Kept only to compute the "before" bytes for
    ``test_wire_tuple_split_is_byte_neutral`` -- ``decode_wire_tuple``
    itself no longer behaves this way.
    """
    command = wire[0]
    sub = wire[1] if len(wire) > 1 else None
    prefix = bytes(wire[2:])
    return command, sub, prefix


def _build_frame_via(
    decoder: Callable[[tuple[int, ...]], tuple[int, int | None, bytes]],
    wire: tuple[int, ...],
) -> bytes:
    """Mirror ``_build_from_map``'s use of a decoder: prepend ``prefix`` to
    (absent) caller data, then build the frame -- with no additional
    caller-supplied data, matching every declared row's own GET/bare shape.
    """
    command, sub, prefix = decoder(wire)
    data = prefix if prefix else None
    return build_civ_frame(_TO_ADDR, _FROM_ADDR, command, sub=sub, data=data)


def _all_declared_rows() -> list[tuple[str, str, tuple[int, ...]]]:
    rows: list[tuple[str, str, tuple[int, ...]]] = []
    for model in sorted(_civ_rig_configs()):
        cmd_map = _civ_rig_configs()[model].to_profile().command_map
        assert cmd_map is not None, f"{model}: to_profile() produced no command_map"
        for name in cmd_map:
            rows.append((model, name, cmd_map.get(name)))
    return rows


_ROWS = _all_declared_rows()


def _row_id(row: tuple[str, str, tuple[int, ...]]) -> str:
    model, name, _wire = row
    return f"{model}-{name}"


@pytest.mark.parametrize("row", _ROWS, ids=[_row_id(r) for r in _ROWS])
def test_decode_wire_tuple_matches_parse_civ_frame(
    row: tuple[str, str, tuple[int, ...]],
) -> None:
    """``decode_wire_tuple(wire)`` and ``parse_civ_frame`` of a frame built
    from that wire assign the same ``(command, sub)`` -- for every
    declared row on every shipped CI-V profile, not just the 51 rows
    known to have disagreed before this fix.
    """
    _model, _name, wire = row
    command, sub, prefix = decode_wire_tuple(wire)

    frame_bytes = build_civ_frame(
        _TO_ADDR, _FROM_ADDR, command, sub=sub, data=prefix if prefix else None
    )
    parsed = parse_civ_frame(frame_bytes)

    assert (parsed.command, parsed.sub) == (command, sub)
    assert parsed.data == prefix


@pytest.mark.parametrize("row", _ROWS, ids=[_row_id(r) for r in _ROWS])
def test_wire_tuple_split_is_byte_neutral(
    row: tuple[str, str, tuple[int, ...]],
) -> None:
    """Moving a byte between ``sub`` and the front of ``prefix`` must not
    change the bytes ``_build_from_map`` puts on the wire, for every
    declared row -- not just the 51 whose split actually changed.

    The "before" bytes are computed here from ``_old_decode_wire_tuple``
    (the pre-fix positional rule), not read from a recorded fixture, so
    this test measures the fix's effect directly rather than trusting a
    stored expectation.
    """
    _model, _name, wire = row
    before = _build_frame_via(_old_decode_wire_tuple, wire)
    after = _build_frame_via(decode_wire_tuple, wire)
    assert before == after
