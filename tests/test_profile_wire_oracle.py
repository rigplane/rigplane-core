"""Compare IC-7300 profile wire bytes with the manual oracle.

The oracle is ``tests/golden/wire_oracle/ic7300.toml`` (transcribed from the
radio manual, never edited here). Each row's expected bytes are recomputed
through the production path the runtime uses to send that command:
``profiles/rig_loader.py: load_rig`` loads ``rigs/ic7300.toml``,
``RigConfig.to_profile`` carries its ``CommandMap``, ``runtime/radio.py:
CoreRadio`` binds it once into ``commands/bound.py: BoundCommands``, and the
builder named in ``_CONTROL_TO_PRODUCTION`` renders the full CI-V frame
(controller 0xE0 to the profile address). Builders that take a
``command29`` flag receive ``profiles: RadioProfile.supports_cmd29`` for
their opcode, the same call ``runtime/radio.py: CoreRadio`` makes before
sending. No CI-V encoding is re-implemented here.

Rules, per the MOR-3261 compare brief:

- a row whose bytes differ from production is marked
  ``pytest.mark.xfail(strict=True)`` with the manual page and both byte
  strings, never fixed by editing the row or the code;
- a row with no production command for this profile goes in
  ``_NOT_DECLARED`` with its reason, and a test asserts each listed
  production name stays absent, so declaring it later fails loudly;
- ``_IC7300_MIN_ROWS`` plus the classification test ratchet the row set:
  removing a row requires editing the constant and the tables together.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio import CoreRadio
from rigplane.types import AgcMode, BreakInMode, Mode

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"
ORACLE_PATH = REPO_ROOT / "tests" / "golden" / "wire_oracle" / "ic7300.toml"

_IC7300_MIN_ROWS = 27

_PROFILE = load_rig(RIGS_DIR / "ic7300.toml").to_profile()
assert _PROFILE.command_map is not None
assert _PROFILE.vfo_main_code is not None
assert _PROFILE.vfo_sub_code is not None
_RADIO = CoreRadio("127.0.0.1", profile=_PROFILE)
_COMMANDS = _RADIO._commands
_TO_ADDR = _PROFILE.civ_addr


@dataclass(frozen=True, slots=True)
class _ProductionCall:
    """One oracle control's production rendering.

    ``builder`` names the ``BoundCommands`` attribute the runtime calls;
    ``kwargs`` are that builder's value arguments (``to_addr`` is supplied
    from the profile); ``cmd29_route`` holds the ``(opcode, sub)`` pair the
    runtime passes to ``RadioProfile.supports_cmd29`` for builders that take
    a ``command29`` flag, or ``None`` for builders without one.
    """

    builder: str
    kwargs: dict[str, object]
    cmd29_route: tuple[int, ...] | None


def _cmd29(route: tuple[int, ...]) -> bool:
    opcode, *rest = route
    sub = rest[0] if rest else None
    return _PROFILE.supports_cmd29(opcode, sub)


_CONTROL_TO_PRODUCTION: dict[str, _ProductionCall] = {
    "frequency_set": _ProductionCall("set_freq", {"freq_hz": 14074000}, None),
    "frequency_read": _ProductionCall("get_freq", {}, None),
    "mode_usb_fil1": _ProductionCall(
        "set_mode", {"mode": Mode.USB, "filter_width": 1}, None
    ),
    "vfo_a": _ProductionCall("set_vfo", {"code": _PROFILE.vfo_main_code}, None),
    "vfo_b": _ProductionCall("set_vfo", {"code": _PROFILE.vfo_sub_code}, None),
    "split_off": _ProductionCall("set_split", {"on": False}, None),
    "split_on": _ProductionCall("set_split", {"on": True}, None),
    "attenuator_off": _ProductionCall("set_attenuator_level", {"db": 0}, (0x11,)),
    "attenuator_on": _ProductionCall("set_attenuator_level", {"db": 20}, (0x11,)),
    "af_level": _ProductionCall("set_af_level", {"level": 128}, (0x14, 0x01)),
    "rf_gain": _ProductionCall("set_rf_gain", {"level": 200}, (0x14, 0x02)),
    "squelch_level": _ProductionCall("set_squelch", {"level": 32}, (0x14, 0x03)),
    "nr_level": _ProductionCall("set_nr_level", {"level": 128}, (0x14, 0x06)),
    "rf_power": _ProductionCall("set_rf_power", {"level": 200}, None),
    "mic_gain": _ProductionCall("set_mic_gain", {"level": 128}, None),
    "cw_key_speed": _ProductionCall("set_key_speed", {"level": 120}, None),
    "comp_level": _ProductionCall("set_compressor_level", {"level": 150}, None),
    "preamp": _ProductionCall("set_preamp", {"level": 1}, (0x16, 0x02)),
    "agc": _ProductionCall("set_agc", {"mode": AgcMode.MID}, (0x16, 0x12)),
    "nb_on": _ProductionCall("set_nb", {"on": True}, (0x16, 0x22)),
    "nr_on": _ProductionCall("set_nr", {"on": True}, (0x16, 0x40)),
    "auto_notch_on": _ProductionCall("set_auto_notch", {"on": True}, (0x16, 0x41)),
    "vox_on": _ProductionCall("set_vox", {"on": True}, None),
    "breakin": _ProductionCall("set_break_in", {"mode": BreakInMode.SEMI}, None),
    "ptt_on": _ProductionCall("ptt_on", {}, None),
    "ptt_off": _ProductionCall("ptt_off", {}, None),
    "tuner_on": _ProductionCall("set_tuner_status", {"value": 1}, None),
}


@dataclass(frozen=True, slots=True)
class _NotDeclared:
    """An oracle control with no production command for this profile.

    ``reason`` says why nothing is declared; ``absent_commands`` names the
    production commands that must stay absent from the profile map, so that
    declaring any of them later fails ``test_not_declared_stays_absent`` and
    forces a comparison row.
    """

    reason: str
    absent_commands: tuple[str, ...] = ()


_NOT_DECLARED: dict[str, _NotDeclared] = {}


def _load_oracle() -> dict[str, bytes]:
    with open(ORACLE_PATH, "rb") as handle:
        rows = tomllib.load(handle)["row"]
    oracle: dict[str, bytes] = {}
    for row in rows:
        oracle[row["control"]] = bytes.fromhex(row["frame"])
    return oracle


_ORACLE = _load_oracle()


def _hex(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)


def _production_bytes(control: str) -> bytes:
    spec = _CONTROL_TO_PRODUCTION[control]
    builder = getattr(_COMMANDS, spec.builder)
    kwargs: dict[str, object] = {"to_addr": _TO_ADDR, **spec.kwargs}
    if spec.cmd29_route is not None:
        kwargs["command29"] = _cmd29(spec.cmd29_route)
    return builder(**kwargs)


def test_oracle_has_minimum_rows() -> None:
    assert len(_ORACLE) >= _IC7300_MIN_ROWS, (
        f"oracle holds {len(_ORACLE)} rows, below the {_IC7300_MIN_ROWS} ratchet"
    )


def test_mapping_controls_exist_in_oracle() -> None:
    for control in list(_CONTROL_TO_PRODUCTION) + list(_NOT_DECLARED):
        assert control in _ORACLE, f"{control!r} is mapped but missing from the oracle"


def test_every_oracle_row_is_classified() -> None:
    for control in _ORACLE:
        assert (control in _CONTROL_TO_PRODUCTION) != (control in _NOT_DECLARED), (
            f"{control!r} must sit in exactly one of "
            "_CONTROL_TO_PRODUCTION / _NOT_DECLARED"
        )


def test_not_declared_stays_absent() -> None:
    assert _PROFILE.command_map is not None
    for control, entry in _NOT_DECLARED.items():
        assert entry.reason, f"{control!r} needs a reason, not an empty string"
        for name in entry.absent_commands:
            assert not _PROFILE.command_map.has(name), (
                f"{control!r}: production command {name!r} is now declared "
                "for the IC-7300 profile; add a comparison row for it"
            )


@pytest.mark.parametrize("control", sorted(_CONTROL_TO_PRODUCTION))
def test_oracle_bytes_match_production(control: str) -> None:
    expected = _ORACLE[control]
    produced = _production_bytes(control)
    assert produced[:4] == bytes((0xFE, 0xFE, _TO_ADDR, 0xE0)), (
        f"{control!r}: production frame does not run controller E0 to "
        f"profile address {_TO_ADDR:#04x}: {_hex(produced)}"
    )
    assert produced == expected, (
        f"{control!r}: production {_hex(produced)} != oracle {_hex(expected)}"
    )
