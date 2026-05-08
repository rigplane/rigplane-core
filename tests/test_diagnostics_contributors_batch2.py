"""Tests for built-in diagnostic contributors batch 2 (#1391).

Covers: ``RadioContributor`` and ``AudioContributor``.

Tests instantiate contributors directly (not via ``discover()``) so they
do not need ``_BUILT_IN_CONTRIBUTORS`` isolation — they only need
``_RUNTIME_REGISTERED`` cleanup to match the project pattern.

Stubbing ``AudioCapable`` for ``isinstance()``: the Protocol is
``runtime_checkable`` and declares ~14 attrs/methods. We provide a
single ``_AudioCapableStub`` class at module level with bare async
stubs and reuse it across radio + audio tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.audio.route import AudioConfigSource
from rigplane.diagnostics import _discovery
from rigplane.diagnostics.contributor import BundleContext
from rigplane.diagnostics.contributors import (
    AudioContributor,
    RadioContributor,
)
from rigplane.types import AudioCodec


@pytest.fixture(autouse=True)
def _clear_runtime_registered() -> Any:
    """Ensure runtime-registered contributors don't leak between tests."""
    _discovery._RUNTIME_REGISTERED.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()


def _make_ctx(**overrides: Any) -> BundleContext:
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": Path("/tmp/cfg-does-not-exist-1391"),
        "log_dir": Path("/tmp/log-does-not-exist-1391"),
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "sub-batch2",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


# ---------------------------------------------------------------- AudioCapable stub
#
# ``AudioCapable`` is ``@runtime_checkable``; ``isinstance()`` checks the
# presence of every declared attribute/method. We provide minimal bare
# stubs sufficient for the runtime check to pass.


class _AudioCapableStub:
    """Structurally satisfies ``AudioCapable`` for ``isinstance`` checks."""

    # Attributes (declared as properties on the Protocol — class attrs OK).
    audio_bus = None
    audio_codec = "PCM_1CH_16BIT"
    audio_sample_rate = 48000

    # The Protocol's declared async methods. None of the bodies matter.
    async def start_audio_rx_opus(self, callback: Any) -> None: ...
    async def stop_audio_rx_opus(self) -> None: ...
    async def push_audio_tx_opus(self, data: bytes) -> None: ...
    async def start_audio_rx_pcm(
        self,
        callback: Any,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...
    async def stop_audio_rx_pcm(self) -> None: ...
    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...
    async def push_audio_tx_pcm(self, data: bytes) -> None: ...
    async def stop_audio_tx_pcm(self) -> None: ...
    async def get_audio_stats(self) -> dict[str, Any]:
        return {}

    async def start_audio_tx_opus(self) -> None: ...
    async def stop_audio_tx_opus(self) -> None: ...


class _PlainRadio:
    """Bare radio object with no AudioCapable surface."""

    model = "MockRig"


# ---------------------------------------------------------------------- radio


def test_radio_emits_unavailable_when_radio_is_none(tmp_path: Path) -> None:
    RadioContributor().contribute(_make_ctx(radio=None), tmp_path)
    out = tmp_path / "radio.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["available"] is False
    assert "note" in payload


def test_radio_emits_capabilities_for_audio_capable_mock(tmp_path: Path) -> None:
    radio = _AudioCapableStub()
    RadioContributor().contribute(_make_ctx(radio=radio), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["available"] is True
    assert "AudioCapable" in payload["capabilities"]


def test_radio_keeps_private_host_ip(tmp_path: Path) -> None:
    """RFC 1918 IP addresses are kept (radio LAN is useful for triage)."""

    class FakeRadio:
        host = "192.168.55.40"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    text = (tmp_path / "radio.json").read_text()
    json.loads(text)  # round-trip
    assert "192.168.55.40" in text


def test_radio_redacts_public_host_ip(tmp_path: Path) -> None:
    """Public IPv4 addresses are redacted to ``<IP>``."""

    class FakeRadio:
        host = "8.8.8.8"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    text = (tmp_path / "radio.json").read_text()
    payload = json.loads(text)
    assert "8.8.8.8" not in text
    assert payload["host"] == "<IP>"


def test_radio_includes_model_and_backend(tmp_path: Path) -> None:
    class FakeRadio:
        model = "IC-7610"
        backend_id = "rigplane"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["model"] == "IC-7610"
    assert payload["backend"] == "rigplane"


def test_radio_backend_id_wins_over_class_name(tmp_path: Path) -> None:
    """``backend_id`` takes precedence over the radio's class name."""

    class WeirdName:
        backend_id = "rigplane"

    RadioContributor().contribute(_make_ctx(radio=WeirdName()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["backend"] == "rigplane"


def test_radio_falls_back_to_class_name_when_no_backend_id(tmp_path: Path) -> None:
    """No ``backend_id`` and no ``backend_name`` → class name fallback."""

    class FakeRadio:
        pass

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["backend"] == "FakeRadio"


def test_radio_safe_attr_handles_missing_attributes(tmp_path: Path) -> None:
    """Missing attributes → ``None`` in output, no exception."""

    class FakeRadio:
        pass  # no model, no firmware, etc.

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["available"] is True
    assert payload["model"] is None
    assert payload["firmware_version"] is None
    # backend falls back to class name
    assert payload["backend"] == "FakeRadio"


def test_radio_falls_back_to_private_host_port(tmp_path: Path) -> None:
    """Live LAN runtimes store connection info as ``_host``/``_port`` (private).

    Regression for Codex review on PR #1410: previously only public
    ``host``/``port`` were read, so an active ``IcomRadio`` reported
    ``host=null``/``port=null``.
    """

    class FakeRadio:
        # Only private attrs — no public ``host``/``port``.
        _host = "192.168.55.40"
        _port = 50001

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    # Private RFC 1918 IP is preserved; port comes through.
    assert payload["host"] == "192.168.55.40"
    assert payload["port"] == 50001


def test_radio_redacts_hostname_in_host_field(tmp_path: Path) -> None:
    """DNS-shape host values are scrubbed via ``redact_hostnames``.

    Regression for Codex review on PR #1410: previously only
    ``redact_paths``/``redact_ips``/``redact_credentials`` ran, so a
    hostname like ``radio.example.com`` was emitted unchanged.
    """

    class FakeRadio:
        host = "radio.example.com"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["host"] == "<HOSTNAME>"


def test_radio_model_field_not_over_redacted_by_hostname(tmp_path: Path) -> None:
    """``redact_hostnames`` must NOT run on non-host fields like ``model``.

    The hostname pattern matches ``label.tld`` shapes including filenames; we
    only apply it to the ``host`` field. Model strings like ``IC-7610`` (no
    dot) and synthetic ``radio.json``-shaped values must pass through other
    fields unscrubbed by hostname rules.
    """

    class FakeRadio:
        model = "IC-7610"
        backend_id = "rigplane"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    payload = json.loads((tmp_path / "radio.json").read_text())
    assert payload["model"] == "IC-7610"
    assert payload["backend"] == "rigplane"


def test_radio_credentials_redacted_in_string_field(tmp_path: Path) -> None:
    """Per-value redaction preserves valid JSON (regex can't span structural chars)."""

    class FakeRadio:
        # Contrived: an attacker-controlled model string.
        model = "credentials: password=secretrigsecret999"

    RadioContributor().contribute(_make_ctx(radio=FakeRadio()), tmp_path)
    text = (tmp_path / "radio.json").read_text()
    # JSON must round-trip — would fail if `\S+` regex consumed the closing
    # quote of the JSON string value when applied post-dump.
    payload = json.loads(text)
    assert "secretrigsecret999" not in text
    assert "REDACTED" in payload["model"]


# ---------------------------------------------------------------------- audio


def test_audio_unavailable_when_radio_is_none(tmp_path: Path) -> None:
    AudioContributor().contribute(_make_ctx(radio=None), tmp_path)
    out = tmp_path / "audio.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["available"] is False
    assert "note" in payload


def test_audio_unavailable_when_radio_not_audio_capable(tmp_path: Path) -> None:
    AudioContributor().contribute(_make_ctx(radio=_PlainRadio()), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())
    assert payload["available"] is False


def test_audio_emits_codec_and_sample_rate(tmp_path: Path) -> None:
    radio = _AudioCapableStub()
    AudioContributor().contribute(_make_ctx(radio=radio), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())
    assert payload["available"] is True
    assert payload["codec"] == "PCM_1CH_16BIT"
    assert payload["sample_rate_hz"] == 48000


def test_audio_emits_requested_and_effective_radio_native_contracts(
    tmp_path: Path,
) -> None:
    class FakeAudioRadio(_AudioCapableStub):
        audio_stream_request = SimpleNamespace(
            rx_codec=AudioCodec.PCM_2CH_16BIT,
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            rx_sample_rate_hz=16000,
            tx_sample_rate_hz=16000,
            rx_channels=2,
            tx_channels=1,
            rx_codec_source=AudioConfigSource.PROFILE_DEFAULT,
            tx_codec_source=AudioConfigSource.PROFILE_DEFAULT,
            rx_sample_rate_source=AudioConfigSource.PROFILE_CODEC_DEFAULT,
            tx_sample_rate_source=AudioConfigSource.PROFILE_CODEC_DEFAULT,
        )
        audio_stream_contract = SimpleNamespace(
            rx_codec=AudioCodec.PCM_1CH_16BIT,
            tx_codec=AudioCodec.PCM_1CH_16BIT,
            rx_sample_rate_hz=16000,
            tx_sample_rate_hz=16000,
            rx_channels=1,
            tx_channels=1,
            rx_codec_source=AudioConfigSource.FALLBACK,
            tx_codec_source=AudioConfigSource.PROFILE_DEFAULT,
            rx_sample_rate_source=AudioConfigSource.PROFILE_CODEC_DEFAULT,
            tx_sample_rate_source=AudioConfigSource.PROFILE_CODEC_DEFAULT,
            fallback_reason="conninfo-stereo-rx-rejected",
        )

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())

    requested = payload["radio_native"]["requested"]
    assert requested["rx"] == {
        "codec": "PCM_2CH_16BIT",
        "sample_rate_hz": 16000,
        "channels": 2,
        "codec_source": "profile-default",
        "sample_rate_source": "profile-codec-default",
    }
    assert requested["tx"]["codec"] == "PCM_1CH_16BIT"
    assert requested["tx"]["channels"] == 1

    effective = payload["radio_native"]["effective"]
    assert effective["rx"]["codec"] == "PCM_1CH_16BIT"
    assert effective["rx"]["codec_source"] == "fallback"
    assert effective["fallback_reason"] == "conninfo-stereo-rx-rejected"


def test_audio_emits_configured_web_rx_policy_without_runtime_client_state(
    tmp_path: Path,
) -> None:
    class FakeAudioRadio(_AudioCapableStub):
        profile = SimpleNamespace(
            browser_rx_transport="auto",
            browser_rx_transcode_to_opus=True,
        )
        audio_stream_contract = SimpleNamespace(
            rx_codec="PCM_2CH_16BIT",
            tx_codec="PCM_1CH_16BIT",
            rx_sample_rate_hz=16000,
            tx_sample_rate_hz=16000,
            rx_channels=2,
            tx_channels=1,
            rx_codec_source="profile-default",
            tx_codec_source="profile-default",
            rx_sample_rate_source="profile-codec-default",
            tx_sample_rate_source="profile-codec-default",
            fallback_reason=None,
        )

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())

    web_rx = payload["web_rx"]
    assert web_rx == {
        "state": "configured-policy",
        "transport": "auto",
        "transcode_to_opus": True,
        "codec": "OPUS",
        "sample_rate_hz": 16000,
        "channels": 2,
        "codec_source": "profile-default",
        "sample_rate_source": "radio-native-effective",
        "channels_source": "radio-native-effective",
    }


def test_audio_emits_usb_audio_contract(tmp_path: Path) -> None:
    class FakeAudioRadio(_AudioCapableStub):
        usb_audio_contract = SimpleNamespace(
            to_dict=lambda: {
                "rx": {
                    "device": {"index": 1, "name": "USB Audio CODEC"},
                    "sample_rate_hz": 16000,
                    "sample_rate_source": "fallback",
                    "fallback_reason": "sample-rate-48000-unsupported",
                },
                "tx": None,
            }
        )

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())

    usb_audio = payload["usb_audio"]
    assert usb_audio["rx"]["sample_rate_hz"] == 16000
    assert usb_audio["rx"]["sample_rate_source"] == "fallback"
    assert usb_audio["rx"]["fallback_reason"] == "sample-rate-48000-unsupported"


def test_audio_redacts_usb_audio_contract_device_labels(tmp_path: Path) -> None:
    class FakeAudioRadio(_AudioCapableStub):
        usb_audio_contract = SimpleNamespace(
            to_dict=lambda: {
                "rx": {
                    "device": {
                        "index": 1,
                        "name": "BlackHole 2ch (moroz's Mac)",
                        "platform_uid": "/Users/moroz/Audio/Device",
                    },
                    "sample_rate_hz": 48000,
                },
                "tx": None,
            }
        )

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    text = (tmp_path / "audio.json").read_text()
    payload = json.loads(text)

    assert "moroz" not in text
    assert (
        payload["usb_audio"]["rx"]["device"]["name"] == "BlackHole 2ch (<USER>'s Mac)"
    )
    assert (
        payload["usb_audio"]["rx"]["device"]["platform_uid"]
        == "/Users/<USER>/Audio/Device"
    )


def test_audio_redacts_device_name_with_username(tmp_path: Path) -> None:
    class FakeAudioRadio(_AudioCapableStub):
        audio_rx_device = "Speakers (/Users/foo/Library/Audio)"
        audio_tx_device = "Mic"

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    text = (tmp_path / "audio.json").read_text()
    payload = json.loads(text)
    assert "/Users/foo" not in text
    assert "<USER>" in payload["rx_device"]


def test_audio_redacts_macos_user_label_in_device_name(tmp_path: Path) -> None:
    """macOS device labels embed usernames in ``(<name>'s Mac)``.

    Regression for Codex review on PR #1410: previously only
    ``redact_paths`` ran, so the ``(moroz's Mac)`` form leaked the username.
    """

    class FakeAudioRadio(_AudioCapableStub):
        audio_rx_device = "BlackHole 2ch (moroz's Mac)"
        audio_tx_device = "USB Audio (Alice's MacBook Pro)"

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    text = (tmp_path / "audio.json").read_text()
    payload = json.loads(text)
    assert "moroz" not in text
    assert "Alice" not in text
    assert payload["rx_device"] == "BlackHole 2ch (<USER>'s Mac)"
    assert payload["tx_device"] == "USB Audio (<USER>'s Mac)"


def test_audio_no_false_positive_on_normal_device_name(tmp_path: Path) -> None:
    """Normal device names without the ``'s Mac`` form are kept verbatim."""

    class FakeAudioRadio(_AudioCapableStub):
        audio_rx_device = "Built-in Output"
        audio_tx_device = "External USB"

    AudioContributor().contribute(_make_ctx(radio=FakeAudioRadio()), tmp_path)
    payload = json.loads((tmp_path / "audio.json").read_text())
    assert payload["rx_device"] == "Built-in Output"
    assert payload["tx_device"] == "External USB"


def test_audio_json_loads_round_trip(tmp_path: Path) -> None:
    radio = _AudioCapableStub()
    AudioContributor().contribute(_make_ctx(radio=radio), tmp_path)
    text = (tmp_path / "audio.json").read_text()
    payload = json.loads(text)  # round-trip guard
    assert isinstance(payload, dict)
    # also assert a value the contributor emits
    assert "codec" in payload


# -------------------------------------------------------------------- wiring


def test_built_in_contributors_includes_batch2() -> None:
    """``_BUILT_IN_CONTRIBUTORS`` includes batch-2 classes with expected names."""
    names = {cls().name for cls in _discovery._BUILT_IN_CONTRIBUTORS}
    assert {"radio", "audio"}.issubset(names)
