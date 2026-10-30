"""C2 RED: adapter Tier A/B writes (MOR-3821, spec 5.5/5.6/5.7)."""

from __future__ import annotations

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.tci.handler import CommandOutcome

from tci_adapter_stub import (
    StubAuthority,
    StubCommandQueue,
    StubCommandService,
    StubIcomRadio,
    StubMinimalRadio,
    StubProfile,
    StubWattsRadio,
    build_store,
    make_handler,
    observe,
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


@pytest.fixture()
def tcp_available() -> None:
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


def _pump(radio, store):  # type: ignore[no-untyped-def]
    """Simulate the command pump: apply queued split writes to radio+store."""

    def _execute(intent):  # type: ignore[no-untyped-def]
        if intent.name == "set_split_freq":
            hz = int(intent.params["freq_hz"])
            radio.freq_b = hz
            observe(
                store,
                FieldPath.active("sub", "freq_mode", "freq_hz"),
                hz,
            )

    return _execute


async def test_vfo_a_set_round_trip() -> None:
    radio = StubIcomRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("vfo", ("0", "0", "14075000"))
    assert outcome.to_sender == ("vfo:0,0,14075000;",)
    assert outcome.to_others == ("vfo:0,0,14075000;",)
    assert radio.freq_a == 14_075_000


async def test_vfo_b_set_survives_split_toggle() -> None:
    radio = StubIcomRadio()
    store = _loaded_store()
    handler = make_handler(radio=radio, store=store)
    assert (await handler.on_command("vfo", ("0", "1", "14076000"))).to_sender == (
        "vfo:0,1,14076000;",
    )
    assert radio.freq_b == 14_076_000
    assert (await handler.on_command("split_enable", ("0", "false"))).to_sender == (
        "split_enable:0,false;",
    )
    assert (await handler.on_command("vfo", ("0", "1"))).to_sender == (
        "vfo:0,1,14076000;",
    )
    assert (await handler.on_command("split_enable", ("0", "true"))).to_sender == (
        "split_enable:0,true;",
    )
    assert (await handler.on_command("vfo", ("0", "1"))).to_sender == (
        "vfo:0,1,14076000;",
    )


async def test_vfo_b_without_surface_echoes_current() -> None:
    handler = make_handler(radio=StubMinimalRadio(), store=_loaded_store())
    outcome = await handler.on_command("vfo", ("0", "1", "14076000"))
    assert outcome.to_sender == ("vfo:0,1,14074000;",)
    assert outcome.to_others == ()


async def test_modulation_set_usb_clears_data() -> None:
    radio = StubIcomRadio(data_a=1)
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("modulation", ("0", "USB"))
    assert outcome.to_sender == ("modulation:0,USB;",)
    assert radio.mode_a == "USB"
    assert radio.data_a == 0


async def test_modulation_set_digu_applies_policy_numeric() -> None:
    profile = StubProfile(modes=("AM", "LSB", "USB", "CW", "FM"), data_mode_count=3)
    radio = StubIcomRadio(profile=profile)
    radio.mod_inputs = {2: 0}
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,DIGU;",)
    assert outcome.to_others == ("modulation:0,DIGU;",)
    assert radio.mode_a == "USB"
    assert radio.data_a == 2
    assert radio.mod_inputs.get(2) == 5


async def test_modulation_digu_without_data_surface_degrades() -> None:
    handler = make_handler(radio=StubMinimalRadio(), store=_loaded_store())
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,USB;",)
    assert outcome.to_others == ()


async def test_modulation_set_digu_bool_fallback_on_serial_route() -> None:
    radio = StubIcomRadio()
    radio.backend_id = "icom_serial"
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("modulation", ("0", "DIGU"))
    assert outcome.to_sender == ("modulation:0,DIGU;",)
    assert radio.mode_a == "USB"
    assert radio.data_a == 1


async def test_modulation_outside_list_degrades_to_read() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    assert (await handler.on_command("modulation", ("0", "NFM"))).to_sender == (
        "modulation:0,USB;",
    )
    assert (await handler.on_command("modulation", ("0", "BOGUS"))).to_sender == (
        "modulation:0,USB;",
    )


async def test_modulation_set_syncs_b_while_split_on() -> None:
    radio = StubIcomRadio()
    store = build_store(
        {
            FieldPath.active("main", "freq_mode", "freq_hz"): 14_074_000,
            FieldPath.active("main", "freq_mode", "mode"): "USB",
            FieldPath.active("main", "freq_mode", "data_mode"): 0,
            FieldPath.global_("tx_state", "split"): True,
        }
    )
    handler = make_handler(radio=radio, store=store)
    await handler.on_command("modulation", ("0", "CW"))
    assert radio.b_mode_writes, "B must track A while split is on"
    last = radio.b_mode_writes[-1]
    assert last["mode"] == 0x03
    assert last["selected"] is False


async def test_split_enable_on_syncs_b_mode() -> None:
    radio = StubIcomRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    await handler.on_command("split_enable", ("0", "true"))
    assert radio.b_mode_writes, "enabling split syncs B to A"


async def test_split_without_capability_echoes_false() -> None:
    handler = make_handler(radio=StubMinimalRadio(), store=_loaded_store())
    outcome = await handler.on_command("split_enable", ("0", "true"))
    assert outcome.to_sender == ("split_enable:0,false;",)
    assert outcome.to_others == ()


async def test_rit_xit_round_trip() -> None:
    radio = StubIcomRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    # Offset writes change only the offset (gate handler:803): the enable
    # state is untouched until the separate enable command runs.
    assert (await handler.on_command("rit_offset", ("0", "100"))).to_sender == (
        "rit_offset:0,100;",
    )
    assert radio.rit_freq == 100
    assert radio.rit_on is False
    assert (await handler.on_command("xit_offset", ("0", "-50"))).to_sender == (
        "xit_offset:0,-50;",
    )
    assert radio.rit_freq == -50
    assert radio.rit_tx is False
    assert (await handler.on_command("rit_enable", ("0", "true"))).to_sender == (
        "rit_enable:0,true;",
    )
    assert radio.rit_on is True
    assert (await handler.on_command("rit_enable", ("0", "false"))).to_sender == (
        "rit_enable:0,false;",
    )
    assert radio.rit_on is False


async def test_rit_without_capability_echoes_current() -> None:
    handler = make_handler(radio=StubMinimalRadio(), store=_loaded_store())
    outcome = await handler.on_command("rit_offset", ("0", "100"))
    assert outcome.to_sender == ("rit_offset:0,0;",)
    assert outcome.to_others == ()


async def test_drive_set_raw255_scales() -> None:
    radio = StubIcomRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("drive", ("0", "50"))
    assert outcome.to_sender == ("drive:0,50;",)
    assert outcome.to_others == ("drive:0,50;",)
    assert radio.rf_power == 128


async def test_drive_set_watts_native_scales() -> None:
    radio = StubWattsRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("drive", ("0", "50"))
    assert outcome.to_sender == ("drive:0,50;",)
    assert radio.rf_power == 50


async def test_drive_out_of_domain_degrades_to_read() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    assert (await handler.on_command("drive", ("0", "150"))).to_sender == (
        "drive:0,50;",
    )
    assert (await handler.on_command("drive", ("0", "abc"))).to_sender == (
        "drive:0,50;",
    )


async def test_failed_write_answers_actual_state_to_requester_only() -> None:
    radio = StubIcomRadio()
    radio.fail_on.add("set_freq")
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("vfo", ("0", "0", "14075000"))
    assert outcome.to_sender == ("vfo:0,0,14074000;",)
    assert outcome.to_others == ()


async def test_cw_keyer_speed_set() -> None:
    radio = StubIcomRadio()
    handler = make_handler(radio=radio, store=_loaded_store())
    outcome = await handler.on_command("cw_keyer_speed", ("25",))
    assert outcome.to_sender == ("cw_keyer_speed:25;",)
    assert radio.key_speed == 25


async def test_tx_enable_set_answers_current() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    outcome = await handler.on_command("tx_enable", ("0", "true"))
    assert outcome.to_sender == ("tx_enable:0,false;",)
    assert outcome.to_others == ()


async def test_start_stop_echo_and_spots_dropped() -> None:
    handler = make_handler(radio=StubIcomRadio(), store=_loaded_store())
    start_outcome = await handler.on_command("start", ())
    assert start_outcome.to_sender == ("start;",)
    assert start_outcome.to_others == ("start;",)
    stop_outcome = await handler.on_command("stop", ())
    assert stop_outcome.to_sender == ("stop;",)
    assert stop_outcome.to_others == ("stop;",)
    assert await handler.on_command("spot", ("W1AW", "CW", "14074000", "1", "x")) == (
        CommandOutcome()
    )
    assert await handler.on_command("spot_delete", ("W1AW",)) == CommandOutcome()
    assert await handler.on_command("spot_clear", ()) == CommandOutcome()


async def test_managed_split_freq_write_goes_through_queue() -> None:
    radio = StubIcomRadio()
    store = _loaded_store()
    queue = StubCommandQueue(execute=_pump(radio, store))
    handler = make_handler(
        radio=radio,
        store=store,
        authority=StubAuthority(),
        queue=queue,
        service=StubCommandService(),
    )
    outcome = await handler.on_command("vfo", ("0", "1", "14076000"))
    assert outcome.to_sender == ("vfo:0,1,14076000;",)
    assert queue.entries and queue.entries[0].name == "set_split_freq"
    assert radio.freq_b == 14_076_000


async def test_conformance_product_surface_through_socket(
    tcp_available: None,
) -> None:
    """Gate tests finding: channel-B, split, RIT/XIT, keyer over a real socket.

    A real leaf server plus the real ``eesdr-tci`` client: every remaining
    user-visible write travels the entry-point-to-outcome path (managed
    channel-B writes through the production queue), asserting the final
    radio/state outcomes with hardware faked.
    """
    import asyncio

    import pytest

    pytest.importorskip("eesdr_tci")
    import importlib

    from rigplane.tci_adapter.server import TciAdapterServer

    listener_mod = importlib.import_module("eesdr_tci.listener")

    store = _loaded_store()
    radio = StubIcomRadio()
    radio.observe_store = store
    queue: StubCommandQueue = StubCommandQueue(execute=_pump(radio, store))
    handler = make_handler(
        radio=radio,
        store=store,
        authority=StubAuthority(),
        queue=queue,
        service=StubCommandService(),
    )
    server = TciAdapterServer(
        radio=radio,
        state_store=store,
        handler_factory=lambda owner: handler,
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    listener = listener_mod.Listener(f"ws://127.0.0.1:{server.bound_port}")
    seen: list = []

    async def _record(name, rx, sub_rx, params):  # type: ignore[no-untyped-def]
        seen.append((name, rx, sub_rx, params))

    for name in (
        "VFO",
        "SPLIT_ENABLE",
        "RIT_OFFSET",
        "XIT_OFFSET",
        "RIT_ENABLE",
        "XIT_ENABLE",
        "CW_KEYER_SPEED",
        "CW_MACROS_SPEED",
    ):
        listener.add_param_listener(name, _record)
    try:
        await listener.start()
        await listener.ready()
        seen.clear()

        async def _send_and_wait(cmd: str, want: tuple) -> None:  # type: ignore[no-untyped-def]
            await listener.send(cmd)
            async with asyncio.timeout(5.0):
                while want not in seen:
                    await asyncio.sleep(0.01)

        # Channel B through the managed queue (production queue path).
        await _send_and_wait("vfo:0,1,14076000;", ("VFO", 0, 1, 14076000))
        assert radio.freq_b == 14_076_000
        assert queue.entries and queue.entries[0].name == "set_split_freq"
        # Split toggles never erase the programmed channel 1.
        await _send_and_wait("split_enable:0,true;", ("SPLIT_ENABLE", 0, None, True))
        assert radio.split is True
        await listener.send("vfo:0,1;")
        await _send_and_wait("split_enable:0,false;", ("SPLIT_ENABLE", 0, None, False))
        assert radio.split is False
        # RIT/XIT offsets leave the enable state untouched over the wire.
        await _send_and_wait("rit_offset:0,100;", ("RIT_OFFSET", 0, None, 100))
        assert radio.rit_freq == 100
        assert radio.rit_on is False
        await _send_and_wait("xit_offset:0,-50;", ("XIT_OFFSET", 0, None, -50))
        assert radio.rit_freq == -50
        assert radio.rit_tx is False
        await _send_and_wait("rit_enable:0,true;", ("RIT_ENABLE", 0, None, True))
        assert radio.rit_on is True
        await _send_and_wait("xit_enable:0,true;", ("XIT_ENABLE", 0, None, True))
        assert radio.rit_tx is True
        # Keyer speeds reach the radio.
        await _send_and_wait("cw_keyer_speed:25;", ("CW_KEYER_SPEED", None, None, 25))
        assert radio.key_speed == 25
        await _send_and_wait("cw_macros_speed:30;", ("CW_MACROS_SPEED", None, None, 30))
        assert radio.key_speed == 30
    finally:
        listener.shutdown()
        try:
            async with asyncio.timeout(5.0):
                await listener.wait()
        except Exception:
            pass
        await asyncio.sleep(0)
        current = asyncio.current_task()
        for task in [
            t for t in asyncio.all_tasks() if t is not current and not t.done()
        ]:
            task.cancel()
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not current],
            return_exceptions=True,
        )
        await server.stop()


async def test_conformance_write_round_trip(tcp_available: None) -> None:
    import asyncio

    import pytest

    pytest.importorskip("eesdr_tci")
    import importlib

    from rigplane.tci_adapter.server import TciAdapterServer

    listener_mod = importlib.import_module("eesdr_tci.listener")

    store = _loaded_store()
    radio = StubIcomRadio()
    radio.observe_store = store
    handler = make_handler(radio=radio, store=store)
    server = TciAdapterServer(
        radio=radio,
        state_store=store,
        handler_factory=lambda owner: handler,
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    listener = listener_mod.Listener(f"ws://127.0.0.1:{server.bound_port}")
    seen: list = []

    async def _record(name, rx, sub_rx, params):  # type: ignore[no-untyped-def]
        seen.append((name, rx, sub_rx, params))

    listener.add_param_listener("VFO", _record)
    listener.add_param_listener("DRIVE", _record)
    try:
        await listener.start()
        await listener.ready()
        seen.clear()
        await listener.send("vfo:0,0,14075000;")
        async with asyncio.timeout(5.0):
            while not [s for s in seen if s[0] == "VFO"]:
                await asyncio.sleep(0.01)
        assert seen[-1] == ("VFO", 0, 0, 14075000)
        await listener.send("drive:0,75;")
        async with asyncio.timeout(5.0):
            while not [s for s in seen if s[0] == "DRIVE"]:
                await asyncio.sleep(0.01)
        assert seen[-1] == ("DRIVE", 0, None, 75)
    finally:
        listener.shutdown()
        try:
            async with asyncio.timeout(5.0):
                await listener.wait()
        except Exception:
            pass
        await asyncio.sleep(0)
        current = asyncio.current_task()
        for task in [
            t for t in asyncio.all_tasks() if t is not current and not t.done()
        ]:
            task.cancel()
        await asyncio.gather(
            *[t for t in asyncio.all_tasks() if t is not current],
            return_exceptions=True,
        )
        await server.stop()
