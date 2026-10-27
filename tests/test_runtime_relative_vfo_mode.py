"""Relative mode writes preserve omitted fields and require observed acceptance."""

import asyncio
from dataclasses import replace

import pytest

from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError, CommandRejectedError, TimeoutError
from rigplane.core.radio_protocol import RelativeVfoModeWritable, RelativeVfoState
from rigplane.runtime.radio import IcomRadio

from test_runtime_relative_vfo_frequency import FakeCivTransport, response


@pytest.fixture(params=["IC-7300", "IC-7300MK2", "IC-705"])
def route(request, monkeypatch):
    radio = IcomRadio("127.0.0.1", model=request.param)
    wire = FakeCivTransport()
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_raw", wire)
    return radio, wire, radio.profile.civ_addr


@pytest.mark.parametrize("readback", ["01 01 02", "04 01 02", "01 00 02", "01 01 03"])
async def test_explicit_mode_tuple_requires_matching_observed_state(route, readback):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, f"26 01 {readback}"),
    ]
    assert isinstance(radio, RelativeVfoModeWritable)
    if readback == "01 01 02":
        observed = await radio.write_relative_vfo_mode(
            0x01, selected=False, data=True, filter_width=2
        )
        assert observed == RelativeVfoState(14_074_000, "USB", 2, 1)
    else:
        with pytest.raises(CommandError, match="readback contradicts"):
            await radio.write_relative_vfo_mode(
                0x01, selected=False, data=True, filter_width=2
            )
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 01 02 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 FD"),
    ]


@pytest.mark.parametrize(
    "fields,payload",
    [({}, "01 03"), ({"data": False}, "00 03"), ({"filter_width": 2}, "01 02")],
)
async def test_omitted_fields_are_preserved_from_target_readback(
    route, fields, payload
):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 01 03"),
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, f"26 01 01 {payload}"),
    ]
    await radio.write_relative_vfo_mode(0x01, selected=False, **fields)
    assert wire.frames[2] == bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 {payload} FD")
    assert all(frame[4] in (0x25, 0x26) for frame in wire.frames)


@pytest.mark.parametrize(
    "reply,error",
    [
        ("FA", CommandRejectedError),
        ("26 01 01 00 01", CommandError),
        (None, CommandError),
        ("timeout", TimeoutError),
    ],
)
async def test_mode_requires_positive_ack_before_readback(route, reply, error):
    radio, wire, addr = route
    if reply == "timeout":
        wire.replies = [TimeoutError("timeout")]
    else:
        wire.replies = [response(addr, reply) if reply is not None else None]
    with pytest.raises(error):
        await radio.write_relative_vfo_mode(
            0x01, selected=False, data=False, filter_width=1
        )
    assert len(wire.frames) == 1


@pytest.mark.parametrize(
    "missing", ["set_unselected_mode", "get_unselected_mode", "get_unselected_freq"]
)
async def test_missing_command_refuses_before_io(route, missing):
    radio, wire, _addr = route
    cmd_map = radio.profile.command_map
    radio._profile = replace(
        radio.profile,
        command_map=CommandMap(
            {key: cmd_map.get(key) for key in cmd_map if key != missing}
        ),
    )
    with pytest.raises(CommandError, match="unavailable"):
        await radio.write_relative_vfo_mode(0x01, selected=False)
    assert wire.frames == []


@pytest.mark.parametrize(
    "mode,fields",
    [
        (0xFF, {}),
        (0x01, {"data": 2}),
        (0x01, {"filter_width": 0}),
        (0x01, {"filter_width": 2400}),
    ],
)
async def test_invalid_tuple_refuses_before_io(route, mode, fields):
    radio, wire, _addr = route
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(mode, selected=False, **fields)
    assert wire.frames == []


@pytest.mark.parametrize("reply", ["26 01 01", "26 01 01 02 03", "26 00 01 01 03"])
async def test_invalid_preservation_readback_does_not_write(route, reply):
    radio, wire, addr = route
    wire.replies = [response(addr, "25 01 00 40 07 14 00"), response(addr, reply)]
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(0x01, selected=False, filter_width=2)
    assert len(wire.frames) == 2
    assert all(len(frame) <= 7 for frame in wire.frames)


@pytest.mark.parametrize("reply", [None, "26 01 01", "26 00 01 01 03"])
async def test_missing_or_wrong_mode_readback_is_failure(route, reply):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, reply) if reply is not None else None,
    ]
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(
            0x01, selected=False, data=False, filter_width=1
        )


async def test_profile_identity_is_not_a_mode_gate(route):
    radio, wire, addr = route
    radio._profile = replace(radio.profile, id="arbitrary_profile_id")
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 00 01"),
    ]
    observed = await radio.write_relative_vfo_mode(
        0x01, selected=False, data=False, filter_width=1
    )
    assert observed == RelativeVfoState(14_074_000, "USB", 1, 0)
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 00 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 FD"),
    ]


async def test_selected_mode_uses_existing_selected_command_declaration(route):
    radio, wire, addr = route
    if not radio.profile.command_map.has("set_selected_mode"):
        with pytest.raises(CommandError, match="unavailable"):
            await radio.write_relative_vfo_mode(0x01, selected=True)
        assert wire.frames == []
        return
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 00 00 40 07 14 00"),
        response(addr, "26 00 01 00 01"),
    ]
    await radio.write_relative_vfo_mode(0x01, selected=True, data=False, filter_width=1)
    assert wire.frames[0] == bytes.fromhex(f"FE FE {addr:02X} E0 26 00 01 00 01 FD")


@pytest.mark.parametrize(
    "model,method,args,kwargs",
    [
        ("IC-7300", "set_vfo_slot", ("B",), {}),
        ("IC-7300", "_set_vfo_slot_confirmed", ("B",), {}),
        ("IC-7300", "_set_vfo_wire", ("B",), {}),
        ("IC-7300", "swap_vfo_ab", (), {}),
        ("IC-7300", "equalize_vfo_ab", (), {}),
        ("IC-7610", "set_vfo_slot", ("B",), {"receiver": 1}),
        ("IC-7610", "_set_vfo_slot_confirmed", ("B",), {"receiver": 1}),
        ("IC-7610", "select_receiver", (1,), {}),
        ("IC-7610", "swap_main_sub", (), {}),
        ("IC-7610", "equalize_main_sub", (), {}),
    ],
)
@pytest.mark.parametrize("ending", ["complete", "error", "cancel"])
async def test_selection_methods_mark_epoch_until_completion(
    monkeypatch, model, method, args, kwargs, ending
):
    radio = IcomRadio("127.0.0.1", model=model)
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(frame, **_kwargs):
        entered.set()
        await release.wait()
        if ending == "error":
            raise RuntimeError("selection failed")
        return response(radio.profile.civ_addr, "FB")

    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_raw", send)
    initial_epoch = radio._vfo_selection_epoch
    task = asyncio.create_task(getattr(radio, method)(*args, **kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert radio._vfo_selection_epoch != initial_epoch
        assert radio._vfo_selection_epoch % 2 == 0
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if ending == "error":
                with pytest.raises(RuntimeError, match="selection failed"):
                    await task
            else:
                await task
        assert radio._vfo_selection_epoch != initial_epoch
        assert radio._vfo_selection_epoch % 2 == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("first_to_finish", [0, 1])
async def test_overlapping_selection_changes_stay_in_progress(
    monkeypatch, first_to_finish
):
    radio = IcomRadio("127.0.0.1", model="IC-7300")
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    frames = []

    async def send(frame, **_kwargs):
        index = len(frames)
        frames.append(frame)
        entered[index].set()
        await release[index].wait()
        return response(radio.profile.civ_addr, "FB")

    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_raw", send)
    initial_epoch = radio._vfo_selection_epoch
    tasks = []
    try:
        tasks.append(asyncio.create_task(radio.set_vfo_slot("B")))
        await asyncio.wait_for(entered[0].wait(), 1)
        tasks.append(asyncio.create_task(radio.swap_vfo_ab()))
        await asyncio.wait_for(entered[1].wait(), 1)
        assert radio._vfo_selection_epoch % 2 == 0
        with pytest.raises(CommandRejectedError):
            await radio.write_relative_vfo_mode(0x01, selected=False)
        assert len(frames) == 2
        release[first_to_finish].set()
        await tasks[first_to_finish]
        assert radio._vfo_selection_epoch % 2 == 0
        with pytest.raises(CommandRejectedError):
            await radio.write_relative_vfo_mode(0x01, selected=False)
        assert len(frames) == 2
        release[1 - first_to_finish].set()
        await tasks[1 - first_to_finish]
        assert radio._vfo_selection_epoch % 2 == 1
        assert radio._vfo_selection_epoch != initial_epoch
    finally:
        for event in release:
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_explicit_tuple_needs_no_preservation_during_selection(route):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 01 02"),
    ]
    with radio._vfo_selection_change():
        await radio.write_relative_vfo_mode(
            0x01, selected=False, data=True, filter_width=2
        )
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 01 02 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 FD"),
    ]


async def test_epoch_is_checked_after_before_set_hook(route):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 01 03"),
    ]

    def selection_changed():
        with radio._vfo_selection_change():
            pass

    with pytest.raises(CommandRejectedError):
        await radio.write_relative_vfo_mode(
            0x01, selected=False, before_set=selection_changed
        )
    assert len(wire.frames) == 2
    assert all(len(frame) == 7 for frame in wire.frames)


async def test_single_receiver_selection_noop_keeps_epoch(route):
    radio, wire, _addr = route
    initial_epoch = radio._vfo_selection_epoch
    await radio.select_receiver(0)
    assert radio._vfo_selection_epoch == initial_epoch
    assert wire.frames == []
