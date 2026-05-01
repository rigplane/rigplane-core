"""Snapshot tests for rigctld ``dump_state`` (IC-7610 and Yaesu).

Variant A 1/5 of epic #1341 — closes #1342 (part).

These tests pin the **current** truncated dump_state output to a golden
file. They pass on `main` today (no xfail). Variant A 5/5 (#1346) will
extend dump_state with the missing ``vfo_list``, ``vfo_ops``,
``status_flags``, and ``targetable_vfo`` blocks; that PR updates the
golden files AND adds new positive assertions for the new blocks.

Why snapshots and not value-by-value assertions?
------------------------------------------------
The dump_state response is parsed positionally by Hamlib's netrigctl
client (atol/sscanf — see ``hamlib/src/rigctl_parse.c``). A drift in
*line count* or *line content* breaks every Hamlib client at once.
A full byte-equality snapshot is the cheapest way to catch that drift
in CI before it ships.

What is **not** asserted here
-----------------------------
- The ``hamlib_model_id`` substitution in :class:`YaesuRouting.dump_state`
  (line index 1) — that's covered by ``test_rigctld_handler.py::
  test_yaesu_dump_state_honors_toml_model_id``.
- The wire-format response (this asserts the in-memory list, not the
  ``\\n``-joined bytes — that's covered by the format_response tests).

Update protocol (when A5 lands)
-------------------------------
1. A5 extends ``_IC7610_DUMP_STATE`` and ``_YAESU_DUMP_STATE`` with VFO
   blocks.
2. Run this test once → it fails with the new lines diffed against the
   old goldens.
3. Manually verify the new lines are correct per the Hamlib spec.
4. Update ``tests/golden/dump_state_ic7610.txt`` and
   ``tests/golden/dump_state_yaesu.txt`` to match.
5. Add positive assertions in this file for the new VFO blocks.

References
----------
- Hamlib spec — https://hamlib.sourceforge.net/manuals/4.5.5/rigctl.1.html
- Issue #1319 — original regression.
- Epic #1341 — five-PR fix plan.
"""

from __future__ import annotations

from pathlib import Path

# Test-only import of private dump_state constants. See module docstring
# for why this is acceptable here.
from icom_lan.rigctld.handler import _IC7610_DUMP_STATE  # noqa: TID251
from icom_lan.rigctld.routing import _YAESU_DUMP_STATE  # noqa: TID251

GOLDEN_DIR = Path(__file__).parent / "golden"


def _load_golden(name: str) -> list[str]:
    """Read a dump_state golden file as a list of lines (no trailing \\n)."""
    text = (GOLDEN_DIR / name).read_text()
    # Strip the trailing newline-only line (file ends with \n).
    return text.splitlines()


class TestIc7610DumpStateSnapshot:
    """Pin the current IC-7610 dump_state output."""

    def test_matches_golden(self) -> None:
        """``_IC7610_DUMP_STATE`` matches ``tests/golden/dump_state_ic7610.txt``.

        If this fails: either the constant changed (intended — update
        the golden file) or it drifted accidentally (regression — fix
        the constant).
        """
        golden = _load_golden("dump_state_ic7610.txt")
        assert list(_IC7610_DUMP_STATE) == golden

    def test_line_count_is_25(self) -> None:
        """Hamlib's dump_state parser reads exactly 25+ positional fields.

        A5 (#1346) will extend this to ~32 with VFO blocks. Until then,
        any divergence from 25 means an unintended change.
        """
        assert len(_IC7610_DUMP_STATE) == 25

    def test_protocol_version_is_zero(self) -> None:
        """Line 0 — Hamlib protocol version. Must stay ``"0"``."""
        assert _IC7610_DUMP_STATE[0] == "0"

    def test_rig_model_is_ic7610(self) -> None:
        """Line 1 — RIG_MODEL_IC7610 = 3078 (Hamlib's rig_id table)."""
        assert _IC7610_DUMP_STATE[1] == "3078"

    def test_itu_region_is_one(self) -> None:
        """Line 2 — ITU region. Region 1 (Europe/Africa) by default."""
        assert _IC7610_DUMP_STATE[2] == "1"


class TestYaesuDumpStateSnapshot:
    """Pin the current Yaesu (FTX-1) dump_state output.

    Note: ``YaesuRouting.dump_state`` substitutes line index 1 with
    ``radio.hamlib_model_id`` at runtime. The raw ``_YAESU_DUMP_STATE``
    constant carries the FTX-1 default (2028); per-radio overrides are
    tested in ``test_rigctld_handler.py``.
    """

    def test_matches_golden(self) -> None:
        """``_YAESU_DUMP_STATE`` matches ``tests/golden/dump_state_yaesu.txt``.

        See module docstring for the update protocol. A5 (#1346) is the
        expected next change.
        """
        golden = _load_golden("dump_state_yaesu.txt")
        assert list(_YAESU_DUMP_STATE) == golden

    def test_line_count_is_25(self) -> None:
        """Same Hamlib-positional-parser concern as IC-7610."""
        assert len(_YAESU_DUMP_STATE) == 25

    def test_default_rig_model_is_ftx1(self) -> None:
        """Line 1 — default RIG_MODEL_FTX1 = 2028; overridden via TOML."""
        assert _YAESU_DUMP_STATE[1] == "2028"

    def test_max_rit_is_9999(self) -> None:
        """Line 13 — Yaesu CAT supports ±9.999 kHz RIT (Icom is 0)."""
        assert _YAESU_DUMP_STATE[13] == "9999"
