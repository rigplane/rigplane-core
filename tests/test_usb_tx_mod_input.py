from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.types import CivFrame, bcd_encode
from rigplane.diagnostics.contributor import BundleContext
from rigplane.diagnostics.contributors.logs import LogsContributor
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import (
    ManagedTxEffectLane,
    TxModInputRefusal,
)
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind, ManagedTxOutcome
from rigplane.runtime.radio import IcomRadio
from rigplane.web.handlers.control import ControlHandler
from rigplane_testkit.leaked_radio_guard import fake_connected


class UsbSimulatorRadio(IcomRadio):
    @property
    def backend_id(self) -> str:
        return "icom_serial"


class ModInputSimulator:
    def __init__(self, radio: IcomRadio) -> None:
        self.radio = radio
        self.split = False
        self.data = [0, 0]
        self.sources = [0, 0, 0, 0]
        self.reads: list[str] = []
        self.keys: list[bool] = []
        self.failure = False
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def read(self, raw: bytes, **_kwargs: object) -> CivFrame:
        frame = parse_civ_frame(raw)
        if frame.command == 0x0F:
            payload = bytes([int(self.split)])
        elif frame.command == 0x25:
            payload = frame.data + bcd_encode(14_200_000)
        elif frame.command == 0x26:
            payload = frame.data + bytes([1, self.data[frame.data[0]], 1])
        else:
            getters = ["get_data_off_mod_input"] + [
                f"get_data{index}_mod_input" for index in range(1, 4)
            ]
            name = next(
                name
                for name in getters
                if self.radio.supports_command(name)
                and raw
                == getattr(self.radio._commands, name)(to_addr=self.radio._radio_addr)
            )
            self.reads.append(name)
            self.entered.set()
            if self.release is not None:
                await self.release.wait()
            if self.failure:
                raise ValueError("simulated unreadable MOD reply")
            payload = frame.data + bytes([self.sources[getters.index(name)]])
        return parse_civ_frame(
            build_civ_frame(
                CONTROLLER_ADDR,
                self.radio._radio_addr,
                frame.command,
                sub=frame.sub,
                data=payload,
            )
        )

    async def write(
        self, raw: bytes, *, is_current: Callable[[], bool], **_kwargs: object
    ) -> CivFrame:
        frame = parse_civ_frame(raw)
        if is_current() and frame.command == 0x1C and frame.sub == 0:
            self.keys.append(frame.data == b"\x01")
        return CivFrame(
            to_addr=CONTROLLER_ADDR, from_addr=self.radio._radio_addr, command=0xFB
        )


@pytest.fixture
async def setup_radio(monkeypatch, tmp_path, virtual_clock):
    authorities = []

    def create(model="IC-705"):
        radio = fake_connected(UsbSimulatorRadio("", model=model))
        sim = ModInputSimulator(radio)
        monkeypatch.setattr(radio, "_send_civ_expect", sim.read)
        monkeypatch.setattr(radio, "_send_civ_raw", sim.write)
        authority = ManagedTxAuthority(
            ManagedTxEffectLane(radio, clock=virtual_clock),
            ManagedTxTotConfigStore(tmp_path / f"{len(authorities)}.json"),
            TxAbortFence(),
            provider_generation=1,
            clock=virtual_clock,
        )
        authorities.append(authority)
        return radio, sim, authority

    yield create
    for authority in authorities:
        await authority.force_off()
        await authority.close()


@pytest.mark.parametrize(
    "model,usb,register",
    [
        ("IC-705", 1, b"\x01\x18"),
        ("IC-7300", 3, b"\x00\x66"),
        ("IC-7300MK2", 1, b"\x00\x84"),
        ("IC-7610", 3, b"\x00\x91"),
        ("IC-905", 1, b"\x01\x26"),
        ("IC-9700", 3, b"\x01\x15"),
    ],
)
async def test_six_profile_mappings_refuse_then_retry(
    setup_radio, model, usb, register
):
    radio, sim, authority = setup_radio(model)
    assert (
        parse_civ_frame(
            radio._commands.get_data_off_mod_input(to_addr=radio._radio_addr)
        ).data
        == register
    )
    with pytest.raises(TxModInputRefusal, match="Set the radio's MOD input to USB"):
        await authority.submit_ptt(True, "local")
    assert sim.keys == []
    state = (await authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.pending_effect is None and not state.release_required
    sim.sources[0] = usb
    receipt = await authority.submit_ptt(True, "local")
    await receipt.wait_settlement()
    assert receipt.outcome is ManagedTxOutcome.ACCEPTED
    assert sim.keys == [True]


@pytest.mark.parametrize(
    "split,data,group",
    [
        (False, [1, 0], 1),
        (True, [0, 1], 1),
        (True, [1, 0], 0),
        (True, [0, 2], 2),
        (True, [0, 3], 3),
    ],
)
async def test_transmitting_vfo_data_group(setup_radio, split, data, group):
    _, sim, authority = setup_radio("IC-7610")
    sim.split, sim.data = split, data
    sim.sources[group] = 3
    receipt = await authority.submit_ptt(True, "local")
    await receipt.wait_settlement()
    assert sim.reads == [
        "get_data_off_mod_input" if group == 0 else f"get_data{group}_mod_input"
    ]
    assert sim.keys == [True]


@pytest.mark.parametrize(
    "source,failure,expected",
    [(3, False, "WLAN"), (9, False, "unknown (9)"), (0, True, None)],
)
async def test_refusal_reason_and_wire(setup_radio, source, failure, expected):
    _, sim, authority = setup_radio()
    sim.sources[0], sim.failure = source, failure
    with pytest.raises(TxModInputRefusal) as caught:
        await authority.submit_ptt(True, "local")
    assert caught.value.source == expected
    assert caught.value.as_wire()["message"] == str(caught.value)
    assert not sim.keys


async def test_cancel_read_and_retry_leaves_no_effect(setup_radio):
    _, sim, authority = setup_radio()
    sim.release = asyncio.Event()
    task = authority.start_ptt_submission(True, "local")
    await sim.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = (await authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.pending_effect is None and not state.release_required
    assert not authority._abort_fence._cancellations
    assert not sim.keys
    sim.release.set()
    sim.sources[0] = 1
    receipt = await authority.submit_ptt(True, "local")
    await receipt.wait_settlement()
    assert sim.keys == [True]


async def test_unkey_during_read_prevents_late_key(setup_radio):
    _, sim, authority = setup_radio()
    sim.sources[0] = 1
    sim.release = asyncio.Event()
    task = authority.start_ptt_submission(True, "local")
    await sim.entered.wait()
    await authority.submit_ptt(False, "local")
    sim.release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert True not in sim.keys
    assert (await authority.snapshot()).state.pending_effect is None


async def test_web_failure_preserves_typed_reason(setup_radio):
    radio, sim, authority = setup_radio()
    sim.sources[0] = 3
    handler = ControlHandler(None, radio, "test", "IC-705")
    sent = []

    async def capture(raw):
        sent.append(json.loads(raw))

    handler._send_direct = capture
    with pytest.raises(TxModInputRefusal) as caught:
        await authority.submit_ptt(True, "local")
    await handler._send_command_failure("voice", "ptt_on", caught.value)
    assert sent[0]["error"] == "tx_mod_input"
    assert sent[0]["source"] == "WLAN"
    assert sent[0]["message"] == str(caught.value)


async def test_support_log_contains_same_reason(setup_radio, caplog, tmp_path):
    _, sim, authority = setup_radio()
    sim.sources[0] = 3
    with pytest.raises(TxModInputRefusal) as caught:
        await authority.submit_ptt(True, "local")
    assert str(caught.value) in caplog.text
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "rigplane.log").write_text(caplog.text)
    output = tmp_path / "bundle"
    output.mkdir()
    context = BundleContext(
        radio=None,
        config_dir=tmp_path,
        log_dir=log_dir,
        user_description=None,
        issue_ref=None,
        contact_email=None,
        contact_callsign=None,
        submission_id="simulator",
        generated_at_unix=0,
    )
    LogsContributor().contribute(context, output)
    assert str(caught.value) in (output / "rigplane.log").read_text()


async def test_remote_controller_refusal_retry(setup_radio, virtual_clock):
    _, sim, authority = setup_radio()

    async def cleanup():
        await authority.force_off()

    controller = ControllerAuthority(
        ready=lambda: True, cleanup=cleanup, clock=virtual_clock
    )
    authority.bind_controller(controller)
    await controller.set_mode("remote")
    grant = controller.acquire("pro")
    ticket = controller.attach(grant["controller_key"], "primary", "remote-voice")
    sim.sources[0] = 3
    try:
        with controller.bind(ticket):
            with pytest.raises(TxModInputRefusal, match="WLAN"):
                await authority.submit_ptt(True, "remote-voice")
            assert not sim.keys
            sim.sources[0] = 1
            receipt = await authority.submit_ptt(True, "remote-voice")
            await receipt.wait_settlement()
            assert receipt.outcome is ManagedTxOutcome.ACCEPTED
            assert sim.keys == [True]
    finally:
        await controller.release(grant["controller_key"])


async def test_latch_refuses_before_key(setup_radio):
    _, sim, authority = setup_radio()
    with pytest.raises(TxModInputRefusal):
        await authority.submit_transmit_on()
    assert not sim.keys
    sim.sources[0] = 1
    receipt = await authority.submit_transmit_on()
    await receipt.wait_settlement()
    assert sim.keys == [True]


async def test_read_timeout_refuses_and_cleans(setup_radio, virtual_clock):
    _, sim, authority = setup_radio()
    sim.release = asyncio.Event()
    task = authority.start_ptt_submission(True, "local")
    await sim.entered.wait()
    await virtual_clock.elapse(3.1)
    with pytest.raises(TxModInputRefusal) as caught:
        await task
    assert caught.value.source is None
    assert not sim.keys
    state = (await authority.snapshot()).state
    assert state.pending_effect is None and not state.release_required
    assert not authority._abort_fence._cancellations


@pytest.mark.parametrize("model", ["FTX-1", "FT-991A", "X6200", "G7250"])
async def test_profile_without_mod_input_skips_read(setup_radio, model):
    radio, sim, _ = setup_radio()
    radio._profile = resolve_radio_profile(model=model)
    assert radio._profile.data_mode_inputs is None
    await radio.check_tx_audio_input()
    assert sim.reads == []


async def test_lan_route_skips_usb_check(setup_radio, monkeypatch):
    radio, sim, _ = setup_radio()
    monkeypatch.setattr(UsbSimulatorRadio, "backend_id", IcomRadio.backend_id)
    await radio.check_tx_audio_input()
    assert sim.reads == []


@pytest.mark.parametrize("ingress", ["ptt", "latch"])
async def test_web_queue_refusal_and_retry(setup_radio, monkeypatch, ingress):
    from rigplane.runtime._poller_types import (
        execute_command_queue_entry,
        execute_positive_tx_queue_entry,
    )
    from rigplane.web.server import WebConfig, WebServer
    from test_managed_tx_http_route import _Writer, _reader

    radio, sim, authority = setup_radio()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    monkeypatch.setattr(server, "_managed_tx_authority", lambda: authority)
    server.command_queue.bind_connection_generation(lambda: "simulator")
    handler = ControlHandler(
        None,
        radio,
        "test",
        "IC-705",
        server=server,
        managed_tx_authority=authority,
        tx_owner="local",
    )
    enqueued = asyncio.Event()
    original_put = server.command_queue.put_ordered

    def put(*args, **kwargs):
        result = original_put(*args, **kwargs)
        enqueued.set()
        return result

    monkeypatch.setattr(server.command_queue, "put_ordered", put)
    for source, accepted in [(3, False), (1, True)]:
        sim.sources[0] = source
        enqueued.clear()
        writer = _Writer()
        if ingress == "ptt":
            task = asyncio.create_task(handler._enqueue_command("ptt_on", {}))
        else:
            reader, headers = _reader({"operation": "transmit_on"})
            task = asyncio.create_task(
                server._handle_http_managed_tx(
                    "/api/v1/managed-transmit/command",
                    writer,
                    headers,
                    reader,
                )
            )
        await enqueued.wait()
        entry = server.command_queue.take_entry()
        assert entry is not None
        try:
            await execute_command_queue_entry(entry, execute_positive_tx_queue_entry)
        except TxModInputRefusal:
            assert not accepted
        if ingress == "ptt" and not accepted:
            with pytest.raises(TxModInputRefusal, match="WLAN"):
                await task
        else:
            await task
        if ingress == "latch":
            assert writer.status == (202 if accepted else 409)
            if not accepted:
                assert writer.payload == TxModInputRefusal("WLAN").as_wire()
        assert sim.keys == ([True] if accepted else [])
