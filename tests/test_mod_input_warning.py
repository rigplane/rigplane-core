"""USB voice MOD-input warning at admission, on CI-V, and over local WS."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_authority import ManagedTxSubmission
from rigplane.runtime.managed_tx_state import (
    ActuationSettled,
    ActuationOperation,
    EffectToken,
    ManagedTxOutcome,
    ManagedTxState,
    ManagedTxTransition,
    PttDown,
    PttUp,
    reduce_managed_tx,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.testing.timing import await_until
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.mod_input_warning import (
    MOD_INPUT_NOT_USB_CODE,
    MOD_INPUT_UNREADABLE_CODE,
    warn_when_mod_input_blocks_voice,
)
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from rigplane.ws import WebSocketConnection
from rigplane_testkit.serial import CivSerialFake
from support.command_consumer import run_command_consumer

_LOGGER_NAME = "rigplane.web.mod_input_warning"

_SIMULATOR_ROWS = (
    ("IC-705", 0xA4, b"\x01\x18", 3, 1, "WLAN"),
    ("IC-7300", 0x94, b"\x00\x66", 0, 3, "MIC"),
    ("IC-9700", 0xA2, b"\x01\x15", 1, 3, "ACC"),
)


class _Authority:
    def __init__(self) -> None:
        self.next_outcome = ManagedTxOutcome.ACCEPTED
        self.state = ManagedTxState()

    def start_ptt_submission(
        self,
        on: bool,
        owner: str,
        *,
        ready: asyncio.Future[None],
        expires_at_monotonic: float,
    ) -> asyncio.Task[ManagedTxSubmission]:
        async def complete() -> ManagedTxSubmission:
            await ready
            return await self.submit_ptt(on, owner)

        return asyncio.create_task(complete())

    async def submit_ptt(self, on: bool, owner: str) -> ManagedTxSubmission:
        if self.next_outcome is ManagedTxOutcome.ACCEPTED:
            event = (
                PttDown(owner, 1, "test", 0, None) if on else PttUp(owner, 1, "test")
            )
            transition = reduce_managed_tx(self.state, event)
            self.state = transition.state
        else:
            transition = ManagedTxTransition(self.state, self.next_outcome)

        async def settle() -> tuple[ManagedTxTransition, ActuationSettled | None]:
            return transition, None

        return ManagedTxSubmission(transition, asyncio.create_task(settle()))


class _Toasts:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str | None, dict[str, Any] | None]] = []

    def __call__(
        self,
        session_id: str,
        level: str,
        message: str,
        category: str = "system",
        *,
        code: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> bool:
        self.sent.append((level, message, category, code, params))
        return True

    def codes(self) -> list[str | None]:
        return [row[3] for row in self.sent]


def _seeded_store(
    *,
    mode: str | None = "USB",
    data_mode: int | None = 0,
    split: bool | None = False,
    active_slot: str | None = "A",
    unselected_mode: str | None = None,
    stale: bool = False,
) -> StateStore:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)

    def apply(path: FieldPath, value: object) -> None:
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="poll_response", provider="test"),
                timestamp_monotonic=clock.now(),
                max_age=60.0,
            )
        )

    facts = {
        FieldPath.active_slot("0"): active_slot,
        FieldPath.active("0", "freq_mode", "mode"): mode,
        FieldPath.active("0", "freq_mode", "data_mode"): data_mode,
        FieldPath.unselected("0", "freq_mode", "mode"): unselected_mode,
        FieldPath.unselected("0", "freq_mode", "data_mode"): 0,
        FieldPath.global_("tx_state", "split"): split,
    }
    for path, value in facts.items():
        if value is not None:
            apply(path, value)
    if stale:
        store.mark_stale_due(now=clock.now() + 120.0)
    return store


def _radio(
    model: str,
    *,
    getter: Callable[[], Awaitable[int]],
    backend_id: str = "icom_serial",
) -> MagicMock:
    profile = resolve_radio_profile(model=model)
    radio = MagicMock(spec=_IcomSerialRadioBase)
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True
    radio.set_ptt = AsyncMock(spec=_IcomSerialRadioBase.set_ptt)
    radio.get_data_off_mod_input = getter
    radio.backend_id = backend_id
    return radio


class _Ingress:
    def __init__(
        self,
        model: str,
        *,
        getter: Callable[[], Awaitable[int]],
        store: StateStore | None = None,
        backend_id: str = "icom_serial",
    ) -> None:
        self.authority = _Authority()
        self.toasts = _Toasts()
        self.retained: set[asyncio.Task[None]] = set()
        server = SimpleNamespace(
            command_queue=MagicMock(spec=CommandQueue),
            command_state_store=(store if store is not None else _seeded_store()),
            enqueue_managed_positive_tx=self._enqueue_positive_tx,
            _spawn=self._spawn,
            send_notification_to_session=self.toasts,
        )
        self.radio = _radio(model, getter=getter, backend_id=backend_id)
        self.handler = ControlHandler(
            ws=MagicMock(spec=WebSocketConnection, outbound_progress=0),
            radio=self.radio,
            server_version="test",
            radio_model=model,
            server=server,
            session_id="websocket-test",
            managed_tx_authority=self.authority,
        )

    def _spawn(self, coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self.retained.add(task)
        task.add_done_callback(self.retained.discard)
        return task

    async def _enqueue_positive_tx(
        self,
        *,
        ready: asyncio.Future[None],
        submission: asyncio.Task[ManagedTxSubmission],
        **_kwargs: Any,
    ) -> ManagedTxSubmission:
        ready.set_result(None)
        return await submission

    async def drain(self) -> None:
        await asyncio.gather(*tuple(self.retained))


@pytest.fixture()
def usb_getter() -> AsyncMock:
    return AsyncMock(spec=_IcomSerialRadioBase.get_data_off_mod_input, return_value=3)


async def _ptt_on(ingress: _Ingress) -> dict[str, Any]:
    result = await ingress.handler._enqueue_command("ptt", {"state": True})
    await ingress.drain()
    return result


async def test_repeat_key_checks_once(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    assert await _ptt_on(ingress) == {"state": True}
    assert await _ptt_on(ingress) == {"state": True}
    usb_getter.assert_awaited_once()
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]


@pytest.mark.parametrize(
    ("model", "not_usb", "usb", "source"),
    (
        ("IC-7300MK2", 0, 1, "MIC"),
        ("IC-7610", 1, 3, "ACC"),
        ("IC-905", 3, 1, "LAN"),
    ),
)
async def test_per_profile_usb_value_mapping(
    usb_getter: AsyncMock, model: str, not_usb: int, usb: int, source: str
) -> None:
    ingress = _Ingress(model, getter=usb_getter)
    usb_getter.return_value = not_usb
    await _ptt_on(ingress)
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
    assert ingress.toasts.sent[0][4] == {"source": source, "expected": "USB"}

    ingress_ok = _Ingress(
        model,
        getter=AsyncMock(
            spec=_IcomSerialRadioBase.get_data_off_mod_input, return_value=usb
        ),
    )
    await _ptt_on(ingress_ok)
    assert ingress_ok.toasts.sent == []


@pytest.mark.parametrize("model", ("FT-991A", "X6100", "G7250"))
async def test_profile_without_mod_input_stays_silent(
    usb_getter: AsyncMock, model: str
) -> None:
    ingress = _Ingress(model, getter=usb_getter)
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


async def test_unreadable_mod_input_warns_with_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    getter = AsyncMock(
        spec=_IcomSerialRadioBase.get_data_off_mod_input,
        side_effect=TimeoutError("civ timeout"),
    )
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        ingress = _Ingress("IC-705", getter=getter)
        await _ptt_on(ingress)
    assert ingress.toasts.codes() == [MOD_INPUT_UNREADABLE_CODE]
    level, message, category, code, params = ingress.toasts.sent[0]
    assert level == "warning"
    assert params == {"reason": "TimeoutError"}
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize(
    "facts",
    [
        *({"mode": mode} for mode in ("CW", "CW-R", "RTTY", "WFM", "DV")),
        {"data_mode": 1},
        {"mode": None},
        {"active_slot": None},
        {"split": None},
        {"stale": True},
        {"split": True, "unselected_mode": "CW"},
    ],
)
async def test_non_voice_or_unusable_state_stays_silent(
    usb_getter: AsyncMock, facts: dict[str, Any]
) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter, store=_seeded_store(**facts))
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


async def test_lan_route_stays_silent(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter, backend_id="rigplane")
    await _ptt_on(ingress)
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


async def test_ab_split_checks_unselected_voice(usb_getter: AsyncMock) -> None:
    ingress = _Ingress(
        "IC-705",
        getter=usb_getter,
        store=_seeded_store(mode="CW", split=True, unselected_mode="USB"),
    )
    await _ptt_on(ingress)
    assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]


@pytest.mark.parametrize("cancel", [False, True])
async def test_ptt_is_admitted_before_the_advisory_reply(cancel: bool) -> None:
    gate = asyncio.Event()

    async def blocked_getter() -> int:
        await gate.wait()
        return 3

    ingress = _Ingress("IC-705", getter=blocked_getter)
    # Returns without awaiting the withheld advisory reply by construction; a
    # regression would block here and fail on the pytest per-test timeout.
    assert await ingress.handler._enqueue_command("ptt", {"state": True}) == {
        "state": True
    }
    (task,) = ingress.retained
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ingress.toasts.sent == []
    else:
        gate.set()
        await task
        assert ingress.toasts.codes() == [MOD_INPUT_NOT_USB_CODE]


async def test_refused_ptt_runs_no_check(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    ingress.authority.next_outcome = ManagedTxOutcome.REJECTED
    with pytest.raises(CommandRejectedError):
        await ingress.handler._enqueue_command("ptt", {"state": True})
    usb_getter.assert_not_awaited()
    assert ingress.toasts.sent == []


async def test_ptt_off_runs_no_check(usb_getter: AsyncMock) -> None:
    ingress = _Ingress("IC-705", getter=usb_getter)
    await _ptt_on(ingress)
    usb_getter.reset_mock()
    assert await ingress.handler._enqueue_command("ptt", {"state": False}) == {
        "state": False
    }
    usb_getter.assert_not_awaited()


@asynccontextmanager
async def _serial_stack(
    model: str, civ_address: int, *, tx_config: Path | None = None
) -> AsyncIterator[
    tuple[CivSerialFake, _IcomSerialRadioBase, ManagedTxComposition | None]
]:
    from rigplane.backends.ic705.serial import Ic705SerialRadio
    from rigplane.backends.ic7300.serial import Ic7300SerialRadio
    from rigplane.backends.ic9700.serial import Ic9700SerialRadio
    from rigplane.backends.icom7610.serial import Icom7610SerialRadio
    from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink

    radio_class = {
        "IC-705": Ic705SerialRadio,
        "IC-7300": Ic7300SerialRadio,
        "IC-9700": Ic9700SerialRadio,
        "IC-7610": Icom7610SerialRadio,
    }[model]
    fake = CivSerialFake(model=model, model_id=bytes((civ_address,)))
    link = SerialCivLink(
        device="fake-tty", open_serial_connection=fake.open_serial_connection
    )
    radio = radio_class(
        device="fake-tty",
        civ_link=link,
        model=model,
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = None
    if tx_config is not None:
        composition = ManagedTxComposition(radio, config_path=tx_config)
        install_managed_tx_composition(radio, composition)
        await composition.bind_state_store(radio.state_store)
    try:
        await radio.connect()
        assert radio.connected
        yield fake, radio, composition
    finally:
        try:
            await radio.disconnect()
        finally:
            if composition is not None:
                await composition.shutdown(asyncio.Event())


def _capture_server(store: StateStore, toasts: _Toasts) -> SimpleNamespace:
    return SimpleNamespace(
        command_state_store=store, send_notification_to_session=toasts
    )


@pytest.mark.parametrize(
    ("model", "civ_address", "sub_address", "not_usb", "usb", "source"),
    _SIMULATOR_ROWS,
    ids=("IC-705", "IC-7300", "IC-9700"),
)
async def test_the_real_wire_read_across_profiles(
    model: str,
    civ_address: int,
    sub_address: bytes,
    not_usb: int,
    usb: int,
    source: str,
) -> None:
    async with _serial_stack(model, civ_address) as (fake, radio, _):
        item = bytes((0x1A, 0x05)) + sub_address
        fake.hold(item, bytes((not_usb,)))
        toasts = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), toasts), radio, "websocket-test"
        )
        assert toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
        assert toasts.sent[0][4] == {"source": source, "expected": "USB"}

        fake.hold(item, bytes((usb,)))
        clear = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), clear), radio, "websocket-test"
        )
        assert clear.sent == []


async def test_an_unanswered_menu_read_warns_as_unreadable() -> None:
    async with _serial_stack("IC-705", 0xA4) as (_fake, radio, _):
        toasts = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(_seeded_store(), toasts), radio, "websocket-test"
        )
        assert toasts.codes() == [MOD_INPUT_UNREADABLE_CODE]
        assert toasts.sent[0][4] == {"reason": "CommandRejectedError"}


async def _observe_modes(
    fake: CivSerialFake,
    radio: _IcomSerialRadioBase,
    *,
    split: bool,
    main: int,
    other: int | None = None,
) -> None:
    fake.hold(b"\x0f", bytes((split,)))
    fake.hold(b"\x26\x00", bytes((main, 0, 1)))
    assert await radio.get_split(strict=True) is split
    assert await radio.send_civ(0x26, data=b"\x00") is not None
    if other is not None:
        fake.hold(b"\x26\x01", bytes((other, 0, 1)))
        assert await radio.send_civ(0x26, data=b"\x01") is not None


@pytest.mark.parametrize(
    ("model", "address", "item", "other_path"),
    [
        ("IC-7610", 0x98, b"\x00\x91", FieldPath.active("1", "freq_mode", "mode")),
        ("IC-9700", 0xA2, b"\x01\x15", FieldPath.unselected("0", "freq_mode", "mode")),
    ],
)
@pytest.mark.parametrize(
    ("split", "main", "other", "warn"),
    [(False, 1, 3, True), (True, 3, 1, True), (True, 1, 3, False)],
    ids=["simplex-voice", "split-voice", "split-cw"],
)
async def test_main_sub_warning_uses_decoded_tx_mode(
    model: str,
    address: int,
    item: bytes,
    other_path: FieldPath,
    split: bool,
    main: int,
    other: int,
    warn: bool,
) -> None:
    async with _serial_stack(model, address) as (fake, radio, _):
        fake.hold(b"\x1a\x05" + item, b"\x00")
        await _observe_modes(fake, radio, split=split, main=main, other=other)
        store = radio.state_store
        assert store.snapshot().field(other_path).value == (
            "USB" if other == 1 else "CW"
        )
        toasts = _Toasts()
        await warn_when_mod_input_blocks_voice(
            _capture_server(store, toasts), radio, "websocket-test"
        )
        assert toasts.codes() == ([MOD_INPUT_NOT_USB_CODE] if warn else [])
        assert sum(
            frame.command == 0x1A and frame.sub == 0x05 and frame.data == item
            for frame in fake.commands
        ) == int(warn)


async def test_ptt_off_reaches_serial_wire_before_pending_mod_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _serial_stack("IC-705", 0xA4) as (fake, radio, _):
        fake.hold(b"\x1a\x05\x01\x18", b"\x03")
        mod_sent = asyncio.Event()
        off_sent = asyncio.Event()
        dispatch = fake._answer
        held_reply = None

        def withhold(frame, connection, raw):
            nonlocal held_reply
            reply = dispatch(frame, connection, raw)
            if (
                frame.command == 0x1A
                and frame.sub == 0x05
                and frame.data == b"\x01\x18"
            ):
                assert reply is not None
                held_reply = (connection, reply)
                mod_sent.set()
                return None
            if frame.command == 0x1C and frame.sub == 0 and frame.data == b"\x00":
                off_sent.set()
            return reply

        monkeypatch.setattr(fake, "_answer", withhold)
        monkeypatch.setattr(radio, "_civ_get_timeout", 10.0)
        token = EffectToken(1, 1, "serial-test")
        await radio.actuate(token, ActuationOperation.PTT_ON, is_current=lambda: True)
        assert fake.ptt
        toasts = _Toasts()
        check = asyncio.create_task(
            warn_when_mod_input_blocks_voice(
                _capture_server(_seeded_store(), toasts), radio, "websocket-test"
            )
        )
        try:
            await asyncio.wait_for(mod_sent.wait(), 2)
            release = asyncio.create_task(
                radio.actuate(
                    token, ActuationOperation.FORCE_RECEIVE, is_current=lambda: True
                )
            )
            try:
                await asyncio.wait_for(off_sent.wait(), 2)
                assert not check.done()
                assert not fake.ptt
                fake.hold(b"\x1a\x05\x01\x19", b"\x01")
                assert await radio.get_data1_mod_input() == 1
                assert not check.done()
            finally:
                assert held_reply is not None
                connection, reply = held_reply
                connection.deliver(reply)
                await asyncio.wait_for(release, 3)
            await asyncio.wait_for(check, 3)
            assert toasts.codes() == [MOD_INPUT_NOT_USB_CODE]
        finally:
            check.cancel()
            await asyncio.gather(check, return_exceptions=True)


async def test_local_websocket_admits_voice_and_delivers_one_warning(
    tmp_path: Path,
) -> None:
    async with _serial_stack("IC-7610", 0x98, tx_config=tmp_path / "tx.json") as (
        fake,
        radio,
        composition,
    ):
        assert composition is not None
        fake.hold(b"\x1a\x05\x00\x91", b"\x00")
        await _observe_modes(fake, radio, split=False, main=1)
        assert await radio.send_civ(0x1C, sub=0) is not None
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        attach_managed_tx_composition(server, composition)
        poller = RadioPoller(
            radio,
            server.command_queue,
            state_store=radio.state_store,
            managed_tx_authority=composition.authority,
        )
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        consumer = asyncio.create_task(
            run_command_consumer(server.command_queue, poller._execute_queued_entry)
        )
        try:
            url = f"http://127.0.0.1:{server.port}/api/v1/ws"
            async with aiohttp.ClientSession() as client, client.ws_connect(url) as ws:
                await ws.send_json(
                    dict(type="cmd", id="key", name="ptt", params={"state": True})
                )
                notifications = []
                async with asyncio.timeout(5):
                    while True:
                        message = await ws.receive_json()
                        if message["type"] == "notification":
                            notifications.append(message)
                        if message.get("id") == "key":
                            assert message["ok"] is True
                            assert message["result"] == {"state": True}
                            break
                    await asyncio.gather(*tuple(server._bg_tasks))
                    (session_id,) = server._session_queues
                    assert server.send_notification_to_session(
                        session_id, "info", "end of notifications", code="barrier"
                    )
                    while True:
                        message = await ws.receive_json()
                        if message.get("code") == "barrier":
                            break
                        if message["type"] == "notification":
                            notifications.append(message)
                (notification,) = notifications
                assert notification["level"] == "warning"
                assert notification["category"] == "audio"
                assert notification["code"] == MOD_INPUT_NOT_USB_CODE
                assert notification["params"] == {"source": "MIC", "expected": "USB"}
                await await_until(lambda: fake.ptt, description="voice PTT on fake")
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await server.stop()
