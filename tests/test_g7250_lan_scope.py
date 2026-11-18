"""Xiegu G7250 hardware scope over LAN (MOR-4436 scope PR).

The G7250 (firmware V1.0.1, wfview network mode) answers its CI-V scope
over LAN. The profile declares it as data (``rigs/g7250.toml`` ``[scope]``);
the shared scope mechanism gains two data-driven facts it needs: a 6-byte
span value and ``full_width`` span presets in the waveform span lookup.
Every wire byte below comes from the recorded bench radio
(``tests/fixtures/g7250/lan_scope_20261113.json``).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from test_radio import MockTransport

from rigplane.commands import parse_civ_frame, parse_scope_span_response, scope_set_span
from rigplane.core.state_pipeline_contracts import FieldScope
from rigplane.profiles import RadioProfile, get_radio_profile
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeAssembler

_FIXTURE = Path(__file__).parent / "fixtures" / "g7250" / "lan_scope_20261113.json"
_RIGS = Path(__file__).resolve().parents[1] / "rigs"


def _capture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_FIXTURE.read_text())
    return data


def _frame(hex_bytes: str) -> bytes:
    return bytes.fromhex(hex_bytes)


def _scope_paths(profile: RadioProfile) -> set[str]:
    acquisition = profile.state_acquisition
    assert acquisition is not None
    return {
        str(capability.path)
        for capability in acquisition.capabilities
        if capability.path.scope is FieldScope.SCOPE_CONTROLS
    }


# ---------------------------------------------------------------------------
# Profile data through the real loader
# ---------------------------------------------------------------------------


def test_g7250_profile_declares_the_lan_scope() -> None:
    profile = get_radio_profile("G7250")
    assert "scope" in profile.capabilities
    assert profile.scope_span_presets_hz == (48000, 96000, 192000, 384000)
    assert (profile.scope_ref_min_db, profile.scope_ref_max_db) == (-20.0, 20.0)
    assert profile.scope_span_preset_semantics == "full_width"
    assert profile.scope_span_value_bytes == 6
    # full_width: each preset is the view width itself.
    assert profile.scope_span_view_widths_hz == profile.scope_span_presets_hz
    assert _scope_paths(profile) == {
        "scope_controls.global.display.mode",
        "scope_controls.global.display.span",
        "scope_controls.global.display.edge",
        "scope_controls.global.display.ref_db",
    }


@pytest.mark.parametrize("model", ["IC-705", "IC-7300", "IC-7610", "IC-9700"])
def test_icom_profiles_keep_the_five_byte_half_width_span(model: str) -> None:
    profile = get_radio_profile(model)
    assert profile.scope_span_value_bytes == 5
    assert profile.scope_span_preset_semantics == "half_width"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("span_value_bytes = 4", "span_value_bytes"),
        ("span_value_bytes = true", "span_value_bytes"),
    ],
)
def test_loader_rejects_bad_span_value_width(
    tmp_path: Path, value: str, message: str
) -> None:
    text = (_RIGS / "g7250.toml").read_text()
    text = text.replace("span_value_bytes = 6", "", 1)
    text = text.replace("[scope]\n", f"[scope]\n{value}\n", 1)
    path = tmp_path / "g7250.toml"
    path.write_text(text)
    with pytest.raises(RigLoadError, match=message):
        load_rig(path)


# ---------------------------------------------------------------------------
# Recorded wire bytes through the shared scope codec
# ---------------------------------------------------------------------------


def test_recorded_g7250_span_reply_decodes() -> None:
    profile = get_radio_profile("G7250")
    reply = parse_civ_frame(_frame(_capture()["requests_replies"]["27 15 00"]))
    receiver, index = parse_scope_span_response(reply, profile.scope_span_presets_hz)
    assert receiver == 0
    assert profile.scope_span_presets_hz[index] == 192000


def test_g7250_span_set_matches_the_applied_bytes() -> None:
    profile = get_radio_profile("G7250")
    assert profile.command_map is not None
    frame = scope_set_span(
        profile.scope_span_presets_hz.index(192000),
        to_addr=profile.civ_addr,
        cmd_map=profile.command_map,
        presets=profile.scope_span_presets_hz,
        receiver=0,
        value_bytes=profile.scope_span_value_bytes,
    )
    assert frame == _frame(_capture()["span_set_sent"])


def test_recorded_g7250_wave_frame_assembles() -> None:
    raw = _frame(_capture()["wave_frame"])
    decoded = _capture()["wave_decoded"]
    frame = ScopeAssembler().feed(raw[7:-1], raw[6])
    assert frame is not None
    assert frame.mode == 0
    assert frame.start_freq_hz == decoded["start_hz"]
    assert frame.end_freq_hz == decoded["end_hz"]
    assert len(frame.pixels) == decoded["pixels"] == 475


# ---------------------------------------------------------------------------
# The waveform stream publishes the full-width span index
# ---------------------------------------------------------------------------


@pytest.fixture
def g7250_radio() -> Generator[IcomRadio, None, None]:
    transport = MockTransport()
    radio = IcomRadio("192.0.2.1", model="G7250")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    yield radio
    radio._connected = False


async def test_recorded_wave_publishes_the_192000_span(g7250_radio: IcomRadio) -> None:
    """Center +/- 96000 on a full_width radio is the 192000 preset, not 96000."""
    frame = parse_civ_frame(_frame(_capture()["wave_frame"]))
    await g7250_radio._civ_runtime._route_civ_frame(
        frame, generation=g7250_radio._civ_epoch
    )
    field = g7250_radio._state_store.snapshot().field(
        "scope_controls.global.display.span"
    )
    presets = g7250_radio.profile.scope_span_presets_hz
    assert field.value == presets.index(192000)
