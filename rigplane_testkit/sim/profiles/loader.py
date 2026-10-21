"""Loader and validator for ``rigplane-sim-profile/1`` simulation profiles.

The loader refuses anything unknown or contradictory, and every error
message starts with the exact key path: ``transport.serial.baud: must be
a positive integer, got 'fast'``. Capability facts stay in rigs/.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from rigplane.profiles import resolve_radio_profile

SCHEMA_ID = "rigplane-sim-profile/1"

_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
_HEX = re.compile(r"[0-9a-fA-F]+")
_K_SIM = frozenset("id radio fidelity evidence gaps".split())
_K_SERIAL = frozenset("baud echo identity".split())
_K_LAN = frozenset("control_port keepalive_hold_s single_owner identity".split())
_K_INITIAL = frozenset("frequency_hz mode filter data_mode".split())
_K_AUDIO = frozenset("transport sample_rate_hz channels sample_format".split())
_K_SCOPE = frozenset("bins span_hz rate_hz frame".split())
_K_TOP = frozenset(
    "schema sim transport initial answers timing unsolicited faults state audio scope".split()
)


class SimProfileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SimInfo:
    id: str
    radio: str
    fidelity: str
    evidence: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Transport:
    kind: str
    baud: int | None = None
    echo: bool | None = None
    control_port: int | None = None
    keepalive_hold_s: float | None = None
    single_owner: bool | None = None
    identity: str | None = None


@dataclass(frozen=True, slots=True)
class InitialConfig:
    frequency_hz: int | None = None
    mode: str | None = None
    filter: int | None = None
    data_mode: bool | None = None


@dataclass(frozen=True, slots=True)
class AnswersConfig:
    recorded: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TimingConfig:
    reply_latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class AudioRxConfig:
    transport: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    sample_format: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeConfig:
    bins: int | None = None
    span_hz: int | None = None
    rate_hz: float | None = None
    frame: str | None = None


@dataclass(frozen=True, slots=True)
class SimProfile:
    schema: str
    sim: SimInfo
    transport: Transport
    initial: InitialConfig = field(default_factory=InitialConfig)
    answers: AnswersConfig = field(default_factory=AnswersConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    audio_rx: AudioRxConfig = field(default_factory=AudioRxConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    unsolicited: Mapping[str, bool | int | float] = field(default_factory=dict)
    faults_defaults: Mapping[str, bool | int | float] = field(default_factory=dict)
    state_extra: Mapping[str, object] = field(default_factory=dict)


def _fail(message: str):
    raise SimProfileError(message)


def _section(raw, path, known=None):
    if not isinstance(raw, dict):
        _fail(f"{path}: must be a table, got {type(raw).__name__}")
    if known is not None:
        for key in raw:
            if key not in known:
                _fail(f"{path + '.' + key if path else key}: unknown key")
    return raw


def _is_num(value, int_only=False):
    nums = int if int_only else (int, float)
    return not isinstance(value, bool) and isinstance(value, nums)


def _scalar(table, key, path, kind, minimum=None, required=False, strict=False):
    """One optional scalar key: type check + bound, path-prefixed error."""
    value = table.get(key)
    if value is None:
        if required:
            _fail(f"{path}.{key}: required")
        return None
    if kind is float:
        ok = _is_num(value) and (value > 0 if strict else value >= 0)
        what = f"a number {'>' if strict else '>='} 0"
    elif kind is int:
        ok = _is_num(value, True) and (minimum is None or value >= minimum)
        what = "a positive integer" if minimum == 1 else f"an integer >= {minimum}"
    else:
        ok = isinstance(value, kind)
        what = f"a {kind.__name__}"
    if not ok:
        _fail(f"{path}.{key}: must be {what}, got {value!r}")
    return value


def _string_list(raw, path):
    if not isinstance(raw, list):
        _fail(f"{path}: must be a list of strings, got {type(raw).__name__}")
    seen, out = set(), []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            _fail(f"{path}: entry {index} must be a non-empty string, got {item!r}")
        if item in seen:
            _fail(f"{path}: duplicate entry {item!r}")
        seen.add(item)
        out.append(item)
    return tuple(out)


def _identity(table, path, radio):
    value = table.get("identity")
    if value is None:
        if not radio.expected_identity_ids:
            _fail(f"{path}.identity: required, no expected identity ids declared")
        return None
    ids = radio.expected_identity_ids
    ok = isinstance(value, str) and _HEX.fullmatch(value) and not len(value) % 2
    if not ok or value not in ids:
        what = "an even-length hex string" if not ok else f"one of {ids}"
        _fail(f"{path}.identity: must be {what}, got {value!r}")
    return value


def _flags(raw, path, nonempty_keys=False):
    values = {}
    for key, value in _section(raw, path).items():
        if nonempty_keys and not key:
            _fail(f"{path}: key names must be non-empty strings")
        if not isinstance(value, (bool, int, float)):
            _fail(f"{path}.{key}: must be a boolean or number, got {value!r}")
        values[key] = value
    return MappingProxyType(values)


def load_sim_profile(path: Path, *, root: Path) -> SimProfile:
    """Load and validate one profile; ``root`` resolves evidence paths."""
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SimProfileError(f"schema: invalid TOML: {exc}") from exc
    if data.get("schema") != SCHEMA_ID:
        _fail(f"schema: must be {SCHEMA_ID!r}, got {data.get('schema')!r}")
    _section(data, "", _K_TOP)
    if "sim" not in data:
        _fail("sim: required")
    sim_t = _section(data["sim"], "sim", _K_SIM)
    sim_id = sim_t.get("id")
    if not isinstance(sim_id, str) or not _ID.fullmatch(sim_id):
        _fail(f"sim.id: must match [a-z0-9][a-z0-9-]*, got {sim_id!r}")
    model = _scalar(sim_t, "radio", "sim", str)
    try:
        radio = resolve_radio_profile(model=model)
    except (KeyError, ValueError) as exc:
        reason = exc.args[0] if exc.args else exc
        _fail(f"sim.radio: {reason}")
    fidelity = sim_t.get("fidelity")
    if fidelity not in ("A", "B", "C"):
        _fail(f"sim.fidelity: must be one of 'A', 'B' or 'C', got {fidelity!r}")
    evidence = _string_list(sim_t.get("evidence", []), "sim.evidence")
    gaps = _string_list(sim_t.get("gaps", []), "sim.gaps")
    sim = SimInfo(sim_id, model, fidelity, evidence, gaps)
    base = root.resolve()
    resolved = [(base / entry).resolve() for entry in evidence]
    for entry, path in zip(evidence, resolved):
        if not path.exists() or not path.is_relative_to(base):
            _fail(f"sim.evidence: {entry!r} missing under root")
    fixtures = (base / "tests" / "fixtures").resolve()
    golden = (base / "tests" / "golden" / "wire_oracle").resolve()
    if fidelity == "A" and not any(p.is_relative_to(fixtures) for p in resolved):
        _fail("sim.evidence: tier A requires a path under tests/fixtures/")
    if fidelity == "B" and not any(p.is_relative_to(golden) for p in resolved):
        _fail("sim.evidence: tier B requires a path under tests/golden/wire_oracle/")
    if fidelity in ("B", "C") and not gaps:
        _fail(f"sim.gaps: must be non-empty for fidelity tier {fidelity!r}")
    tr_keys = frozenset("serial lan yaesu_cat".split())
    tr = data.get("transport")
    tr_t = _section(tr, "transport", tr_keys) if "transport" in data else {}
    names = [name for name in ("serial", "lan", "yaesu_cat") if name in tr_t]
    if len(names) != 1:
        got = ", ".join(names) or "none"
        _fail(f"transport: exactly one of serial, lan or yaesu_cat required ({got})")
    kind = names[0]
    p = f"transport.{kind}"
    keys = {"serial": _K_SERIAL, "lan": _K_LAN}.get(kind, frozenset({"baud"}))
    t = _section(tr_t[kind], p, keys)
    civ_like = {"civ": ("serial", "lan"), "yaesu_cat": ("yaesu_cat",)}
    if kind not in civ_like.get(radio.protocol_type, ()):
        _fail(f"{p}: {radio.model!r} speaks {radio.protocol_type!r}, not {kind}")
    baud = _scalar(t, "baud", p, int, 1, kind != "lan")
    echo = _scalar(t, "echo", p, bool) if kind == "serial" else None
    port = hold = owner = None
    if kind == "lan":
        if not radio.has_lan:
            _fail(f"{p}: {radio.model!r} capability profile declares no LAN")
        port = t.get("control_port")
        if port is not None and (not _is_num(port, True) or not 1 <= port <= 65535):
            _fail(f"{p}.control_port: must be an integer in 1..65535, got {port!r}")
        hold = _scalar(t, "keepalive_hold_s", p, float, strict=True)
        owner = _scalar(t, "single_owner", p, bool)
    ident = _identity(t, p, radio) if kind != "yaesu_cat" else None
    transport = Transport(kind, baud, echo, port, hold, owner, ident)
    initial_t = _section(data.get("initial", {}), "initial", _K_INITIAL)
    frequency = initial_t.get("frequency_hz")
    if frequency is not None and not _is_num(frequency, True):
        _fail(f"initial.frequency_hz: must be an integer, got {frequency!r}")
    in_freq = _is_num(frequency, True) and radio.freq_ranges
    if in_freq and not any(b.start <= frequency <= b.end for b in radio.freq_ranges):
        _fail(f"initial.frequency_hz: {frequency} outside the profile frequency ranges")
    mode = _scalar(initial_t, "mode", "initial", str)
    if mode is not None and radio.modes and mode not in radio.modes:
        _fail(f"initial.mode: must be one of {radio.modes}, got {mode!r}")
    flt = _scalar(initial_t, "filter", "initial", int, 0)
    if flt is not None and radio.filters and flt > len(radio.filters):
        _fail(f"initial.filter: must be 0..{len(radio.filters)}, got {flt}")
    dmode = _scalar(initial_t, "data_mode", "initial", bool)
    initial = InitialConfig(frequency, mode, flt, dmode)
    answers_t = _section(data.get("answers", {}), "answers", frozenset({"recorded"}))
    entries = answers_t.get("recorded", [])
    if not isinstance(entries, list):
        got = type(entries).__name__
        _fail(f"answers.recorded: must be a list of tables, got {got}")
    recorded = []
    for index, entry in enumerate(entries):
        e = f"answers.recorded[{index}]"
        item = _section(entry, e, frozenset({"file", "transport"}))
        answer_file = _scalar(item, "file", e, str)
        answer_transport = _scalar(item, "transport", e, str)
        if not answer_file:
            _fail(f"{e}.file: required non-empty string, got {answer_file!r}")
        if not answer_transport:
            _fail(f"{e}.transport: required non-empty string, got {answer_transport!r}")
        resolved = (base / answer_file).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(base):
            _fail(f"{e}.file: {answer_file!r} missing under root")
        recorded.append((answer_file, answer_transport))
    tm_t = _section(data.get("timing", {}), "timing", frozenset({"reply_latency_ms"}))
    faults_t = _section(data.get("faults", {}), "faults", frozenset({"defaults"}))
    state_t = _section(data.get("state", {}), "state", frozenset({"extra"}))
    extra = _section(state_t.get("extra", {}), "state.extra")
    for key, value in extra.items():
        if not isinstance(value, (bool, int, float, str)):
            name = type(value).__name__
            _fail(f"state.extra.{key}: must be a bool, int, float or str, got {name}")
    audio_t = _section(data.get("audio", {}), "audio", frozenset({"rx"}))
    rx = _section(audio_t.get("rx", {}), "audio.rx", _K_AUDIO)
    atp = _scalar(rx, "transport", "audio.rx", str)
    if atp not in (None, "lan_stream", "usb_device"):
        _fail(f"audio.rx.transport: must be lan_stream or usb_device, got {atp!r}")
    if atp == "lan_stream" and not radio.has_lan:
        _fail(
            f"audio.rx.transport: lan_stream needs a LAN radio, {radio.model!r} has none"
        )
    channels = rx.get("channels")
    if channels is not None and (not _is_num(channels, True) or channels not in (1, 2)):
        _fail(f"audio.rx.channels: must be 1 or 2, got {channels!r}")
    fmt = _scalar(rx, "sample_format", "audio.rx", str)
    if fmt is not None and fmt != "s16le":
        _fail(f"audio.rx.sample_format: must be s16le, got {fmt!r}")
    rate = _scalar(rx, "sample_rate_hz", "audio.rx", int, 1)
    audio = AudioRxConfig(atp, rate, channels, fmt)
    scope = ScopeConfig()
    if "scope" in data:
        scope_t = _section(data["scope"], "scope", _K_SCOPE)
        if "scope" not in radio.capabilities:
            _fail(f"scope: {radio.model!r} capability profile declares no scope")
        frame = _scalar(scope_t, "frame", "scope", str)
        if frame is not None and not frame:
            _fail(f"scope.frame: must be a non-empty string, got {frame!r}")
        bins = _scalar(scope_t, "bins", "scope", int, 1)
        span = _scalar(scope_t, "span_hz", "scope", int, 1)
        srate = _scalar(scope_t, "rate_hz", "scope", float, strict=True)
        scope = ScopeConfig(bins, span, srate, frame)
    return SimProfile(
        schema=SCHEMA_ID,
        sim=sim,
        transport=transport,
        initial=initial,
        answers=AnswersConfig(recorded=tuple(recorded)),
        timing=TimingConfig(_scalar(tm_t, "reply_latency_ms", "timing", float)),
        audio_rx=audio,
        scope=scope,
        unsolicited=_flags(data.get("unsolicited", {}), "unsolicited", True),
        faults_defaults=_flags(faults_t.get("defaults", {}), "faults.defaults"),
        state_extra=MappingProxyType(dict(extra)),
    )
