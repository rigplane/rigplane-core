"""VFO, scan, dual watch, split, tuning step commands.

Migrated onto the bound command map in MOR-2007 Steps 5..N (module 3,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4): every builder
here requires ``cmd_map``, with no hardcoded fallback left. This module
accounted for 9 of the rows `tests/command_map_parity_divergences.txt`
recorded before this migration (re-measured at this head -- the plan's
original count of 10 included one row an earlier D2 pass had already
closed) -- all now gone, since a divergence needs two disagreeing
implementations and this module has only one left.

Four owner rulings from the MOR-2007 ticket are implemented here:

1. **Split dual-watch setter keys.** ``set_dual_watch_on``/``_off`` resolve
   the split, get_/set_-prefixed keys ``set_dual_watch_on``/
   ``set_dual_watch_off`` instead of the bare ``set_dual_watch`` the
   hardcoded fallback used to resolve -- IC-7610/IC-9700 declare the two
   directions at different wire addresses (``[0x07,0xC1]``/``[0x07,0xC0]``
   vs ``[0x16,0x59,0x01]``/``[0x16,0x59,0x00]``), which one shared key could
   never carry. ``set_dual_watch`` itself still delegates by name, per
   `tests/test_profile_command_coverage.py`'s ``_delegate_target_names``.
2. **Quick Split / Quick Dual Watch are persistent menu toggles, not
   one-shot triggers.** Bench-confirmed on the live IC-7300: ``1A 05 0030``
   is readable, writable and persistent, restored across power cycles.
   The pre-migration ``quick_split()``/``quick_dual_watch()`` builders
   always sent a bare GET frame (the menu address alone, no data byte) and
   their only caller (`runtime/radio.py`) never read the reply -- they
   fired nothing. Replaced by real ``get_/set_quick_split`` and
   ``get_/set_quick_dual_watch`` pairs: the getter reads the toggle, the
   setter writes the caller-supplied boolean as a single BCD byte, the
   same shape `config.py: set_civ_transceive` uses for its own 0x1A 0x05
   boolean toggle.
3. **No 0x07 family on IC-9700.** IC-9700's dual watch lives at
   ``0x16 0x59`` (Command29's PREAMP command, not the VFO-select family);
   nothing here special-cases that address -- the split-key resolution in
   (1) is what lets `rigs/ic9700.toml`'s ``[0x16, 0x59, ...]`` tuples carry
   it without a code branch.
4. **Scan-type and scan-resume validation domains move to profile data.**
   ``scan_start_type``/``scan_set_resume`` no longer validate their sub-byte
   against a code-level frozenset -- ``VALID_SCAN_TYPES`` (which omitted
   the documented 0x13 fine-dF scan on IC-7300/IC-7610/IC-9700 and IC-705's
   0x24 mode-select scan) and ``VALID_SCAN_RESUME`` (which accepted
   0xD0-0xD3 where every documented CI-V guide lists only 0xD0/0xD3) are
   deleted. Domain validation moves to the profile-aware caller
   (`runtime/radio.py: CoreRadio.scan_start`/``scan_set_resume``, reading
   ``RadioProfile.scan_type_values``/``scan_resume_values``), the same
   shape `commands/dsp.py: set_agc` already uses for AGC-mode validation
   (MOR-1522). ``VALID_DF_SPANS`` is unaffected and stays here: every
   profile with a citation for it (IC-7300/7610/9700/705) declares the
   identical 0xA1-0xA7 table, so it is a wire-format constant, not a
   per-radio domain.

Every builder calls `_frame.py: _build_from_map` directly rather than a
shared template, matching `config.py`/`levels.py`'s reasoning: this module
never had a shared per-builder template to de-delegate from (its
`_CMD_*`/`_SUB_*`/`_CTL_MEM_*`/`_VFO_*` constants were only ever read
inline, by this module's own fallback branches, now deleted along with the
branches -- verified by grep against the whole repo before removal from
`_frame.py`; ``_CMD_CTL_MEM``/``_SUB_CTL_MEM`` survive there, since
`system.py`'s date/time/UTC-offset getters still route through
`_builders.py: _build_ctl_mem_get`, unmigrated).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._codec import _bcd_byte, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "get_vfo")
@require_cmd_map
def get_vfo(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'get VFO' CI-V command (0x07 read back current VFO)."""
    return _build_from_map(cmd_map, "get_vfo", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "get_main_sub_band")
@require_cmd_map
def get_main_sub_band(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build a 'get main/sub band' CI-V command (0x07 0xD2)."""
    return _build_from_map(
        cmd_map, "get_main_sub_band", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_vfo")
@require_cmd_map
def set_vfo(
    code: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap,
) -> bytes:
    """Select a VFO or receiver by its wire code.

    Args:
        code: The selector byte to send, taken from the caller's profile.

    Which byte names which VFO is a property of the radio, declared per
    profile as ``[vfo] main_select`` / ``sub_select``. ``commands`` may not
    import ``profiles``, so the resolution belongs one layer up --
    ``runtime/radio.py: CoreRadio._set_vfo_wire`` does it.
    """
    return _build_from_map(
        cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=bytes([code])
    )


@expose_command_key(lambda cmd_map: "set_vfo")
@require_cmd_map
def vfo_a_equals_b(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Copy VFO A to VFO B (A=B).

    No production caller reaches this builder (or ``vfo_swap`` below):
    `runtime/_dual_rx_runtime.py: equalize_vfo_ab`/``swap_vfo_ab`` build
    the same ``0x07`` frame directly from ``RadioProfile.equal_ab_code``/
    ``swap_ab_code`` instead, which is the profile-driven byte this
    builder's ``data=b"\\xa0"`` is not -- out of scope for MOR-2007 (no
    divergence row and no owner ruling name it); left unchanged, migrated
    only for ``cmd_map`` per the module-wide contract.
    """
    return _build_from_map(
        cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=b"\xa0"
    )


@expose_command_key(lambda cmd_map: "set_vfo")
@require_cmd_map
def vfo_swap(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Swap VFO A and B. See ``vfo_a_equals_b`` -- same caller situation."""
    return _build_from_map(
        cmd_map, "set_vfo", to_addr=to_addr, from_addr=from_addr, data=b"\xb0"
    )


@expose_command_key(lambda cmd_map: "set_split")
@require_cmd_map
def set_split(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Enable or disable split mode."""
    return _build_from_map(
        cmd_map,
        "set_split",
        to_addr=to_addr,
        from_addr=from_addr,
        data=b"\x01" if on else b"\x00",
    )


@expose_command_key(lambda cmd_map: "get_split")
@require_cmd_map
def get_split(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to read split state (0x0F)."""
    return _build_from_map(cmd_map, "get_split", to_addr=to_addr, from_addr=from_addr)


@expose_command_key(lambda cmd_map: "get_tuning_step")
@require_cmd_map
def get_tuning_step(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to get tuning step (0x10)."""
    return _build_from_map(
        cmd_map, "get_tuning_step", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_tuning_step")
@require_cmd_map
def set_tuning_step(
    step: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to set tuning step (0x10).

    ``step`` (0-8, the BCD-encoded index) is a wire-format bound uniform
    across every profile that declares tuning steps, not a per-radio
    domain -- unlike scan type/resume below, this check stays here
    (unaffected by MOR-2007 ruling 4).
    """
    if not 0 <= step <= 8:
        raise ValueError(f"Tuning step must be 0-8, got {step}")
    return _build_from_map(
        cmd_map,
        "set_tuning_step",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([_bcd_byte(step)]),
    )


@expose_command_key(lambda cmd_map: "scan_start")
@require_cmd_map
def scan_start(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to start scanning (0x0E 0x01)."""
    return _build_from_map(
        cmd_map, "scan_start", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
    )


@expose_command_key(lambda cmd_map: "scan_stop")
@require_cmd_map
def scan_stop(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to stop scanning (0x0E 0x00)."""
    return _build_from_map(
        cmd_map, "scan_stop", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
    )


# Valid DeltaF span sub-bytes: 0xA1=+-5kHz .. 0xA7=+-1MHz. Every profile
# with a citation for this domain (IC-7300/IC-7610/IC-9700/IC-705 CI-V
# guides) declares the identical 7-value table, so -- unlike
# scan_start_type/scan_set_resume's domains below, which vary by radio and
# moved to profile data under MOR-2007 ruling 4 -- this one is a
# wire-format constant and stays a module-level frozenset.
VALID_DF_SPANS = frozenset(range(0xA1, 0xA8))


@expose_command_key(lambda cmd_map: "scan_start_type")
@require_cmd_map
def scan_start_type(
    scan_type: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to start scan with specific type (0x0E sub).

    Which sub-bytes are legal is a per-radio domain (MOR-2007 ruling 4):
    the deleted code-level ``VALID_SCAN_TYPES`` frozenset omitted 0x13
    (fine ΔF scan, documented on IC-7300/IC-7610/IC-9700) and IC-705's
    0x24 (mode-select scan). This builder only encodes the byte the
    caller supplies; the profile-aware caller
    (`runtime/radio.py: CoreRadio.scan_start`) validates it against
    ``RadioProfile.scan_type_values`` first -- the same shape
    `commands/dsp.py: set_agc` uses for AGC-mode domain validation
    (MOR-1522).
    """
    return _build_from_map(
        cmd_map,
        "scan_start_type",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([scan_type]),
    )


@expose_command_key(lambda cmd_map: "scan_set_df_span")
@require_cmd_map
def scan_set_df_span(
    df_span: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to set DeltaF scan span (0x0E 0xA1-0xA7)."""
    if df_span not in VALID_DF_SPANS:
        raise ValueError(f"df_span must be 0xA1-0xA7, got {hex(df_span)}")
    return _build_from_map(
        cmd_map,
        "scan_set_df_span",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([df_span]),
    )


@expose_command_key(lambda cmd_map: "scan_set_resume")
@require_cmd_map
def scan_set_resume(
    resume_mode: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to set scan resume mode (0x0E sub).

    Which sub-bytes are legal is a per-radio domain (MOR-2007 ruling 4):
    every documented CI-V guide (IC-7300/IC-7610/IC-9700/IC-705) lists only
    0xD0 (resume OFF) and 0xD3 ("Close&Delay") -- not the 0xD1/0xD2
    5s/10s states the deleted code-level ``VALID_SCAN_RESUME`` frozenset
    accepted on every radio regardless. This builder only encodes the byte
    the caller supplies; the profile-aware caller
    (`runtime/radio.py: CoreRadio.scan_set_resume`) validates it against
    ``RadioProfile.scan_resume_values`` first, same shape as
    ``scan_start_type`` above.
    """
    return _build_from_map(
        cmd_map,
        "scan_set_resume",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([resume_mode]),
    )


@expose_command_key(lambda cmd_map: "set_dual_watch_off")
@require_cmd_map
def set_dual_watch_off(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to turn off dual watch.

    Resolves the split key ``set_dual_watch_off`` (MOR-2007 ruling 1), not
    the bare ``set_dual_watch`` the hardcoded fallback used to resolve --
    see the module docstring.
    """
    return _build_from_map(
        cmd_map, "set_dual_watch_off", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_dual_watch_on")
@require_cmd_map
def set_dual_watch_on(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to turn on dual watch. See ``set_dual_watch_off``."""
    return _build_from_map(
        cmd_map, "set_dual_watch_on", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_dual_watch")
@require_cmd_map
def get_dual_watch(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V command to query dual watch status.

    Unlike the setter, the getter needs no key split: every profile that
    declares dual watch reads it back through one unified entry
    (IC-7610's ``[0x07, 0xC2]``, IC-9700's ``[0x16, 0x59]``), since only
    the write side needs a separate address per direction.
    """
    return _build_from_map(
        cmd_map, "get_dual_watch", to_addr=to_addr, from_addr=from_addr
    )


@require_cmd_map
def set_dual_watch(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V command to enable or disable dual watch.

    Dispatches to ``set_dual_watch_on``/``set_dual_watch_off`` (MOR-2007
    ruling 1's split keys) rather than resolving a key of its own --
    `tests/test_profile_command_coverage.py` resolves this delegate by
    walking both branches by name; it deliberately carries no
    ``@expose_command_key`` (its own key is a function of ``on``, not of
    ``cmd_map`` alone).
    """
    return (
        set_dual_watch_on(to_addr=to_addr, from_addr=from_addr, cmd_map=cmd_map)
        if on
        else set_dual_watch_off(to_addr=to_addr, from_addr=from_addr, cmd_map=cmd_map)
    )


@expose_command_key(lambda cmd_map: "get_quick_split")
@require_cmd_map
def get_quick_split(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Read the persistent Quick Split menu toggle (0x1A 0x05 <addr>).

    MOR-2007 ruling 2: bench-confirmed on the live IC-7300, this menu item
    is readable, writable and persistent (restored across power cycles) --
    not the one-shot trigger the pre-migration ``quick_split()`` name
    implied. That builder always sent this same bare-GET frame (the menu
    address alone, no data byte) and its only caller
    (`runtime/radio.py`) never read the reply, so it fired nothing;
    deleted along with ``quick_dual_watch()`` below.
    """
    return _build_from_map(
        cmd_map, "get_quick_split", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_quick_split")
@require_cmd_map
def set_quick_split(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Write the persistent Quick Split menu toggle. See ``get_quick_split``."""
    return _build_from_map(
        cmd_map,
        "set_quick_split",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(1 if enabled else 0, byte_count=1),
    )


@expose_command_key(lambda cmd_map: "get_quick_dual_watch")
@require_cmd_map
def get_quick_dual_watch(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Read the persistent Quick Dual Watch menu toggle.

    See ``get_quick_split`` -- same ruling, same shape.
    """
    return _build_from_map(
        cmd_map, "get_quick_dual_watch", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_quick_dual_watch")
@require_cmd_map
def set_quick_dual_watch(
    enabled: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Write the persistent Quick Dual Watch menu toggle.

    See ``get_quick_split``.
    """
    return _build_from_map(
        cmd_map,
        "set_quick_dual_watch",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(1 if enabled else 0, byte_count=1),
    )


# Backward-compat aliases
select_vfo = set_vfo
start_scan = scan_start
stop_scan = scan_stop
