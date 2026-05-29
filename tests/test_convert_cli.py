"""CLI driver tests for the ``rigplane convert`` verb (MOR-220, slice 204b).

These cover the I/O driver and wiring around the pure MOR-203 converter:
model resolution (numeric id + name), draft TOML emission, degraded-caps
handling, the ``--compare-profile`` cross-check, and the ``discover_rigs``
``*.draft.toml`` auto-load skip.

Argv is driven through the real ``rigplane.cli._build_parser()`` so the
dispatch wiring (``_COMMAND_NAMES`` + the ``elif`` branch) is exercised.
"""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

import rigplane.cli as cli
import rigplane.cli._convert as convert_mod
from rigplane.backends.hamlib_models import HamlibCaps


def _run_convert(argv: list[str]) -> int:
    """Parse *argv* through the real parser and dispatch to the convert run()."""
    parser = cli._build_parser()
    args = parser.parse_args(argv)
    assert args.command == "convert"
    return convert_mod.run(args)


def _good_caps() -> HamlibCaps:
    """A non-degraded caps view: RF/AF levels, NB func, USB/CWR modes, set freq."""
    return HamlibCaps(
        get_levels=frozenset({"RF", "AF"}),
        get_funcs=frozenset({"NB"}),
        modes=frozenset({"USB", "CWR"}),
        has_set_freq=True,
        model_id=3091,
    )


# ---------------------------------------------------------------------------
# 1. convert <numeric id> --draft-out
# ---------------------------------------------------------------------------


def test_convert_numeric_id_writes_draft(tmp_path, monkeypatch, capsys) -> None:
    out = tmp_path / "x.draft.toml"
    monkeypatch.setattr(
        convert_mod, "load_hamlib_caps", lambda model_id, **kw: _good_caps()
    )

    rc = _run_convert(["convert", "3091", "--draft-out", str(out)])

    assert rc == 0
    assert out.exists()
    data = tomllib.loads(out.read_text())
    # Required loader sections present.
    for section in ("radio", "protocol", "capabilities", "modes", "filters", "vfo"):
        assert section in data
    # Features mapped from Hamlib tokens.
    features = set(data["capabilities"]["features"])
    assert {"rf_gain", "af_level", "nb"} <= features
    # Modes normalized: CWR -> CW-R, USB passthrough.
    assert "CW-R" in data["modes"]["list"]
    assert "USB" in data["modes"]["list"]
    # Driver reports the written path on stderr.
    assert str(out) in capsys.readouterr().err


def test_convert_default_draft_out_is_cwd_slug(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        convert_mod, "load_hamlib_caps", lambda model_id, **kw: _good_caps()
    )

    rc = _run_convert(["convert", "3091"])

    assert rc == 0
    expected = tmp_path / "3091.draft.toml"
    assert expected.exists()


# ---------------------------------------------------------------------------
# 2. name resolution + unknown name
# ---------------------------------------------------------------------------


def test_convert_name_resolves_to_hamlib_model_id(tmp_path, monkeypatch) -> None:
    out = tmp_path / "x6200.draft.toml"
    seen: dict[str, int] = {}

    def _fake_load(model_id: int, **kw: object) -> HamlibCaps:
        seen["model_id"] = model_id
        return _good_caps()

    monkeypatch.setattr(convert_mod, "load_hamlib_caps", _fake_load)

    rc = _run_convert(["convert", "X6200", "--draft-out", str(out)])

    assert rc == 0
    # X6200 profile carries hamlib_model_id 3091.
    assert seen["model_id"] == 3091


def test_convert_unknown_name_exits_2(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        convert_mod,
        "load_hamlib_caps",
        lambda model_id, **kw: pytest.fail("should not load caps for unknown model"),
    )

    rc = _run_convert(["convert", "NO-SUCH-RADIO", "--draft-out", str(tmp_path / "x")])

    assert rc == 2
    assert "unknown model" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# 3. degraded caps -> exit 3, no file written
# ---------------------------------------------------------------------------


def test_convert_degraded_caps_exits_3_no_file(tmp_path, monkeypatch, capsys) -> None:
    out = tmp_path / "x.draft.toml"
    monkeypatch.setattr(
        convert_mod,
        "load_hamlib_caps",
        lambda model_id, **kw: HamlibCaps(degraded_reason="rigctl not found"),
    )

    rc = _run_convert(["convert", "3091", "--draft-out", str(out)])

    assert rc == 3
    assert not out.exists()
    assert capsys.readouterr().err.strip() != ""


# ---------------------------------------------------------------------------
# 4. --compare-profile --json
# ---------------------------------------------------------------------------


def test_convert_compare_profile_json(tmp_path, monkeypatch, capsys) -> None:
    import json

    out = tmp_path / "x.draft.toml"
    monkeypatch.setattr(
        convert_mod, "load_hamlib_caps", lambda model_id, **kw: _good_caps()
    )

    rc = _run_convert(
        [
            "convert",
            "3091",
            "--draft-out",
            str(out),
            "--compare-profile",
            "X6200",
            "--json",
        ]
    )

    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert "agreed" in report
    assert "rigplane_only" in report
    assert "hamlib_only" in report


def test_convert_compare_profile_human(tmp_path, monkeypatch, capsys) -> None:
    out = tmp_path / "x.draft.toml"
    monkeypatch.setattr(
        convert_mod, "load_hamlib_caps", lambda model_id, **kw: _good_caps()
    )

    rc = _run_convert(
        ["convert", "3091", "--draft-out", str(out), "--compare-profile", "X6200"]
    )

    assert rc == 0
    assert "Cross-check report" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 5. discover_rigs skips *.draft.toml
# ---------------------------------------------------------------------------

_REAL_TOML = """\
[radio]
id = "icom_ic7300"
model = "IC-7300"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

[capabilities]
features = ["audio", "tx"]

[modes]
list = ["USB", "LSB"]

[filters]
list = ["FIL1"]

[vfo]
scheme = "ab"

[commands]
get_freq = [0x03]
"""


def test_discover_rigs_skips_draft_toml(tmp_path) -> None:
    from rigplane.profiles.rig_loader import discover_rigs

    (tmp_path / "real.toml").write_text(textwrap.dedent(_REAL_TOML))
    # A stray draft that must NOT be auto-loaded (it would fail validation anyway,
    # but more importantly it must be skipped silently like _-prefixed files).
    (tmp_path / "x6200.draft.toml").write_text(
        textwrap.dedent(_REAL_TOML).replace('model = "IC-7300"', 'model = "DRAFT"')
    )

    rigs = discover_rigs(Path(tmp_path))

    assert "IC-7300" in rigs
    assert "DRAFT" not in rigs
    assert len(rigs) == 1
