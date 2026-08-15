"""Published FTX-1 scalar control domains remain exact and loader-derived."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from rigplane.profiles import RadioProfile
from rigplane.rig_loader import load_rig


_PROFILE_PATH = Path(__file__).resolve().parents[1] / "rigs" / "ftx1.toml"
_DOMAINS = {
    "rit": {
        "mapping": "identity",
        "raw_min": -9999,
        "raw_max": 9999,
        "raw_step": 1,
        "raw_origin": 0,
        "display_min": "-9999",
        "display_max": "9999",
        "display_step": "1",
        "display_origin": "0",
        "display_unit": "Hz",
        "quantization": "reject",
        "restoration": "exact",
    },
    "nr_level": {
        "mapping": "identity",
        "raw_min": 0,
        "raw_max": 10,
        "raw_step": 1,
        "raw_origin": 0,
        "display_min": "0",
        "display_max": "10",
        "display_step": "1",
        "display_origin": "0",
        "display_unit": "level",
        "quantization": "reject",
        "restoration": "exact",
    },
    "manual_notch_freq": {
        "mapping": "linear",
        "raw_min": 1,
        "raw_max": 320,
        "raw_step": 1,
        "raw_origin": 1,
        "display_min": "10",
        "display_max": "3200",
        "display_step": "10",
        "display_origin": "10",
        "display_unit": "Hz",
        "quantization": "reject",
        "restoration": "exact",
    },
    "if_shift": {
        "mapping": "identity",
        "raw_min": -1200,
        "raw_max": 1200,
        "raw_step": 20,
        "raw_origin": 0,
        "display_min": "-1200",
        "display_max": "1200",
        "display_step": "20",
        "display_origin": "0",
        "display_unit": "Hz",
        "quantization": "reject",
        "restoration": "exact",
    },
    "cw_pitch": {
        "mapping": "identity",
        "raw_min": 300,
        "raw_max": 1050,
        "raw_step": 10,
        "raw_origin": 300,
        "display_min": "300",
        "display_max": "1050",
        "display_step": "10",
        "display_origin": "300",
        "display_unit": "Hz",
        "quantization": "reject",
        "restoration": "exact",
    },
}


def _profile() -> RadioProfile:
    """Always derive the public capability wire from the normal TOML loader."""
    return load_rig(_PROFILE_PATH).to_profile()


def _decode(domain: dict[str, object], raw: int) -> str:
    """Exact scalar equivalent of MOR-1722's decode/encode lattice contract."""
    if domain["mapping"] == "identity":
        return str(raw)
    return str(
        int(domain["display_origin"])
        + ((raw - int(domain["raw_origin"])) // int(domain["raw_step"]))
        * int(domain["display_step"])
    )


def _encode(domain: dict[str, object], display: str) -> int:
    if domain["mapping"] == "identity":
        return int(display)
    return int(domain["raw_origin"]) + (
        (int(display) - int(domain["display_origin"])) // int(domain["display_step"])
    ) * int(domain["raw_step"])


def test_ftx1_scalar_domains_are_exact_loader_published_capabilities() -> None:
    first = _profile()
    second = _profile()

    assert first.controls is not None
    assert second.controls is not None
    assert first.controls is not second.controls
    assert first.controls["rit"] is not second.controls["rit"]
    assert first.controls["nr"] == {
        "style": "level_is_toggle",
        "range_min": 0,
        "range_max": 10,
    }
    assert first.controls.keys() == {
        "attenuator",
        "nb",
        "nr",
        "compressor_level",
        *_DOMAINS,
    }
    assert {name: first.controls[name] for name in _DOMAINS} == _DOMAINS
    assert json.loads(json.dumps(first.controls, allow_nan=False)) == first.controls
    assert all(
        getattr(first, field.name) == getattr(second, field.name)
        for field in fields(RadioProfile)
        if field.name != "controls"
    )

    for domain in _DOMAINS.values():
        assert all(
            isinstance(domain[name], str)
            for name in ("display_min", "display_max", "display_step", "display_origin")
        )
        for raw in range(
            int(domain["raw_min"]),
            int(domain["raw_max"]) + 1,
            int(domain["raw_step"]),
        ):
            display = _decode(domain, raw)
            assert _encode(domain, display) == raw

    assert _decode(_DOMAINS["rit"], -9999) == "-9999"
    assert _decode(_DOMAINS["rit"], 0) == "0"
    assert _decode(_DOMAINS["rit"], 9999) == "9999"
    assert _decode(_DOMAINS["manual_notch_freq"], 1) == "10"
    assert _decode(_DOMAINS["manual_notch_freq"], 160) == "1600"
    assert _decode(_DOMAINS["manual_notch_freq"], 320) == "3200"
    assert _encode(_DOMAINS["manual_notch_freq"], "10") == 1
    assert _encode(_DOMAINS["manual_notch_freq"], "3200") == 320
    assert 0 not in range(
        int(_DOMAINS["manual_notch_freq"]["raw_min"]),
        int(_DOMAINS["manual_notch_freq"]["raw_max"]) + 1,
    )


def test_ftx1_profile_is_immutable() -> None:
    profile = _profile()
    with pytest.raises((AttributeError, TypeError)):
        profile.model = "mutable"  # type: ignore[misc]
