"""One frequency codec for every CI-V frequency field (MOR-3983).

``bcd_encode``/``bcd_decode`` in ``rigplane.core.types`` is the single
place that reads and writes CI-V frequency bytes for all radios: decode
width comes from the payload length (5 or 6 bytes, little-endian BCD,
Icom order), encode width from an explicit caller argument (default 5).

The guard test below fails if any module outside the codec grows its own
frequency-BCD implementation. The allowlist is deliberately empty: no
module may implement frequency BCD on its own.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from rigplane.core.types import bcd_decode, bcd_encode

# Modules allowed their own frequency-BCD implementation: none.
FREQUENCY_BCD_IMPLEMENTATION_ALLOWLIST: list[str] = []

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCANNED_TREES = (REPO_ROOT / "src" / "rigplane", REPO_ROOT / "rigplane_testkit")
_CODEC_FILE = REPO_ROOT / "src" / "rigplane" / "core" / "types.py"

# Local helpers deleted by MOR-3983; reintroducing any of them is a
# second frequency-BCD implementation.
_FORBIDDEN_DEF_NAMES = frozenset(
    {
        "_bcd_int",
        "_bcd_encode_freq",
        "_bcd_decode_freq",
        "_encode_bcd_freq",
        "_decode_bcd_freq",
    }
)

# The little-endian positional idiom of the codec
# (``low * (10 ** (2 * i)) + high * ...``); dB/gain math elsewhere uses
# ``10 **`` with other exponents and is unaffected.
_FORBIDDEN_TEXT_SNIPPETS = (
    "10 ** (2 *",
    "block[4::-1]",
)

# Every module that touches a CI-V frequency field must take the codec
# from the single implementation (directly, or through the
# ``rigplane.types`` alias, which is the same module object, not a copy;
# ``rigplane.commands`` only re-exports the name).
_CODEC_SOURCES = frozenset(
    {"rigplane.core.types", "rigplane.types", "rigplane.commands"}
)
_KNOWN_CONSUMERS = (
    "src/rigplane/commands/freq.py",
    "src/rigplane/commands/memory.py",
    "src/rigplane/commands/scope.py",
    "src/rigplane/commands/tx_band.py",
    "src/rigplane/runtime/_civ_rx.py",
    "src/rigplane/runtime/_dual_rx_runtime.py",
    "src/rigplane/scope/__init__.py",
    "src/rigplane/web/radio_poller.py",
    "rigplane_testkit/icom/simulator.py",
    "rigplane_testkit/serial/civ_serial.py",
)


def _scanned_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for tree in _SCANNED_TREES:
        files.extend(sorted(tree.rglob("*.py")))
    return files


def _import_target(path: pathlib.Path, level: int, module: str | None) -> str:
    """Resolve an ``ImportFrom`` to its absolute module name."""
    if level == 0:
        return module or ""
    package = path.relative_to(REPO_ROOT / "src").with_suffix("").parts[:-1]
    base = package[: len(package) - level + 1]
    return ".".join((*base, module)) if module else ".".join(base)


def test_allowlist_is_empty() -> None:
    assert FREQUENCY_BCD_IMPLEMENTATION_ALLOWLIST == []


def test_no_local_frequency_bcd_implementation() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in _FORBIDDEN_DEF_NAMES
            ):
                offenders.append(f"{path}:{node.lineno}")
    offenders = [
        o for o in offenders if o not in FREQUENCY_BCD_IMPLEMENTATION_ALLOWLIST
    ]
    assert offenders == []


def test_no_le_positional_bcd_math_outside_codec() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        if path == _CODEC_FILE:
            continue
        text = path.read_text()
        for snippet in _FORBIDDEN_TEXT_SNIPPETS:
            if snippet in text:
                offenders.append(f"{path}: {snippet!r}")
    offenders = [
        o for o in offenders if o not in FREQUENCY_BCD_IMPLEMENTATION_ALLOWLIST
    ]
    assert offenders == []


def test_every_frequency_site_uses_the_codec() -> None:
    missing: list[str] = []
    for rel in _KNOWN_CONSUMERS:
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(), filename=str(path))
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _import_target(path, node.level, node.module)
            if target not in _CODEC_SOURCES:
                continue
            names = {alias.name for alias in node.names}
            if names & {"bcd_encode", "bcd_decode"}:
                found = True
                break
        if not found:
            missing.append(rel)
    assert missing == []


class TestFiveByteField:
    @pytest.mark.parametrize(
        ("freq_hz", "expected_hex"),
        [
            (14_074_000, "0040071400"),
            (7_074_000, "0040070700"),
            (0, "0000000000"),
            (1_296_000_000, "0000009612"),
            (9_999_999_999, "9999999999"),
        ],
    )
    def test_roundtrip(self, freq_hz: int, expected_hex: str) -> None:
        assert bcd_encode(freq_hz).hex() == expected_hex
        assert bcd_decode(bytes.fromhex(expected_hex)) == freq_hz

    def test_default_width_is_five_bytes(self) -> None:
        assert len(bcd_encode(1)) == 5

    def test_eleven_digits_do_not_fit_five_bytes(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            bcd_encode(10_000_000_000)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            bcd_encode(-1)


class TestSixByteField:
    def test_10_368_ghz_roundtrip(self) -> None:
        # Icom guide digit diagram, little-endian by byte: byte 4 is
        # GHz|100 MHz (0x03), byte 5 is 100 GHz|10 GHz (0x01).
        encoded = bcd_encode(10_368_000_000, width=6)
        assert encoded.hex() == "000000680301"
        assert encoded[4] == 0x03
        assert encoded[5] == 0x01
        assert bcd_decode(encoded) == 10_368_000_000

    def test_width_comes_from_payload_length(self) -> None:
        assert bcd_decode(bcd_encode(14_074_000, width=6)) == 14_074_000
        assert bcd_decode(bcd_encode(14_074_000)) == 14_074_000

    def test_eleven_digits_fit_six_bytes(self) -> None:
        assert bcd_decode(bcd_encode(10_000_000_000, width=6)) == 10_000_000_000

    def test_twelve_digits_do_not_fit_six_bytes(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            bcd_encode(1_000_000_000_000, width=6)

    @pytest.mark.parametrize("width", [0, 1, 4, 7])
    def test_rejected_width_raises(self, width: int) -> None:
        with pytest.raises(ValueError, match="width"):
            bcd_encode(14_074_000, width=width)

    @pytest.mark.parametrize("length", [0, 1, 4, 7])
    def test_rejected_length_raises(self, length: int) -> None:
        with pytest.raises(
            ValueError, match=r"must be 5 bytes \(6 for SHF fields\), got"
        ):
            bcd_decode(bytes(length))

    def test_invalid_bcd_digit_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid BCD"):
            bcd_decode(bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0xFF]))
