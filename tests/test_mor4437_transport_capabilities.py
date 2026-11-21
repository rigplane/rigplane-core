"""MOR-4437: capabilities that depend on the control transport.

A profile describes the radio; the *transport* describes the connection
actually in use.  The Xiegu G7250 (firmware V1.0.1) answers CI-V scope
``27 00`` over LAN and stays silent over USB serial, so consumers that
treat profile fields as facts about the current connection must derive
the transport from the backend in use — never from ``RadioProfile.has_lan``.

These tests go through the real profile loader (shipped ``rigs/*.toml``
plus temp-file rigs) and real backend radio objects (constructed but never
connected) on both transports.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from rigplane.audio.route import (
    DataModePolicy,
    RxAudioSource,
    TxAudioSource,
    resolve_audio_route,
)
from rigplane.backends.ic705.serial import XieguSerialRadio
from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.core.radio_state import RadioState
from rigplane.core.radio_transport import RadioTransport, control_transport_of
from rigplane.core.state_store import StateStore
from rigplane.profiles import (
    TransportCapabilityGaps,
    get_radio_profile,
)
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.radio import IcomRadio
from rigplane.runtime import radio_initial_state
from rigplane.runtime._poller_types import CommandQueue
from rigplane.runtime.radio_initial_state import fetch_initial_state
from rigplane.web.radio_poller import (
    _FAST_INTERVAL,
    _FAST_INTERVAL_SERIAL,
    _GAP,
    _GAP_SERIAL,
    RadioPoller,
)
from rigplane.web.runtime_helpers import runtime_capabilities
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import _startup_gap_seconds
from test_web_server import _http_get

G7250 = "xiegu_g7250"


# ── Transport derivation (one shared mechanism) ──────────────────────


class TestControlTransportOf:
    def test_lan_backend(self):
        radio = IcomRadio(host="192.0.2.1", model="G7250")
        assert control_transport_of(radio) is RadioTransport.LAN

    def test_serial_backends(self):
        xiegu = XieguSerialRadio(device="/dev/null", model="G7250")
        ic7300 = Ic7300SerialRadio(device="/dev/null", model="IC-7300")
        assert control_transport_of(xiegu) is RadioTransport.SERIAL
        assert control_transport_of(ic7300) is RadioTransport.SERIAL

    def test_unknown_backend_id(self):
        class _Foreign:
            backend_id = "something_else"

        assert control_transport_of(_Foreign()) is RadioTransport.UNKNOWN

    def test_no_backend_id(self):
        assert control_transport_of(object()) is RadioTransport.UNKNOWN


class TestPacesLikeSerial:
    def test_lan_is_fast(self):
        assert RadioTransport.LAN.paces_like_serial(has_lan=True) is False
        assert RadioTransport.LAN.paces_like_serial(has_lan=False) is False

    def test_serial_is_slow(self):
        assert RadioTransport.SERIAL.paces_like_serial(has_lan=True) is True
        assert RadioTransport.SERIAL.paces_like_serial(has_lan=False) is True

    def test_remote_and_unknown_keep_legacy_has_lan_answer(self):
        # rigctld and unrecognized backends predate the explicit transport
        # value: their pacing stays exactly what ``not has_lan`` gave them.
        assert RadioTransport.REMOTE.paces_like_serial(has_lan=True) is False
        assert RadioTransport.REMOTE.paces_like_serial(has_lan=False) is True
        assert RadioTransport.UNKNOWN.paces_like_serial(has_lan=True) is False
        assert RadioTransport.UNKNOWN.paces_like_serial(has_lan=False) is True


# ── Profile data: per-transport capability gaps through the loader ───


class TestTransportCapabilityGapsLoader:
    def test_g7250_declares_serial_scope_gap(self):
        profile = get_radio_profile(G7250)
        assert profile.transport_capability_gaps == TransportCapabilityGaps(
            serial=frozenset({"scope"})
        )

    def test_g7250_scope_survives_lan_drops_serial(self):
        profile = get_radio_profile(G7250)
        declared = set(profile.capabilities)
        assert "scope" in declared
        assert "scope" in profile.capabilities_for_transport(
            RadioTransport.LAN, declared
        )
        assert "scope" not in profile.capabilities_for_transport(
            RadioTransport.SERIAL, declared
        )
        # The gap is serial-only: REMOTE and UNKNOWN keep the declared set.
        assert "scope" in profile.capabilities_for_transport(
            RadioTransport.REMOTE, declared
        )
        assert "scope" in profile.capabilities_for_transport(
            RadioTransport.UNKNOWN, declared
        )

    @pytest.mark.parametrize(
        "profile_id",
        ["icom_ic7300", "icom_ic705", "icom_ic7610", "xiegu_x6200"],
    )
    def test_existing_profiles_have_no_gaps(self, profile_id):
        # Existing profiles keep current behaviour: the declared set is
        # unchanged on EVERY transport, including one the radio is never
        # connected over.
        profile = get_radio_profile(profile_id)
        assert profile.transport_capability_gaps == TransportCapabilityGaps()
        declared = set(profile.capabilities)
        for transport in (
            RadioTransport.LAN,
            RadioTransport.SERIAL,
            RadioTransport.REMOTE,
        ):
            assert profile.capabilities_for_transport(transport, declared) == set(
                declared
            )

    def test_minimal_toml_parses_gap(self, tmp_path):
        rig = load_rig(_write_rig(tmp_path, _MINIMAL_WITH_GAPS))
        profile = rig.to_profile()
        assert profile.transport_capability_gaps == TransportCapabilityGaps(
            lan=frozenset({"meters"}),
            serial=frozenset({"scope"}),
        )

    def test_absent_table_means_no_gaps(self, tmp_path):
        rig = load_rig(_write_rig(tmp_path, _MINIMAL_TOML))
        assert rig.transport_capability_gaps == TransportCapabilityGaps()

    def test_unknown_transport_key_rejected(self, tmp_path):
        with pytest.raises(RigLoadError, match="transport_gaps.*key"):
            load_rig(
                _write_rig(
                    tmp_path,
                    _MINIMAL_TOML + '[capabilities.transport_gaps]\nusb = ["scope"]\n',
                )
            )

    def test_non_list_value_rejected(self, tmp_path):
        with pytest.raises(RigLoadError, match="must be a list"):
            load_rig(
                _write_rig(
                    tmp_path,
                    _MINIMAL_TOML + '[capabilities.transport_gaps]\nserial = "scope"\n',
                )
            )

    def test_gap_on_undeclared_capability_rejected(self, tmp_path):
        with pytest.raises(RigLoadError, match="not declared"):
            load_rig(
                _write_rig(
                    tmp_path,
                    _MINIMAL_TOML
                    + '[capabilities.transport_gaps]\nserial = ["dual_rx"]\n',
                )
            )

    def test_non_table_gaps_rejected(self, tmp_path):
        with pytest.raises(RigLoadError, match="must be a table"):
            load_rig(
                _write_rig(
                    tmp_path,
                    _MINIMAL_TOML.replace(
                        'features = ["audio", "scope", "meters", "tx"]',
                        'features = ["audio", "scope", "meters", "tx"]\n'
                        'transport_gaps = "serial"',
                    ),
                )
            )


def _write_rig(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(textwrap.dedent(content))
    return p


_MINIMAL_TOML = """\
[radio]
id = "icom_ic7300"
model = "IC-7300"
civ_addr = 0x94
receiver_count = 1
has_lan = true
has_wifi = false

[spectrum]
seq_max = 11
amp_max = 160
data_len_max = 475

[capabilities]
features = ["audio", "scope", "meters", "tx"]

[modes]
list = ["USB", "LSB", "CW"]

[filters]
list = ["FIL1", "FIL2"]

[vfo]
scheme = "ab"

[[freq_ranges.ranges]]
label = "HF"
start_hz = 30000
end_hz = 60000000

[commands]
get_freq = [0x03]
set_freq = [0x05]

[commands.overrides]
"""

_MINIMAL_WITH_GAPS = (
    _MINIMAL_TOML
    + """\
[capabilities.transport_gaps]
lan = ["meters"]
serial = ["scope"]
"""
)


# ── runtime_capabilities: the scope gate reads the live transport ────


class TestRuntimeCapabilitiesTransportGate:
    def test_g7250_scope_over_lan(self):
        radio = IcomRadio(host="192.0.2.1", model="G7250")
        caps = runtime_capabilities(radio)
        assert "scope" in caps
        # Non-gapped capabilities ride along unchanged.
        assert {"audio", "tx", "meters"} <= caps

    def test_g7250_no_scope_over_serial(self):
        radio = XieguSerialRadio(device="/dev/null", model="G7250")
        caps = runtime_capabilities(radio)
        assert "scope" not in caps
        assert {"audio", "tx", "meters"} <= caps

    @pytest.mark.parametrize("model", ["IC-7300", "IC-705", "IC-7610"])
    def test_no_gap_profile_keeps_scope_on_both_transports(self, model):
        lan = IcomRadio(host="192.0.2.1", model=model)
        serial = Ic7300SerialRadio(device="/dev/null", model=model)
        assert "scope" in runtime_capabilities(lan)
        assert "scope" in runtime_capabilities(serial)

    def test_remote_transport_not_serial_gapped(self):
        class _RemoteRadio:
            backend_id = "rigctld"
            # "tx"/"meters" carry no protocol gate in runtime_capabilities
            # (unlike "scope"/"audio"), so a bare stub survives unchanged.
            capabilities = {"tx", "meters"}
            profile = get_radio_profile(G7250)

        # The G7250 gap is serial-specific; a REMOTE link keeps the set.
        assert runtime_capabilities(_RemoteRadio()) == {"tx", "meters"}

    def test_unknown_transport_keeps_declared_set(self):
        class _BareRadio:
            capabilities = {"tx", "meters"}
            profile = get_radio_profile(G7250)

        assert runtime_capabilities(_BareRadio()) == {"tx", "meters"}

    def test_no_profile_keeps_declared_set(self):
        class _NoProfile:
            backend_id = "icom_serial"
            capabilities = {"tx", "meters"}

        assert runtime_capabilities(_NoProfile()) == {"tx", "meters"}


# ── One derivation: the audio route consumes it, never re-classifies ─


class TestAudioRouteTransportConsistency:
    """``resolve_audio_route`` reports the shared transport, not its own.

    Codex round-one P1: the resolver kept a private backend-ID → transport
    classification that disagreed with ``control_transport_of`` — a rigctld
    link read ``UNKNOWN`` here while every other consumer said ``REMOTE``,
    and ``/api/v1/info`` publishes the disagreement through
    ``audioRoute``.  The control transport now comes from the one shared
    derivation; this resolver owns only the audio-source consequences.
    """

    def test_lan_backend(self):
        radio = IcomRadio(host="192.0.2.1", model="G7250")
        route = resolve_audio_route(radio)
        assert route.radio_transport is control_transport_of(radio)
        assert route.radio_transport is RadioTransport.LAN
        assert route.tx_audio_source is TxAudioSource.LAN
        assert route.rx_audio_source is RxAudioSource.LAN

    def test_serial_backend(self):
        radio = XieguSerialRadio(device="/dev/null", model="G7250")
        route = resolve_audio_route(radio)
        assert route.radio_transport is control_transport_of(radio)
        assert route.radio_transport is RadioTransport.SERIAL
        assert route.tx_audio_source is TxAudioSource.USB
        assert route.rx_audio_source is RxAudioSource.USB
        assert route.data_mode_policy is DataModePolicy.DATA1_USB
        assert route.bridge_required is True

    def test_rigctld_backend_reports_remote_not_unknown(self):
        # The P1 case: rigctld used to fall into this resolver's UNKNOWN
        # arm while every other transport consumer said REMOTE.  The audio
        # sources stay unavailable (the daemon carries no radio audio), so
        # only the transport field joins the one derivation.
        radio = RigctldClientRadio(host="127.0.0.1")
        route = resolve_audio_route(radio)
        assert route.radio_transport is control_transport_of(radio)
        assert route.radio_transport is RadioTransport.REMOTE
        assert route.tx_audio_source is TxAudioSource.UNAVAILABLE
        assert route.rx_audio_source is RxAudioSource.UNAVAILABLE
        assert route.data_mode_policy is DataModePolicy.LEGACY
        assert route.bridge_required is False


# ── The real API: GET /api/v1/capabilities applies the transport gate ─


class TestCapabilitiesEndpointTransportGate:
    """The served capability payload follows the live transport.

    Codex round-one P1: the serial G7250 gate was pinned only on the
    ``runtime_capabilities`` helper, so a server that stopped applying the
    helper and advertised serial scope again kept those assertions green.
    Every case here drives the real public API path — a real ``WebServer``
    on a real socket, real backend objects (constructed, never connected:
    hardware/network faked; the transport is a class-level fact of the
    backend) — and asserts on the ``GET /api/v1/capabilities`` body over
    the wire, never on the helper directly.
    """

    @staticmethod
    async def _served_capabilities(radio: object) -> dict:
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
        try:
            host, port = web._server.sockets[0].getsockname()
            status, _, body = await _http_get(host, port, "/api/v1/capabilities")
            assert status == 200, f"GET /api/v1/capabilities: HTTP {status}"
            return json.loads(body)
        finally:
            web._server.close()
            await web._server.wait_closed()
            web._server = None
            tasks = tuple(web._client_tasks)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_serial_g7250_serves_no_scope(self):
        data = await self._served_capabilities(
            XieguSerialRadio(device="/dev/null", model="G7250")
        )
        assert data["scope"] is False
        assert "scope" not in data["capabilities"]
        assert "radio" not in data["scopeSources"]
        # The default scope source must never be the gated radio source
        # (the audio FFT fallback remains a legitimate scope source).
        assert data["scopeDefaultSourceId"] != "radio"

    @pytest.mark.asyncio
    async def test_lan_g7250_keeps_scope_and_radio_source(self):
        data = await self._served_capabilities(
            IcomRadio(host="192.0.2.1", model="G7250")
        )
        assert data["scope"] is True
        assert "scope" in data["capabilities"]
        assert "radio" in data["scopeSources"]
        assert data["scopeDefaultSourceId"] == "radio"

    @pytest.mark.asyncio
    async def test_unaffected_radio_keeps_scope_on_both_transports(self):
        # Control: a profile without transport gaps keeps scope on every
        # transport, so the gate must not leak beyond the declared gaps.
        for radio in (
            IcomRadio(host="192.0.2.1", model="IC-7300"),
            Ic7300SerialRadio(device="/dev/null", model="IC-7300"),
        ):
            data = await self._served_capabilities(radio)
            assert data["scope"] is True
            assert "scope" in data["capabilities"]
# ── Pacing: poller, startup gate, initial-state sweep ────────────────


class TestPollerPacing:
    def test_serial_backend_gets_serial_budget(self):
        radio = XieguSerialRadio(device="/dev/null", model="G7250")
        poller = RadioPoller(radio, CommandQueue())
        assert poller._is_serial is True
        assert poller._gap == _GAP_SERIAL
        assert poller._fast_interval == _FAST_INTERVAL_SERIAL

    def test_lan_backend_gets_lan_budget_even_for_has_lan_false_profile(self):
        # The transport of the connection beats the profile's has_lan in
        # both directions: an IC-7300 profile (has_lan = false) driven over
        # LAN takes the LAN budget.
        radio = IcomRadio(host="192.0.2.1", model="IC-7300")
        poller = RadioPoller(radio, CommandQueue())
        assert poller._is_serial is False
        assert poller._gap == _GAP
        assert poller._fast_interval == _FAST_INTERVAL

    def test_usb_connected_lan_capable_radio_gets_serial_budget(self):
        # The ticket's case: a USB-connected IC-705/7610/7300MK2 has
        # has_lan = true in its profile but is a serial link and must be
        # serial-paced.
        radio = Ic7300SerialRadio(device="/dev/null", model="IC-7610")
        poller = RadioPoller(radio, CommandQueue())
        assert radio.profile.has_lan is True
        assert poller._is_serial is True

    def test_backend_without_transport_keeps_legacy_has_lan_answer(self):
        class _BareRadio:
            # No backend_id, no profile attribute routing: poller resolves
            # the profile from the model.
            model = "IC-7300"
            capabilities = set()

        poller = RadioPoller(_BareRadio(), CommandQueue())
        # IC-7300 profile has_lan = false → serial, exactly as before.
        assert poller._is_serial is True


class _GapProbeRadio:
    """Minimal stand-in exposing what the pacing consumers read."""

    _INITIAL_STATE_GAP_LAN = 0.222
    _INITIAL_STATE_GAP_SERIAL = 0.111

    def __init__(self, profile, backend_id):
        self._profile = profile
        self.backend_id = backend_id
        self._state_store = StateStore()
        self.radio_state = RadioState()
        self._initial_state_fetched = False

    async def send_civ(self, *args, **kwargs):
        raise RuntimeError("no radio connected")


def _record_sleep(monkeypatch, sleeps: list) -> None:
    """Patch ``asyncio.sleep`` (as radio_initial_state sees it) to record."""

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(radio_initial_state.asyncio, "sleep", _fake_sleep)


class TestStartupGap:
    def test_usb_link_serial_paced_despite_has_lan_profile(self):
        # The ticket's scenario: profile.has_lan is true (the radio model
        # offers LAN), but the actual connection is serial.
        radio = _GapProbeRadio(get_radio_profile(G7250), "icom_serial")
        assert radio._profile.has_lan is True
        assert _startup_gap_seconds(radio) == 0.111

    def test_lan_link_lan_paced(self):
        radio = _GapProbeRadio(get_radio_profile(G7250), "rigplane")
        assert _startup_gap_seconds(radio) == 0.222

    def test_no_backend_falls_back_to_has_lan(self):
        radio = _GapProbeRadio(get_radio_profile(G7250), None)
        assert _startup_gap_seconds(radio) == 0.222
        radio_no_lan = _GapProbeRadio(get_radio_profile("icom_ic7300"), None)
        assert radio_no_lan._profile.has_lan is False
        assert _startup_gap_seconds(radio_no_lan) == 0.111


class TestInitialStateGap:
    @pytest.mark.asyncio
    async def test_serial_transport_uses_serial_gap(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), "icom_serial")
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert radio._initial_state_fetched is True
        assert sleeps, "the sweep should sleep between queries"
        assert set(sleeps) == {0.111}

    @pytest.mark.asyncio
    async def test_lan_transport_uses_lan_gap(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), "rigplane")
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert set(sleeps) == {0.222}

    @pytest.mark.asyncio
    async def test_unknown_transport_falls_back_to_has_lan(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), None)
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert set(sleeps) == {0.222}


# ── Pacing: poller, startup gate, initial-state sweep ────────────────


class TestPollerPacing:
    def test_serial_backend_gets_serial_budget(self):
        radio = XieguSerialRadio(device="/dev/null", model="G7250")
        poller = RadioPoller(radio, CommandQueue())
        assert poller._is_serial is True
        assert poller._gap == _GAP_SERIAL
        assert poller._fast_interval == _FAST_INTERVAL_SERIAL

    def test_lan_backend_gets_lan_budget_even_for_has_lan_false_profile(self):
        # The transport of the connection beats the profile's has_lan in
        # both directions: an IC-7300 profile (has_lan = false) driven over
        # LAN takes the LAN budget.
        radio = IcomRadio(host="192.0.2.1", model="IC-7300")
        poller = RadioPoller(radio, CommandQueue())
        assert poller._is_serial is False
        assert poller._gap == _GAP
        assert poller._fast_interval == _FAST_INTERVAL

    def test_usb_connected_lan_capable_radio_gets_serial_budget(self):
        # The ticket's case: a USB-connected IC-705/7610/7300MK2 has
        # has_lan = true in its profile but is a serial link and must be
        # serial-paced.
        radio = Ic7300SerialRadio(device="/dev/null", model="IC-7610")
        poller = RadioPoller(radio, CommandQueue())
        assert radio.profile.has_lan is True
        assert poller._is_serial is True

    def test_backend_without_transport_keeps_legacy_has_lan_answer(self):
        class _BareRadio:
            # No backend_id, no profile attribute routing: poller resolves
            # the profile from the model.
            model = "IC-7300"
            capabilities = set()

        poller = RadioPoller(_BareRadio(), CommandQueue())
        # IC-7300 profile has_lan = false → serial, exactly as before.
        assert poller._is_serial is True


class _GapProbeRadio:
    """Minimal stand-in exposing what the pacing consumers read."""

    _INITIAL_STATE_GAP_LAN = 0.222
    _INITIAL_STATE_GAP_SERIAL = 0.111

    def __init__(self, profile, backend_id):
        self._profile = profile
        self.backend_id = backend_id
        self._state_store = StateStore()
        self.radio_state = RadioState()
        self._initial_state_fetched = False

    async def send_civ(self, *args, **kwargs):
        raise RuntimeError("no radio connected")


def _record_sleep(monkeypatch, sleeps: list) -> None:
    """Patch ``asyncio.sleep`` (as radio_initial_state sees it) to record."""

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(radio_initial_state.asyncio, "sleep", _fake_sleep)


class TestStartupGap:
    def test_usb_link_serial_paced_despite_has_lan_profile(self):
        # The ticket's scenario: profile.has_lan is true (the radio model
        # offers LAN), but the actual connection is serial.
        radio = _GapProbeRadio(get_radio_profile(G7250), "icom_serial")
        assert radio._profile.has_lan is True
        assert _startup_gap_seconds(radio) == 0.111

    def test_lan_link_lan_paced(self):
        radio = _GapProbeRadio(get_radio_profile(G7250), "rigplane")
        assert _startup_gap_seconds(radio) == 0.222

    def test_no_backend_falls_back_to_has_lan(self):
        radio = _GapProbeRadio(get_radio_profile(G7250), None)
        assert _startup_gap_seconds(radio) == 0.222
        radio_no_lan = _GapProbeRadio(get_radio_profile("icom_ic7300"), None)
        assert radio_no_lan._profile.has_lan is False
        assert _startup_gap_seconds(radio_no_lan) == 0.111


class TestInitialStateGap:
    @pytest.mark.asyncio
    async def test_serial_transport_uses_serial_gap(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), "icom_serial")
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert radio._initial_state_fetched is True
        assert sleeps, "the sweep should sleep between queries"
        assert set(sleeps) == {0.111}

    @pytest.mark.asyncio
    async def test_lan_transport_uses_lan_gap(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), "rigplane")
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert set(sleeps) == {0.222}

    @pytest.mark.asyncio
    async def test_unknown_transport_falls_back_to_has_lan(self, monkeypatch):
        radio = _GapProbeRadio(get_radio_profile(G7250), None)
        sleeps = []
        _record_sleep(monkeypatch, sleeps)
        await fetch_initial_state(radio)
        assert set(sleeps) == {0.222}
