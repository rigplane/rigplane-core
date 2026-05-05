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


def _scope_query(
    sub: int,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    receiver: int | None = None,
) -> bytes:
    data = None if receiver is None else bytes([_validate_scope_receiver(receiver)])
    return build_civ_frame(to_addr, from_addr, _CMD_SCOPE, sub=sub, data=data)


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
            cmd_map, "get_scope_mode", to_addr=to_addr, from_addr=from_addr
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
            cmd_map, "get_scope_span", to_addr=to_addr, from_addr=from_addr
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
            cmd_map, "get_scope_edge", to_addr=to_addr, from_addr=from_addr
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
            cmd_map, "get_scope_hold", to_addr=to_addr, from_addr=from_addr
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
            cmd_map, "get_scope_ref", to_addr=to_addr, from_addr=from_addr
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
            cmd_map, "get_scope_speed", to_addr=to_addr, from_addr=from_addr
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
    *,
    receiver: int | None = None,
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_center_type", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(
        _SUB_SCOPE_CENTER_TYPE, to_addr=to_addr, from_addr=from_addr, receiver=receiver
    )


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
            cmd_map, "get_scope_vbw", to_addr=to_addr, from_addr=from_addr
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
    to_addr: int, from_addr: int = CONTROLLER_ADDR, cmd_map: CommandMap | None = None
) -> bytes:
    if cmd_map is not None:
        return _build_from_map(
            cmd_map, "get_scope_fixed_edge", to_addr=to_addr, from_addr=from_addr
        )
    return _scope_query(_SUB_SCOPE_FIXED_EDGE, to_addr=to_addr, from_addr=from_addr)


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
            cmd_map, "get_scope_rbw", to_addr=to_addr, from_addr=from_addr
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
