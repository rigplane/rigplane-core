"""Regression tests for MOR-505 — v2 AUDIO SCOPE blank on FTX-1.

Root cause: ``WebServer._update_fft_scope_freq`` / ``_update_fft_scope_mode``
read the active receiver's freq/mode from the StateStore snapshot using a
HARDCODED receiver-id ``"0"`` (``FieldPath.active("0", "freq_mode", ...)``).
That id is only correct for the legacy Icom state poller. Yaesu CAT and
rigctld backends key the primary receiver under ``"main"`` (see
``backends/yaesu_cat/observations.py`` / ``backends/rigctld_client/
observations.py``), so the snapshot lookup raised ``KeyError`` and the method
returned early. ``AudioFftScope._center_freq`` stayed 0, and ``feed_audio``'s
``_center_freq <= 0`` guard then dropped every frame — a blank scope.

The fix resolves the primary receiver scheme-agnostically via
``runtime_helpers.primary_receiver_snapshot_ids`` (derived from the canonical
``_SNAPSHOT_RECEIVER_IDS`` map used by the public-state builder), trying
``"0"`` then ``"main"`` and using the first present id. Icom ("0") behavior is
unchanged; Yaesu ("main") now works.

These tests are hardware-free: they construct ``StateSnapshot`` objects
directly and drive ``_update_fft_scope_freq`` / ``_update_fft_scope_mode``,
asserting the scope's center freq / bandwidth.
"""

from __future__ import annotations

from rigplane.capabilities import CAP_AUDIO
from rigplane.core.state_pipeline_contracts import FieldPath, SourceMetadata
from rigplane.core.state_store import FieldSnapshot, FreshnessState, StateSnapshot
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.server import WebConfig, WebServer

_SOURCE = SourceMetadata(
    source="state_poller",
    provider="test",
    transport="backend",
    native_id="test",
)


class _AudioOnlyRadio:
    """Minimal fake radio: audio capability (so the FFT scope is wired), no
    hardware scope. Carries a real profile so mode-bandwidth resolution works.
    """

    def __init__(self, *, model: str) -> None:
        self.capabilities = frozenset({CAP_AUDIO})
        self.radio_state = RadioState()
        self.audio_codec = None
        self.audio_sample_rate = 48_000
        self.model = model
        self.profile = resolve_radio_profile(model=model)


def _snapshot(*fields: FieldSnapshot) -> StateSnapshot:
    return StateSnapshot(
        state_revision=1,
        freshness_revision=1,
        observation_seq=1,
        generated_at_monotonic=0.0,
        fields=tuple(fields),
    )


def _freq_field(receiver_id: str, freq_hz: int) -> FieldSnapshot:
    return FieldSnapshot(
        path=FieldPath.active(receiver_id, "freq_mode", "freq_hz"),
        value=freq_hz,
        freshness=FreshnessState.FRESH,
        last_observed_monotonic=0.0,
        max_age=None,
        source=_SOURCE,
    )


def _mode_field(receiver_id: str, mode: str) -> FieldSnapshot:
    return FieldSnapshot(
        path=FieldPath.active(receiver_id, "freq_mode", "mode"),
        value=mode,
        freshness=FreshnessState.FRESH,
        last_observed_monotonic=0.0,
        max_age=None,
        source=_SOURCE,
    )


def _make_server(*, model: str) -> WebServer:
    server = WebServer(radio=_AudioOnlyRadio(model=model), config=WebConfig())
    assert server._audio_fft_scope is not None, "audio FFT scope must be wired"
    return server


# ── center frequency ─────────────────────────────────────────────────────────


def test_center_freq_set_for_main_keyed_receiver() -> None:
    """FTX-1/Yaesu: freq keyed under receiver "main" must set center_freq.

    THIS IS THE MOR-505 GUARD. On the unfixed code the hardcoded "0" lookup
    raises KeyError, the method returns early, and center_freq stays 0 (blank
    scope). After the fix the "main" id is resolved and center_freq is set.
    """
    server = _make_server(model="FTX-1")
    scope = server._audio_fft_scope
    assert scope is not None
    assert scope._center_freq == 0

    server._update_fft_scope_freq(_snapshot(_freq_field("main", 14_074_000)))

    assert scope._center_freq == 14_074_000


def test_center_freq_set_for_icom_zero_keyed_receiver() -> None:
    """Icom: freq keyed under receiver "0" must still set center_freq (no regression)."""
    server = _make_server(model="IC-7610")
    scope = server._audio_fft_scope
    assert scope is not None

    server._update_fft_scope_freq(_snapshot(_freq_field("0", 7_074_000)))

    assert scope._center_freq == 7_074_000


def test_center_freq_unchanged_when_freq_missing() -> None:
    """No freq field in the snapshot: no-op, no exception, center_freq stays 0."""
    server = _make_server(model="FTX-1")
    scope = server._audio_fft_scope
    assert scope is not None

    server._update_fft_scope_freq(_snapshot())  # empty snapshot

    assert scope._center_freq == 0


def test_center_freq_unchanged_when_freq_zero() -> None:
    """freq == 0 must not push a bogus center_freq (would still gate frames)."""
    server = _make_server(model="FTX-1")
    scope = server._audio_fft_scope
    assert scope is not None
    scope.set_center_freq(14_074_000)

    server._update_fft_scope_freq(_snapshot(_freq_field("main", 0)))

    # 0 is not > 0 — center_freq must be left at its prior value.
    assert scope._center_freq == 14_074_000


# ── mode bandwidth ───────────────────────────────────────────────────────────


def test_mode_bandwidth_set_for_main_keyed_receiver() -> None:
    """Mode keyed under "main" must resolve and set the filter bandwidth.

    Use an IC-7610 profile (whose USB rule has a concrete ``max_hz``) but key
    the mode under "main" to prove receiver-id resolution is scheme-agnostic
    for the mode path too.
    """
    server = _make_server(model="IC-7610")
    scope = server._audio_fft_scope
    assert scope is not None
    assert scope.bandwidth_hz is None

    server._update_fft_scope_mode(_snapshot(_mode_field("main", "USB")))

    # IC-7610 USB rule has max_hz=3600.
    assert scope.bandwidth_hz == 3600


def test_mode_bandwidth_set_for_icom_zero_keyed_receiver() -> None:
    """Mode keyed under "0" must still resolve (no regression)."""
    server = _make_server(model="IC-7610")
    scope = server._audio_fft_scope
    assert scope is not None

    server._update_fft_scope_mode(_snapshot(_mode_field("0", "USB")))

    assert scope.bandwidth_hz == 3600


def test_mode_bandwidth_unchanged_when_mode_missing() -> None:
    """No mode field: no-op, no exception, bandwidth stays at prior value."""
    server = _make_server(model="IC-7610")
    scope = server._audio_fft_scope
    assert scope is not None
    scope.set_mode_bandwidth(2700)

    server._update_fft_scope_mode(_snapshot())  # empty snapshot

    assert scope.bandwidth_hz == 2700
