"""MOR-3632/MOR-3633: every managed PTT entry passes ONE positive-TX admission.

The one seat is ``ManagedTxAuthority`` consulting
``ManagedTxComposition.tx_frequency_refusal`` under its lock, after the ordered
queue barrier, immediately before any positive transition. Every managed entry
that can key the transmitter is driven here through its public interface on
the production stack (``test_tx_under_fault_matrix._stand_up_stack``: real
``WebServer`` and ``RigctldServer`` listeners, the production ``RadioPoller``
loop and acquisition, ``IcomRadio`` over the ``MockIcomRadio`` LAN simulator
modelling split and the CI-V 1C 03 transmit-frequency read):

* WebSocket ``ptt_on`` and ``ptt`` ``{"state": true}``,
* HTTP ``transmit_on`` (the latched ``ManagedTxAuthority.transmit_on``),
* rigctld ``T 1`` over TCP,
* the shared managed-key submission a TCI server uses
  (``managed_tx_submission.execute_managed_ptt`` with ``source="tci"`` and a
  ``before_on`` route-preparation hook; no TCI listener exists in Core).

Each entry gets the same four verdicts: (a) a known out-of-band target is
refused ``outside_tx_range``; (b) an unknown target on a profile that publishes
one is refused ``ptt_tx_frequency_unknown``; (c) a profile that can never
publish a target keys; (d) a known in-band target keys. Releases never consult
the probe. The retune-and-key regressions drive the write and the key through
the same public interfaces and never publish the resulting target by hand.

FAKE RADIO ONLY -- the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.mock_integration

from rigplane.core.state_pipeline_contracts import FieldPath  # noqa: E402
from rigplane.core.tx_target import KnownTxTarget  # noqa: E402
from rigplane.runtime.controller_authority import ControllerError  # noqa: E402
from rigplane.runtime.managed_tx_submission import (  # noqa: E402
    ManagedKeyRefused,
    ManagedPttTracker,
    execute_managed_ptt,
)

from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEY_DEADLINE_S,
    _LEASE_BUDGET_S,
    _LINK_FAULT_TOT_S,
    _http_json,
    _RigctldKeyClient,
    _Stack,
    _stand_up_stack,
    _tear_down_stack,
    _wait_ptt,
    _wait_ptt_observed,
    _wait_released,
    _WsKeyClient,
)

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")
_MANAGED = "/api/v1/managed-transmit/command"
# Band facts come from the shipped profiles: IC-7610 transmits on HF + 6 m
# only, so 2 m is outside every TX row; IC-9700 transmits on 2 m/70 cm/23 cm
# only, so 20 m would be outside every TX row if the permit applied to it.
_TWENTY_METRES = 14_074_000
_TWO_METRES = 145_000_000
_IC9700_CIV_ADDR = 0xA2
# Publishing a target takes one acquisition cadence (2 s on IC-7610) after
# the split flag that labels the 1C 03 reply is FRESH.
_TARGET_DEADLINE_S = 15.0


@dataclass(frozen=True)
class _Verdict:
    keyed: bool
    code: str | None = None
    details: dict[str, Any] | None = None


class _Entry:
    """One managed PTT entry: key once, then release what it keyed."""

    name = ""

    async def open(self, stack: _Stack) -> None:
        self.stack = stack

    async def key(self) -> _Verdict:
        raise NotImplementedError

    async def release(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class _WsEntry(_Entry):
    def __init__(
        self, name: str, on: tuple[str, dict[str, Any]], off: tuple[str, dict[str, Any]]
    ) -> None:
        self.name = name
        self._on, self._off = on, off

    async def open(self, stack: _Stack) -> None:
        await super().open(stack)
        self.client = _WsKeyClient("127.0.0.1", stack.web.port)
        await self.client.connect()

    async def key(self) -> _Verdict:
        response = await self.client.command("key", *self._on)
        if response.get("ok") is True:
            return _Verdict(True)
        return _Verdict(False, response.get("message"), response.get("details"))

    async def release(self) -> None:
        response = await self.client.command("unkey", *self._off)
        assert response.get("ok") is True, response

    async def close(self) -> None:
        await self.client.close()


class _HttpEntry(_Entry):
    name = "http_transmit_on"

    async def key(self) -> _Verdict:
        status, body = await _http_json(
            self.stack.web.port, "POST", _MANAGED, {"operation": "transmit_on"}
        )
        if status == 202:
            return _Verdict(True)
        assert status == 409, (status, body)
        return _Verdict(False, body.get("error"), body.get("details"))

    async def release(self) -> None:
        status, body = await _http_json(
            self.stack.web.port, "POST", _MANAGED, {"operation": "force_off"}
        )
        assert status == 202, (status, body)


class _RigctldEntry(_Entry):
    name = "rigctld_t1"

    async def open(self, stack: _Stack) -> None:
        await super().open(stack)
        self.client = _RigctldKeyClient("127.0.0.1", stack.rigctld_port)
        await self.client.connect()

    async def line(self, text: str) -> bytes:
        writer, reader = self.client._writer, self.client._reader  # noqa: SLF001
        writer.write(text.encode() + b"\n")
        await writer.drain()
        return await asyncio.wait_for(reader.readline(), timeout=10.0)

    async def key(self) -> _Verdict:
        reply = await self.line("T 1")
        if reply == b"RPRT 0\n":
            return _Verdict(True)
        # rigctld carries no reason text: every TX refusal is ERJCTED.
        assert reply == b"RPRT -9\n", reply
        return _Verdict(False)

    async def release(self) -> None:
        assert await self.line("T 0") == b"RPRT 0\n"

    async def close(self) -> None:
        await self.client.close()


class _TciEntry(_Entry):
    """The shared managed-key submission a TCI server drives (MOR-3821)."""

    name = "tci_managed_key"
    _owner = "tci-client-1"

    async def open(self, stack: _Stack) -> None:
        await super().open(stack)
        self.tracker = ManagedPttTracker()
        self.route_prepared = 0

    async def _submit(self, on: bool) -> None:
        web = self.stack.web
        authority = self.stack.composition.authority
        loop = asyncio.get_running_loop()

        async def before_on() -> bool:
            self.route_prepared += 1
            return True

        submission, ready = self.tracker.begin_key(
            authority,
            owner=self._owner,
            on=on,
            expires_at_monotonic=loop.time() + 10.0,
            connection_generation=(
                web.command_queue.capture_connection_generation() if on else None
            ),
            # The route preparation runs inside an admitted key-down only.
            before_on=before_on if on else None,
        )
        await execute_managed_ptt(
            authority=authority,
            queue=web.command_queue,
            owner=self._owner,
            on=on,
            submission=submission,
            ready=ready,
            source="tci",
            provider_generation=web.command_state_store.provider_generation,
        )

    async def key(self) -> _Verdict:
        try:
            await self._submit(True)
        except ControllerError as exc:
            assert self.route_prepared == 0, "before_on ran for a refused key"
            return _Verdict(False, exc.code, exc.details or None)
        except ManagedKeyRefused:
            return _Verdict(False)
        assert self.route_prepared == 1
        return _Verdict(True)

    async def release(self) -> None:
        await self._submit(False)


_ENTRIES: dict[str, Callable[[], _Entry]] = {
    "ws_ptt_on": lambda: _WsEntry("ws_ptt_on", ("ptt_on", {}), ("ptt_off", {})),
    "ws_ptt_state": lambda: _WsEntry(
        "ws_ptt_state", ("ptt", {"state": True}), ("ptt", {"state": False})
    ),
    "http_transmit_on": _HttpEntry,
    "rigctld_t1": _RigctldEntry,
    "tci_managed_key": _TciEntry,
}


@dataclass(frozen=True)
class _Case:
    model: str
    sim: dict[str, Any]
    frequency_hz: int
    keyed: bool
    code: str | None
    details: dict[str, str] | None = None


_CASES = {
    "out_of_band": _Case(
        "IC-7610", {}, _TWO_METRES, keyed=False, code="outside_tx_range"
    ),
    "unknown_target": _Case(
        "IC-7610",
        {"models_tx_frequency": False},
        _TWENTY_METRES,
        keyed=False,
        code="ptt_tx_frequency_unknown",
        details={"txTargetReason": "not-observed"},
    ),
    "non_publishing_profile": _Case(
        "IC-9700",
        {"radio_addr": _IC9700_CIV_ADDR},
        _TWENTY_METRES,
        keyed=True,
        code=None,
    ),
    "in_band": _Case("IC-7610", {}, _TWENTY_METRES, keyed=True, code=None),
}


async def _wait_target(
    stack: _Stack, accept: Callable[[object], bool], deadline_s: float
) -> None:
    """Wait for the production poller to publish a target ``accept`` takes."""
    end = time.monotonic() + deadline_s
    value: object = None
    while time.monotonic() < end:
        with contextlib.suppress(KeyError):
            value = (
                stack.web.command_state_store.snapshot().field(_TX_TARGET_PATH).value
            )
            if accept(value):
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"TX target never satisfied the wait: last {value!r}")


def _known_at(frequency_hz: int) -> Callable[[object], bool]:
    return lambda value: (
        isinstance(value, KnownTxTarget) and value.frequency_hz == frequency_hz
    )


def _count_probe(stack: _Stack) -> list[int]:
    """Count the composition's admission-probe calls (the authority's seat)."""
    composition = stack.composition
    probe = composition.tx_frequency_refusal
    calls = [0]

    def counted() -> Any:
        calls[0] += 1
        return probe()

    composition.tx_frequency_refusal = counted  # type: ignore[method-assign]
    return calls


@contextlib.asynccontextmanager
async def _stack(tmp_path: Path, case: _Case) -> Any:
    sim_options = dict(case.sim)
    stack = await _stand_up_stack(
        tmp_path,
        loss="release",
        tot_seconds=_LINK_FAULT_TOT_S,
        unsolicited=False,
        sim_options=sim_options,
        model=case.model,
    )
    try:
        yield stack
    finally:
        await _tear_down_stack(stack)


@pytest.mark.parametrize("case_name", tuple(_CASES))
@pytest.mark.parametrize("entry_name", tuple(_ENTRIES))
async def test_every_managed_ptt_entry_passes_the_one_admission_probe(
    entry_name: str, case_name: str, tmp_path: Path
) -> None:
    case = _CASES[case_name]
    entry = _ENTRIES[entry_name]()
    async with _stack(tmp_path, case) as stack:
        stack.sim.set_frequency(case.frequency_hz)
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)
        if case_name == "non_publishing_profile":
            from rigplane.runtime._state_queries import profile_publishes_tx_target

            assert profile_publishes_tx_target(stack.radio.profile) is False
        elif case.code != "ptt_tx_frequency_unknown":
            await _wait_target(stack, _known_at(case.frequency_hz), _TARGET_DEADLINE_S)
        calls = _count_probe(stack)
        writes = len(stack.sim.ptt_set_writes)
        await entry.open(stack)
        try:
            verdict = await entry.key()
            assert calls[0] == 1, "every positive transition consults the probe once"
            if case.keyed:
                assert verdict == _Verdict(True)
                await _wait_ptt(stack.sim, True, _KEY_DEADLINE_S)
                await entry.release()
                await _wait_ptt(stack.sim, False, _LEASE_BUDGET_S)
                assert calls[0] == 1, "a release never consults the probe"
            else:
                assert verdict.keyed is False
                if entry_name != "rigctld_t1":
                    assert verdict.code == case.code, verdict
                    assert verdict.details == case.details, verdict
                assert stack.sim.ptt_set_writes[writes:] == [], (
                    "a refused key reached the wire"
                )
                assert stack.composition.authority.tx_holder_nowait() is None
        finally:
            await entry.close()


async def _rigctld(stack: _Stack) -> _RigctldEntry:
    entry = _RigctldEntry()
    await entry.open(stack)
    return entry


async def test_rigctld_retune_out_of_band_then_key_is_refused_and_back_keys(
    tmp_path: Path,
) -> None:
    """WSJT-X shape: ``F`` then ``T 1`` at once, both directions (P1 #2)."""
    async with _stack(tmp_path, _CASES["in_band"]) as stack:
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)
        await _wait_target(stack, _known_at(_TWENTY_METRES), _TARGET_DEADLINE_S)
        rig = await _rigctld(stack)
        writes = len(stack.sim.ptt_set_writes)
        try:
            assert await rig.line(f"F {_TWO_METRES}") == b"RPRT 0\n"
            # No wait: the key is judged on the radio after the retune, not
            # on the target published before it.
            assert await rig.key() == _Verdict(False)
            assert stack.sim.ptt_set_writes[writes:] == []
            assert await rig.line(f"F {_TWENTY_METRES}") == b"RPRT 0\n"
            assert await rig.key() == _Verdict(True)
            await _wait_ptt(stack.sim, True, _KEY_DEADLINE_S)
            await rig.release()
            await _wait_ptt(stack.sim, False, _LEASE_BUDGET_S)
        finally:
            await rig.close()


async def test_ws_retune_out_of_band_then_key_is_refused(tmp_path: Path) -> None:
    """The WS write and the WS key share the ordered queue (P1 #2)."""
    async with _stack(tmp_path, _CASES["in_band"]) as stack:
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)
        await _wait_target(stack, _known_at(_TWENTY_METRES), _TARGET_DEADLINE_S)
        entry = _WsEntry("ws_ptt_on", ("ptt_on", {}), ("ptt_off", {}))
        await entry.open(stack)
        writes = len(stack.sim.ptt_set_writes)
        try:
            response = await entry.client.command(
                "retune", "set_freq", {"freq": _TWO_METRES}
            )
            assert response.get("ok") is True, response
            assert await entry.key() == _Verdict(False, "outside_tx_range")
            assert stack.sim.ptt_set_writes[writes:] == []
        finally:
            await entry.close()


async def test_split_receive_retune_keeps_the_out_of_band_tx_vfo_refused(
    tmp_path: Path,
) -> None:
    """Retuning the receive VFO must not exempt a split TX VFO (P1 #4)."""
    async with _stack(tmp_path, _CASES["in_band"]) as stack:
        sim = stack.sim
        sim.set_unselected_frequency(_TWO_METRES)
        sim.set_split(True)
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)
        await _wait_target(stack, _known_at(_TWO_METRES), _TARGET_DEADLINE_S)
        rig = await _rigctld(stack)
        writes = len(sim.ptt_set_writes)
        try:
            # The selected (receive) VFO moves inside 20 m; TX stays on 2 m.
            assert await rig.line(f"F {_TWENTY_METRES + 1_000}") == b"RPRT 0\n"
            assert await rig.key() == _Verdict(False)
            assert sim.ptt_set_writes[writes:] == []
        finally:
            await rig.close()


async def test_a_refused_key_leaves_the_seat_clean_for_the_next_one(
    tmp_path: Path,
) -> None:
    async with _stack(tmp_path, _CASES["out_of_band"]) as stack:
        stack.sim.set_frequency(_TWO_METRES)
        await _wait_ptt_observed(stack.web, _GATE_TIMEOUT_S)
        await _wait_target(stack, _known_at(_TWO_METRES), _TARGET_DEADLINE_S)
        rig = await _rigctld(stack)
        try:
            assert await rig.key() == _Verdict(False)
            await _wait_released(stack.composition)
            assert await rig.line(f"F {_TWENTY_METRES}") == b"RPRT 0\n"
            assert await rig.key() == _Verdict(True)
            await _wait_ptt(stack.sim, True, _KEY_DEADLINE_S)
            await rig.release()
        finally:
            await rig.close()
