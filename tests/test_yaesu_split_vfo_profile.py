"""MOR-4427: real-loader contracts for a non-product single-RX fixture."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands.command_spec import AbsentCommandSpec
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import VfoEqualize, VfoSwap
from rigplane.web.runtime_helpers import projected_vfo_capability_tags
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake
from test_web_server import _http_get, _read_http_response

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"
SIM_FIXTURE = ROOT / "rigplane_testkit/sim/profiles/yaesu-single-rx-fixture.toml"


@pytest.fixture
def radio():
    result = YaesuCatRadio("/dev/null", profile=load_rig(FIXTURE))
    result._transport._connected = True
    result._transport.write = AsyncMock()
    result._transport.query = AsyncMock()
    return result


@pytest.mark.asyncio
async def test_split_uses_separate_write_and_answer_maps(radio):
    await radio.set_split(True)
    await radio.set_split(False)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "FT3;",
        "FT2;",
    ]
    radio._transport.query.side_effect = ["FT1", "FT0"]
    assert await radio.read_split() is True
    assert await radio.get_split() is False
    assert [c.args[0] for c in radio._transport.query.call_args_list] == ["FT;", "FT;"]
    assert radio.receiver_count == radio.transceiver_count == 1


@pytest.mark.asyncio
async def test_direct_vfo_commands_and_absent_selection(radio):
    await radio.swap_vfo_ab()
    await radio.vfo_a_to_b()
    await radio.vfo_b_to_a()
    await radio.set_freq(14_074_000)
    await radio.set_freq(14_080_000, receiver=1)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "SV;",
        "AB;",
        "BA;",
        "FA014074000;",
        "FB014080000;",
    ]
    for operation in (
        radio.get_vfo_slot(),
        radio.set_vfo_slot("B"),
        radio.get_vfo_select(),
        radio.set_vfo_select(1),
    ):
        with pytest.raises(NotImplementedError):
            await operation
    assert await radio.get_active_receiver() == 0
    assert not radio.supports_command("get_vfo_select")
    assert not radio.supports_command("get_vfo_slot")
    assert radio.supports_command("swap_vfo_ab")
    assert radio._transport.query.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement", ["", 'vfo_swap = { absent = "Fixture variant" }']
)
async def test_missing_swap_follows_absent_convention(tmp_path, replacement):
    path = tmp_path / "fixture.toml"
    path.write_text(
        FIXTURE.read_text().replace(
            'vfo_swap = { cat = { write = "SV;" } }', replacement
        )
    )
    config = load_rig(path)
    if replacement:
        assert isinstance(config.commands["vfo_swap"], AbsentCommandSpec)
    radio = YaesuCatRadio("/dev/null", profile=config)
    with pytest.raises(NotImplementedError):
        await radio.swap_vfo_ab()
    assert not radio.supports_command("swap_vfo_ab")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile,select_read", [(FIXTURE, False), (ROOT / "rigs/ftx1.toml", True)]
)
async def test_poller_only_reads_declared_vfo_selection(profile, select_read):
    radio = YaesuCatRadio("/dev/null", profile=load_rig(profile))
    radio._transport._connected = True
    wire: list[str] = []

    async def query(command, **kwargs):
        wire.append(command)
        return {"VS;": "VS0", "FT;": "FT0", "ST;": "ST0"}.get(command, "?")

    radio._transport.query = query
    await YaesuCatPoller(radio)._poll_slow()
    assert ("VS;" in wire) is select_read
    if not select_read:
        assert "FR;" not in wire


@pytest.mark.asyncio
async def test_ftx1_identity_maps_and_profile_transceiver_count():
    config = load_rig(ROOT / "rigs/ftx1.toml")
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    radio._transport.query = AsyncMock(side_effect=["ST1", "ST0"])
    assert config.transceiver_count == radio.transceiver_count == 2
    await radio.set_split(True)
    await radio.set_split(False)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "ST1;",
        "ST0;",
    ]
    assert await radio.get_split() is True
    assert await radio.get_split() is False
    assert [c.args[0] for c in radio._transport.query.call_args_list] == ["ST;", "ST;"]
    with pytest.raises(NotImplementedError):
        await radio.swap_vfo_ab()


@pytest.mark.parametrize(
    "replacement,match",
    [
        ('write_map = { off = "2" }', "write_map"),
        ('write_map = { off = "2", on = 3 }', "write_map"),
        ('write_map = { off = "2", on = "2" }', "write_map"),
        ('read_map = { "0" = "off" }', "read_map"),
        ('read_map = { "0" = "off", "1" = "maybe" }', "read_map"),
        ('read_map = ["off", "on"]', "read_map"),
    ],
)
def test_split_validation(tmp_path, replacement, match):
    text = FIXTURE.read_text()
    key = replacement.split(" =", 1)[0]
    lines = [
        replacement if line.startswith(key + " =") else line
        for line in text.splitlines()
    ]
    path = tmp_path / "invalid.toml"
    path.write_text("\n".join(lines))
    with pytest.raises(RigLoadError, match=match):
        load_rig(path)


def test_split_contract_is_typed_and_consumed():
    config = load_rig(FIXTURE)
    assert config.split.write_map == {False: "2", True: "3"}
    assert config.split.read_map == {"0": False, "1": True}
    radio = YaesuCatRadio("/dev/null", profile=config)
    # MOR-4433 follow-up A: the fixture declares vfo_a_to_b, so the shared
    # admission rule projects vfo_equalize alongside vfo_swap.
    assert projected_vfo_capability_tags(radio, None) == {
        "vfo_swap",
        "vfo_equalize",
    }


def test_transceiver_count_comes_from_profile_without_model_branch(tmp_path):
    path = tmp_path / "two-transceivers.toml"
    path.write_text(
        FIXTURE.read_text().replace("[radio]", "[radio]\ntransceiver_count = 2")
    )
    radio = YaesuCatRadio("/dev/null", profile=load_rig(path))
    assert radio.transceiver_count == 2


def test_web_admits_declared_cat_ab_swap(radio):
    # MOR-4433 follow-up A: the fixture declares vfo_a_to_b, so the web
    # layer admits vfo_equalize exactly as it projects it (the refusal
    # lives on in test_yaesu_ab_equalize_refused_without_the_copy_primitive
    # on a variant without the primitive).
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    queued: list[object] = []
    queue = SimpleNamespace(put=queued.append)
    assert handler._enqueue_rc_frequency("vfo_swap", {}, queue, radio) == {}
    assert handler._enqueue_rc_frequency("vfo_equalize", {}, queue, radio) == {}
    assert len(queued) == 2
    assert isinstance(queued[0], VfoSwap)
    assert isinstance(queued[1], VfoEqualize)


@pytest.mark.asyncio
async def test_yaesu_poller_dispatches_cat_ab_swap(radio):
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    await poller._execute_command(VfoSwap())
    assert [c.args[0] for c in radio._transport.write.await_args_list] == ["SV;"]


@pytest.mark.asyncio
async def test_yaesu_ab_equalize_admits_through_declared_copy(tmp_path):
    """MOR-4433 follow-up A replaces the MOR-4427 R2 family-wide refusal:
    projection, admission and execution agree that Yaesu ``ab`` exposes
    ``vfo_equalize`` exactly when the profile declares the ``vfo_a_to_b``
    copy primitive (equalize is that A→B copy). A declared ``vfo_equalize``
    command entry is not required and not consulted; a profile without the
    copy primitive keeps the refusal.
    """
    config = load_rig(FIXTURE)
    assert config.to_profile().supports_vfo_command("vfo_equalize")
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    # projection
    assert radio.profile.supports_vfo_command("vfo_swap")
    assert radio.profile.supports_vfo_command("vfo_equalize")
    assert projected_vfo_capability_tags(radio, None) == {
        "vfo_swap",
        "vfo_equalize",
    }
    # admission
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    queued: list[object] = []
    queue = SimpleNamespace(put=queued.append)
    assert handler._enqueue_rc_frequency("vfo_swap", {}, queue, radio) == {}
    assert handler._enqueue_rc_frequency("vfo_equalize", {}, queue, radio) == {}
    assert len(queued) == 2
    assert isinstance(queued[0], VfoSwap)
    assert isinstance(queued[1], VfoEqualize)
    # execution agrees with admission: equalize writes the A->B copy
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    await poller._execute_command(VfoSwap())
    await poller._execute_command(VfoEqualize())
    assert [c.args[0] for c in radio._transport.write.await_args_list] == [
        "SV;",
        "AB;",
    ]


@pytest.mark.asyncio
async def test_yaesu_ab_equalize_refused_without_the_copy_primitive(tmp_path):
    """The same shared rule, negative side: no ``vfo_a_to_b`` declaration
    means no projection, no admission and no execution."""
    path = tmp_path / "no-copy.toml"
    # Drop both directional-copy declarations with their exact file text:
    # a whitespace-mismatched replace would silently keep the primitive
    # and the variant would still admit equalize.
    text = FIXTURE.read_text()
    text = text.replace('vfo_a_to_b = { cat = { write = "AB;" } }', "")
    text = text.replace('vfo_b_to_a = { cat = { write = "BA;" } }', "")
    path.write_text(text)
    config = load_rig(path)
    assert not config.to_profile().supports_vfo_command("vfo_equalize")
    radio = YaesuCatRadio("/dev/null", profile=config)
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    assert projected_vfo_capability_tags(radio, None) == {"vfo_swap"}
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    queued: list[object] = []
    queue = SimpleNamespace(put=queued.append)
    with pytest.raises(ValueError, match="not supported"):
        handler._enqueue_rc_frequency("vfo_equalize", {}, queue, radio)
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    with pytest.raises(NotImplementedError, match="vfo_a_to_b"):
        await poller._execute_command(VfoEqualize())


@pytest.mark.asyncio
async def test_fixture_split_and_vfo_roundtrip_over_cat_transport(monkeypatch):
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=FIXTURE)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line):
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(FIXTURE))
    await radio._transport.connect()
    try:
        for enabled in (True, False):
            await radio.set_split(enabled)
            assert await radio.get_split() is enabled
        await radio.set_freq(14_074_000)
        await radio.set_freq(14_080_000, receiver=1)
        await radio.swap_vfo_ab()
        assert await radio.get_freq() == 14_080_000
        assert await radio.get_freq(1) == 14_074_000
        await radio.vfo_a_to_b()
        assert await radio.get_freq(1) == 14_080_000
        await radio.set_freq(14_090_000, receiver=1)
        await radio.vfo_b_to_a()
        assert await radio.get_freq() == 14_090_000
        await YaesuCatPoller(radio)._poll_slow()
        assert not any(cmd.startswith(("VS", "FR", "ST")) for cmd in wire)
        for command in ("VS;", "FR;", "ST;", "VS1;", "FR1;", "ST1;"):
            assert fake.model.handle(command) == "?;"
    finally:
        await radio._transport.close()


async def _http_post_command(host: str, port: int, name: str, params: dict) -> dict:
    """POST one command to /api/v1/commands over a real socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        payload = json.dumps({"name": name, "params": params}).encode()
        writer.write(
            (
                "POST /api/v1/commands HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        status, _, response = await asyncio.wait_for(_read_http_response(reader), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    assert status == 200
    body = json.loads(response)
    assert body["ok"] is True
    return body


@pytest.mark.realtime(
    reason="real HTTP socket and the production backend poller/readback path"
)
@pytest.mark.asyncio
async def test_vfo_swap_end_to_end_over_http_with_cat_sim(tmp_path, monkeypatch):
    """MOR-4427 R2 P1-2: ``vfo_swap`` through the real local HTTP API.

    The real ``WebServer`` serves a real fixture-profiled ``YaesuCatRadio``
    on the CAT simulator: the suite posts ``vfo_swap`` to
    ``/api/v1/commands`` over a real socket (real handler, real queue,
    real backend-poller dispatch, real post-write readback — no
    constructor bypasses, no mocked readback), then reads the exchanged
    frequencies back from the user-visible ``/api/v1/state``.

    No existing harness serves this fixture over HTTP — the web-server
    tests use mock/Icom radios and the sim tests stop at the radio layer
    — so this test composes the two proven pieces (``test_web_server``
    HTTP helpers + the CAT-sim wiring above). The acquisition section is
    a test-local variant append: the observation adapter that feeds
    ``/api/v1/state`` requires ``[state_acquisition]`` metadata, which the
    committed fixture does not carry.
    """
    rig_path = tmp_path / "swap-e2e.toml"
    rig_path.write_text(
        FIXTURE.read_text() + "\n[state_acquisition]\n"
        'provider = "yaesu_cat"\n'
        "\n[state_acquisition.capabilities]\n"
        "polling_only = [\n"
        '    "receiver.main.active.freq_mode.freq_hz",\n'
        "]\n"
        "command_response_observable = [\n"
        '    "receiver.main.active.freq_mode.freq_hz",\n'
        "]\n"
    )
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(rig_path))
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=rig_path)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line):
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    await radio._transport.connect()
    try:
        await radio.set_freq(14_074_000)
        await radio.set_freq(14_080_000, receiver=1)
        server = WebServer(
            radio,
            WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
        )
        await server.start()
        try:
            host, port = "127.0.0.1", server.port

            async def main_freq_hz() -> int | None:
                status, _, body = await _http_get(host, port, "/api/v1/state")
                assert status == 200
                return json.loads(body).get("main", {}).get("freqHz")

            async def wait_for_freq_hz(want: int) -> None:
                deadline = time.monotonic() + 10.0
                while await main_freq_hz() != want:
                    if time.monotonic() >= deadline:
                        raise AssertionError(f"state main.freqHz never became {want}")
                    await asyncio.sleep(0.1)

            # the observation path is live before the swap is submitted
            await wait_for_freq_hz(14_074_000)

            reader, writer = await asyncio.open_connection(host, port)
            try:
                payload = json.dumps(
                    {"id": "swap-e2e", "name": "vfo_swap", "params": {}}
                ).encode()
                writer.write(
                    (
                        "POST /api/v1/commands HTTP/1.1\r\n"
                        f"Host: {host}:{port}\r\n"
                        "Connection: close\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(payload)}\r\n\r\n"
                    ).encode()
                    + payload
                )
                await writer.drain()
                status, _, response = await asyncio.wait_for(
                    _read_http_response(reader), 10
                )
            finally:
                writer.close()
                await writer.wait_closed()
            assert status == 200
            assert json.loads(response)["ok"] is True
            assert "SV;" in wire
            # the exchanged frequencies surface in the user-visible state
            await wait_for_freq_hz(14_080_000)
        finally:
            await server.stop()
    finally:
        await radio._transport.close()


def test_sim_applies_frequency_domain_to_inactive_slot_writes():
    """MOR-4427 R2 P2: the single-receiver inactive VFO slot enforces the
    same frequency domain as VFO A (30 kHz–470 MHz from the fixture
    ranges); a refused write leaves the slot unchanged."""
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=FIXTURE)
    model = fake.model
    assert model.handle("FA;") == "FA014074000;"
    assert model.handle("FB;") == "FB014074000;"
    # above the max and below the floor are refused on both slots ...
    assert model.handle("FB999999999;") == "?;"
    assert model.handle("FB000029999;") == "?;"
    assert model.handle("FA999999999;") == "?;"
    assert model.handle("FA;") == "FA014074000;"
    assert model.handle("FB;") == "FB014074000;"
    # ... while the domain edges are accepted on both slots.
    assert model.handle("FB470000000;") is None
    assert model.handle("FB;") == "FB470000000;"
    assert model.handle("FA000030000;") is None
    assert model.handle("FA;") == "FA000030000;"


async def _serve_fixture_over_http(tmp_path, monkeypatch, extra_features=()):
    """Serve the single-RX fixture over HTTP on the CAT simulator.

    Returns ``(radio, wire, host, port, server)`` with the server started;
    the caller stops the server and closes the transport. The observation
    path polls frequency, mode, narrow state and filter width so the
    public state carries all four. ``extra_features`` appends [capabilities]
    feature tags to the test-local variant (the harness default keeps the
    caller's existing wire logs untouched).
    """
    features = ["split", "vfo_swap", "narrow", *extra_features]
    rig_path = tmp_path / "http-e2e.toml"
    rig_path.write_text(
        FIXTURE.read_text().replace(
            'features = ["split", "vfo_swap"]',
            f"features = {features!r}".replace("'", '"'),
        )
        + "\n[state_acquisition]\n"
        'provider = "yaesu_cat"\n'
        "\n[state_acquisition.capabilities]\n"
        "polling_only = [\n"
        '    "receiver.main.active.freq_mode.freq_hz",\n'
        '    "receiver.main.active.freq_mode.mode",\n'
        '    "receiver.main.operator_toggles.narrow",\n'
        '    "receiver.main.active.freq_mode.filter_width",\n'
        "]\n"
        "command_response_observable = [\n"
        '    "receiver.main.active.freq_mode.freq_hz",\n'
        "]\n"
    )
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(rig_path))
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=rig_path)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line):
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    await radio._transport.connect()
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    return radio, wire, "127.0.0.1", server.port, server


async def _wait_for_state(host: str, port: int, key: str, want: object) -> None:
    deadline = time.monotonic() + 10.0
    while True:
        status, _, body = await _http_get(host, port, "/api/v1/state")
        assert status == 200
        if json.loads(body).get("main", {}).get(key) == want:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"state main.{key} never became {want!r}")
        await asyncio.sleep(0.1)


@pytest.mark.realtime(
    reason="real HTTP socket and the production backend poller/readback path"
)
@pytest.mark.asyncio
async def test_vfo_equalize_end_to_end_over_http_with_cat_sim(tmp_path, monkeypatch):
    """MOR-4433: ``vfo_equalize`` through the real local HTTP API.

    The real ``WebServer`` serves a fixture-profiled ``YaesuCatRadio``
    on the CAT simulator: the suite posts ``vfo_equalize`` to
    ``/api/v1/commands`` over a real socket (real handler, real queue,
    real backend-poller dispatch, real post-write readback), then proves
    the A->B copy landed by retuning A away and swapping: the swapped-in
    VFO A is the copied frequency, read back from public state.
    """
    radio, wire, host, port, server = await _serve_fixture_over_http(
        tmp_path, monkeypatch
    )
    try:
        await radio.set_freq(14_074_000)
        await radio.set_freq(14_080_000, receiver=1)
        await _wait_for_state(host, port, "freqHz", 14_074_000)

        await _http_post_command(host, port, "vfo_equalize", {})
        assert "AB;" in wire

        # The copy is on the inactive slot: move A away, swap the copy in.
        await radio.set_freq(14_090_000)
        await _wait_for_state(host, port, "freqHz", 14_090_000)
        await _http_post_command(host, port, "vfo_swap", {})
        # A held 14090000, B the equalized 14074000: the swap surfaces it.
        await _wait_for_state(host, port, "freqHz", 14_074_000)
    finally:
        await server.stop()
        await radio._transport.close()


@pytest.mark.realtime(
    reason="real HTTP socket and the production backend poller/readback path"
)
@pytest.mark.asyncio
async def test_narrow_width_end_to_end_over_http_with_cat_sim(tmp_path, monkeypatch):
    """MOR-4433: narrow-state width admission through the real HTTP API.

    Submits ``set_filter_width`` in the wide state (the response admits
    the nearest wide-table entry), enables NARROW, then submits a
    narrow-table width: the response carries the narrow ``admitted_width``
    and the width reads back narrow over the CAT wire. Real handler, real queue,
    real dispatch and readback throughout.
    """
    radio, wire, host, port, server = await _serve_fixture_over_http(
        tmp_path, monkeypatch
    )
    try:
        # Mode and narrow flag are freshly observed before the first
        # submission: the admission hint maps through live state.
        await _wait_for_state(host, port, "mode", "USB")
        await _wait_for_state(host, port, "narrow", False)

        # Wide state: 1500 Hz is not on the wide table ([2400, ...]), so
        # the response admits the nearest wide entry.
        body = await _http_post_command(host, port, "set_filter_width", {"width": 1500})
        assert body["result"]["admitted_width"] == 2400

        await _http_post_command(host, port, "set_narrow", {"on": True})
        assert "NA01;" in wire
        await _wait_for_state(host, port, "narrow", True)

        body = await _http_post_command(host, port, "set_filter_width", {"width": 1800})
        assert body["result"]["admitted_width"] == 1800
        assert "SH0001;" in wire
        # The fixture profile declares no width polling, so the public
        # state carries no filterWidth; read the width back through the
        # real radio over the CAT wire instead (SH answer mapped through
        # the narrow table because NARROW is on).
        reading = await radio.read_filter_width_reading(0, mode="USB")
        assert reading.hz == 1800
    finally:
        await server.stop()
        await radio._transport.close()


@pytest.mark.realtime(
    reason="real HTTP socket and the production backend poller/readback path"
)
@pytest.mark.asyncio
async def test_narrow_table_end_to_end_over_http_with_cat_sim(tmp_path, monkeypatch):
    """MOR-4434: the narrow/wide filter tables through the real HTTP API.

    The seam the browser drives: ``/api/v1/capabilities`` publishes the
    mode's narrow table beside the wide one, the frontend resolver's
    active-table rule (observed narrow on with a non-empty narrow table
    selects it, anything else keeps the wide table) is replayed here
    against that payload, and ``set_filter_width`` is submitted in both
    states — pinning both the admitted-width hint and the public
    ``main.filterWidth`` the poller reads back over the CAT wire.
    """
    # The width readback needs the filter_width runtime tag: the shared
    # harness omits it (its wire logs pin no SH polling), so this test
    # serves its own variant with the tag appended.
    radio, wire, host, port, server = await _serve_fixture_over_http(
        tmp_path, monkeypatch, extra_features=("filter_width",)
    )
    try:
        status, _, body = await _http_get(host, port, "/api/v1/capabilities")
        assert status == 200
        caps = json.loads(body)
        ssb = caps["filterConfig"]["SSB"]
        assert ssb["table"] == [2400, 2600, 3000, 3200, 4000]
        assert ssb["narrowTable"] == [1500, 1800, 2000]

        def active_table(caps_doc: dict, narrow: bool | None) -> list:
            # The frontend resolver's rule, replayed against the served
            # payload (panel-props.ts ``resolveFilterModeConfig``): USB
            # resolves to the SSB entry through the shared fallback, and
            # only a freshly observed narrow-on with a declared narrow
            # table selects it.
            rule = caps_doc["filterConfig"]["SSB"]
            if narrow is True and rule.get("narrowTable"):
                return rule["narrowTable"]
            return rule["table"]

        await _wait_for_state(host, port, "mode", "USB")
        await _wait_for_state(host, port, "narrow", False)
        assert active_table(caps, False) == [2400, 2600, 3000, 3200, 4000]

        # Wide state: a wide-only width commits and reads back over the wire.
        body = await _http_post_command(host, port, "set_filter_width", {"width": 2600})
        assert body["result"]["admitted_width"] == 2600
        assert "SH0001;" in wire
        await _wait_for_state(host, port, "filterWidth", 2600)

        # Narrow state: the narrow-only width is accepted (not quantized to
        # the wide table) and reads back narrow.
        await _http_post_command(host, port, "set_narrow", {"on": True})
        await _wait_for_state(host, port, "narrow", True)
        assert active_table(caps, True) == [1500, 1800, 2000]
        body = await _http_post_command(host, port, "set_filter_width", {"width": 1500})
        assert body["result"]["admitted_width"] == 1500
        assert "SH0000;" in wire
        await _wait_for_state(host, port, "filterWidth", 1500)
    finally:
        await server.stop()
        await radio._transport.close()
