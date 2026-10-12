"""Compare IC-7300, IC-7610 and FTX-1 profile wire bytes with their manual oracles.

Each oracle (``tests/golden/wire_oracle/ic7300.toml``,
``tests/golden/wire_oracle/ic7610.toml``) is transcribed from the radio
manual and never edited here. Each row's expected bytes are recomputed
through the production path the runtime uses to send that command:
``profiles/rig_loader.py: load_rig`` loads ``rigs/<model>.toml``,
``RigConfig.to_profile`` carries its ``CommandMap``, ``runtime/radio.py:
CoreRadio`` binds it once into ``commands/bound.py: BoundCommands``, and
the builder named in the per-profile table renders the full CI-V frame
(controller 0xE0 to the profile address). Builders that take a
``command29`` flag receive ``profiles: RadioProfile.supports_cmd29`` for
their opcode, and builders that take a ``receiver`` receive the row's
receiver (``main`` = 0, ``sub`` = 1); both are the same arguments the
runtime's own ``CoreRadio.set_*`` methods pass before sending. No CI-V
encoding is re-implemented here.

Rows are keyed by ``(control, receiver, direction, value)`` for both
profiles: the IC-7610 table is not unique by ``control`` (two ``ptt``
rows, two ``dualwatch`` rows, and every dual-receiver control has a
``main`` and a ``sub`` row), and ``test_oracle_keys_unique`` fails on a
duplicate key so two rows can never silently collapse into one entry
(MOR-3369 item 2).

The FTX-1 section below compares ``tests/golden/wire_oracle/ftx1.toml``
with the Yaesu ASCII CAT production path. Its rows key on ``side``
(``"main"`` / ``"sub"`` / ``"none"``) where the CI-V rows key on
``receiver``. Each row's expected string is rendered through the
profile's own CAT template (``rigs/ftx1.toml``) with
``backends/yaesu_cat/parser.py: format_command`` — the same encoder
``backends/yaesu_cat/radio.py: YaesuCatRadio._write`` uses — with the
command name the runtime method selects for the row's side and the
value arguments that method passes. No CAT encoding (digit padding,
side digit, terminator) is re-implemented here.

Rules, per the MOR-3261 compare brief:

- a row whose bytes differ from production is marked
  ``pytest.mark.xfail(strict=True)`` with the manual page and both byte
  strings, never fixed by editing the row or the code;
- a row with no production command for its profile goes in that
  profile's ``_NOT_DECLARED`` table with its reason, and a test asserts
  each listed production name stays absent, so declaring it later fails
  loudly;
- ``_IC7300_MIN_ROWS`` / ``_IC7610_MIN_ROWS`` plus the classification
  test ratchet the row sets: removing a row requires editing the
  constant and the tables together.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rigplane.backends.yaesu_cat.parser import format_command
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio import CoreRadio
from rigplane.types import AgcMode, BreakInMode, Mode

REPO_ROOT = Path(__file__).resolve().parents[1]
RIGS_DIR = REPO_ROOT / "rigs"
ORACLE_DIR = REPO_ROOT / "tests" / "golden" / "wire_oracle"

_IC7300_MIN_ROWS = 27
_IC7610_MIN_ROWS = 34

#: Oracle row key: (control, receiver, direction, value). Rows without a
#: receiver field (the whole IC-7300 table) key as ``"none"``.
_Key = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class _ProductionCall:
    """One oracle row's production rendering.

    ``builder`` names the ``BoundCommands`` attribute the runtime calls;
    ``kwargs`` are that builder's value arguments (``to_addr`` is supplied
    from the profile). ``receiver`` is passed as the builder's
    ``receiver`` argument for dual-receiver rows (``main`` = 0,
    ``sub`` = 1) and omitted for receiver-independent rows, mirroring
    ``runtime/radio.py: CoreRadio``. ``cmd29_route`` holds the
    ``(opcode, sub)`` pair the runtime passes to
    ``RadioProfile.supports_cmd29`` for builders that take a
    ``command29`` flag, or ``None`` for builders without one.
    """

    builder: str
    kwargs: dict[str, object]
    cmd29_route: tuple[int, ...] | None = None
    receiver: int | None = None


@dataclass(frozen=True, slots=True)
class _NotDeclared:
    """An oracle row with no production command for its profile.

    ``reason`` says why nothing is declared; ``absent_commands`` names
    the production commands that must stay absent from the profile map,
    so that declaring any of them later fails
    ``test_not_declared_stays_absent`` and forces a comparison row.
    """

    reason: str
    absent_commands: tuple[str, ...] = ()


@dataclass(slots=True)
class _ProfileSetup:
    """Live production path for one profile under test."""

    label: str
    mapping: dict[_Key, _ProductionCall]
    not_declared: dict[_Key, _NotDeclared]
    min_rows: int
    profile: object = field(init=False)
    commands: object = field(init=False)
    to_addr: int = field(init=False)
    oracle: dict[_Key, bytes] = field(init=False)
    manual: dict[_Key, str] = field(init=False)
    row_keys: list = field(init=False)


def _cmd29(setup: _ProfileSetup, route: tuple[int, ...]) -> bool:
    opcode, *rest = route
    sub = rest[0] if rest else None
    return setup.profile.supports_cmd29(opcode, sub)  # type: ignore[union-attr]


_PROFILE_7300 = load_rig(RIGS_DIR / "ic7300.toml").to_profile()
assert _PROFILE_7300.command_map is not None
assert _PROFILE_7300.vfo_main_code is not None
assert _PROFILE_7300.vfo_sub_code is not None
_PROFILE_7610 = load_rig(RIGS_DIR / "ic7610.toml").to_profile()
assert _PROFILE_7610.command_map is not None
assert _PROFILE_7610.vfo_main_code is not None
assert _PROFILE_7610.vfo_sub_code is not None

_IC7300_MAPPING: dict[_Key, _ProductionCall] = {
    ("frequency_set", "none", "set", "14.074 MHz"): _ProductionCall(
        "set_freq", {"freq_hz": 14074000}
    ),
    ("frequency_read", "none", "read", "n/a (read request)"): _ProductionCall(
        "get_freq", {}
    ),
    ("mode_usb_fil1", "none", "set", "USB, filter 1"): _ProductionCall(
        "set_mode", {"mode": Mode.USB, "filter_width": 1}
    ),
    ("vfo_a", "none", "set", "VFO A"): _ProductionCall(
        "set_vfo", {"code": _PROFILE_7300.vfo_main_code}
    ),
    ("vfo_b", "none", "set", "VFO B"): _ProductionCall(
        "set_vfo", {"code": _PROFILE_7300.vfo_sub_code}
    ),
    ("split_off", "none", "set", "off"): _ProductionCall("set_split", {"on": False}),
    ("split_on", "none", "set", "on"): _ProductionCall("set_split", {"on": True}),
    ("attenuator_off", "none", "set", "off (0 dB)"): _ProductionCall(
        "set_attenuator_level", {"db": 0}, (0x11,)
    ),
    ("attenuator_on", "none", "set", "on (20 dB)"): _ProductionCall(
        "set_attenuator_level", {"db": 20}, (0x11,)
    ),
    ("af_level", "none", "set", "128 of 0..255"): _ProductionCall(
        "set_af_level", {"level": 128}, (0x14, 0x01)
    ),
    ("rf_gain", "none", "set", "200 of 0..255"): _ProductionCall(
        "set_rf_gain", {"level": 200}, (0x14, 0x02)
    ),
    ("squelch_level", "none", "set", "32 of 0..255"): _ProductionCall(
        "set_squelch", {"level": 32}, (0x14, 0x03)
    ),
    ("nr_level", "none", "set", "128 of 0..255"): _ProductionCall(
        "set_nr_level", {"level": 128}, (0x14, 0x06)
    ),
    ("rf_power", "none", "set", "200 of 0..255"): _ProductionCall(
        "set_rf_power", {"level": 200}
    ),
    ("mic_gain", "none", "set", "128 of 0..255"): _ProductionCall(
        "set_mic_gain", {"level": 128}
    ),
    ("cw_key_speed", "none", "set", "120 of 0..255"): _ProductionCall(
        "set_key_speed", {"level": 120}
    ),
    ("comp_level", "none", "set", "150 of 0..255"): _ProductionCall(
        "set_compressor_level", {"level": 150}
    ),
    ("preamp", "none", "set", "preamp 1 on"): _ProductionCall(
        "set_preamp", {"level": 1}, (0x16, 0x02)
    ),
    ("agc", "none", "set", "MID"): _ProductionCall(
        "set_agc", {"mode": AgcMode.MID}, (0x16, 0x12)
    ),
    ("nb_on", "none", "set", "on"): _ProductionCall(
        "set_nb", {"on": True}, (0x16, 0x22)
    ),
    ("nr_on", "none", "set", "on"): _ProductionCall(
        "set_nr", {"on": True}, (0x16, 0x40)
    ),
    ("auto_notch_on", "none", "set", "on"): _ProductionCall(
        "set_auto_notch", {"on": True}, (0x16, 0x41)
    ),
    ("vox_on", "none", "set", "on"): _ProductionCall("set_vox", {"on": True}),
    ("breakin", "none", "set", "semi break-in"): _ProductionCall(
        "set_break_in", {"mode": BreakInMode.SEMI}
    ),
    ("ptt_on", "none", "set", "transmit"): _ProductionCall("ptt_on", {}),
    ("ptt_off", "none", "set", "receive"): _ProductionCall("ptt_off", {}),
    ("tuner_on", "none", "set", "on"): _ProductionCall(
        "set_tuner_status", {"value": 1}
    ),
}

_NOT_DECLARED_7300: dict[_Key, _NotDeclared] = {}

_IC7610_MAPPING: dict[_Key, _ProductionCall] = {
    ("operating_freq", "none", "set", "14.074 MHz"): _ProductionCall(
        "set_freq", {"freq_hz": 14074000}
    ),
    ("operating_freq", "none", "read", "readback"): _ProductionCall("get_freq", {}),
    ("operating_mode", "none", "set", "USB FIL1"): _ProductionCall(
        "set_mode", {"mode": Mode.USB, "filter_width": 1}
    ),
    ("select_main", "none", "set", "MAIN band"): _ProductionCall(
        "set_vfo", {"code": _PROFILE_7610.vfo_main_code}
    ),
    ("select_sub", "none", "set", "SUB band"): _ProductionCall(
        "set_vfo", {"code": _PROFILE_7610.vfo_sub_code}
    ),
    ("dualwatch", "none", "set", "on"): _ProductionCall("set_dual_watch", {"on": True}),
    ("dualwatch", "none", "set", "off"): _ProductionCall(
        "set_dual_watch", {"on": False}
    ),
    ("split", "none", "set", "on"): _ProductionCall("set_split", {"on": True}),
    ("split", "none", "set", "off"): _ProductionCall("set_split", {"on": False}),
    ("rf_power", "none", "set", "200 of 0..255"): _ProductionCall(
        "set_rf_power", {"level": 200}
    ),
    ("mic_gain", "none", "set", "128 of 0..255"): _ProductionCall(
        "set_mic_gain", {"level": 128}
    ),
    ("cw_key_speed", "none", "set", "0128 of 0000..0255"): _ProductionCall(
        "set_key_speed", {"level": 128}
    ),
    ("comp_level", "none", "set", "0100 of 0000..0255"): _ProductionCall(
        "set_compressor_level", {"level": 100}
    ),
    ("vox", "none", "set", "on"): _ProductionCall("set_vox", {"on": True}),
    ("break_in", "none", "set", "semi break-in"): _ProductionCall(
        "set_break_in", {"mode": BreakInMode.SEMI}
    ),
    ("ptt", "none", "set", "on"): _ProductionCall("ptt_on", {}),
    ("ptt", "none", "set", "off"): _ProductionCall("ptt_off", {}),
    ("antenna_tuner", "none", "set", "on"): _ProductionCall(
        "set_tuner_status", {"value": 1}
    ),
    ("af_level", "main", "set", "128 of 0..255"): _ProductionCall(
        "set_af_level", {"level": 128}, (0x14, 0x01), receiver=0
    ),
    ("af_level", "sub", "set", "64 of 0..255"): _ProductionCall(
        "set_af_level", {"level": 64}, (0x14, 0x01), receiver=1
    ),
    ("rf_gain", "main", "set", "200 of 0..255"): _ProductionCall(
        "set_rf_gain", {"level": 200}, (0x14, 0x02), receiver=0
    ),
    ("rf_gain", "sub", "set", "128 of 0..255"): _ProductionCall(
        "set_rf_gain", {"level": 128}, (0x14, 0x02), receiver=1
    ),
    ("squelch", "main", "set", "0085 of 0000..0255"): _ProductionCall(
        "set_squelch", {"level": 85}, (0x14, 0x03), receiver=0
    ),
    ("squelch", "sub", "set", "0050 of 0000..0255"): _ProductionCall(
        "set_squelch", {"level": 50}, (0x14, 0x03), receiver=1
    ),
    ("attenuator", "main", "set", "3 dB"): _ProductionCall(
        "set_attenuator_level", {"db": 3}, (0x11,), receiver=0
    ),
    ("attenuator", "sub", "set", "30 dB"): _ProductionCall(
        "set_attenuator_level", {"db": 30}, (0x11,), receiver=1
    ),
    ("preamp", "main", "set", "preamp 1 on"): _ProductionCall(
        "set_preamp", {"level": 1}, (0x16, 0x02), receiver=0
    ),
    ("preamp", "sub", "set", "preamp 2 on"): _ProductionCall(
        "set_preamp", {"level": 2}, (0x16, 0x02), receiver=1
    ),
    ("agc", "main", "set", "MID"): _ProductionCall(
        "set_agc", {"mode": AgcMode.MID}, (0x16, 0x12), receiver=0
    ),
    ("agc", "sub", "set", "SLOW"): _ProductionCall(
        "set_agc", {"mode": AgcMode.SLOW}, (0x16, 0x12), receiver=1
    ),
    ("noise_blanker", "main", "set", "on"): _ProductionCall(
        "set_nb", {"on": True}, (0x16, 0x22), receiver=0
    ),
    ("noise_blanker", "sub", "set", "on"): _ProductionCall(
        "set_nb", {"on": True}, (0x16, 0x22), receiver=1
    ),
    ("noise_reduction", "main", "set", "on"): _ProductionCall(
        "set_nr", {"on": True}, (0x16, 0x40), receiver=0
    ),
    ("noise_reduction", "sub", "set", "on"): _ProductionCall(
        "set_nr", {"on": True}, (0x16, 0x40), receiver=1
    ),
}

_NOT_DECLARED_7610: dict[_Key, _NotDeclared] = {}


def _load_oracle(path: Path) -> tuple[dict[_Key, bytes], dict[_Key, str], list[_Key]]:
    with open(path, "rb") as handle:
        rows = tomllib.load(handle)["row"]
    frames: dict[_Key, bytes] = {}
    manuals: dict[_Key, str] = {}
    keys: list[_Key] = []
    for row in rows:
        key = (
            row["control"],
            row.get("receiver", "none"),
            row["direction"],
            row["value"],
        )
        keys.append(key)
        frames[key] = bytes.fromhex(row["frame"])
        manuals[key] = row["manual"]
    return frames, manuals, keys


def _make_setup(
    label: str,
    mapping: dict[_Key, _ProductionCall],
    not_declared: dict[_Key, _NotDeclared],
    min_rows: int,
    profile: object,
) -> _ProfileSetup:
    setup = _ProfileSetup(
        label=label, mapping=mapping, not_declared=not_declared, min_rows=min_rows
    )
    object.__setattr__(setup, "profile", profile)
    radio = CoreRadio("127.0.0.1", profile=profile)  # type: ignore[arg-type]
    object.__setattr__(setup, "commands", radio._commands)
    object.__setattr__(setup, "to_addr", profile.civ_addr)  # type: ignore[union-attr]
    oracle_file = "ic7300.toml" if label == "IC-7300" else "ic7610.toml"
    frames, manuals, keys = _load_oracle(ORACLE_DIR / oracle_file)
    object.__setattr__(setup, "oracle", frames)
    object.__setattr__(setup, "manual", manuals)
    object.__setattr__(setup, "row_keys", keys)
    return setup


_SETUPS: dict[str, _ProfileSetup] = {
    "IC-7300": _make_setup(
        "IC-7300",
        _IC7300_MAPPING,
        _NOT_DECLARED_7300,
        _IC7300_MIN_ROWS,
        _PROFILE_7300,
    ),
    "IC-7610": _make_setup(
        "IC-7610",
        _IC7610_MAPPING,
        _NOT_DECLARED_7610,
        _IC7610_MIN_ROWS,
        _PROFILE_7610,
    ),
}

_XFAIL_7610: dict[_Key, str] = {}


def _hex(frame: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in frame)


def _production_bytes(setup: _ProfileSetup, key: _Key) -> bytes:
    spec = setup.mapping[key]
    builder = getattr(setup.commands, spec.builder)
    kwargs: dict[str, object] = {"to_addr": setup.to_addr, **spec.kwargs}
    if spec.receiver is not None:
        kwargs["receiver"] = spec.receiver
    if spec.cmd29_route is not None:
        kwargs["command29"] = _cmd29(setup, spec.cmd29_route)
    return builder(**kwargs)


def _page(manual: str) -> str:
    match = re.search(r"p\. (\d[\d-]*)", manual)
    return match.group(1) if match else manual


def _compare_params() -> list:
    params: list = []
    for label, setup in _SETUPS.items():
        for key in sorted(setup.mapping):
            if label == "IC-7610" and key in _XFAIL_7610:
                expected = setup.oracle[key]
                produced = _production_bytes(setup, key)
                reason = (
                    f"IC-7610 reference p. {_page(setup.manual[key])} disagrees "
                    f"with the profile: {_XFAIL_7610[key]} "
                    f"(production {_hex(produced)} != oracle {_hex(expected)})"
                )
                params.append(
                    pytest.param(
                        label,
                        key,
                        marks=pytest.mark.xfail(
                            strict=True, raises=AssertionError, reason=reason
                        ),
                    )
                )
            else:
                params.append(pytest.param(label, key))
    return params


@pytest.mark.parametrize("label", sorted(_SETUPS))
def test_oracle_has_minimum_rows(label: str) -> None:
    setup = _SETUPS[label]
    assert len(setup.oracle) >= setup.min_rows, (
        f"{label} oracle holds {len(setup.oracle)} rows, "
        f"below the {setup.min_rows} ratchet"
    )


@pytest.mark.parametrize("label", sorted(_SETUPS))
def test_oracle_keys_unique(label: str) -> None:
    """Every oracle row keys uniquely; a duplicate key fails instead of collapsing."""
    setup = _SETUPS[label]
    counts = Counter(setup.row_keys)
    duplicates = sorted(str(key) for key, count in counts.items() if count > 1)
    assert not duplicates, f"{label} oracle has duplicate row keys: {duplicates}"
    assert len(setup.oracle) == len(setup.row_keys), (
        f"{label} oracle rows collapse to fewer keys "
        f"({len(setup.oracle)} keys for {len(setup.row_keys)} rows)"
    )


@pytest.mark.parametrize("label", sorted(_SETUPS))
def test_mapping_keys_exist_in_oracle(label: str) -> None:
    setup = _SETUPS[label]
    for key in list(setup.mapping) + list(setup.not_declared):
        assert key in setup.oracle, (
            f"{label}: {key!r} is mapped but missing from the oracle"
        )


@pytest.mark.parametrize("label", sorted(_SETUPS))
def test_every_oracle_row_is_classified(label: str) -> None:
    setup = _SETUPS[label]
    for key in setup.oracle:
        assert (key in setup.mapping) != (key in setup.not_declared), (
            f"{label}: {key!r} must sit in exactly one of mapping / not_declared"
        )


@pytest.mark.parametrize("label", sorted(_SETUPS))
def test_not_declared_stays_absent(label: str) -> None:
    setup = _SETUPS[label]
    assert setup.profile.command_map is not None  # type: ignore[union-attr]
    for key, entry in setup.not_declared.items():
        assert entry.reason, f"{label}: {key!r} needs a reason, not an empty string"
        for name in entry.absent_commands:
            assert not setup.profile.command_map.has(name), (  # type: ignore[union-attr]
                f"{label}: {key!r}: production command {name!r} is now declared "
                f"for the {label} profile; add a comparison row for it"
            )


@pytest.mark.parametrize("label,key", _compare_params())
def test_oracle_bytes_match_production(label: str, key: _Key) -> None:
    setup = _SETUPS[label]
    expected = setup.oracle[key]
    produced = _production_bytes(setup, key)
    assert produced[:4] == bytes((0xFE, 0xFE, setup.to_addr, 0xE0)), (
        f"{label} {key!r}: production frame does not run controller E0 to "
        f"profile address {setup.to_addr:#04x}: {_hex(produced)}"
    )
    assert produced == expected, (
        f"{label} {key!r}: production {_hex(produced)} != oracle {_hex(expected)}"
    )


# ---------------------------------------------------------------------------
# FTX-1 (Yaesu ASCII CAT) oracle comparison.
#
# Production path: ``rigs/ftx1.toml`` holds one CAT template per command
# name; ``YaesuCatRadio._write`` renders ``spec.write`` through
# ``format_command`` and sends the resulting string. This section renders
# the same way: the mapping names the CAT command the runtime method
# writes for the row's side, with the value arguments that method passes.
# The command name is the one the runtime method selects for the row:
#
# - ``set_freq`` / ``set_freq_sub`` <- ``set_freq(freq, receiver)``
#   (0 = VFO-A, 1 = VFO-B); ``get_freq`` <- the ``FA;`` read;
# - ``set_mode`` <- ``set_mode("USB", receiver=0)`` after the profile
#   ``[modes].codes`` lookup (USB -> code ``"2"``);
# - ``set_split`` <- ``set_split(state)``; ``set_power`` <-
#   ``set_power(watts, head=2)``; ``set_mic_gain`` <- ``set_mic_gain``;
#   ``set_keyer_speed`` <- ``set_keyer_speed``;
#   ``set_processor_level`` <- ``set_processor_level``;
#   ``set_vox`` <- ``set_vox``; ``set_break_in`` <- ``set_break_in``
#   (ON = 1); ``set_ptt`` <- ``set_ptt``;
#   ``set_tuner`` <- ``set_tuner(state=1, src=0, typ=0)``;
# - ``set_af_level`` / ``set_rf_gain`` / ``set_squelch`` and their
#   ``_sub`` twins <- ``set_af_level`` / ``set_rf_gain`` /
#   ``set_squelch`` with ``receiver`` 0 / 1;
# - ``set_preamp`` <- ``set_preamp(level=1, band=0)`` (single path, the
#   receiver argument is accepted but selects nothing);
#   ``set_attenuator`` <- ``set_attenuator`` (single ``RA0`` path);
# - ``set_agc`` / ``set_agc_sub`` <- ``set_agc(mode=1, receiver)``
#   (FAST = 1); ``set_nb_level`` / ``set_nr_level`` and their ``_sub``
#   twins <- ``set_nb_level`` / ``set_nr_level`` with ``receiver`` 0 / 1
#   (level 5 also switches the function on: the NL/RL level doubles as
#   the on/off switch, 000 = OFF).
#
# Rules, per the MOR-3261 compare brief: a row whose string differs from
# production is marked ``pytest.mark.xfail(strict=True)``, never fixed by
# editing the row or the code; a row with no production command goes in
# ``_NOT_DECLARED_FTX1`` with its reason.
# ---------------------------------------------------------------------------

_FTX1_MIN_ROWS = 29

_FTX1_CONFIG = load_rig(RIGS_DIR / "ftx1.toml")

_FTX1_MAPPING: dict[_Key, _ProductionCall] = {
    ("vfo_a_freq", "none", "set", "14.074 MHz (000030000-470000000 Hz)"): (
        _ProductionCall("set_freq", {"freq": 14074000})
    ),
    ("vfo_a_freq", "none", "read", "14.074 MHz (000030000-470000000 Hz)"): (
        _ProductionCall("get_freq", {})
    ),
    ("vfo_b_freq", "none", "set", "7.074 MHz (000030000-470000000 Hz)"): (
        _ProductionCall("set_freq_sub", {"freq": 7074000}, receiver=1)
    ),
    ("mode", "main", "set", "USB 2 (modes 0-9,A-J)"): (
        _ProductionCall("set_mode", {"mode": "2"}, receiver=0)
    ),
    ("split", "none", "set", "ON (0 OFF, 1 ON)"): (
        _ProductionCall("set_split", {"state": "1"})
    ),
    ("split", "none", "set", "OFF (0 OFF, 1 ON)"): (
        _ProductionCall("set_split", {"state": "0"})
    ),
    ("rf_power", "none", "set", "50 W (005-100 W, SPA-1)"): (
        _ProductionCall("set_power", {"head": "2", "watts": 50})
    ),
    ("mic_gain", "none", "set", "50 (000-100)"): (
        _ProductionCall("set_mic_gain", {"level": 50})
    ),
    ("cw_key_speed", "none", "set", "20 WPM (004-060)"): (
        _ProductionCall("set_keyer_speed", {"wpm": 20})
    ),
    ("speech_proc_level", "none", "set", "50 (000 OFF, 001-100)"): (
        _ProductionCall("set_processor_level", {"level": 50})
    ),
    ("vox", "none", "set", "ON (0 OFF, 1 ON)"): (
        _ProductionCall("set_vox", {"state": "1"})
    ),
    ("break_in", "none", "set", "ON (0 OFF, 1 ON)"): (
        _ProductionCall("set_break_in", {"state": "1"})
    ),
    ("ptt", "none", "set", "CAT TX ON (0 OFF, 1 ON; 2 answer)"): (
        _ProductionCall("set_ptt", {"state": "1"})
    ),
    ("ptt", "none", "set", "TX OFF (0 OFF, 1 ON; 2 answer)"): (
        _ProductionCall("set_ptt", {"state": "0"})
    ),
    ("tuner", "none", "set", "internal ON (P1 0, P2 0, P3 1)"): (
        _ProductionCall("set_tuner", {"src": "0", "type": "0", "state": "1"})
    ),
    ("af_gain", "main", "set", "100 (000-255)"): (
        _ProductionCall("set_af_level", {"level": 100}, receiver=0)
    ),
    ("af_gain", "sub", "set", "80 (000-255)"): (
        _ProductionCall("set_af_level_sub", {"level": 80}, receiver=1)
    ),
    ("rf_gain", "main", "set", "100 (000-255)"): (
        _ProductionCall("set_rf_gain", {"level": 100}, receiver=0)
    ),
    ("rf_gain", "sub", "set", "120 (000-255)"): (
        _ProductionCall("set_rf_gain_sub", {"level": 120}, receiver=1)
    ),
    ("squelch", "main", "set", "100 (000-255)"): (
        _ProductionCall("set_squelch", {"level": 100}, receiver=0)
    ),
    ("squelch", "sub", "set", "80 (000-255)"): (
        _ProductionCall("set_squelch_sub", {"level": 80}, receiver=1)
    ),
    ("preamp", "none", "set", "HF AMP1 (0 IPO, 1 AMP1, 2 AMP2)"): (
        _ProductionCall("set_preamp", {"band": "0", "value": "1"})
    ),
    ("attenuator", "none", "set", "ON (0 OFF, 1 ON)"): (
        _ProductionCall("set_attenuator", {"state": "1"})
    ),
    ("agc", "main", "set", "FAST (0 OFF, 1 FAST, 2 MID, 3 SLOW, 4 AUTO)"): (
        _ProductionCall("set_agc", {"mode": "1"}, receiver=0)
    ),
    ("agc", "sub", "set", "FAST (0 OFF, 1 FAST, 2 MID, 3 SLOW, 4 AUTO)"): (
        _ProductionCall("set_agc_sub", {"mode": "1"}, receiver=1)
    ),
    ("noise_blanker", "main", "set", "5 (000 OFF, 001-010)"): (
        _ProductionCall("set_nb_level", {"level": 5}, receiver=0)
    ),
    ("noise_blanker", "sub", "set", "5 (000 OFF, 001-010)"): (
        _ProductionCall("set_nb_level_sub", {"level": 5}, receiver=1)
    ),
    ("noise_reduction", "main", "set", "05 (00 OFF, 01-10)"): (
        _ProductionCall("set_nr_level", {"level": 5}, receiver=0)
    ),
    ("noise_reduction", "sub", "set", "05 (00 OFF, 01-10)"): (
        _ProductionCall("set_nr_level_sub", {"level": 5}, receiver=1)
    ),
}

_NOT_DECLARED_FTX1: dict[_Key, _NotDeclared] = {}

_XFAIL_FTX1: dict[_Key, str] = {}


def _load_ftx1_oracle() -> tuple[dict[_Key, str], dict[_Key, str], list[_Key]]:
    """Load the FTX-1 oracle frames as CAT strings keyed like the CI-V rows.

    FTX-1 rows carry ``side`` where CI-V rows carry ``receiver``; the key
    maps it explicitly so the composite key and duplicate-key guard work
    unchanged.
    """
    with open(ORACLE_DIR / "ftx1.toml", "rb") as handle:
        rows = tomllib.load(handle)["row"]
    frames: dict[_Key, str] = {}
    manuals: dict[_Key, str] = {}
    keys: list[_Key] = []
    for row in rows:
        key = (
            row["control"],
            row.get("receiver", row.get("side", "none")),
            row["direction"],
            row["value"],
        )
        keys.append(key)
        frames[key] = row["frame"]
        manuals[key] = row["manual"]
    return frames, manuals, keys


_FTX1_ORACLE, _FTX1_MANUAL, _FTX1_ROW_KEYS = _load_ftx1_oracle()


def _ftx1_production_str(key: _Key) -> str:
    """Render one FTX-1 row through the production CAT encoder.

    The template comes from the loaded FTX-1 profile and the string is
    formatted by ``format_command`` — the same template and encoder
    ``YaesuCatRadio._write`` uses. Read rows render the read template;
    set rows render the write template.
    """
    call = _FTX1_MAPPING[key]
    spec = _FTX1_CONFIG.commands.get(call.builder)
    assert isinstance(spec, CatCommandSpec), (
        f"FTX-1 {key!r}: production command {call.builder!r} is not a CAT command spec"
    )
    template = spec.read if key[2] == "read" else spec.write
    assert template is not None, (
        f"FTX-1 {key!r}: production command {call.builder!r} "
        "declares no template for this direction"
    )
    return format_command(template, **call.kwargs)


def _ftx1_compare_params() -> list:
    params: list = []
    for key in sorted(_FTX1_MAPPING):
        if key in _XFAIL_FTX1:
            expected = _FTX1_ORACLE[key]
            produced = _ftx1_production_str(key)
            reason = (
                f"FTX-1 manual p. {_page(_FTX1_MANUAL[key])} disagrees "
                f"with the profile: {_XFAIL_FTX1[key]} "
                f"(production {produced} != oracle {expected})"
            )
            params.append(
                pytest.param(
                    key,
                    marks=pytest.mark.xfail(
                        strict=True, raises=AssertionError, reason=reason
                    ),
                )
            )
        else:
            params.append(pytest.param(key))
    return params


def test_ftx1_oracle_has_minimum_rows() -> None:
    assert len(_FTX1_ORACLE) >= _FTX1_MIN_ROWS, (
        f"FTX-1 oracle holds {len(_FTX1_ORACLE)} rows, "
        f"below the {_FTX1_MIN_ROWS} ratchet"
    )


def test_ftx1_oracle_keys_unique() -> None:
    """Every FTX-1 oracle row keys uniquely; a duplicate key fails instead of collapsing."""
    counts = Counter(_FTX1_ROW_KEYS)
    duplicates = sorted(str(key) for key, count in counts.items() if count > 1)
    assert not duplicates, f"FTX-1 oracle has duplicate row keys: {duplicates}"
    assert len(_FTX1_ORACLE) == len(_FTX1_ROW_KEYS), (
        f"FTX-1 oracle rows collapse to fewer keys "
        f"({len(_FTX1_ORACLE)} keys for {len(_FTX1_ROW_KEYS)} rows)"
    )


def test_ftx1_mapping_keys_exist_in_oracle() -> None:
    for key in list(_FTX1_MAPPING) + list(_NOT_DECLARED_FTX1):
        assert key in _FTX1_ORACLE, (
            f"FTX-1: {key!r} is mapped but missing from the oracle"
        )


def test_ftx1_every_oracle_row_is_classified() -> None:
    for key in _FTX1_ORACLE:
        assert (key in _FTX1_MAPPING) != (key in _NOT_DECLARED_FTX1), (
            f"FTX-1: {key!r} must sit in exactly one of mapping / not_declared"
        )


def test_ftx1_not_declared_stays_absent() -> None:
    for key, entry in _NOT_DECLARED_FTX1.items():
        assert entry.reason, f"FTX-1: {key!r} needs a reason, not an empty string"
        for name in entry.absent_commands:
            spec = _FTX1_CONFIG.commands.get(name)
            assert not (isinstance(spec, CatCommandSpec) and spec.write), (
                f"FTX-1: {key!r}: production command {name!r} now declares "
                "a CAT write for the FTX-1 profile; add a comparison row for it"
            )


@pytest.mark.parametrize("key", _ftx1_compare_params())
def test_ftx1_oracle_string_matches_production(key: _Key) -> None:
    expected = _FTX1_ORACLE[key]
    produced = _ftx1_production_str(key)
    assert produced == expected, (
        f"FTX-1 {key!r}: production {produced} != oracle {expected}"
    )
