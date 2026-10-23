"""CI consistency guard: ``chk_vfo`` ↔ parser must stay in sync.

Variant A 1/5 of epic #1341 — closes #1342 (part).

Property
--------
If any radio profile causes ``chk_vfo`` to return ``"1"`` (i.e. Hamlib
will then prefix every command with ``VFOA``/``VFOB``/``currVFO``),
then ``parse_line`` MUST accept the prefixed form for every short in
``VFO_PREFIXABLE_SHORTS`` without raising ``ValueError``.

Why this guard exists
---------------------
Regression #1319 escaped CI in #722 because the developer flipped
``_cmd_chk_vfo`` to advertise vfo_opt without updating the parser to
consume the leading VFO token Hamlib then started sending. Result:
every command failed with ``RPRT -4``, breaking WSJT-X / fldigi /
JS8Call on dual-RX profiles.

This guard is the structural fix. Once Variant A 2-5 lands and the
parser becomes vfo-aware, this test passes permanently. If a future
change re-flips ``chk_vfo`` to ``"1"`` AND breaks the parser, this
test fails immediately — preventing another #1319.

Lifecycle
---------
- Pre-A2: ``chk_vfo`` returns ``"0"`` unconditionally → property is
  vacuously true. The test was xfailed because the *parser* could not
  yet accept ``f VFOA`` (``max_args=0``).
- After A2 (#1343, this state): parser accepts the prefix → test
  passes naturally; the xfail marker has been removed. ``chk_vfo``
  still returns ``"0"`` per Variant B, so the guard is currently
  vacuously satisfied.
- After A5 (#1346): chk_vfo flips back to ``"1"`` for dual-RX → guard
  becomes load-bearing; future re-regressions fail this test.

References
----------
- Issue #1319 — the bug.
- Epic #1341 — five-PR fix plan.
- Hamlib spec — https://hamlib.sourceforge.net/manuals/4.5.5/rigctl.1.html
"""

from __future__ import annotations

from unittest.mock import create_autospec

import pytest

from rigplane.core.command_dispatch import execute_command_intent
from rigplane.core.command_service import command_intent_from_request
from rigplane.core.radio_protocol import (
    RelativeVfoFrequencyWritable,
    RelativeVfoModeWritable,
)
from rigplane.rigctld.handler import _classify_rigctld_tx_intent
from rigplane.rigctld.protocol import parse_line

# Canonical list of short commands Hamlib prefixes with VFO under
# chk_vfo=1, per the rigctl(1) spec.
#
#   GET  f m t j s l u i x
#   SET  F M T L U S I X
#
# (``v``/``V`` and ``\get_vfo``/``\set_vfo`` are not in this set —
# they explicitly *take* a VFO as their primary argument and were
# already supported pre-#722.)
VFO_PREFIXABLE_SHORTS: frozenset[str] = frozenset(
    {
        "f",
        "m",
        "t",
        "j",
        "s",
        "l",
        "u",
        "i",
        "x",
        "F",
        "M",
        "T",
        "L",
        "U",
        "S",
        "I",
        "X",
    }
)


@pytest.mark.parametrize("short", sorted(VFO_PREFIXABLE_SHORTS))
def test_chk_vfo_implies_parser_accepts_vfo_arg(short: str) -> None:
    """``parse_line(b"<short> VFOA ...")`` must not raise ``ValueError``.

    The trailing args are minimal-valid for each command (e.g. ``f``
    needs only the VFO; ``L`` needs VFO + level + value). Anything
    that raises here is a #1319-class consistency break.
    """
    # Build the minimum-valid wire form per command's payload arity.
    extra: dict[str, str] = {
        "l": " STRENGTH",
        "u": " NB",
        "F": " 14250000",
        "M": " USB 2400",
        "T": " 1",
        "L": " RFPOWER 0.5",
        "U": " NB 1",
        "S": " 1 VFOB",
        "I": " 14080100",
        "X": " USB 2400",
    }
    wire = f"{short} VFOA{extra.get(short, '')}".encode("ascii")
    # The contract: no ValueError. The exact ``cmd.args`` shape is up to
    # A2's design and is not asserted here — that's covered in
    # tests/test_rigctld_protocol.py::TestParseLineVfoPrefix.
    parse_line(wire)


def test_vfo_prefixable_set_documented() -> None:
    """The canonical list of VFO-prefixable shorts is exhaustive.

    Pure-Python self-check — passes on `main`. If a maintainer adds a
    new VFO-prefixable command (e.g. ``\\set_split_freq``), this list
    must be updated AND the parametrised guard above will assert the
    parser-side support is present.
    """
    # GET shorts (Hamlib's "Reading" group).
    assert {"f", "m", "t", "j", "s", "l", "u", "i", "x"} <= VFO_PREFIXABLE_SHORTS
    # SET shorts (Hamlib's "Writing" group).
    assert {"F", "M", "T", "L", "U", "S", "I", "X"} <= VFO_PREFIXABLE_SHORTS
    # Total = 17 commands (9 GET + 8 SET).
    assert len(VFO_PREFIXABLE_SHORTS) == 17


@pytest.mark.parametrize(
    ("name", "params", "family"),
    [
        ("set_split_freq", {"freq_hz": 14_080_100}, "frequency"),
        ("set_split_mode", {"mode": 1, "filter_width": None, "data": None}, "mode"),
        ("set_split_mode", {"mode": 1, "filter_width": 2, "data": True}, "mode"),
    ],
)
def test_split_write_intents_have_no_relative_role_overlay(
    name, params, family
) -> None:
    intent = command_intent_from_request(
        name, params, source="rigctld", session_id="split-client"
    )
    assert intent.target is None
    assert intent.pending_policy == "none"
    assert intent.expected_observations == ()
    assert intent.params["selected"] is False
    assert intent.params["session_id"] == "split-client"
    classification = _classify_rigctld_tx_intent(intent)
    assert classification.disposition.value == "tx-safe"
    assert classification.family is not None
    assert classification.family.value == family


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "capability", "method", "kwargs"),
    [
        (
            "set_split_freq",
            {"freq_hz": 14_080_100},
            RelativeVfoFrequencyWritable,
            "write_relative_vfo_frequency",
            {"freq_hz": 14_080_100, "selected": False},
        ),
        (
            "set_split_mode",
            {"mode": 1, "filter_width": 2, "data": True},
            RelativeVfoModeWritable,
            "write_relative_vfo_mode",
            {"mode": 1, "selected": False, "filter_width": 2, "data": True},
        ),
    ],
)
async def test_split_descriptors_call_runtime_protocol(
    name, params, capability, method, kwargs
) -> None:
    radio = create_autospec(capability, instance=True)
    intent = command_intent_from_request(name, params, source="rigctld")
    await execute_command_intent(radio, intent)
    getattr(radio, method).assert_awaited_once_with(**kwargs)
