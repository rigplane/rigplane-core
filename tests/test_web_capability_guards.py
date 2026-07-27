"""Web UI capability guards — verify API responses adapt to rig profile.

TDD: these tests were written FIRST, then the backend was modified to pass them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio.route import TxAudioSource
from rigplane.types import AudioCodec
from rigplane.web.handlers.audio import AudioHandler, browser_tx_audio_facts
from rigplane.web.server import WebServer


class _FakeWriter:
    """Minimal asyncio.StreamWriter stand-in that captures bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _parse_json_body(writer: _FakeWriter) -> dict:
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    return json.loads(text[body_start:])


# ── Helpers: fake radios with profiles ──────────────────────────


def _make_radio(model: str = "IC-7610", caps: set[str] | None = None):
    """Build a fake radio with a real RadioProfile resolved by model name."""
    from rigplane.profiles import resolve_radio_profile
    from rigplane.radio_protocol import AudioCapable, DualReceiverCapable, ScopeCapable

    profile = resolve_radio_profile(model=model)

    class _FakeRadio(ScopeCapable, AudioCapable, DualReceiverCapable):
        pass

    radio = _FakeRadio()
    radio.model = model
    radio.backend_id = "rigplane" if model == "IC-7610" else "yaesu_cat"
    radio.connected = True
    radio.control_connected = False
    radio.radio_ready = True
    radio.capabilities = caps if caps is not None else set(profile.capabilities)
    radio.profile = profile
    return radio


# ── /api/v1/info tests ─────────────────────────────────────────


class TestInfoEndpoint:
    """Verify /api/v1/info includes rig-specific metadata."""

    @pytest.mark.asyncio
    async def test_info_includes_receivers_for_dual(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["maxReceivers"] == 2

    @pytest.mark.asyncio
    async def test_info_includes_receivers_for_single(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["maxReceivers"] == 1

    @pytest.mark.asyncio
    async def test_info_includes_vfo_scheme_main_sub(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["vfoScheme"] == "main_sub"

    @pytest.mark.asyncio
    async def test_info_includes_vfo_scheme_ab(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["vfoScheme"] == "ab"

    @pytest.mark.asyncio
    async def test_info_includes_has_lan_true(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["hasLan"] is True

    @pytest.mark.asyncio
    async def test_info_includes_has_lan_false(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["capabilities"]["hasLan"] is False

    @pytest.mark.asyncio
    async def test_info_ic7300_no_dual_rx_tag(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_info(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert "dual_rx" not in data["capabilities"]["tags"]


# ── /api/v1/capabilities tests ─────────────────────────────────


class TestCapabilitiesEndpoint:
    """Verify /api/v1/capabilities includes rig-specific metadata."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "receivers", "vfo_scheme"),
        [
            ("IC-7610", 2, "main_sub"),
            ("IC-7300", 1, "ab"),
            ("FTX-1", 2, "ab_shared"),
        ],
    )
    async def test_capabilities_pins_known_wire_subset_and_types(
        self, model: str, receivers: int, vfo_scheme: str
    ):
        """Known fields stay typed while additive fields remain compatible."""
        radio = _make_radio(model)
        srv = WebServer(radio)
        writer = _FakeWriter()

        await srv._serve_capabilities(writer)  # noqa: SLF001

        data = _parse_json_body(writer)
        # Deliberately assert a subset: clients must tolerate future additive
        # capability fields, but these established fields and their types are
        # the frontend wire contract.
        assert data["model"] == model
        assert type(data["receivers"]) is int
        assert data["receivers"] == receivers
        assert data["vfoScheme"] == vfo_scheme
        assert isinstance(data["capabilities"], list)
        assert all(isinstance(capability, str) for capability in data["capabilities"])
        assert isinstance(data["scope"], bool)
        assert isinstance(data["audio"], bool)
        assert isinstance(data["tx"], bool)
        assert isinstance(data["audioTx"], bool)
        assert data["audioTxRoute"] in {"lan", "usb", "acc", None}
        assert isinstance(data["freqRanges"], list)
        assert isinstance(data["modes"], list)
        assert isinstance(data["filters"], list)
        assert isinstance(data["audioConfig"], dict)
        assert type(data["audioConfig"]["sampleRate"]) is int
        assert isinstance(data["webrtc"], dict)
        assert isinstance(data["webrtc"]["available"], bool)
        assert isinstance(data["webrtc"]["enabled"], bool)
        assert isinstance(data["txBands"], list)
        assert all(
            isinstance(band["name"], str)
            and type(band["start"]) is int
            and type(band["end"]) is int
            for band in data["txBands"]
        )

    @pytest.mark.asyncio
    async def test_capabilities_reports_hardware_and_audio_scope_independently(self):
        """Audio FFT availability is not inferred from hardware scope support."""
        hardware_radio = _make_radio("IC-7610")
        hardware_srv = WebServer(hardware_radio)
        hardware_writer = _FakeWriter()
        await hardware_srv._serve_capabilities(hardware_writer)  # noqa: SLF001
        hardware_data = _parse_json_body(hardware_writer)

        audio_radio = _make_radio("FTX-1")
        audio_srv = WebServer(audio_radio)
        audio_writer = _FakeWriter()
        await audio_srv._serve_capabilities(audio_writer)  # noqa: SLF001
        audio_data = _parse_json_body(audio_writer)

        assert hardware_data["scope"] is True
        assert hardware_data["audio"] is True
        assert audio_data["scope"] is False
        assert audio_data["audio"] is True

    @pytest.mark.asyncio
    async def test_capabilities_allows_nullable_tx_bands(self):
        """Profiles without configured bands serialize the established null value."""
        radio = _make_radio("IC-7610")
        radio.profile = replace(radio.profile, freq_ranges=())
        srv = WebServer(radio)
        writer = _FakeWriter()

        await srv._serve_capabilities(writer)  # noqa: SLF001

        data = _parse_json_body(writer)
        assert data["txBands"] is None

    @pytest.mark.asyncio
    async def test_capabilities_includes_receivers(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["receivers"] == 2

    @pytest.mark.asyncio
    async def test_capabilities_includes_vfo_scheme(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["vfoScheme"] == "main_sub"

    @pytest.mark.asyncio
    async def test_capabilities_single_receiver(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["receivers"] == 1
        assert data["vfoScheme"] == "ab"

    @pytest.mark.asyncio
    async def test_capabilities_include_filter_config(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["filterConfig"]["USB"]["defaults"] == [3000, 2400, 1800]
        assert data["filterConfig"]["USB-D"]["defaults"] == [3000, 1200, 500]
        assert data["filterConfig"]["AM"]["stepHz"] == 200
        assert data["filterConfig"]["FM"]["fixed"] is True

    @pytest.mark.asyncio
    async def test_capabilities_include_keyboard_config(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["keyboard"]["leaderKey"] == "g"
        assert data["keyboard"]["leaderTimeoutMs"] == 1000
        assert data["keyboard"]["altHints"] is True
        assert any(
            binding["action"] == "toggle_help"
            for binding in data["keyboard"]["bindings"]
        )

    @pytest.mark.asyncio
    async def test_capabilities_include_antenna_count(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["antennas"] == 2

    @pytest.mark.asyncio
    async def test_capabilities_single_antenna_default(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_capabilities(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert data["antennas"] == 1

    @pytest.mark.asyncio
    async def test_capabilities_browser_tx_audio_facts(self):
        cases = (("IC-7610", (True, "lan", 5)), ("FTX-1", (True, "usb", None)))
        for model, expected in cases:
            radio, writer = _make_radio(model), _FakeWriter()
            await WebServer(radio)._serve_capabilities(writer)  # noqa: SLF001
            data, facts = _parse_json_body(writer), browser_tx_audio_facts(radio)
            keys = ("audioTx", "audioTxRoute", "audioTxRequiredModInputSource")
            assert (
                tuple(data[key] for key in keys)
                == (facts.available, facts.route, facts.required_mod_input_source)
                == expected
            )


def _neutral_tx_radio(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "capabilities": {"audio", "tx"},
        "backend_id": "yaesu_cat",
        "has_usb_audio": True,
        "start_tx": AsyncMock(),
        "push_tx": AsyncMock(),
        "stop_tx": AsyncMock(),
    }
    return SimpleNamespace(**(values | changes))


def test_browser_tx_audio_requires_caps_and_complete_neutral_transport():
    cases = (
        ({}, True),
        ({"start_tx": None}, False),
        ({"push_tx": None}, False),
        ({"stop_tx": None}, False),
        ({"start_tx": None, "push_tx": None, "stop_tx": None}, False),
        ({"capabilities": {"audio"}}, False),
        ({"capabilities": {"tx"}}, False),
    )
    for changes, expected in cases:
        facts = browser_tx_audio_facts(_neutral_tx_radio(**changes))
        assert (facts.available, facts.route) == (
            (True, "usb") if expected else (False, None)
        )
    assert browser_tx_audio_facts(None).available is False


def test_browser_tx_audio_accepts_only_complete_selected_legacy():
    for codec, suffix in (
        (AudioCodec.PCM_1CH_16BIT, "pcm"),
        (AudioCodec.OPUS_1CH, "opus"),
    ):
        for missing in (None, "start", "push", "stop"):
            radio = _neutral_tx_radio(
                start_tx=None, push_tx=None, stop_tx=None, audio_codec=codec
            )
            for operation in ("start", "push", "stop"):
                setattr(radio, f"{operation}_audio_tx_{suffix}", AsyncMock())
            if missing:
                setattr(radio, f"{missing}_audio_tx_{suffix}", None)
            assert browser_tx_audio_facts(radio).available is (missing is None)


def test_browser_tx_audio_session_and_unknown_mod_source():
    radio = _neutral_tx_radio(
        capabilities={"audio", "tx", "mod_input_routing"},
        audio_session=SimpleNamespace(acquire_tx=AsyncMock()),
    )
    facts = browser_tx_audio_facts(radio)
    assert (facts.available, facts.required_mod_input_source) == (True, None)
    radio.audio_session.acquire_tx = None
    assert browser_tx_audio_facts(radio).available is False


def test_browser_tx_audio_route_exception_and_acc(monkeypatch):
    radio = _neutral_tx_radio()
    for source, expected in (
        (TxAudioSource.ACC, "acc"),
        (TxAudioSource.UNAVAILABLE, None),
    ):
        monkeypatch.setattr(
            "rigplane.web.handlers.audio.resolve_audio_route",
            lambda _radio, source=source: SimpleNamespace(tx_audio_source=source),
        )
        facts = browser_tx_audio_facts(radio)
        assert (facts.route if facts.available else None) == expected
    monkeypatch.setattr(
        "rigplane.web.handlers.audio.resolve_audio_route",
        lambda _radio: (_ for _ in ()).throw(RuntimeError("route failed")),
    )
    assert browser_tx_audio_facts(radio).available is False


@pytest.mark.asyncio
async def test_browser_tx_audio_denial_has_no_lifecycle_side_effects():
    radio = _neutral_tx_radio(push_tx=None, stop_tx=None)
    handler = AudioHandler(SimpleNamespace(send_text=AsyncMock()), radio)
    await handler._handle_control({"type": "audio_start", "direction": "tx"})
    assert (handler._tx_active, handler._tx_lease) == (False, None)
    radio.start_tx.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_tx_audio_failed_start_keeps_handler_inactive():
    radio = _neutral_tx_radio(
        start_tx=AsyncMock(side_effect=RuntimeError("start failed")),
    )
    handler = AudioHandler(SimpleNamespace(send_text=AsyncMock()), radio)
    handler._tx_active = True
    with pytest.raises(RuntimeError, match="start failed"):
        await handler._handle_control({"type": "audio_start", "direction": "tx"})
    assert handler._tx_active is False


# ── /api/v1/state tests ───────────────────────────────────────


class TestStateEndpoint:
    """Verify /api/v1/state adapts to receiver count."""

    @pytest.mark.asyncio
    async def test_state_dual_receiver_includes_sub(self):
        radio = _make_radio("IC-7610")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_state(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert "main" in data
        assert "sub" in data

    @pytest.mark.asyncio
    async def test_state_single_receiver_omits_sub(self):
        radio = _make_radio("IC-7300")
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_state(writer)  # noqa: SLF001
        data = _parse_json_body(writer)
        assert "main" in data
        assert "sub" not in data


# ── Command capability guards ──────────────────────────────────


class TestCommandGuards:
    """Commands for unsupported capabilities return clear error."""

    def test_dual_watch_guard_single_receiver(self):
        """set_dual_watch on IC-7300 (no dual_rx) should raise ValueError."""
        from rigplane.web.handlers import ControlHandler

        radio = _make_radio("IC-7300")
        handler = ControlHandler.__new__(ControlHandler)
        handler._radio = radio

        with pytest.raises(ValueError, match="dual_rx"):
            handler._ensure_capability("dual_rx", "set_dual_watch")

    def test_digisel_guard_ic7300(self):
        """set_digisel on IC-7300 (no digisel) should raise ValueError."""
        from rigplane.web.handlers import ControlHandler

        radio = _make_radio("IC-7300")
        handler = ControlHandler.__new__(ControlHandler)
        handler._radio = radio

        with pytest.raises(ValueError, match="digisel"):
            handler._ensure_capability("digisel", "set_digisel")

    def test_ip_plus_allowed_ic7300(self):
        """IC-7300 profile includes ip_plus (wfview CI-V 0x16 0x65)."""
        from rigplane.web.handlers import ControlHandler

        radio = _make_radio("IC-7300")
        handler = ControlHandler.__new__(ControlHandler)
        handler._radio = radio

        handler._ensure_capability("ip_plus", "set_ip_plus")

    def test_capability_passes_when_supported(self):
        """_ensure_capability does NOT raise for supported capabilities."""
        from rigplane.web.handlers import ControlHandler

        radio = _make_radio("IC-7610")
        handler = ControlHandler.__new__(ControlHandler)
        handler._radio = radio

        # Should not raise
        handler._ensure_capability("dual_rx", "set_dual_watch")
        handler._ensure_capability("dual_rx", "set_dual_watch")
