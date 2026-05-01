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

import pytest

from icom_lan.rigctld.protocol import parse_line

# Canonical list of short commands Hamlib prefixes with VFO under
# chk_vfo=1, per the rigctl(1) spec.
#
#   GET  f m t j s l u
#   SET  F M T L U S
#
# (``v``/``V`` and ``\get_vfo``/``\set_vfo`` are not in this set —
# they explicitly *take* a VFO as their primary argument and were
# already supported pre-#722.)
VFO_PREFIXABLE_SHORTS: frozenset[str] = frozenset(
    {"f", "m", "t", "j", "s", "l", "u", "F", "M", "T", "L", "U", "S"}
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
    assert {"f", "m", "t", "j", "s", "l", "u"} <= VFO_PREFIXABLE_SHORTS
    # SET shorts (Hamlib's "Writing" group).
    assert {"F", "M", "T", "L", "U", "S"} <= VFO_PREFIXABLE_SHORTS
    # Total = 13 commands (7 GET + 6 SET).
    assert len(VFO_PREFIXABLE_SHORTS) == 13
