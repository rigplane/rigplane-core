"""C1 RED: adapter init burst, read-only commands, notifier (MOR-3821, 5.4/5.5).

First failing test (design 5.1): a handler over a composed store answers
``vfo:0,0;`` with no scheduler, no acquisition, no radio I/O.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.tci.handler import CommandOutcome

from tci_adapter_stub import (
    StubCommandQueue,
    StubCommandService,
    StubAuthority,
    StubIcomRadio,
    StubMinimalRadio,
    StubProfile,
    build_store,
    make_handler,
)


def _loaded_store() -> StateStore:
    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 128,
        }
    )


async def test_no_scheduler_answers_vfo_from_composed_store() -> None:
    """Design 5.1: the composed store alone answers; nothing is started."""
    before = set(asyncio.all_tasks())
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    outcome = await handler.on_command("vfo", ("0", "0"))
    assert outcome == CommandOutcome(
        to_sender=("vfo:0,0,14074000;",), to_others=("vfo:0,0,14074000;",)
    )
    assert set(asyncio.all_tasks()) <= before | {asyncio.current_task()}  # noqa: SLF001


async def test_read_requests_projection_paths_for_refresh() -> None:
    """P2 follow-up: reads must name the projected fields for ``ensure_fresh``.

    An empty path request is rejected by the scheduler, so TCI reads would
    never trigger the configured refresh.
    """
    calls: list[tuple[tuple[object, ...], float, object, str]] = []

    class Service:
        def ensure_fresh(self, paths, *, max_age, priority, reason):  # type: ignore[no-untyped-def]
            calls.append((tuple(paths), max_age, priority, reason))

    radio = StubIcomRadio()
    radio.state_model_service = Service()
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("vfo", ("0", "0"))
    assert outcome == CommandOutcome(
        to_sender=("vfo:0,0,14074000;",), to_others=("vfo:0,0,14074000;",)
    )
    assert len(calls) == 1
    paths, max_age, _priority, _reason = calls[0]
    assert len(paths) > 0, "reads must request refresh paths; () is rejected"
    assert FieldPath.active("main", "freq_mode", "freq_hz") in paths
    assert FieldPath.global_("tx_state", "split") in paths
    assert FieldPath.global_("operator_controls", "power_level") in paths
    assert max_age == 0.2


async def test_init_burst_exact_order_and_content() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    burst = list(await handler.on_connect())
    assert burst == [
        "protocol:ExpertSDR3,2.1;",
        "device:IC-7610;",
        "receive_only:true;",
        "trx_count:1;",
        "channels_count:2;",
        "vfo_limits:30000,54000000;",
        "modulations_list:AM,LSB,USB,CW,FM,DIGL,DIGU;",
        "dds:0,14074000;",
        "if:0,0,0;",
        "if:0,1,0;",
        "vfo:0,0,14074000;",
        "vfo:0,1,14074000;",
        "modulation:0,USB;",
        "trx:0,false;",
        "tune:0,false;",
        "drive:0,50;",
        "tune_drive:0,50;",
        "rit_enable:0,false;",
        "xit_enable:0,false;",
        "split_enable:0,false;",
        "rit_offset:0,0;",
        "xit_offset:0,0;",
        "tx_enable:0,false;",
        "tx_frequency:14074000;",
        "ready;",
    ]


async def test_init_burst_reflects_split_and_data_and_managed() -> None:
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 7_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "LSB",
            FieldPath.active("main", "freq_mode", "data_mode"): 1,
            FieldPath.active("sub", "freq_mode", "freq_hz"): 7_078_000,
            FieldPath.global_("tx_state", "split"): True,
            FieldPath.global_("operator_controls", "rit_freq"): 100,
            FieldPath.global_("tx_state", "rit_on"): True,
            FieldPath.global_("tx_state", "rit_tx"): True,
            FieldPath.global_("operator_controls", "power_level"): 255,
        }
    )
    handler = make_handler(
        radio=StubIcomRadio(),
        store=store,
        authority=StubAuthority(),
        queue=StubCommandQueue(),
        service=StubCommandService(),
    )
    burst = list(await handler.on_connect())
    assert "device:IC-7610;" in burst
    assert "receive_only:false;" in burst
    assert "vfo:0,0,7074000;" in burst
    assert "vfo:0,1,7078000;" in burst
    assert "modulation:0,DIGL;" in burst
    assert "dds:0,7074000;" in burst
    assert "if:0,1,4000;" in burst
    assert "split_enable:0,true;" in burst
    assert "rit_enable:0,true;" in burst
    assert "xit_enable:0,true;" in burst
    assert "rit_offset:0,100;" in burst
    assert "xit_offset:0,100;" in burst
    assert "tx_enable:0,true;" in burst
    # Split on: TX is B plus the enabled XIT offset.
    assert "tx_frequency:7078100;" in burst
    assert "drive:0,100;" in burst
    assert burst[-1] == "ready;"


async def test_vfo_limits_come_from_profile_ranges() -> None:
    from rigplane.profiles import FreqRangeInfo

    profile = StubProfile(
        freq_ranges=(
            FreqRangeInfo(start=30_000, end=54_000_000, label="hf"),
            FreqRangeInfo(start=144_000_000, end=148_000_000, label="vhf"),
        )
    )
    handler = make_handler(radio=StubIcomRadio(profile=profile), store=_loaded_store())
    burst = list(await handler.on_connect())
    assert "vfo_limits:30000,148000000;" in burst


async def test_minimal_radio_reports_no_split_no_data_no_rit() -> None:
    handler = make_handler(radio=StubMinimalRadio(), store=_loaded_store())
    burst = list(await handler.on_connect())
    assert "split_enable:0,false;" in burst
    assert "rit_enable:0,false;" in burst
    assert "xit_enable:0,false;" in burst
    assert "modulations_list:AM,LSB,USB,CW,FM;" in burst
    assert "vfo:0,1,14074000;" in burst


@pytest.mark.parametrize("radio_factory", [StubIcomRadio, StubMinimalRadio])
async def test_read_round_trips(radio_factory: Any) -> None:
    handler = make_handler(radio=radio_factory(), store=_loaded_store())
    assert (await handler.on_command("vfo", ("0", "0"))).to_sender == (
        "vfo:0,0,14074000;",
    )
    assert (await handler.on_command("vfo", ("0", "1"))).to_sender == (
        "vfo:0,1,14074000;",
    )
    assert (await handler.on_command("modulation", ("0",))).to_sender == (
        "modulation:0,USB;",
    )
    assert (await handler.on_command("split_enable", ("0",))).to_sender == (
        "split_enable:0,false;",
    )
    assert (await handler.on_command("rit_enable", ())).to_sender == (
        "rit_enable:0,false;",
    )
    assert (await handler.on_command("xit_enable", ())).to_sender == (
        "xit_enable:0,false;",
    )
    assert (await handler.on_command("rit_offset", ())).to_sender == (
        "rit_offset:0,0;",
    )
    assert (await handler.on_command("drive", ("0",))).to_sender == ("drive:0,50;",)
    assert (await handler.on_command("tune_drive", ("0",))).to_sender == (
        "tune_drive:0,50;",
    )
    assert (await handler.on_command("tune", ("0",))).to_sender == ("tune:0,false;",)
    assert (await handler.on_command("trx", ("0",))).to_sender == ("trx:0,false;",)
    assert (await handler.on_command("tx_frequency", ())).to_sender == (
        "tx_frequency:14074000;",
    )
    assert (await handler.on_command("dds", ("0",))).to_sender == (
        "dds:0,14074000;",
        "if:0,0,0;",
        "if:0,1,0;",
    )
    assert (await handler.on_command("cw_keyer_speed", ())).to_sender == (
        "cw_keyer_speed:20;",
    )
    assert (await handler.on_command("cw_macros_speed", ())).to_sender == (
        "cw_macros_speed:20;",
    )


async def test_out_of_range_trx_is_ignored_without_reply() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    assert await handler.on_command("vfo", ("1", "0")) == CommandOutcome()
    assert await handler.on_command("trx", ("1",)) == CommandOutcome()
    assert await handler.on_command("modulation", ("1",)) == CommandOutcome()


async def test_unknown_commands_produce_no_reply() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    assert await handler.on_command("frobnicate", ("1",)) == CommandOutcome()
    assert await handler.on_command("ready", ()) == CommandOutcome()


async def test_tier_c_ignored_without_reply_and_logged_once(caplog: Any) -> None:
    import logging

    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    with caplog.at_level(logging.INFO, logger="rigplane.tci_adapter.handler"):
        assert await handler.on_command("iq_start", ("0",)) == CommandOutcome()
        assert await handler.on_command("iq_start", ("0",)) == CommandOutcome()
    hits = [
        r
        for r in caplog.records
        if "iq_start" in r.getMessage() and r.levelno == logging.INFO
    ]
    assert len(hits) == 1
    assert "0" not in hits[0].getMessage()


@pytest.fixture()
def tcp_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


async def _connect(port: int) -> Any:
    pytest.importorskip("eesdr_tci")
    import importlib

    listener_mod = importlib.import_module("eesdr_tci.listener")
    listener = listener_mod.Listener(f"ws://127.0.0.1:{port}")
    seen: list[Any] = []

    async def _record(name: Any, rx: Any, sub_rx: Any, params: Any) -> None:
        seen.append((name, rx, sub_rx, params))

    listener.add_param_listener("*", _record)
    await listener.start()
    await listener.ready()
    return listener, seen


async def _close(listener: Any) -> None:
    listener.shutdown()
    try:
        async with asyncio.timeout(5.0):
            await listener.wait()
    except Exception:
        pass
    await asyncio.sleep(0)
    current = asyncio.current_task()
    for task in [t for t in asyncio.all_tasks() if t is not current and not t.done()]:
        task.cancel()
    await asyncio.gather(
        *[t for t in asyncio.all_tasks() if t is not current], return_exceptions=True
    )


def _serve(handler: Any, **kwargs: Any) -> Any:
    import time

    from rigplane.tci.server import TciServer
    from rigplane.tci_adapter.server import tci_allow_predicate

    return TciServer(
        lambda: handler,
        host="127.0.0.1",
        port=0,
        allow=tci_allow_predicate(),
        clock=time.monotonic,
        **kwargs,
    )


async def test_conformance_init_burst_only_emits_known_commands(
    tcp_available: None,
) -> None:
    """The eesdr-tci output guard: an unknown name raises in its listener."""
    from rigplane.tci_adapter.server import TciAdapterServer

    store = _loaded_store()
    handler = make_handler(radio=StubIcomRadio(), store=store)
    server = TciAdapterServer(
        radio=StubIcomRadio(),
        state_store=store,
        handler_factory=lambda owner: handler,
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    try:
        listener, seen = await _connect(server.bound_port)
        try:
            async with asyncio.timeout(5.0):
                while len(seen) < 25:
                    await asyncio.sleep(0.01)
            names = [name for name, _rx, _sub, _params in seen]
            assert names[0] == "PROTOCOL"
            assert "READY" in names
        finally:
            await _close(listener)
    finally:
        await server.stop()


async def test_multi_client_sync_and_unknown_silence(tcp_available: None) -> None:
    from rigplane.tci_adapter.server import TciAdapterServer

    store = _loaded_store()
    handler_a = make_handler(radio=StubIcomRadio(), store=store, owner="tci-client-1")
    handler_b = make_handler(radio=StubIcomRadio(), store=store, owner="tci-client-2")
    handlers = iter([handler_a, handler_b])
    server = TciAdapterServer(
        radio=StubIcomRadio(),
        state_store=store,
        handler_factory=lambda owner: next(handlers),
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    try:
        listener_a, params_a = await _connect(server.bound_port)
        listener_b, params_b = await _connect(server.bound_port)
        try:
            count_b = len(params_b)
            await listener_a.send("vfo:0,0;")
            async with asyncio.timeout(5.0):
                while len(params_b) <= count_b:
                    await asyncio.sleep(0.01)
            assert params_b[-1][0] == "VFO"
            count_a = len(params_a)
            await listener_a.send("frobnicate:1;")
            await asyncio.sleep(0.3)
            assert len(params_a) == count_a
        finally:
            await _close(listener_a)
            await _close(listener_b)
    finally:
        await server.stop()
