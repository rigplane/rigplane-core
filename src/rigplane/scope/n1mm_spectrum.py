"""N1MM+ external-spectrum datagram parser and emitter (MOR-4394).

Pure parser for one ``<Spectrum>`` datagram (edges in kHz, scaling factor,
point count, integer levels). ``parse_n1mm_spectrum`` never raises;
``emit_n1mm_spectrum`` builds the XML from the dataclass for tests, replay
fixtures and a future simulator. Levels are relative dB (``level * scale``);
the absolute reference is the sender's.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape as _escape

__all__ = [
    "MAX_DATAGRAM_BYTES",
    "MAX_FREQ_HZ",
    "MAX_LEVEL",
    "MAX_POINTS",
    "MIN_POINTS",
    "N1mmSpectrumPacket",
    "N1mmSpectrumRejected",
    "emit_n1mm_spectrum",
    "parse_n1mm_spectrum",
]

MAX_DATAGRAM_BYTES = 65_507
MAX_POINTS = 65_535
MIN_POINTS = 2
MAX_STRING_LEN = 64
MAX_FREQ_HZ = 1e11
MAX_LEVEL = 65_535

_INT_RE = re.compile(r"[+-]?[0-9]+$")
_REQUIRED = (
    "app",
    "Name",
    "LowScopeFrequency",
    "HighScopeFrequency",
    "ScalingFactor",
    "DataCount",
)


@dataclass(frozen=True)
class N1mmSpectrumPacket:
    app: str
    name: str
    low_hz: float
    high_hz: float
    scaling_factor: float
    levels: tuple[int, ...]


@dataclass(frozen=True)
class N1mmSpectrumRejected:
    reason: str
    detail: str


class _Reject(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _scalar(text: str, field: str) -> Decimal:
    value = text.strip()
    if (
        not value
        or ("," in value and "." in value)
        or value.count(",") > 1
        or value.count(".") > 1
    ):
        raise _Reject("bad_number", f"<{field}> is not a number: {text!r}")
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        raise _Reject("bad_number", f"<{field}> is not a number: {text!r}") from None


def _required_text(root: ET.Element, tag: str) -> str:
    element = root.find(tag)
    if element is None or element.text is None or not element.text.strip():
        raise _Reject("missing_field", f"missing <{tag}>")
    return element.text.strip()


def _parse(datagram: bytes) -> N1mmSpectrumPacket | N1mmSpectrumRejected:
    if len(datagram) > MAX_DATAGRAM_BYTES:
        raise _Reject("too_large", f"{len(datagram)} bytes > {MAX_DATAGRAM_BYTES}")
    lowered = datagram.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise _Reject("doctype_forbidden", "DOCTYPE/ENTITY declarations are rejected")
    try:
        text = datagram.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise _Reject("malformed_xml", "datagram is not UTF-8") from None
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise _Reject("malformed_xml", f"XML parse failed: {exc}") from None
    if root.tag != "Spectrum":
        raise _Reject("wrong_root", f"root is <{root.tag}>, want <Spectrum>")

    fields = {tag: _required_text(root, tag) for tag in _REQUIRED}
    if len(fields["app"]) > MAX_STRING_LEN or len(fields["Name"]) > MAX_STRING_LEN:
        raise _Reject("too_long", "app/Name exceed 64 chars")

    low_dec = _scalar(fields["LowScopeFrequency"], "LowScopeFrequency")
    high_dec = _scalar(fields["HighScopeFrequency"], "HighScopeFrequency")
    scale = _scalar(fields["ScalingFactor"], "ScalingFactor")
    if not low_dec.is_finite() or not high_dec.is_finite() or not scale.is_finite():
        raise _Reject("bad_number", "non-finite scalar (NaN/inf)")
    low_hz = float(low_dec * 1000)
    high_hz = float(high_dec * 1000)
    scaling_factor = float(scale)
    if not math.isfinite(scaling_factor) or not scaling_factor > 0:
        raise _Reject(
            "bad_number", f"ScalingFactor must be > 0: {fields['ScalingFactor']!r}"
        )
    if not 0 < low_hz < high_hz <= MAX_FREQ_HZ:
        raise _Reject("bad_range", f"need 0 < low < high <= {MAX_FREQ_HZ:g} Hz")

    if not _INT_RE.match(fields["DataCount"]):
        raise _Reject(
            "bad_number", f"DataCount is not an integer: {fields['DataCount']!r}"
        )
    count = int(fields["DataCount"])
    if count > MAX_POINTS:
        raise _Reject("too_many_points", f"DataCount {count} > {MAX_POINTS}")
    if count < MIN_POINTS:
        raise _Reject("count_mismatch", f"DataCount {count} < {MIN_POINTS}")

    data_element = root.find("SpectrumData")
    if data_element is None:
        raise _Reject("missing_field", "missing <SpectrumData>")
    raw = data_element.text or ""
    items = raw.split(",") if raw.strip() else []
    levels: list[int] = []
    for item in items:
        cell = item.strip()
        if not _INT_RE.match(cell):
            raise _Reject("bad_number", f"level is not an integer: {item!r}")
        level = int(cell)
        if not 0 <= level <= MAX_LEVEL:
            raise _Reject("level_out_of_range", f"level {level} outside 0..{MAX_LEVEL}")
        levels.append(level)
    if len(levels) != count:
        raise _Reject("count_mismatch", f"DataCount {count} != {len(levels)} items")
    return N1mmSpectrumPacket(
        fields["app"], fields["Name"], low_hz, high_hz, scaling_factor, tuple(levels)
    )


def parse_n1mm_spectrum(
    datagram: bytes,
) -> N1mmSpectrumPacket | N1mmSpectrumRejected:
    """Parse one N1MM+ spectrum datagram; never raises, rejects instead."""
    try:
        return _parse(datagram)
    except _Reject as exc:
        return N1mmSpectrumRejected(exc.reason, exc.detail)
    except Exception as exc:  # noqa: BLE001 — the contract is "never raises"
        return N1mmSpectrumRejected("malformed_xml", f"unexpected failure: {exc}")


def emit_n1mm_spectrum(packet: N1mmSpectrumPacket) -> bytes:
    """Build the canonical N1MM+ spectrum XML for ``packet`` (kHz, 3 decimals)."""
    levels = ",".join(str(level) for level in packet.levels)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<Spectrum><app>{_escape(packet.app)}</app>"
        f"<Name>{_escape(packet.name)}</Name>"
        f"<LowScopeFrequency>{packet.low_hz / 1000:.3f}</LowScopeFrequency>"
        f"<HighScopeFrequency>{packet.high_hz / 1000:.3f}</HighScopeFrequency>"
        f"<ScalingFactor>{packet.scaling_factor!r}</ScalingFactor>"
        f"<DataCount>{len(packet.levels)}</DataCount>"
        f"<SpectrumData>{levels}</SpectrumData></Spectrum>"
    ).encode("utf-8")
