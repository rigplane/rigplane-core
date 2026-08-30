"""Spectrum / waterfall scope commands (0x27 family)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import ScopeFixedEdge, bcd_decode, bcd_encode
from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_SCOPE,
    _SUB_SCOPE_CENTER_TYPE,
    _SUB_SCOPE_DATA_OUTPUT,
    _SUB_SCOPE_DURING_TX,
    _SUB_SCOPE_EDGE,
    _SUB_SCOPE_FIXED_EDGE,
    _SUB_SCOPE_HOLD,
    _SUB_SCOPE_MAIN_SUB,
    _SUB_SCOPE_MODE,
    _SUB_SCOPE_ON,
    _SUB_SCOPE_RBW,
    _SUB_SCOPE_REF,
    _SUB_SCOPE_SINGLE_DUAL,
    _SUB_SCOPE_SPAN,
    _SUB_SCOPE_SPEED,
    _SUB_SCOPE_VBW,
    _build_from_map,
    build_civ_frame,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import CivFrame


_SCOPE_SPAN_PRESETS_HZ: tuple[int, ...] = (
    2_500,
    5_000,
    10_000,
    25_000,
    50_000,
    100_000,
    250_000,
    500_000,
)
_SCOPE_FIXED_EDGE_RANGE_STARTS_HZ: tuple[int, ...] = (
    50_000_000,
    28_000_000,
    24_890_000,
    21_000_000,
    18_068_000,
    14_000_000,
    10_100_000,
    7_000_000,
    5_250_000,
    3_500_000,
    1_800_000,
    472_000,
    135_000,
    10_000,
)


# Scope sub-commands whose READ query carries a one-byte Main/Sub scope
# selector between the sub-command byte and the frame terminator
# (0x00 = MAIN, 0x01 = SUB).  Without it the IC-7610 silently ignores the
# query and the IC-7300 refuses it with a NAK.  Every other 0x27 read carries
# no selector byte -- except 0x1E, which carries a ``<range><edge>`` selector
# of its own; see ``get_scope_fixed_edge`` below.
#
# Imported by ``runtime/_state_queries.py: build_state_queries``, whose list
# ``runtime/radio_initial_state.py: fetch_initial_state`` then sends, and by
# ``web/radio_poller.py: RadioPoller._send_one_state_query``.  Those are the
# senders that consult this set.
#
# Two further places hold this membership and do NOT import it, so an edit
# here does not reach them -- change all three together:
#   ``runtime/_scope_runtime.py`` passes ``receiver=`` per getter, reaching
#     exactly these eight sub-commands and no others.
#   ``runtime/_civ_rx.py: CivRuntime._civ_expects_response`` repeats them as
#     a local tuple to decide whether a 0x27 reply is expected.  That copy is
#     executable and unpinned: a sub-command added here but not there is sent
#     with its selector byte and then classified as expecting no response, so
#     nothing awaits the reply.
# Nothing asserts that the three agree.  Making the RX path import this
# constant is the real fix and is deliberately not done here (MOR-1981).
#
# ``rigctld/server.py: RigctldServer._send_one_state_query`` has the same
# shape and does NOT consult this set.  It cannot reach 0x27 today because
# ``core/acquisition_scheduler.py: IcomCivAcquisitionExecutor.query_for_path``
# has no ``scope_controls`` branch, so every scope field resolves to None.
# Nothing pins that.  If that branch is ever added, this set is the third
# place to wire up, or rigctld will send ``27 14`` bare while web does not.
#
# Membership (MOR-1981).  Measured on a live IC-7300, six runs with no
# variance: each sub-command below is refused with a NAK when sent bare,
# while 0x12, 0x13, 0x1B and 0x1C answer.  Icom's CI-V Reference Guide for
# the IC-705 -- the same single-scope architecture -- prints a two-byte data
# field for 0x14, 0x15, 0x16, 0x17, 0x19, 0x1A and 0x1D and a one-byte field
# for 0x12, 0x13, 0x1B and 0x1C, matching that bench result with no
# exception.  0x1F (RBW) has no row in that guide, but Hamlib supplies the
# selector for it and the bare form NAKs, so it is the same class.  The
# IC-7300's own command table (advanced manual, table 19-8) and the
# IC-7610 and IC-9700 CI-V Reference Guides have since been read directly
# and print the same split: the IC-7300 leg now has both the bench
# measurement and its own manual behind it, and the IC-7610 leg is
# confirmed from its guide.  Documentary evidence exists for the IC-9700
# (its CI-V Reference Guide); no bench evidence exists for the IC-9700.
# The de-risking fact is that ``web/radio_poller.py`` has been putting this
# exact split on the wire to all four scope-capable profiles since long
# before this constant existed: the connect sweep is catching up to
# shipped behaviour, not trying something new.
#
# The exclusions are not omissions -- on a sub-command that takes no
# selector the extra byte is a WRITE, not a no-op:
#   0x1C (center type): ``27 1C 00`` is read as SET center_type=0 (Filter
#     center).
#   0x1E (fixed edge): takes ``<frequency range><edge number>`` instead, and
#     00 is not a legal range -- ranges start at 01.  ``get_scope_fixed_edge``
#     below builds that pair.
SCOPE_RECEIVER_SELECTOR_SUBS: frozenset[int] = frozenset(
    {
        _SUB_SCOPE_MODE,  # 0x14 mode (center/fixed/scroll)
        _SUB_SCOPE_SPAN,  # 0x15 span
        _SUB_SCOPE_EDGE,  # 0x16 edge number
        _SUB_SCOPE_HOLD,  # 0x17 hold
        _SUB_SCOPE_REF,  # 0x19 ref level
        _SUB_SCOPE_SPEED,  # 0x1A sweep speed
        _SUB_SCOPE_VBW,  # 0x1D VBW
        _SUB_SCOPE_RBW,  # 0x1F RBW
    }
)

# The selector value naming the MAIN scope.  It is the only legal value on a
# single-scope radio, and the value a sender must use when it does not (yet)
# know which scope the radio has selected.
SCOPE_SELECTOR_MAIN: int = 0x00


def _validate_scope_range(name: str, value: int, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be {minimum}-{maximum}, got {value}")
    return value


def _validate_scope_receiver(receiver: int) -> int:
    if receiver not in (0, 1):
        raise ValueError(f"scope receiver must be 0 or 1, got {receiver}")
    return receiver


def _scope_payload(value: bytes, receiver: int | None = None) -> bytes:
    if receiver is None:
        return value
    return bytes([_validate_scope_receiver(receiver)]) + value


def _scope_selector_data(receiver: int | None) -> bytes | None:
    """Selector byte for a 0x27 read, or ``None`` when no receiver is named.

    Only the sub-commands in ``SCOPE_RECEIVER_SELECTOR_SUBS`` may carry it --
    on any other 0x27 read the extra byte is a write.  For those eight both
    branches of the getter agree through this function: the fallback builds
    its frame with it via ``_scope_query``, and the ``cmd_map`` branch passes
    it as ``data``.

    ``get_scope_center_type`` used to accept a ``receiver`` argument and
    forward it here too, even though its 0x1C sub is outside the set: on
    0x1C the extra byte is a SET, not a selector -- ``27 1C 00`` sets
    center_type=0 rather than reading it, confirmed by a live IC-7300
    bench recheck and by all four official CI-V references (IC-705,
    IC-7300, IC-7610, IC-9700 guides).  Its ``cmd_map`` branch never took
    the argument, so the two branches disagreed whenever a caller passed
    one -- pinned as the ``receiver=0`` rows in
    ``tests/command_map_parity_divergences.txt`` before this fix.  Closing
    that (MOR-1981) meant refusing the argument outright rather than
    special-casing 0x1C: ``get_scope_center_type`` now takes no
    ``receiver`` parameter at all and always reaches here with
    ``receiver=None``, so its two branches agree by construction.  The
    eight selector subs above are the only sub-commands whose getters
    still pass a real receiver value through this function.
    """
    return None if receiver is None else bytes([_validate_scope_receiver(receiver)])


def _scope_query(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int | None = None,
) -> bytes:
    return build_civ_frame(
        to_addr, from_addr, _CMD_SCOPE, sub=sub, data=_scope_selector_data(receiver)
    )


def _parse_scope_frame(frame: CivFrame, sub: int) -> bytes:
    if frame.command != _CMD_SCOPE or frame.sub != sub:
        got = 0 if frame.sub is None else frame.sub
        raise ValueError(
            f"Not a scope response: command 0x{frame.command:02x} sub 0x{got:02x}"
        )
    if not frame.data:
        raise ValueError("Scope response has no payload")
    return frame.data


def _split_scope_receiver_prefix(
    data: bytes, *, expected_lengths: tuple[int, ...]
) -> tuple[int | None, bytes]:
    if len(data) in {length + 1 for length in expected_lengths} and data[0] in (
        0x00,
        0x01,
    ):
        return data[0], data[1:]
    if len(data) not in expected_lengths:
        expected = " or ".join(str(length) for length in expected_lengths)
        raise ValueError(
            f"Unexpected scope payload length: expected {expected} byte(s), got {len(data)}"
        )
    return None, data


def _decode_scope_bool(frame: CivFrame, sub: int) -> bool:
    data = _parse_scope_frame(frame, sub)
    if len(data) != 1:
        raise ValueError(f"Scope bool response must be 1 byte, got {len(data)}")
    return data[0] != 0x00


def _decode_scope_value(
    frame: CivFrame, sub: int, *, minimum: int, maximum: int
) -> tuple[int | None, int]:
    data = _parse_scope_frame(frame, sub)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(1,))
    value = payload[0]
    _validate_scope_range("scope value", value, minimum, maximum)
    return receiver, value


def _decode_scope_bcd_value(
    frame: CivFrame, sub: int, *, minimum: int, maximum: int
) -> tuple[int | None, int]:
    data = _parse_scope_frame(frame, sub)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(1,))
    value = _bcd_decode_value(payload)
    _validate_scope_range("scope value", value, minimum, maximum)
    return receiver, value


def _resolve_scope_fixed_edge_range(start_hz: int) -> int:
    if start_hz < 0:
        raise ValueError(f"scope fixed edge start_hz must be >= 0, got {start_hz}")
    for index, band_start in enumerate(_SCOPE_FIXED_EDGE_RANGE_STARTS_HZ, start=1):
        if start_hz >= band_start:
            return index
    raise ValueError(
        f"scope fixed edge start_hz {start_hz} is outside known IC-7610 bands"
    )


def _scope_ref_encode(ref: float) -> bytes:
    """Encode scope reference level as 3-byte Icom BCD format.

    Wire format (IC-7610 CI-V Reference p.15, Command 27 19):
      byte 0: high nibble = 10 dB digit (0-3), low nibble = 1 dB digit (0-9)
      byte 1: high nibble = 0.1 dB digit (0 or 5), low nibble = 0 (fixed)
      byte 2: 0x00 = positive, 0x01 = negative

    Range: -30.0 to +10.0 dB in 0.5 dB steps.
    Example: -5.0 dB → [0x05, 0x00, 0x01]
    """
    if not -30.0 <= ref <= 10.0:
        raise ValueError(f"scope ref must be -30.0 to +10.0 dB, got {ref}")
    is_negative = ref < 0
    tenths = int(round(abs(ref) * 10))
    tens_db = tenths // 100  # 10 dB digit (0-3)
    ones_db = (tenths // 10) % 10  # 1 dB digit (0-9)
    frac_db = tenths % 10  # 0.1 dB digit (0 or 5)
    b0 = (tens_db << 4) | ones_db
    b1 = frac_db << 4  # low nibble fixed 0
    sign = 0x01 if is_negative else 0x00
    return bytes([b0, b1, sign])


# --- Public API ---


def scope_on(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scope_on", to_addr=to_addr, from_addr=from_addr, data=b"\x01"
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_SCOPE, sub=_SUB_SCOPE_ON, data=b"\x01"
    )


def get_scope_enabled(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Query whether the radio's panel scope is currently enabled."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scope_on", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_ON, to_addr=to_addr, from_addr=from_addr)


def scope_off(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scope_off", to_addr=to_addr, from_addr=from_addr, data=b"\x00"
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_SCOPE, sub=_SUB_SCOPE_ON, data=b"\x00"
    )


def scope_data_output(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scope_data_output",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_DATA_OUTPUT,
        data=b"\x01" if on else b"\x00",
    )


def get_scope_data_output_enabled(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    """Query whether CI-V scope waveform output is currently enabled."""
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "scope_data_output", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_DATA_OUTPUT, to_addr=to_addr, from_addr=from_addr)


def scope_data_output_on(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scope_data_output",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01",
        )
    return scope_data_output(True, to_addr=to_addr, from_addr=from_addr)


def scope_data_output_off(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "scope_data_output",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x00",
        )
    return scope_data_output(False, to_addr=to_addr, from_addr=from_addr)


def get_scope_main_sub(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_main_sub", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_MAIN_SUB, to_addr=to_addr, from_addr=from_addr)


def scope_main_sub(
    receiver: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_main_sub",
            to_addr=to_addr,
            from_addr=from_addr,
            data=bytes([_validate_scope_receiver(receiver)]),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_MAIN_SUB,
        data=bytes([_validate_scope_receiver(receiver)]),
    )


def get_scope_single_dual(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_single_dual", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_SINGLE_DUAL, to_addr=to_addr, from_addr=from_addr)


def scope_single_dual(
    dual: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_single_dual",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(b"\x01" if dual else b"\x00", receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_SINGLE_DUAL,
        data=_scope_payload(b"\x01" if dual else b"\x00", receiver),
    )


def get_scope_mode(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_mode",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_MODE, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_mode(
    mode: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope mode", mode, 0, 3)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_mode",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(bytes([mode]), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_MODE,
        data=_scope_payload(bytes([mode]), receiver),
    )


def get_scope_span(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_span",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_SPAN, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_span(
    span: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope span", span, 0, 7)
    span_hz = _SCOPE_SPAN_PRESETS_HZ[span]
    span_bcd = bcd_encode(span_hz)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_span",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(span_bcd, receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_SPAN,
        data=_scope_payload(span_bcd, receiver),
    )


def get_scope_edge(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_edge",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_EDGE, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_edge(
    edge: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope edge", edge, 1, 4)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_edge",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(bytes([edge]), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_EDGE,
        data=_scope_payload(bytes([edge]), receiver),
    )


def get_scope_hold(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_hold",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_HOLD, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_hold(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_hold",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(b"\x01" if on else b"\x00", receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_HOLD,
        data=_scope_payload(b"\x01" if on else b"\x00", receiver),
    )


def get_scope_ref(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_ref",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_REF, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_ref(
    ref: float,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_ref",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(_scope_ref_encode(ref), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_REF,
        data=_scope_payload(_scope_ref_encode(ref), receiver),
    )


def get_scope_speed(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_speed",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_SPEED, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_speed(
    speed: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope speed", speed, 0, 2)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_speed",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(bytes([speed]), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_SPEED,
        data=_scope_payload(bytes([speed]), receiver),
    )


def get_scope_during_tx(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_during_tx", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_DURING_TX, to_addr=to_addr, from_addr=from_addr)


def scope_set_during_tx(
    on: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_during_tx",
            to_addr=to_addr,
            from_addr=from_addr,
            data=b"\x01" if on else b"\x00",
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_DURING_TX,
        data=b"\x01" if on else b"\x00",
    )


def get_scope_center_type(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    """Build a bare 'get scope center type' CI-V command (0x27 0x1C).

    Takes no ``receiver`` argument (MOR-1981, MOR-2002 step 2b-vfo-scope):
    0x1C is outside ``SCOPE_RECEIVER_SELECTOR_SUBS``, so a receiver byte on
    this read is a SET, not a selector -- see ``_scope_selector_data``.
    """
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_center_type", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_CENTER_TYPE, to_addr=to_addr, from_addr=from_addr)


def scope_set_center_type(
    center_type: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope center type", center_type, 0, 2)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_center_type",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(bytes([center_type]), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_CENTER_TYPE,
        data=_scope_payload(bytes([center_type]), receiver),
    )


def get_scope_vbw(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_vbw",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_VBW, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_vbw(
    narrow: bool,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_vbw",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(b"\x01" if narrow else b"\x00", receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_VBW,
        data=_scope_payload(b"\x01" if narrow else b"\x00", receiver),
    )


def get_scope_fixed_edge(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    range_index: int = 1,
    edge: int = 1,
) -> bytes:
    # The IC-7610 NAKs (0xFA) a bare 0x27 0x1E query — it requires a
    # <range_index><edge> selector (1-byte BCD each). Mirror the setter's
    # encoding so the radio answers with the fixed-edge start/end (MOR-662).
    _validate_scope_range("scope fixed edge range", range_index, 1, 99)
    _validate_scope_range("scope fixed edge", edge, 1, 4)
    selector = bcd_encode_value(range_index, byte_count=1) + bcd_encode_value(
        edge, byte_count=1
    )
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_fixed_edge",
            to_addr=to_addr,
            from_addr=from_addr,
            data=selector,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_SCOPE, sub=_SUB_SCOPE_FIXED_EDGE, data=selector
    )


def scope_set_fixed_edge(
    *,
    edge: int,
    start_hz: int,
    end_hz: int,
    to_addr: int,
    range_index: int | None = None,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
) -> bytes:
    _validate_scope_range("scope fixed edge", edge, 1, 4)
    if start_hz < 0:
        raise ValueError(f"scope fixed edge start_hz must be >= 0, got {start_hz}")
    if end_hz <= start_hz:
        raise ValueError(
            f"scope fixed edge end_hz must be greater than start_hz, got {start_hz}..{end_hz}"
        )
    resolved_range = (
        _resolve_scope_fixed_edge_range(start_hz)
        if range_index is None
        else _validate_scope_range("scope fixed edge range", range_index, 1, 99)
    )
    payload = (
        bcd_encode_value(resolved_range, byte_count=1)
        + bcd_encode_value(edge, byte_count=1)
        + bcd_encode(start_hz)
        + bcd_encode(end_hz)
    )
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_fixed_edge",
            to_addr=to_addr,
            from_addr=from_addr,
            data=payload,
        )
    return build_civ_frame(
        to_addr, from_addr, _CMD_SCOPE, sub=_SUB_SCOPE_FIXED_EDGE, data=payload
    )


def get_scope_rbw(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_rbw",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_selector_data(receiver),
        )
    return _scope_query(
        _SUB_SCOPE_RBW, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


def scope_set_rbw(
    rbw: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap | None = None,
    *,
    receiver: int | None = None,
) -> bytes:
    _validate_scope_range("scope rbw", rbw, 0, 2)
    if cmd_map is not None:
        return _build_from_map(
            cmd_map,
            "get_scope_rbw",
            to_addr=to_addr,
            from_addr=from_addr,
            data=_scope_payload(bytes([rbw]), receiver),
        )
    return build_civ_frame(
        to_addr,
        from_addr,
        _CMD_SCOPE,
        sub=_SUB_SCOPE_RBW,
        data=_scope_payload(bytes([rbw]), receiver),
    )


# --- Parse functions ---


def parse_scope_enabled_response(frame: CivFrame) -> bool:
    """Parse a ``0x27 0x10`` panel-scope state response."""
    return _decode_scope_bool(frame, _SUB_SCOPE_ON)


def parse_scope_data_output_enabled_response(frame: CivFrame) -> bool:
    """Parse a ``0x27 0x11`` waveform-output state response."""
    return _decode_scope_bool(frame, _SUB_SCOPE_DATA_OUTPUT)


def parse_scope_main_sub_response(frame: CivFrame) -> int:
    data = _parse_scope_frame(frame, _SUB_SCOPE_MAIN_SUB)
    if len(data) != 1:
        raise ValueError(f"Scope receiver response must be 1 byte, got {len(data)}")
    return _validate_scope_range("scope receiver", data[0], 0, 1)


def parse_scope_single_dual_response(frame: CivFrame) -> bool:
    return _decode_scope_bool(frame, _SUB_SCOPE_SINGLE_DUAL)


def parse_scope_mode_response(frame: CivFrame) -> tuple[int | None, int]:
    return _decode_scope_value(frame, _SUB_SCOPE_MODE, minimum=0, maximum=3)


def parse_scope_span_response(frame: CivFrame) -> tuple[int | None, int]:
    data = _parse_scope_frame(frame, _SUB_SCOPE_SPAN)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(1, 5))
    if len(payload) == 1:
        return receiver, _validate_scope_range("scope span", payload[0], 0, 7)
    hz = bcd_decode(payload)
    try:
        span = _SCOPE_SPAN_PRESETS_HZ.index(hz)
    except ValueError as exc:
        raise ValueError(f"Unknown scope span frequency {hz}") from exc
    return receiver, span


def parse_scope_ref_response(frame: CivFrame) -> tuple[int | None, float]:
    """Decode scope REF level from CI-V response.

    Wire format (IC-7610 CI-V Reference p.15):
      byte 0: high nibble = 10 dB digit, low nibble = 1 dB digit
      byte 1: high nibble = 0.1 dB digit, low nibble = 0
      byte 2: sign (0x00 = +, 0x01 = -)
    """
    data = _parse_scope_frame(frame, _SUB_SCOPE_REF)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(3,))
    b0, b1 = payload[0], payload[1]
    tens_db = (b0 >> 4) & 0x0F
    ones_db = b0 & 0x0F
    frac_db = (b1 >> 4) & 0x0F
    ref = tens_db * 10.0 + ones_db + frac_db * 0.1
    if payload[2]:
        ref *= -1
    return receiver, ref


def parse_scope_speed_response(frame: CivFrame) -> tuple[int | None, int]:
    return _decode_scope_value(frame, _SUB_SCOPE_SPEED, minimum=0, maximum=2)


def parse_scope_edge_response(frame: CivFrame) -> tuple[int | None, int]:
    return _decode_scope_bcd_value(frame, _SUB_SCOPE_EDGE, minimum=1, maximum=4)


def parse_scope_hold_response(frame: CivFrame) -> tuple[int | None, bool]:
    data = _parse_scope_frame(frame, _SUB_SCOPE_HOLD)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(1,))
    return receiver, payload[0] != 0x00


def parse_scope_during_tx_response(frame: CivFrame) -> bool:
    return _decode_scope_bool(frame, _SUB_SCOPE_DURING_TX)


def parse_scope_center_type_response(frame: CivFrame) -> tuple[int | None, int]:
    return _decode_scope_value(frame, _SUB_SCOPE_CENTER_TYPE, minimum=0, maximum=2)


def parse_scope_vbw_response(frame: CivFrame) -> tuple[int | None, bool]:
    data = _parse_scope_frame(frame, _SUB_SCOPE_VBW)
    receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(1,))
    return receiver, payload[0] != 0x00


def parse_scope_fixed_edge_response(frame: CivFrame) -> ScopeFixedEdge:
    data = _parse_scope_frame(frame, _SUB_SCOPE_FIXED_EDGE)
    _receiver, payload = _split_scope_receiver_prefix(data, expected_lengths=(12,))
    return ScopeFixedEdge(
        range_index=_bcd_decode_value(payload[:1]),
        edge=_bcd_decode_value(payload[1:2]),
        start_hz=bcd_decode(payload[2:7]),
        end_hz=bcd_decode(payload[7:12]),
    )


def parse_scope_rbw_response(frame: CivFrame) -> tuple[int | None, int]:
    return _decode_scope_value(frame, _SUB_SCOPE_RBW, minimum=0, maximum=2)
