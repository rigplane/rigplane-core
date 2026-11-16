"""Closure of the probe write-check table (MOR-4464 B1a rewrite, RED first).

Frames are BUILT from a closed table of settings; ``parse_frame`` accepts only
what the table's encoders produce and ``sendable`` adds the restore cover.
These tests pin that from outside the table:

T1 round trip, T2 exhaustive prefixes, T3 one-byte perturbations, T4 the old
never-lists (kept here as test constants only), T5 coverage per table entry,
T6 one judging path, T7 digital-voice domains.
"""

from __future__ import annotations

import ast
import random
import string
import tomllib
from pathlib import Path

import pytest

from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec

REPO = Path(__file__).resolve().parents[2]
RIGS = REPO / "rigs"
MODULE = REPO / "src" / "rigplane" / "profiles" / "probe_checks.py"

# --- the old never-lists: test constants only (T4) ------------------------------

YAESU_NEVER = frozenset(
    "TX MX AC KY KM PS MW MC EX PB LM AM MT QI MA QR VM CH SC VX BI DT "
    "MR KR DA QS VG VD SD".split()
)
CIV_NEVER_COMMANDS = frozenset(
    (0x08, 0x09, 0x0A, 0x0B, 0x0E, 0x12, 0x17, 0x18, 0x1C, 0x1E, 0x1F, 0x22, 0x28)
)
CIV_NEVER_PAIRS = frozenset(
    {(0x16, 0x46), (0x16, 0x47), (0x1A, 0x00), (0x1A, 0x01), (0x1A, 0x02)}
    | {(0x1A, 0x05), (0x0F, 0x10), (0x0F, 0x11), (0x0F, 0x12)}
    | {(0x07, op) for op in (0x00, 0x01, 0xC0, 0xC1, 0xD0, 0xD1)}
    | {(0x14, sub) for sub in (0x0B, 0x0E, 0x0F, 0x15, 0x16, 0x17)}
)
#: Read forms never carry these command bytes (the shipped read pass rule).
CIV_READ_REFUSED = frozenset(
    (0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x17, 0x18, 0x1C)
)

# --- profiles -------------------------------------------------------------------

#: FT-991A plus the two declarations B2 adds for its checks (PR, IF).
CAT_EXTRA = {
    "get_processor": CatCommandSpec(read="PR0;", parse="PR0{state};"),
    "set_processor": CatCommandSpec(write="PR0{state};"),
    "get_if_status": CatCommandSpec(read="IF;", parse="IF{rest};"),
}
#: FTX-1 declares PR and IF itself, but split is ST and power carries a head.
FTX1_IDS = ("freq_a", "freq_b", "mode_main", "speech_processor", "info")
CIV_RIGS = ("ic705", "ic7300", "ic7610", "ic9700", "ic905")


def _config(name: str):
    from rigplane.profiles.rig_loader import load_rig

    return load_rig(RIGS / f"{name}.toml")


def facts(name: str, extra: dict | None = None, **override):
    """The producer's own facts type, filled from a shipped profile."""
    from dataclasses import replace

    from rigplane.profiles import selector_one_is_sub
    from rigplane.profiles.probe_checks import RadioFacts

    config = _config(name)
    made = RadioFacts(
        protocol=config.protocol_type,
        commands={**config.commands, **(extra or {})},
        modes=tuple(config.modes),
        mode_codes=None if config.mode_codes is None else tuple(config.mode_codes),
        cmd29_routes=frozenset(config.cmd29_routes),
        swap_ab=config.vfo_swap_ab,
        swap_main_sub=config.vfo_swap_main_sub,
        unselected_is_sub=selector_one_is_sub(
            config.receiver_count, config.split_tx_target
        ),
        data_modes=config.data_mode_count,
        freq_ranges=tuple((r["start_hz"], r["end_hz"]) for r in config.freq_ranges),
    )
    return replace(made, **override)


def _facts_for(name: str):
    if name == "ft991a+":
        return facts("ft991a", CAT_EXTRA)
    return facts(name)


def table_for(name: str):
    from rigplane.profiles.probe_checks import compile_table

    ids = FTX1_IDS if name == "ftx1" else None
    return compile_table(_facts_for(name), ids)


ALL_TABLES = ("ft991a+", "ftx1", *CIV_RIGS)

#: Candidate values per entry; the ones the compiled codec accepts are used.
SAMPLES: dict[str, tuple[object, ...]] = {
    "freq_a": (7074000, 145000000),
    "freq_b": (14250000, 145000000),
    "mode_main": ("USB", "FM"),
    "split": ("on", "off"),
    "power": (50, 100),
    "speech_processor": ("1", "2"),
    "freq_selected": (7074000, 145000000),
    "freq_unselected": (14250000, 145000000),
    "mode_selected": (("USB", 0, 1), ("FM", 1, 2), ("USB", 1), ("CW", 3)),
    "mode_unselected": (("USB", 0, 1), ("LSB", 1, 3), ("USB", 2, 1)),
    "vfo_exchange": ((),),
    "rf_power": (0, 128, 255),
    "af_level": (0, 77),
}


def _built(table, entry_id: str, form: str, value=None, receiver=None):
    from rigplane.profiles.probe_checks import build_frame

    try:
        return build_frame(table, entry_id, form, value, receiver)
    except ValueError:
        return None


def valid_frames(table) -> list[tuple[str, str, bytes, bytes]]:
    """(entry id, form, wrapper, frame) for every form, sample value and
    receiver the table allows."""
    out = []
    for entry in table.entries:
        receivers = [None] + ([0, 1] if entry.wrapper_ok else [])
        for receiver in receivers:
            wrap = b"" if receiver is None else bytes((0x29, receiver))
            if entry.read is not None:
                frame = _built(table, entry.id, "read", None, receiver)
                assert frame is not None, entry.id
                out.append((entry.id, "read", wrap, frame))
            if entry.write is not None:
                frames = [
                    _built(table, entry.id, "write", value, receiver)
                    for value in SAMPLES[entry.id]
                ]
                good = [frame for frame in frames if frame is not None]
                assert good, f"no sample value fits {entry.id!r}"
                out += [(entry.id, "write", wrap, frame) for frame in good]
    return out


# --- T1 round trip ------------------------------------------------------------------

CAT_ALPHABET = (string.ascii_uppercase + string.digits + ";").encode("ascii")
CIV_HOT = bytes((0x03, 0x04, 0x05, 0x06, 0x07, 0x14, 0x25, 0x26, 0x29, 0x00, 0x01))


@pytest.mark.parametrize("name", ("ft991a+", "ftx1", "ic705", "ic7610"))
def test_t1_round_trip_parse_then_encode_gives_the_same_bytes(name: str) -> None:
    from rigplane.profiles.probe_checks import build_frame, parse_frame

    table = table_for(name)
    rng = random.Random(4464)
    frames = [frame for *_, frame in valid_frames(table)]
    cat = table.protocol == "yaesu_cat"
    alphabet = CAT_ALPHABET if cat else bytes(range(256))

    def noise(count: int) -> bytes:
        pool = alphabet if cat or rng.random() < 0.5 else CIV_HOT
        return bytes(rng.choice(pool) for _ in range(count))

    parsed = 0
    for _ in range(100_000):
        roll = rng.random()
        if roll < 0.4:
            data = noise(rng.randint(1, 13))
        elif roll < 0.8:
            base = bytearray(rng.choice(frames))
            base[rng.randrange(len(base))] = rng.choice(alphabet)
            data = bytes(base)
        else:
            data = (
                noise(rng.randint(0, 2)) + rng.choice(frames) + noise(rng.randint(0, 2))
            )
        got = parse_frame(table, data)
        if got is None:
            continue
        parsed += 1
        again = build_frame(table, got.entry.id, got.form, got.value, got.receiver)
        assert again == data, (data, got)
    assert parsed > 1_000


# --- T2 exhaustive prefixes ---------------------------------------------------------


def _expected(table) -> set[tuple[str, str, int, bytes]]:
    return {
        (eid, form, len(frame) - len(wrap), wrap)
        for eid, form, wrap, frame in valid_frames(table)
    }


def _seen(table, bodies) -> set[tuple[str, str, int, bytes]]:
    from rigplane.profiles.probe_checks import parse_frame

    out = set()
    for wrap, body in bodies:
        got = parse_frame(table, wrap + body)
        if got is not None:
            out.add((got.entry.id, got.form, len(body), wrap))
    return out


@pytest.mark.parametrize("name", CIV_RIGS)
def test_t2_civ_every_command_and_sub_parses_exactly_the_table(name: str) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    wraps = (b"", b"\x29\x00", b"\x29\x01")
    datas = (b"", b"\x01", b"\x00\x01")
    heads = [bytes((cmd,)) for cmd in range(256)]
    heads += [bytes((cmd, sub)) for cmd in range(256) for sub in range(256)]
    bodies = [(wrap, head + data) for head in heads for data in datas for wrap in wraps]
    frames = valid_frames(table)
    bodies += [
        (wrap, frame[len(wrap) :] + tail)
        for _, _, wrap, frame in frames
        for tail in (b"", b"\x00")
    ]
    assert _seen(table, bodies) == _expected(table)
    double = b"\x29\x00\x29\x01"
    for head in heads:
        for data in datas:
            assert parse_frame(table, double + head + data) is None, head + data
    for _, _, wrap, frame in frames:
        assert parse_frame(table, double + frame) is None, frame
        if wrap:
            assert parse_frame(table, wrap + frame) is None, frame


@pytest.mark.parametrize("name", ("ft991a+", "ftx1"))
def test_t2_cat_every_verb_parses_exactly_the_table(name: str) -> None:
    table = table_for(name)
    verbs = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    assert len(verbs) == 676
    frames = valid_frames(table)
    tails = {"", "0", "1", "2", "3", "00", "02", "000", "050", "0;", "1;"}
    tails |= {frame[2:-1].decode() for *_, frame in frames}
    tails |= {frame[2:-1].decode() + "0" for *_, frame in frames}
    bodies = [(b"", (verb + tail + ";").encode()) for verb in verbs for tail in tails]
    assert _seen(table, bodies) == _expected(table)


# --- T3 one-byte perturbations -----------------------------------------------------


@pytest.mark.parametrize("name", ALL_TABLES)
def test_t3_a_valid_frame_with_one_byte_more_does_not_parse(name: str) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    cat = table.protocol == "yaesu_cat"
    extra = CAT_ALPHABET if cat else bytes(range(256))
    inserts = (b";X",) if cat else (b"\xfd", b"\xfe\xfe")
    for *_, frame in valid_frames(table):
        bad = [frame + bytes((b,)) for b in extra]
        bad += [bytes((b,)) + frame for b in extra]
        bad += [
            frame[:i] + ins + frame[i:]
            for ins in inserts
            for i in range(len(frame) + 1)
        ]
        for data in bad:
            assert parse_frame(table, data) is None, (frame, data)


# --- T4 nothing that parses is on an old never-list --------------------------------


@pytest.mark.parametrize("name", ALL_TABLES)
def test_t4_nothing_parseable_is_on_the_old_never_lists(name: str) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    for _, form, wrap, frame in valid_frames(table):
        assert parse_frame(table, frame) is not None
        if table.protocol == "yaesu_cat":
            assert frame[:2].decode() not in YAESU_NEVER, frame
            continue
        inner = frame[len(wrap) :]
        assert inner[0] not in CIV_NEVER_COMMANDS, frame
        assert tuple(inner[:2]) not in CIV_NEVER_PAIRS, frame
        if form == "read":
            assert inner[0] not in CIV_READ_REFUSED, frame


# --- T5 coverage, parametrised over the table ---------------------------------------


def _writable() -> list[tuple[str, str]]:
    from rigplane.profiles.probe_checks import TABLE

    return [
        (protocol, spec.id)
        for protocol, specs in TABLE.items()
        for spec in specs
        if any(form.write is not None for form in spec.forms)
    ]


CHECK_HEAD = """
[probe]
[[probe.checks]]
id = "cover"
title = "Cover"
explain_what = "Changes one setting for a moment."
explain_after = "Puts it back."
"""


def _toml_value(value: object) -> str:
    if isinstance(value, tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return f'"{value}"' if isinstance(value, str) else str(value)


def check_toml(steps: list[tuple], restore: list[str], extra: str = "") -> str:
    """steps: ("write", entry, value[, receiver]) | ("read", entry) | ("ask", text)."""
    out = [CHECK_HEAD, extra]
    for step in steps:
        out.append(f'[[probe.checks.steps]]\nkind = "{step[0]}"')
        if step[0] == "ask":
            out.append(f'question = "{step[1]}"')
            continue
        out.append(f'entry = "{step[1]}"')
        if step[0] == "write" and step[2] != ():
            out.append(f"value = {_toml_value(step[2])}")
        if len(step) > 3:
            out.append(f'receiver = "{step[3]}"')
    for item in restore:
        entry, _, receiver = item.partition("@")
        out.append(f'[[probe.checks.restore]]\nentry = "{entry}"')
        if receiver:
            out.append(f'receiver = "{receiver}"')
    return "\n".join(out) + "\n"


def load_checks(facts_, toml: str):
    from rigplane.profiles.probe_checks import parse_probe_checks

    return parse_probe_checks("t.toml", tomllib.loads(toml)["probe"], facts=facts_)


@pytest.mark.parametrize(("protocol", "entry_id"), _writable())
def test_t5_a_write_without_every_changed_restore_fails_and_is_not_sendable(
    protocol: str, entry_id: str
) -> None:
    from rigplane.profiles.probe_checks import (
        compile_table,
        judged_frame,
        parse_frame,
        sendable,
    )
    from rigplane.profiles.rig_loader import RigLoadError

    facts_ = _facts_for("ft991a+" if protocol == "yaesu_cat" else "ic705")
    table = compile_table(facts_)
    entry = next(e for e in table.entries if e.id == entry_id)
    (frame,) = [
        f
        for eid, form, wrap, f in valid_frames(table)
        if eid == entry_id and form == "write" and not wrap
    ][:1]
    value = parse_frame(table, frame).value
    full = list(entry.changes)
    assert full and (entry_id in full or entry.read is None)
    (check,) = load_checks(facts_, check_toml([("write", entry_id, value)], full))
    assert judged_frame(table, entry_id, "write", value, None, check.restored) == frame
    assert sendable(parse_frame(table, frame), {(c, None) for c in full})
    for missing in full:
        partial = [c for c in full if c != missing]
        with pytest.raises(RigLoadError, match="restore"):
            load_checks(facts_, check_toml([("write", entry_id, value)], partial))
        assert not sendable(parse_frame(table, frame), {(c, None) for c in partial})


# --- T6 one judge -------------------------------------------------------------------

SYNTH_CIV = check_toml(
    [
        ("write", "vfo_exchange", ()),
        ("read", "freq_selected"),
        ("write", "rf_power", 128),
        ("read", "rf_power"),
        ("ask", "Did the display swap the VFOs?"),
    ],
    [
        "freq_selected",
        "mode_selected",
        "freq_unselected",
        "mode_unselected",
        "rf_power",
    ],
    'witness = ["freq_selected", "mode_selected"]',
)
SYNTH_SPLIT = check_toml(
    [
        ("write", "freq_a", 7074000),
        ("write", "freq_b", 145000000),
        ("write", "split", "on"),
        ("read", "split"),
        ("ask", "Does the display show TX on VFO-B?"),
    ],
    ["freq_a", "freq_b", "split"],
    'witness = ["freq_a", "freq_b"]',
)
SYNTH_PROC = check_toml(
    [
        ("write", "speech_processor", "1"),
        ("read", "speech_processor"),
        ("write", "mode_main", "USB"),
    ],
    ["speech_processor", "mode_main"],
    'witness = ["info"]',
)


def _shipped() -> list[str]:
    return sorted(p.stem for p in RIGS.glob("*.toml") if not p.stem.startswith("_"))


def _ids(check) -> set[str]:
    ids = {step.entry for step in check.steps if step.entry}
    ids |= {item.entry for item in check.restore} | set(check.witness)
    return ids | {pre.entry for pre in check.preconditions}


def test_t6_load_time_bytes_equal_the_single_build_and_judge_function() -> None:
    from rigplane.profiles.probe_checks import compile_table, judged_frame
    from rigplane.profiles.rig_loader import RigLoadError

    loaded: list[str] = []
    for name in _shipped():
        protocol = _config(name).protocol_type
        texts = {"civ": (SYNTH_CIV,), "yaesu_cat": (SYNTH_SPLIT, SYNTH_PROC)}.get(
            protocol, ()
        )
        for text in texts:
            facts_ = facts(name)
            try:
                (check,) = load_checks(facts_, text)
            except RigLoadError as error:
                assert "not available" in str(error) or "needs" in str(error), (
                    name,
                    error,
                )
                continue
            loaded.append(f"{name}:{check.steps[0].entry}")
            ids = tuple(sorted(_ids(check)))
            load_time, engine = (
                compile_table(facts_, ids),
                compile_table(facts(name), ids),
            )
            for step in check.steps:
                if step.kind == "ask":
                    continue
                args = (
                    step.entry,
                    step.kind,
                    step.value,
                    step.receiver,
                    check.restored,
                )
                assert judged_frame(load_time, *args) == judged_frame(engine, *args)
    exchange = sorted(item for item in loaded if item.endswith("vfo_exchange"))
    assert exchange == ["ic705:vfo_exchange", "ic905:vfo_exchange"], loaded
    assert "ft991a:freq_a" in loaded and "ftx1:speech_processor" in loaded, loaded


def test_t6_the_module_has_exactly_one_judging_path() -> None:
    """parse_frame and sendable are called only by judged_frame, and no check
    record carries bytes (a stored frame would be an unjudged send).

    B1b hook: the engine test asserting every write reaches the port only
    through ``judged_frame`` belongs next to this one when the engine exists.
    """
    from dataclasses import fields

    from rigplane.profiles import probe_checks

    for record in (
        probe_checks.ProbeCheck,
        probe_checks.ProbeCheckStep,
        probe_checks.ProbeRestoreItem,
        probe_checks.ProbePrecondition,
    ):
        assert all("bytes" not in str(f.type) for f in fields(record)), record
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    callers: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                    callers.setdefault(call.func.id, set()).add(node.name)
    assert callers.get("sendable") == {"judged_frame"}
    assert callers.get("parse_frame") == {"judged_frame"}
    for gone in (
        "policy_refusal",
        "check_send_allowed",
        "setting_of",
        "restored_settings",
        "declared_body",
        "YAESU_NEVER",
        "YAESU_WRITE_ALLOW",
        "CIV_WRITE_ALLOW",
        "CIV_READ_REFUSED",
    ):
        assert not hasattr(probe_checks, gone), gone


# --- T7 digital-voice domains -------------------------------------------------------

CIV_VOICE = (0x17, 0x22)
CAT_VOICE = {"ft991a+": ("E", "H", "I"), "ftx1": ("H", "I")}


def _mode_frames(table, code: int) -> list[bytes]:
    out = []
    for entry in table.entries:
        if entry.id not in ("mode_selected", "mode_unselected") or not entry.write:
            continue
        tail = (code, 0x00, 0x01) if entry.write[0] == 0x26 else (code, 0x01)
        out.append(entry.write + bytes(tail))
    return out


@pytest.mark.parametrize("code", CIV_VOICE)
def test_t7_civ_digital_voice_mode_codes_are_outside_every_domain(code: int) -> None:
    from rigplane.profiles.probe_checks import build_frame, parse_frame
    from rigplane.profiles.rig_loader import RigLoadError

    refused_on = 0
    for name in CIV_RIGS:
        table = table_for(name)
        frames = _mode_frames(table, code)
        assert frames, name
        for frame in frames:
            assert parse_frame(table, frame) is None, (name, frame)
            usb = frame.replace(bytes((code,)), b"\x01", 1)
            assert parse_frame(table, usb) is not None, (name, usb)
        for value in (("DV", 0, 1), ("DD", 0, 1)):
            with pytest.raises(ValueError):
                build_frame(table, "mode_unselected", "write", value, None)
        toml = check_toml(
            [("write", "mode_unselected", ("DV", 0, 1))], ["mode_unselected"]
        )
        with pytest.raises(RigLoadError, match="domain"):
            load_checks(facts(name), toml)
        refused_on += 1
    assert refused_on >= 3


@pytest.mark.parametrize("name", sorted(CAT_VOICE))
def test_t7_cat_c4fm_codes_from_the_profile_are_outside_the_mode_domain(
    name: str,
) -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for(name)
    for code in CAT_VOICE[name]:
        assert parse_frame(table, f"MD0{code};".encode()) is None, code
    assert parse_frame(table, b"MD02;") is not None
    with pytest.raises(Exception, match="domain"):
        load_checks(
            _facts_for(name),
            check_toml([("write", "mode_main", "C4FM")], ["mode_main"]),
        )


def test_t7_mode_codes_are_profile_data_not_one_table() -> None:
    """FTX-1 code E is PSK (in its domain); on the FT-991A E is C4FM."""
    from rigplane.profiles.probe_checks import parse_frame

    assert parse_frame(table_for("ftx1"), b"MD0E;").value == "PSK"
    assert parse_frame(table_for("ft991a+"), b"MD0E;") is None


# --- kept pinned: framing, wrapper, exchange, 26, RF power ---------------------------


def test_single_command_framing() -> None:
    from rigplane.profiles.probe_checks import parse_frame

    cat = table_for("ft991a+")
    for line in (b"FA007074000;TX1;", b"FA;TX1;", b"FA007074000", b"MD02;TX1;"):
        assert parse_frame(cat, line) is None, line
    assert parse_frame(cat, b"FA007074000;") is not None
    civ = table_for("ic705")
    for hexed in ("2500000074070000fdfefe94e01c0001", "14010001fd", "1401fefe0001"):
        assert parse_frame(civ, bytes.fromhex(hexed)) is None, hexed


def test_receiver_wrapper_is_unwrapped_once_and_only_where_the_profile_routes_it() -> (
    None
):
    from rigplane.profiles.probe_checks import parse_frame

    ic7610 = table_for("ic7610")
    got = parse_frame(ic7610, bytes.fromhex("290114010077"))
    assert (got.entry.id, got.form, got.receiver, got.value) == (
        "af_level",
        "write",
        1,
        77,
    )
    assert parse_frame(ic7610, bytes.fromhex("2900290114010077")) is None
    assert parse_frame(ic7610, bytes.fromhex("2901140a0128")) is None
    assert parse_frame(table_for("ic705"), bytes.fromhex("290114010077")) is None


def test_sub_receiver_write_needs_the_sub_receiver_restore() -> None:
    from rigplane.profiles.probe_checks import judged_frame
    from rigplane.profiles.rig_loader import RigLoadError

    steps = [("write", "af_level", 77, "sub")]
    with pytest.raises(RigLoadError, match="restore"):
        load_checks(facts("ic7610"), check_toml(steps, ["af_level"]))
    (check,) = load_checks(facts("ic7610"), check_toml(steps, ["af_level@sub"]))
    table = table_for("ic7610")
    frame = judged_frame(table, "af_level", "write", 77, 1, check.restored)
    assert frame == bytes.fromhex("290114010077")
    with pytest.raises(RigLoadError, match="receiver"):
        load_checks(facts("ic705"), check_toml(steps, ["af_level@sub"]))


@pytest.mark.parametrize("name", ("ic705", "ic905"))
def test_vfo_exchange_changes_frequency_and_mode_of_both_vfos(name: str) -> None:
    table = table_for(name)
    (entry,) = [e for e in table.entries if e.id == "vfo_exchange"]
    assert set(entry.changes) == {
        "freq_selected",
        "freq_unselected",
        "mode_selected",
        "mode_unselected",
    }
    assert entry.read is None and entry.write == b"\x07\xb0"
    (check,) = load_checks(facts(name), SYNTH_CIV)
    assert check.steps[0].entry == "vfo_exchange"


def test_selected_vfo_mode_26_is_one_entry_of_mode_data_and_filter() -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for("ic705")
    got = parse_frame(table, bytes.fromhex("2600010102"))
    assert (got.entry.id, got.form, got.value) == (
        "mode_selected",
        "write",
        ("USB", 1, 2),
    )
    for short in ("26000101", "260001", "26000102ff"):
        assert parse_frame(table, bytes.fromhex(short)) is None, short


@pytest.mark.parametrize(
    ("name", "read", "write"),
    [
        ("ic705", b"\x26\x00", b"\x26\x00"),
        ("ic7300", b"\x04", None),
        ("ic7610", b"\x04", None),
    ],
)
def test_selected_mode_compiles_from_what_the_profile_declares(
    name: str, read: bytes, write: bytes
) -> None:
    """IC-7300/IC-7610 declare no 26 00 write: their selected-VFO mode is
    read-only (04), never written through 06, which cannot restore DATA."""
    (entry,) = [e for e in table_for(name).entries if e.id == "mode_selected"]
    assert (entry.read, entry.write) == (read, write)


@pytest.mark.parametrize(
    ("name", "steps", "restore"),
    [
        *((rig, [("write", "rf_power", 128)], ["rf_power"]) for rig in CIV_RIGS),
        ("ft991a", [("write", "power", 100), ("write", "power", 50)], ["power"]),
    ],
)
def test_rf_power_is_restorable(name: str, steps: list, restore: list) -> None:
    """Never above the original is a B1b engine run-time rule, not a load rule."""
    (check,) = load_checks(facts(name), check_toml(steps, restore))
    assert check.restored == frozenset({(restore[0], None)})


# --- A3: literal pins of what parses (not derived from build_frame) -----------------

CAT_TAILS = ("", "0", "1", "2", "3", "00", "01", "02", "050", "100")
CAT_TAILS += ("007074000", "014250000", "145000000", "0;", "1;")


def test_a3_ft991a_parseable_verbs_are_exactly_the_written_set() -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for("ft991a+")
    verbs = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    lines = [(verb + tail + ";").encode() for verb in verbs for tail in CAT_TAILS]
    parsed = {line[:2].decode() for line in lines if parse_frame(table, line)}
    assert parsed == {"FA", "FB", "MD", "FT", "PC", "PR", "IF"}


IC705_WRITES = (
    "25000040070700",
    "25010050420100",
    "2600010001",
    "2601000101",
    "07b0",
    "140a0128",
    "14010077",
)
IC705_PAIRS = (
    {
        (cmd, sub, form)
        for cmd, sub in ((0x25, 0x00), (0x25, 0x01), (0x26, 0x00), (0x26, 0x01))
        for form in ("read", "write")
    }
    | {(0x14, s, f) for s in (0x0A, 0x01) for f in ("read", "write")}
    | {(0x07, 0xB0, "write")}
)


def test_a3_ic705_parseable_command_pairs_are_exactly_the_written_set() -> None:
    from rigplane.profiles.probe_checks import parse_frame

    table = table_for("ic705")
    heads = [bytes((cmd,)) for cmd in range(256)]
    heads += [bytes((cmd, sub)) for cmd in range(256) for sub in range(256)]
    bodies = [head + data for head in heads for data in (b"", b"\x01", b"\x00\x01")]
    bodies += [bytes.fromhex(h) for h in IC705_WRITES]
    seen = set()
    for wrap in (b"", b"\x29\x00", b"\x29\x01"):
        for body in bodies:
            got = parse_frame(table, wrap + body)
            if got is not None:
                assert not wrap, (wrap, body)
                seen.add((body[0], body[1], got.form))
    assert seen == IC705_PAIRS


def test_the_cat_line_rule_alone_refuses_a_second_semicolon() -> None:
    """A test-only entry whose codec accepts ';X': the whole-line rule refuses."""
    from rigplane.profiles.probe_checks import Entry, Table, _Choice, parse_frame

    def table(raw: bytes):
        odd = Entry("odd", None, b"ZZ", _Choice((("x", raw),)), ("odd",), False)
        return Table("yaesu_cat", (odd,))

    assert parse_frame(table(b"QX"), b"ZZQX;") is not None
    assert parse_frame(table(b";X"), b"ZZ;X;") is None


# --- pre-review: LSB, DATA domain, complete mode restore, exchange topology ----------


@pytest.mark.parametrize(
    ("name", "entry", "hexed"),
    [
        ("ic705", "mode_selected", "2600000001"),
        ("ic705", "mode_unselected", "2601000001"),
        ("ic7300", "mode_unselected", "2601000101"),
        ("ic9700", "mode_unselected", "2601000003"),
    ],
)
def test_lsb_the_zero_mode_byte_is_in_the_domain(
    name: str, entry: str, hexed: str
) -> None:
    from rigplane.profiles.probe_checks import judged_frame, parse_frame

    table = table_for(name)
    got = parse_frame(table, bytes.fromhex(hexed))
    assert (got.entry.id, got.form, got.value[0]) == (entry, "write", "LSB")
    (check,) = load_checks(
        facts(name), check_toml([("write", entry, got.value)], [entry])
    )
    frame = judged_frame(table, entry, "write", got.value, None, check.restored)
    assert frame == bytes.fromhex(hexed)


def test_data_field_domain_comes_from_the_profile() -> None:
    from rigplane.profiles.probe_checks import parse_frame

    ic7610 = table_for("ic7610")
    for data in (0, 1, 2, 3):
        got = parse_frame(ic7610, bytes((0x26, 0x01, 0x01, data, 0x01)))
        assert got.value == ("USB", data, 1), data
    assert parse_frame(ic7610, bytes.fromhex("2601010401")) is None
    for name in ("ic705", "ic7300"):
        assert parse_frame(table_for(name), bytes.fromhex("2601010101")) is not None
        assert parse_frame(table_for(name), bytes.fromhex("2601010201")) is None
    g7250 = compile_table_for("g7250")
    assert parse_frame(g7250, bytes.fromhex("2601010001")) is not None
    assert parse_frame(g7250, bytes.fromhex("2601010101")) is None


def compile_table_for(name: str, **override):
    from rigplane.profiles.probe_checks import compile_table

    return compile_table(facts(name, **override))


@pytest.mark.parametrize("name", ("ic7300", "ic7300mk2", "ic7610", "ic9700"))
def test_exchange_is_unavailable_without_complete_mode_restore(name: str) -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    assert "vfo_exchange" not in {e.id for e in table_for(name).entries}
    with pytest.raises(RigLoadError, match="not available"):
        load_checks(facts(name), SYNTH_CIV)


def test_exchange_restores_unequal_data_states_on_both_vfos() -> None:
    from rigplane.profiles.probe_checks import judged_frame

    table = table_for("ic705")
    (check,) = load_checks(facts("ic705"), SYNTH_CIV)
    restored = check.restored
    before = {"mode_selected": ("USB", 1, 1), "mode_unselected": ("USB", 0, 2)}
    for entry, value in before.items():
        frame = judged_frame(table, entry, "write", value, None, restored)
        sub = 0x00 if entry == "mode_selected" else 0x01
        assert frame == bytes((0x26, sub, 0x01, value[1], value[2])), entry
    for entry in table.entry("vfo_exchange").changes:
        assert table.entry(entry).write is not None, entry


SELECTED_WRITES = {
    "set_selected_freq": CivCommandSpec(bytes=(0x25, 0x00)),
    "set_selected_mode": CivCommandSpec(bytes=(0x26, 0x00)),
}


def test_main_sub_exchange_needs_selector_01_to_be_the_physical_sub() -> None:
    """IC-7610: 25/26 01 is the SUB band, so MAIN/SUB swap is restorable once
    the profile declares the 00 writes; IC-9700: 01 is MAIN's other VFO, so
    the swap changes SUB that no entry captures."""
    from rigplane.profiles.rig_loader import RigLoadError

    ic7610 = facts("ic7610", SELECTED_WRITES)
    (check,) = load_checks(ic7610, SYNTH_CIV)
    assert check.steps[0].entry == "vfo_exchange"
    ic9700 = facts("ic9700", SELECTED_WRITES)
    with pytest.raises(RigLoadError, match="not available"):
        load_checks(ic9700, SYNTH_CIV)
    with pytest.raises(RigLoadError, match="not available"):
        load_checks(
            facts("ic7610", SELECTED_WRITES, unselected_is_sub=False), SYNTH_CIV
        )


def test_ab_exchange_comes_only_from_the_ab_swap() -> None:
    from rigplane.profiles.rig_loader import RigLoadError

    for override in (
        {"swap_ab": None},
        {"swap_ab": None, "swap_main_sub": (0xB0,)},
    ):
        with pytest.raises(RigLoadError, match="not available"):
            load_checks(facts("ic705", **override), SYNTH_CIV)


@pytest.mark.parametrize("swap", [(0xB0,), (0x07, 0xB0)])
@pytest.mark.parametrize(
    ("name", "key", "extra"),
    [
        ("ic705", "swap_ab", None),
        ("ic905", "swap_ab", None),
        ("ic7610", "swap_main_sub", SELECTED_WRITES),
    ],
)
def test_short_and_complete_swap_tuples_compile_alike(name, key, extra, swap) -> None:
    from rigplane.profiles.probe_checks import compile_table

    table = compile_table(facts(name, extra, **{key: swap}))
    assert table.entry("vfo_exchange").write == b"\x07\xb0"
