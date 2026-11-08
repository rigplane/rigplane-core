"""C3 RED: adapter TX path (MOR-3821, spec 7.2/7.3/7.4/5.7/6.4)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from test_managed_tx_authority import FakeConfigStore, FakeLane

from tci_adapter_stub import (
    StubAuthority,
    StubCommandQueue,
    StubCommandService,
    StubIcomRadio,
    StubProfile,
    build_store,
    make_handler,
)


def _loaded_store():  # type: ignore[no-untyped-def]
    return build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
            FieldPath.global_("operator_controls", "power_level"): 0.5,
        }
    )


def _data2_radio():  # type: ignore[no-untyped-def]
    profile = StubProfile(modes=("AM", "LSB", "USB", "CW", "FM"), data_mode_count=3)
    radio = StubIcomRadio(profile=profile)
    radio.mod_inputs = {2: 5}
    return radio


def _seam(radio=None, store=None, **kwargs):  # type: ignore[no-untyped-def]
    """Real ManagedTxAuthority composition (MOR-3821 C3 seam)."""
    radio = radio if radio is not None else _data2_radio()
    store = store if store is not None else _loaded_store()
    lane = kwargs.pop("lane", FakeLane())
    authority = ManagedTxAuthority(
        lane,
        FakeConfigStore(kwargs.pop("tot_seconds", 10.0)),
        TxAbortFence(),
        provider_generation=7,
        attempt_timeout_seconds=kwargs.pop("attempt_timeout_seconds", 1.0),
        retry_delay_seconds=kwargs.pop("retry_delay_seconds", 2.0),
    )
    queue: StubCommandQueue = kwargs.pop("queue", StubCommandQueue())
    handler = make_handler(
        radio=radio,
        store=store,
        authority=authority,
        queue=queue,
        service=StubCommandService(),
        **kwargs,
    )
    return handler, authority, lane, queue


async def _program_digu(handler):  # type: ignore[no-untyped-def]
    handler._radio.observe_store = handler._store  # noqa: SLF001
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,DIGU;",)


async def _await_release_settled(authority: ManagedTxAuthority) -> None:
    """Wait until the TX release has settled (MOR-4001).

    The settle event that clears ``release_required`` and ``pending_effect``
    is reduced after the force-off effect lands, so closing the authority
    right after observing the effect races with disposal's clean-RX check.
    """
    while True:
        snapshot = await authority.snapshot()
        if (
            not snapshot.state.release_required
            and snapshot.state.pending_effect is None
        ):
            return
        await asyncio.sleep(0.01)


async def test_key_accepted_end_to_end() -> None:
    handler, authority, lane, _queue = _seam()
    await _program_digu(handler)
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,true;",)
    assert outcome.to_others == ("trx:0,true;",)
    assert [e.operation.name for e in lane.effects] == ["PTT_ON"]
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.PTT
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.close()


async def test_unkey_releases() -> None:
    handler, authority, _lane, _queue = _seam()
    await _program_digu(handler)
    assert (await handler.on_command("trx", ("0", "true"))).to_sender == (
        "trx:0,true;",
    )
    outcome = await handler.on_command("trx", ("0", "false"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ("trx:0,false;",)
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
    await authority.close()


async def test_key_unmanaged_always_refused() -> None:
    radio = _data2_radio()
    handler = make_handler(radio=radio, store=_loaded_store())
    await handler.on_command("modulation", ("0", "DIGU"))
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()


async def test_key_explicit_source_refused_without_ptt_write() -> None:
    handler, authority, lane, _queue = _seam()
    await _program_digu(handler)
    for source in ("mic1", "tci", "ipc"):
        outcome = await handler.on_command("trx", ("0", "true", source))
        assert outcome.to_sender == ("trx:0,false;",), source
        assert outcome.to_others == (), source
    assert lane.effects == []
    await authority.close()


async def test_key_competing_lease_refused() -> None:
    handler, authority, lane, _queue = _seam()
    await _program_digu(handler)
    assert (await handler.on_command("trx", ("0", "true"))).to_sender == (
        "trx:0,true;",
    )
    other = make_handler(
        radio=handler._radio,  # noqa: SLF001
        store=handler._store,  # noqa: SLF001
        owner="tci-client-9",
        authority=authority,
        queue=StubCommandQueue(),
        service=StubCommandService(),
    )
    await other.on_command("modulation", ("0", "DIGU"))
    outcome = await other.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert len(lane.effects) == 1
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.close()


async def test_key_radio_tx_refused() -> None:
    radio = _data2_radio()
    store = _loaded_store()
    authority = StubAuthority()
    authority.radio_tx = "radio transmits and no owner keyed it"
    handler = make_handler(
        radio=radio,
        store=store,
        authority=authority,
        queue=StubCommandQueue(),
        service=StubCommandService(),
    )
    await _program_digu(handler)
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert authority.submit_calls == []


async def test_key_remote_controller_refused() -> None:
    radio = _data2_radio()
    store = _loaded_store()
    authority = StubAuthority()
    controller = SimpleNamespace(mode="local")
    handler = make_handler(
        radio=radio,
        store=store,
        authority=authority,
        queue=StubCommandQueue(),
        service=StubCommandService(),
        controller_authority=controller,
    )
    await _program_digu(handler)
    controller.mode = "remote"
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert authority.submit_calls == []


async def test_key_without_prior_modulation_keys_current_input() -> None:
    """Gate handler:565 — no per-socket programming prerequisite: a bare
    ``trx:0,true`` on a fresh connection keys the current input when the
    radio is correctly configured and the authority admits it."""
    handler, authority, lane, _queue = _seam()
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,true;",)
    assert outcome.to_others == ("trx:0,true;",)
    assert [e.operation.name for e in lane.effects] == ["PTT_ON"]
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.close()


async def test_key_split_mismatch_refused() -> None:
    radio = _data2_radio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 2,
            FieldPath.active("sub", "freq_mode", "freq_hz"): 14_078_000,
            FieldPath.active("sub", "freq_mode", "mode"): "LSB",
            FieldPath.active("sub", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): True,
        }
    )
    handler, authority, lane, _queue = _seam(radio=radio, store=store)
    await handler.on_command("modulation", ("0", "DIGU"))
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert [
        e.operation.name for e in lane.effects if e.operation.name == "PTT_ON"
    ] == []
    # A refused admission leaves the documented release debt (RX intent,
    # release owed; cf. the authority's own before_on tests): the explicit
    # unkey still answers, then force_off settles the debt before close.
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.force_off()
    await authority.close()


async def test_key_mod_input_mismatch_refused() -> None:
    handler, authority, lane, _queue = _seam()
    await _program_digu(handler)
    handler._radio.mod_inputs[2] = 0  # noqa: SLF001  front panel moved to MIC
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert [
        e.operation.name for e in lane.effects if e.operation.name == "PTT_ON"
    ] == []
    # A refused admission leaves the documented release debt (RX intent,
    # release owed; cf. the authority's own before_on tests): the explicit
    # unkey still answers, then force_off settles the debt before close.
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.force_off()
    await authority.close()


async def test_key_truncated_tuple_refused() -> None:
    radio = _data2_radio()
    radio.fail_on.add("read_relative_vfo")
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 2,
            FieldPath.global_("tx_state", "split"): True,
        }
    )
    handler, authority, lane, _queue = _seam(radio=radio, store=store)
    await handler.on_command("modulation", ("0", "DIGU"))
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,false;",)
    assert outcome.to_others == ()
    assert [
        e.operation.name for e in lane.effects if e.operation.name == "PTT_ON"
    ] == []
    # A refused admission leaves the documented release debt (RX intent,
    # release owed; cf. the authority's own before_on tests): the explicit
    # unkey still answers, then force_off settles the debt before close.
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.force_off()
    await authority.close()


async def test_disconnect_while_keyed_releases() -> None:
    handler, authority, lane, _queue = _seam()
    await _program_digu(handler)
    key_task = asyncio.ensure_future(handler.on_command("trx", ("0", "true")))
    try:
        async with asyncio.timeout(5.0):
            while not lane.effects:
                await asyncio.sleep(0.01)
        await handler.on_disconnect()
        async with asyncio.timeout(5.0):
            await asyncio.gather(key_task, return_exceptions=True)
    finally:
        if not key_task.done():
            key_task.cancel()
    async with asyncio.timeout(5.0):
        await _await_release_settled(authority)
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
    await authority.close()


async def test_tot_expiry_bounds_keyed_over() -> None:
    lane = FakeLane()
    handler, authority, _lane, _queue = _seam(lane=lane, tot_seconds=0.05)
    await _program_digu(handler)
    async with asyncio.timeout(5.0):
        outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,true;",)
    assert outcome.to_others == ("trx:0,true;",)
    async with asyncio.timeout(5.0):
        while not any(
            effect.operation.name == "FORCE_RECEIVE" for effect in lane.effects
        ):
            await asyncio.sleep(0.01)
        await _await_release_settled(authority)
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
    assert snapshot.configured_tot_seconds == 0.05
    await authority.close()


async def test_tot_disabled_holds_over_until_unkey() -> None:
    handler, authority, _lane, _queue = _seam(tot_seconds=None)
    await _program_digu(handler)
    async with asyncio.timeout(5.0):
        outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,true;",)
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.PTT
    assert snapshot.configured_tot_seconds is None
    assert snapshot.remaining_tot_seconds is None
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.close()


async def test_cancellation_loses_key_race() -> None:
    handler, authority, lane, queue = _seam()
    queue.hold_ready = True
    await _program_digu(handler)
    key_task = asyncio.ensure_future(handler.on_command("trx", ("0", "true")))
    try:
        async with asyncio.timeout(5.0):
            while not queue.entries:
                await asyncio.sleep(0.01)
        assert not key_task.done()
        key_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(5.0):
                await key_task
    finally:
        queue.release_ready()
    snapshot = await authority.snapshot()
    assert snapshot.state.intent.kind is ManagedTxIntentKind.RX
    assert lane.effects == []
    await authority.close()


async def test_tune_set_ignored_and_tune_drive_echoes() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    outcome = await handler.on_command("tune", ("0", "true"))
    assert outcome.to_sender == ("tune:0,false;",)
    assert outcome.to_others == ()
    outcome = await handler.on_command("tune_drive", ("0", "99"))
    assert outcome.to_sender == ("tune_drive:0,50;",)
    assert outcome.to_others == ()


async def test_tx_enable_managed_true() -> None:
    authority = StubAuthority()
    handler = make_handler(
        radio=StubIcomRadio(),
        store=_loaded_store(),
        authority=authority,
        queue=StubCommandQueue(),
        service=StubCommandService(),
    )
    assert (await handler.on_command("tx_enable", ("0",))).to_sender == (
        "tx_enable:0,true;",
    )
    outcome = await handler.on_command("tx_enable", ("0", "true"))
    assert outcome.to_sender == ("tx_enable:0,true;",)
    assert outcome.to_others == ()


async def test_notifier_refresh_emits_external_change() -> None:
    import time

    from rigplane.tci_adapter.server import TciAdapterServer

    store = _loaded_store()
    radio = StubIcomRadio()
    server = TciAdapterServer(
        radio=radio, state_store=store, host="127.0.0.1", port=0, poll_interval=0.01
    )
    handler = make_handler(radio=radio, store=store)
    await handler.on_connect()
    from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata

    store.apply(
        Observation(
            path=FieldPath.active("main", "freq_mode", "freq_hz"),
            value=14_080_000,
            source=SourceMetadata(
                source="poll_response",
                provider="test",
                transport="fake",
                native_id="tci",
            ),
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )
    )
    lines = await server.notifier.refresh()
    assert "vfo:0,0,14080000;" in lines
    assert "dds:0,14080000;" in lines
    assert "tx_frequency:14080000;" in lines
    await server.stop()
