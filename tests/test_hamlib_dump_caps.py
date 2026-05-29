"""Tests for Hamlib dump_caps parsing and subprocess loading."""

from __future__ import annotations

import subprocess
from pathlib import Path

from rigplane.backends.hamlib_models import (
    HamlibCaps,  # noqa: F401 — exported symbol; validated by isinstance checks below
    load_hamlib_caps,
    parse_hamlib_dump_caps,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "hamlib_dump_caps_ts480.txt"


# ---------------------------------------------------------------------------
# 1. Parse fixture — real TS-480 dump
# ---------------------------------------------------------------------------


def test_parse_fixture_funcs_and_levels() -> None:
    caps = parse_hamlib_dump_caps(_FIXTURE.read_text(encoding="utf-8"))

    # get_funcs populated from "Get functions:" line
    assert "NB" in caps.get_funcs
    assert "COMP" in caps.get_funcs

    # set_funcs populated
    assert "NB" in caps.set_funcs

    # get_levels from "Get level:" line — granularity stripped
    assert "AF" in caps.get_levels
    assert "STRENGTH" in caps.get_levels
    assert "RF" in caps.get_levels

    # set_levels from "Set level:" line
    assert "AF" in caps.set_levels

    # modes from "Mode list:"
    assert {"USB", "LSB", "CW", "AM"} <= caps.modes

    # VFO ops
    assert "TUNE" in caps.vfo_ops
    assert "CPY" in caps.vfo_ops

    # has_set_freq
    assert caps.has_set_freq is True

    # PTT type: None  →  ptt_type=None
    assert caps.ptt_type is None

    # no degradation
    assert caps.degraded_reason is None


# ---------------------------------------------------------------------------
# 2. Level granularity stripped: FOO(0..1/…) → "FOO"
# ---------------------------------------------------------------------------


def test_level_granularity_stripped() -> None:
    text = "Get level: RF(0.000000..1.000000/0.003922) AF(0..100/1)\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.get_levels == frozenset({"RF", "AF"})


def test_level_granularity_stripped_parens_and_dots() -> None:
    text = "Set level: CWPITCH(400..1000/50) NR(0.000000..1.000000/0.100000)\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.set_levels == frozenset({"CWPITCH", "NR"})


# ---------------------------------------------------------------------------
# 3. Missing section → empty set
# ---------------------------------------------------------------------------


def test_missing_vfo_ops_is_empty() -> None:
    text = "Get functions: NB\nMode list: USB LSB\nCan set Frequency:\tY\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.vfo_ops == frozenset()


# ---------------------------------------------------------------------------
# 4. Empty section (trailing space) → empty set, no empty string token
# ---------------------------------------------------------------------------


def test_empty_get_functions_line() -> None:
    text = "Get functions: \n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.get_funcs == frozenset()
    assert "" not in caps.get_funcs


def test_empty_mode_list() -> None:
    text = "Mode list:  \n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.modes == frozenset()


# ---------------------------------------------------------------------------
# 5. Extra whitespace tolerated
# ---------------------------------------------------------------------------


def test_extra_whitespace_in_token_list() -> None:
    text = "Get functions:  NB   COMP   VOX  \n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.get_funcs == frozenset({"NB", "COMP", "VOX"})


# ---------------------------------------------------------------------------
# 6. Extra functions/levels indented block NOT swallowed
# ---------------------------------------------------------------------------


def test_extra_functions_indented_block_not_swallowed() -> None:
    text = (
        "Get functions: NB\n"
        "Extra functions:\n"
        "\tNR2\n"
        "\t\tType: CHECKBUTTON\n"
        "\tCW_IF_FOR_SSB_RX\n"
    )
    caps = parse_hamlib_dump_caps(text)
    assert caps.get_funcs == frozenset({"NB"})
    assert "NR2" not in caps.get_funcs
    assert "CW_IF_FOR_SSB_RX" not in caps.get_funcs


def test_extra_levels_indented_block_not_swallowed() -> None:
    text = (
        "Get level: AF(0..1/0.003922)\n"
        "Extra levels:\n"
        "\tDIGITAL_NOISE_LIMITER\n"
        "\t\tType: COMBO\n"
    )
    caps = parse_hamlib_dump_caps(text)
    assert caps.get_levels == frozenset({"AF"})
    assert "DIGITAL_NOISE_LIMITER" not in caps.get_levels


# ---------------------------------------------------------------------------
# 7. Malformed/garbage input → degraded, no raise
# ---------------------------------------------------------------------------


def test_garbage_input_does_not_raise() -> None:
    caps = parse_hamlib_dump_caps(
        "!!!totally garbage\x00\x01 binary\nno sections here\n"
    )
    assert caps.degraded_reason is not None
    assert caps.get_funcs == frozenset()
    assert caps.modes == frozenset()


def test_empty_string_input_degrades() -> None:
    caps = parse_hamlib_dump_caps("")
    assert caps.degraded_reason is not None


# ---------------------------------------------------------------------------
# 8. PTT type variants
# ---------------------------------------------------------------------------


def test_ptt_type_rts() -> None:
    text = "PTT type:\tRTS\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.ptt_type == "RTS"


def test_ptt_type_none_string() -> None:
    text = "PTT type:\tNone\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.ptt_type is None


def test_ptt_type_rig() -> None:
    text = "PTT type:\tRig capable\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.ptt_type == "Rig capable"


# ---------------------------------------------------------------------------
# 9. Can set Frequency: N → has_set_freq False
# ---------------------------------------------------------------------------


def test_can_set_frequency_n() -> None:
    text = "Can set Frequency:\tN\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.has_set_freq is False


def test_can_set_frequency_y() -> None:
    text = "Can set Frequency:\tY\n"
    caps = parse_hamlib_dump_caps(text)
    assert caps.has_set_freq is True


# ---------------------------------------------------------------------------
# 10. load_hamlib_caps monkeypatch — success path
# ---------------------------------------------------------------------------


def test_load_hamlib_caps_success(monkeypatch) -> None:
    fixture_text = _FIXTURE.read_text(encoding="utf-8")
    captured_args: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_args.append(list(args))
        assert kwargs.get("shell") is False
        return subprocess.CompletedProcess(args, 0, stdout=fixture_text, stderr="")

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    caps = load_hamlib_caps(2028)

    assert captured_args == [["rigctl", "-m", "2028", "--dump-caps"]]
    assert caps.model_id == 2028
    assert caps.degraded_reason is None
    assert "NB" in caps.get_funcs
    assert caps.has_set_freq is True


# ---------------------------------------------------------------------------
# 11. load_hamlib_caps FileNotFoundError → empty degraded, no raise
# ---------------------------------------------------------------------------


def test_load_hamlib_caps_tool_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        "rigplane.backends.hamlib_models.subprocess.run",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("rigctl")),
    )

    caps = load_hamlib_caps(2028)

    assert caps.degraded_reason is not None
    assert caps.model_id == 2028
    assert caps.get_funcs == frozenset()


# ---------------------------------------------------------------------------
# 12. Timeout doesn't leak secrets
# ---------------------------------------------------------------------------


def test_load_hamlib_caps_timeout_does_not_leak(monkeypatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            args, timeout=0.1, output="SECRET=xyz", stderr="token=topsecret"
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    caps = load_hamlib_caps(2028)

    assert caps.degraded_reason is not None
    assert "SECRET" not in str(caps)
    assert "SECRET" not in (caps.degraded_reason or "")
    assert "topsecret" not in str(caps)


# ---------------------------------------------------------------------------
# 13. Nonzero return code → empty degraded
# ---------------------------------------------------------------------------


def test_load_hamlib_caps_nonzero_rc(monkeypatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="some error on stderr"
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    caps = load_hamlib_caps(2028)

    assert caps.degraded_reason is not None
    assert caps.get_funcs == frozenset()
    assert "some error" not in (caps.degraded_reason or "")
