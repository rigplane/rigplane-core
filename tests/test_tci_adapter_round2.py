"""Round 2 RED: root fixes for gate round 1 + bot findings (MOR-3821 PR 4).

Each test pins the fixed behaviour; all fail on the pre-fix tree for the
stated reason. GREEN implements the root fixes (notifier projection layer,
controller-gated writes + session registration, before_on tuple check).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_fence import TxAbortFence
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


def _seam(radio=None, store=None, **kwargs):  # type: ignore[no-untyped-def]
    radio = radio if radio is not None else _data2_radio()
    if store is None:
        store = build_store(
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


def _data2_radio():  # type: ignore[no-untyped-def]
    profile = StubProfile(modes=("AM", "LSB", "USB", "CW", "FM"), data_mode_count=3)
    radio = StubIcomRadio(profile=profile)
    radio.mod_inputs = {2: 5}
    return radio


async def _program_digu(handler):  # type: ignore[no-untyped-def]
    handler._radio.observe_store = handler._store  # noqa: SLF001
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,DIGU;",)


async def test_session_registered_on_connect_and_removed_on_disconnect() -> None:
    """Gate handler:180 — the socket session must join the shared queue."""
    radio = StubIcomRadio()
    queue = StubCommandQueue()
    handler = make_handler(
        radio=radio,
        store=build_store({}),
        authority=StubAuthority(),
        queue=queue,
        service=StubCommandService(),
    )
    # Pre-fix: on_connect never registers; the queue has no session set entry.
    await handler.on_connect()
    assert "tci-client-1" in queue.sessions
    await handler.on_disconnect()
    assert "tci-client-1" not in queue.sessions


async def test_write_goes_through_command_service_when_composed() -> None:
    """Brief B: every TCI write is a source=tci intent via the service."""
    radio = StubIcomRadio()
    store = build_store({FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000})
    radio.observe_store = store
    used: list[tuple[str, object]] = []

    class FakeService:
        async def execute(self, intent, *, executor=None):  # type: ignore[no-untyped-def]
            used.append((intent.name, executor))
            assert executor is not None
            assert intent.source == "tci"
            return await executor.execute(intent)

    handler = make_handler(
        radio=radio,
        store=store,
        authority=StubAuthority(),
        queue=StubCommandQueue(),
        service=FakeService(),  # type: ignore[arg-type]
    )
    outcome = await handler.on_command("vfo", ("0", "0", "14075000"))
    assert outcome.to_sender == ("vfo:0,0,14075000;",)
    assert [name for name, _ in used] == ["set_freq"]
    assert radio.freq_a == 14_075_000


async def test_controller_remote_refuses_frequency_write() -> None:
    """Bot handler:617 — remote controller mode refuses a frequency write."""
    radio = StubIcomRadio()
    store = build_store({FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000})
    handler = make_handler(
        radio=radio,
        store=store,
        controller_authority=SimpleNamespace(mode="remote"),
    )
    outcome = await handler.on_command("vfo", ("0", "0", "14075000"))
    assert outcome.to_sender == ("vfo:0,0,14074000;",)
    assert outcome.to_others == ()
    assert radio.freq_a == 14_074_000


async def test_power_normalized_half_is_drive_50() -> None:
    """Gate notifier:315 — the store power is normalized 0..1, not native."""
    radio = StubIcomRadio()
    store = build_store({FieldPath.global_("operator_controls", "power_level"): 0.5})
    handler = make_handler(radio=radio, store=store)
    outcome = await handler.on_command("drive", ("0",))
    assert outcome.to_sender == ("drive:0,50;",)


async def test_yaesu_data_u_projects_digu() -> None:
    """Gate notifier:210 — native Yaesu DATA-U projects to DIGU."""
    radio = StubIcomRadio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "DATA-U",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
        }
    )
    handler = make_handler(radio=radio, store=store)
    outcome = await handler.on_command("modulation", ("0",))
    assert outcome.to_sender == ("modulation:0,DIGU;",)


async def test_channel_b_from_unselected_path() -> None:
    """Gate notifier:46 / bot notifier:47 — B lives under unselected("0")."""
    radio = StubIcomRadio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.unselected("0", "freq_mode", "freq_hz"): 14_078_000,
            FieldPath.unselected("0", "freq_mode", "mode"): "USB",
            FieldPath.unselected("0", "freq_mode", "data_mode"): 2,
        }
    )
    handler = make_handler(radio=radio, store=store)
    outcome = await handler.on_command("vfo", ("0", "1"))
    assert outcome.to_sender == ("vfo:0,1,14078000;",)


async def test_notification_cache_reemits_return_to_false() -> None:
    """Gate notifier:481 / bot notifier:485 — keys replace, not accumulate."""
    import time

    from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata

    def _obs(path, value):  # type: ignore[no-untyped-def]
        return Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source="poll_response",
                provider="test",
                transport="fake",
                native_id="tci",
            ),
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )

    from rigplane.core.state_store import StateStore
    from rigplane.tci_adapter.notifier import TciNotifier

    store = StateStore()
    radio = StubIcomRadio()
    notifier = TciNotifier(radio=radio, store=store, managed_present=False)
    await notifier.init_burst()
    store.apply(_obs(FieldPath.active("main", "freq_mode", "freq_hz"), 14_080_000))
    first = await notifier.refresh()
    assert any(line.startswith("vfo:0,0,14080000") for line in first)
    store.apply(_obs(FieldPath.active("main", "freq_mode", "freq_hz"), 14_074_000))
    second = await notifier.refresh()
    # Pre-fix: the return to 14074000 is suppressed (payload already seen).
    assert any(line.startswith("vfo:0,0,14074000") for line in second)


async def test_bare_trx_keys_current_input_without_programming() -> None:
    """Gate handler:565 — no per-socket programming prerequisite for bare trx."""
    handler, authority, lane, _queue = _seam()
    outcome = await handler.on_command("trx", ("0", "true"))
    assert outcome.to_sender == ("trx:0,true;",)
    assert [e.operation.name for e in lane.effects] == ["PTT_ON"]
    assert (await handler.on_command("trx", ("0", "false"))).to_sender == (
        "trx:0,false;",
    )
    await authority.close()


async def test_rit_offset_leaves_enable_unchanged() -> None:
    """Gate handler:803 — offset writes change only the offset."""
    radio = StubIcomRadio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("operator_controls", "rit_freq"): 0,
            FieldPath.global_("tx_state", "rit_on"): False,
            FieldPath.global_("tx_state", "rit_tx"): False,
        }
    )
    handler = make_handler(radio=radio, store=store)
    assert (await handler.on_command("xit_offset", ("0", "100"))).to_sender == (
        "xit_offset:0,100;",
    )
    assert radio.rit_tx is False
    assert (await handler.on_command("rit_offset", ("0", "100"))).to_sender == (
        "rit_offset:0,100;",
    )
    assert radio.rit_on is False


async def test_managed_idle_reports_trx_false_with_unknown_ptt() -> None:
    """Gate handler:353 (P2) / bot handler:353 — RX intent kind reads false."""
    from rigplane.core.state_store import StateStore

    # StubAuthority with owner None: snapshot intent is None (falsy) today, but
    # the real authority always carries an RX intent object (truthy). Pin the
    # real shape: an RX-kind intent must read false.
    from types import SimpleNamespace as _NS

    from rigplane.runtime.managed_tx_state import ManagedTxIntent, ManagedTxIntentKind

    class _RxAuthority(StubAuthority):
        def snapshot_nowait(self):  # type: ignore[no-untyped-def]
            return _NS(state=_NS(intent=ManagedTxIntent(ManagedTxIntentKind.RX)))

    radio = StubIcomRadio()
    handler = make_handler(
        radio=radio,
        store=StateStore(),
        authority=_RxAuthority(),
        queue=StubCommandQueue(),
        service=StubCommandService(),
    )
    outcome = await handler.on_command("trx", ("0",))
    assert outcome.to_sender == ("trx:0,false;",)


async def test_rtty_raw_refused_at_key() -> None:
    """Bot handler:574 — an unmapped raw mode can never match the tuple."""
    radio = _data2_radio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "RTTY",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): False,
        }
    )
    handler, authority, lane, _queue = _seam(radio=radio, store=store)
    await _program_digu(handler)
    # Front panel moved to RTTY after programming: key must refuse.
    from tci_adapter_stub import observe as _observe

    _observe(handler._store, FieldPath.active("main", "freq_mode", "mode"), "RTTY")  # noqa: SLF001
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


async def test_queued_tuple_change_refused_without_ptt() -> None:
    """Gate P0 handler:489 — the check runs inside admission (before_on)."""
    handler, authority, lane, queue = _seam()
    queue.hold_ready = True
    await _program_digu(handler)
    key_task = asyncio.ensure_future(handler.on_command("trx", ("0", "true")))
    try:
        async with asyncio.timeout(5.0):
            while not queue.entries:
                await asyncio.sleep(0.01)
        # A queued mode change lands after the early look, before admission.
        from tci_adapter_stub import observe as _observe

        _observe(
            handler._store,  # noqa: SLF001
            FieldPath.active("main", "freq_mode", "mode"),
            "LSB",
        )
        queue.release_ready()
        async with asyncio.timeout(5.0):
            outcome = await key_task
    finally:
        if not key_task.done():
            key_task.cancel()
        queue.release_ready()
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
