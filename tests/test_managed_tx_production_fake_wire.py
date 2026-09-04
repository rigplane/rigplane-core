import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.radio import CoreRadio


class _DelayedWire:
    def __init__(self) -> None:
        self.on_started = asyncio.Event()
        self.release_on = asyncio.Event()
        self.wire: list[bool] = []

    async def finish(self, on: bool, is_current: Callable[[], bool]) -> None:
        if on:
            self.on_started.set()
            try:
                await self.release_on.wait()
            except asyncio.CancelledError:
                await self.release_on.wait()
        if is_current():
            self.wire.append(on)


class _IcomActuator(_DelayedWire):
    actuate = CoreRadio.actuate

    def __init__(self) -> None:
        super().__init__()
        self._commands = type(
            "Commands",
            (),
            {
                "ptt_on": staticmethod(lambda **_kw: b"ON"),
                "ptt_off": staticmethod(lambda **_kw: b"OFF"),
                "stop_cw": staticmethod(lambda **_kw: b"CW-OFF"),
                "set_tuner_status": staticmethod(lambda *_a, **_kw: b"TUNE-OFF"),
            },
        )()
        self._radio_addr = 0x94

    async def _send_civ_raw(
        self, frame: bytes, *, is_current: Callable[[], bool], **_kw: Any
    ) -> None:
        if frame in (b"ON", b"OFF"):
            await self.finish(frame == b"ON", is_current)


class _YaesuActuator(_DelayedWire):
    actuate = YaesuCatRadio.actuate

    async def _write(
        self,
        command: str,
        *,
        is_current: Callable[[], bool],
        **params: Any,
    ) -> None:
        if command == "set_ptt":
            await self.finish(params["state"] == "1", is_current)


class _RigctldActuator(_DelayedWire):
    actuate = RigctldClientRadio.actuate

    async def _set_ptt(
        self,
        on: bool,
        *,
        is_current: Callable[[], bool],
        urgent: bool,
    ) -> None:
        del urgent
        await self.finish(on, is_current)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_real_provider_actuator_suppresses_stale_on_at_final_wire(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator = actuator_type()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{actuator_type.__name__}.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(actuator)
    await composition.bind_state_store(store)

    submission = await composition.authority.submit_ptt(True, "provider-owner")
    await asyncio.wait_for(actuator.on_started.wait(), 0.2)
    store.begin_provider_generation()
    actuator.release_on.set()
    await submission.wait_settlement()
    await composition.transport_ready(object())

    assert True not in actuator.wire
    assert actuator.wire[-1:] == [False]
    await composition.shutdown(asyncio.Event())
