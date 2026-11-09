"""Which declared path a refused read (CI-V FA) belongs to (MOR-4058).

The read goes out fire-and-forget through the real CI-V runtime
(``CivRuntime._execute_civ_raw`` registers its tracker sink), the radio's
bare ``FA`` comes back through ``CivRuntime._route_civ_frame``, and the
profile's own acquisition scheduler records the refusal. Only the path whose
read the radio refused may be marked refused.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import ClassVar

import pytest

from rigplane.core.transport import IcomCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.acquisition_scheduler import AcquisitionScheduler
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio import IcomRadio
from rigplane.runtime._state_queries import (
    acquisition_query_resolver_for_profile,
    wire_parts_for_query,
)

from _helpers import observe_selected_receiver
from rigplane_testkit.leaked_radio_guard import fake_connected


class _SilentTransport:
    """Records outgoing packets; the radio's answers are routed by hand."""

    #: The LAN transport's ingress-order contract (MOR-4209).
    ordered_ingress: ClassVar[bool] = IcomCivTransport.ordered_ingress

    my_id = 0x00010001
    remote_id = 0xDEADBEEF

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        self.sent.append(data)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


def _radio(model: str) -> tuple[IcomRadio, AcquisitionScheduler]:
    radio = IcomRadio("192.168.1.100", model=model)
    transport = _SilentTransport()
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    fake_connected(radio)
    observe_selected_receiver(radio, 0)
    scheduler = AcquisitionScheduler(profile=radio._profile.state_acquisition)
    radio._acquisition_scheduler = scheduler
    return radio, scheduler


async def _refuse(radio: IcomRadio, command: int, sub: int | None, data: bytes) -> None:
    """Send one fire-and-forget read and answer it with a bare FA."""
    frame = build_civ_frame(
        radio._radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data
    )
    await radio._civ_runtime._execute_civ_raw(frame, wait_response=False)
    nak = build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFA)
    await radio._civ_runtime._route_civ_frame(
        parse_civ_frame(nak), generation=radio._civ_epoch
    )


def _menu_paths(
    radio: IcomRadio, scheduler: AcquisitionScheduler
) -> dict[bytes, FieldPath]:
    """Declared paths read through ``1A 05 <menu address>``, by address."""
    resolve = acquisition_query_resolver_for_profile(radio._profile)
    found: dict[bytes, FieldPath] = {}
    for path in sorted(scheduler.declared_paths(), key=str):
        query = resolve(path)
        if query is not None and (query.command, query.sub) == (0x1A, 0x05):
            if query.receiver is None:
                found.setdefault(query.data, path)
    return found


@pytest.fixture(autouse=True)
async def _stop_pump():
    yield
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task() and "_civ_rx_loop" in repr(task):
            task.cancel()


@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
async def test_a_refused_menu_read_refuses_only_its_own_menu_item(model: str) -> None:
    """A ``1A 05`` read answers by its menu address: a refused read of
    another menu item (sent through ``send_civ`` by any caller) is not a
    refusal of the declared one."""
    radio, scheduler = _radio(model)
    menus = _menu_paths(radio, scheduler)
    assert menus, "not vacuous: the profile declares a menu-item read"
    address, path = next(iter(menus.items()))
    other = bytes([address[0], address[1] ^ 0x01])
    assert other not in menus

    await _refuse(radio, 0x1A, 0x05, other)

    assert scheduler.consecutive_refusals(path) == 0
    await _refuse(radio, 0x1A, 0x05, address)
    assert scheduler.consecutive_refusals(path) == 1


async def test_ic705_nb_depth_refusal_is_not_a_vox_delay_refusal() -> None:
    """The reviewed case: NB depth ``1A 05 03 57``, VOX delay ``1A 05 03 59``."""
    radio, scheduler = _radio("IC-705")
    vox_delay = FieldPath.global_("operator_controls", "vox_delay")
    menus = _menu_paths(radio, scheduler)
    assert menus[b"\x03\x59"] == vox_delay

    await _refuse(radio, 0x1A, 0x05, b"\x03\x57")

    assert scheduler.consecutive_refusals(vox_delay) == 0
    await _refuse(radio, 0x1A, 0x05, b"\x03\x59")
    assert scheduler.consecutive_refusals(vox_delay) == 1


def _receiver_routed(radio: IcomRadio, scheduler: AcquisitionScheduler) -> tuple:
    """A declared MAIN path and its declared SUB twin, both read via cmd29."""
    resolve = acquisition_query_resolver_for_profile(radio._profile)
    declared = sorted(scheduler.declared_paths(), key=str)
    for main in declared:
        query = resolve(main)
        if (
            query is None
            or query.receiver != 0
            or not radio._profile.supports_cmd29(query.command, query.sub)
        ):
            continue
        for sub in declared:
            twin = resolve(sub)
            if (
                sub.family == main.family
                and sub.name == main.name
                and sub.slot == main.slot
                and twin is not None
                and twin.receiver == 1
                and (twin.command, twin.sub, twin.data)
                == (query.command, query.sub, query.data)
            ):
                return main, sub, query
    raise AssertionError("no cmd29-routed MAIN/SUB pair declared")


async def test_plain_read_of_the_selected_sub_does_not_refuse_main() -> None:
    """IC-7610 reads MAIN and SUB through cmd29. A plain read (SUB selected,
    in another mode than MAIN) answered with FA names no declared read:
    neither receiver's path is refused. A wrapped refusal refuses exactly
    its own receiver."""
    radio, scheduler = _radio("IC-7610")
    observe_selected_receiver(radio, 1)
    main, sub, query = _receiver_routed(radio, scheduler)
    data = query.data

    await _refuse(radio, query.command, query.sub, data)

    assert scheduler.consecutive_refusals(main) == 0
    assert scheduler.consecutive_refusals(sub) == 0

    command, wire_sub, wire_data = wire_parts_for_query(replace(query, receiver=1), 0)
    await _refuse(radio, command, wire_sub, wire_data)
    assert scheduler.consecutive_refusals(sub) == 1
    assert scheduler.consecutive_refusals(main) == 0


async def test_a_direct_answer_ends_a_refusal_with_no_request_pending() -> None:
    """Pre-review finding 1: the tuner read is refused and its request is
    gone; a real answer then arrives for a direct getter, not for an
    acquisition request. That answer ends the refusal and its withholding."""
    radio, scheduler = _radio("IC-705")
    tuner = FieldPath.global_("operator_controls", "tuner_status")
    await _refuse(radio, 0x1C, 0x01, b"")
    assert scheduler.consecutive_refusals(tuner) == 1
    assert tuner not in scheduler.unobserved_startup_paths(())
    assert scheduler.pending_requests() == ()

    answer = build_civ_frame(
        CONTROLLER_ADDR, radio._radio_addr, 0x1C, sub=0x01, data=b"\x01"
    )
    await radio._civ_runtime._route_civ_frame(
        parse_civ_frame(answer), generation=radio._civ_epoch
    )

    assert radio._state_store.snapshot().field(tuner).value is not None
    assert scheduler.consecutive_refusals(tuner) == 0
    assert tuner in scheduler.unobserved_startup_paths(())
