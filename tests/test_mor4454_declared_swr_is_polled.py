"""MOR-4454: a profile that declares ``get_swr`` must have SWR acquired.

The G7250 declared ``get_swr`` (and a calibrated ``[meters.swr]`` table) yet
listed ``global.meters.swr`` as ``unknown``, so no acquisition path ever read
it: every snapshot carried ``swrMeter`` null with availability "undeclared".

Two layers, both through the shared mechanism (profile data, no per-radio
branch):

- a census over every shipped ``rigs/*.toml``: a declared ``get_swr`` with a
  calibration table is polled TX-only on the canonical PTT; a declared
  ``get_swr`` without a scale is classified explicitly (never left silent);
- a seam test: the real ``AcquisitionScheduler`` + ``StateFreshnessService``
  + ``RadioPoller`` over a fake CI-V wire, on the LAN and the USB serial
  radio classes, reads ``0x15 0x12`` while PTT is on and never in RX, and
  publishes a calibrated SWR into the store and into the web state payload.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.ic705.serial import XieguSerialRadio
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    MeterObservationCoalescer,
    StateFreshnessService,
    civ_transport_budget_hz,
)
from rigplane.core.state_acquisition_policy import AvailabilityClause
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessClock, FreshnessState
from rigplane.profiles import get_radio_profile
from rigplane.rig_loader import discover_rigs
from rigplane.runtime.radio import IcomRadio
from rigplane.web.state_schema import ServerStatePublic
from rigplane.web.runtime_helpers import (
    build_public_state_payload_from_snapshot,
    snapshot_field_status_inputs,
)
from rigplane.types import CivFrame
from rigplane.web.radio_poller import CommandQueue, RadioPoller

RIGS_DIR = Path(__file__).parents[1] / "rigs"
SWR = FieldPath.global_("meters", "swr")
PTT = FieldPath.global_("tx_state", "ptt")
PTT_CLAUSE = AvailabilityClause(field=PTT, operator="equals", value=True)
SWR_SUB = 0x12


def _declaring_profiles() -> list[str]:
    models = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        profile = rig.to_profile()
        if profile.state_acquisition is None:
            continue
        command_map = profile.command_map
        if command_map is not None and command_map.has("get_swr"):
            models.append(model)
    return models


@pytest.mark.parametrize("model", _declaring_profiles())
def test_declared_get_swr_is_acquired_or_explicitly_classified(model: str) -> None:
    profile = get_radio_profile(model)
    acquisition = profile.state_acquisition
    assert acquisition is not None
    calibrations = profile.meter_calibrations or {}
    if "swr" in calibrations:
        # A scale exists: the reading is meaningful, so it must be read, only
        # while transmitting, like every other TX meter (MOR-2540).
        assert acquisition.capability_for(SWR).can_poll, model
        policy = acquisition.policy_for(SWR)
        assert policy.tx_only is True, model
        assert policy.available_when == (PTT_CLAUSE,), model
        assert acquisition.capability_for(PTT).can_poll, model
    else:
        # No scale: not polled, but the profile must say so on purpose.
        assert SWR in {c.path for c in acquisition.capabilities}, model
        assert not acquisition.capability_for(SWR).can_poll, model


def test_g7250_swr_is_polled_and_x6200_stays_unpolled() -> None:
    g7250 = get_radio_profile("G7250").state_acquisition
    x6200 = get_radio_profile("X6200").state_acquisition
    assert g7250 is not None and x6200 is not None
    assert g7250.capability_for(SWR).can_poll
    assert not x6200.capability_for(SWR).can_poll


# ── Seam: real scheduler + poller over a fake CI-V wire ─────────────────────

RadioFactory = Callable[[], Any]


def _lan(model: str) -> RadioFactory:
    return lambda: IcomRadio("192.0.2.1", model=model)


def _usb(model: str) -> RadioFactory:
    return lambda: XieguSerialRadio(device="/dev/null", model=model)


class _Rig:
    def __init__(self, radio: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.clock = FreshnessClock(start=100.0)
        monkeypatch.setattr("time.monotonic", self.clock.now)
        self.radio = radio
        session = getattr(radio, "_serial_session", None)
        if session is not None:
            # A serial radio here models an OPEN port (the wire is stubbed via
            # ``_civ`` below); acquisition pauses only on a closed serial link.
            monkeypatch.setattr(type(session), "connected", property(lambda _s: True))
        self.store = radio.state_store
        self.store._freshness_clock = self.clock
        acquisition = radio.profile.state_acquisition
        assert acquisition is not None
        scheduler = AcquisitionScheduler(
            profile=acquisition,
            clock=self.clock,
            transport_budget_hz=civ_transport_budget_hz(radio),
        )
        radio._acquisition_scheduler = scheduler
        radio._meter_observation_coalescer = MeterObservationCoalescer()
        self.service = StateFreshnessService(
            store=self.store, scheduler=scheduler, radio=radio
        )
        radio._state_freshness_service = self.service
        self.poller = RadioPoller(radio, CommandQueue(), state_store=self.store)
        self.sent: list[tuple[int, int | None, bytes]] = []
        self.keyed = False
        monkeypatch.setattr(self.poller, "_civ", self._wire)

    async def receive(self, command: int, sub: int, data: bytes) -> None:
        radio = self.radio
        radio._last_civ_data_received = self.clock.now()
        await radio._civ_runtime._route_civ_frame(
            CivFrame(0xE0, radio.profile.civ_addr, command, sub, data),
            generation=radio._civ_epoch,
            store_provider_generation=self.store.provider_generation,
        )

    async def _wire(self, command: int, **kwargs: Any) -> None:
        sub = kwargs.get("sub")
        data = kwargs.get("data", b"")
        self.sent.append((command, sub, data))
        if command == 0x1C and sub == 0x00:
            await self.receive(command, sub, bytes([int(self.keyed)]))
        elif command == 0x15 and sub == SWR_SUB:
            await self.receive(command, sub, b"\x00\x48")  # raw 48 -> SWR 1.5
        elif command == 0x15 and sub is not None:
            await self.receive(command, sub, b"\x00\x20")

    async def run(self, seconds: float) -> None:
        for _ in range(int(seconds / 0.05)):
            self.clock.advance(0.05)
            self.service.tick(now=self.clock.now())
            await self.poller._send_scheduler_requests()

    def swr_reads(self) -> int:
        return sum(1 for c, s, _ in self.sent if c == 0x15 and s == SWR_SUB)


_SWR_RADIOS = [
    pytest.param(_lan("G7250"), id="G7250-lan"),
    pytest.param(_usb("G7250"), id="G7250-usb"),
    pytest.param(_lan("IC-7300"), id="IC-7300-lan"),
    pytest.param(_lan("IC-7610"), id="IC-7610-lan"),
    pytest.param(_lan("IC-9700"), id="IC-9700-lan"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _SWR_RADIOS)
async def test_swr_is_read_only_while_keyed_and_published(
    factory: RadioFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _Rig(factory(), monkeypatch)
    await rig.receive(0x1C, 0x00, b"\x00")
    await rig.run(3.0)
    assert rig.swr_reads() == 0, "SWR is a TX-only meter: no read in RX"
    rx_frames = len(rig.sent)

    rig.keyed = True
    await rig.receive(0x1C, 0x00, b"\x01")
    rig.sent.clear()
    await rig.run(4.0)
    assert rig.swr_reads() >= 1, "SWR was never read while PTT was on"

    field = rig.store.snapshot().field(SWR)
    assert field.freshness is FreshnessState.FRESH
    assert field.value == pytest.approx(1.5)
    assert rx_frames > 0

    # No transmit behaviour change: the SWR lane adds exactly one query frame
    # (0x15 0x12, empty payload), and the poller never writes the transmit
    # state: no PTT (0x1C 0x00) or tuner (0x1C 0x01) write frame is sent.
    assert {f for f in rig.sent if f[:2] == (0x15, SWR_SUB)} == {(0x15, SWR_SUB, b"")}
    for command, sub, data in rig.sent:
        assert not (command == 0x1C and data), (command, sub, data)

    # Dekey: the last TX reading is discarded and the read stops.
    rig.keyed = False
    await rig.receive(0x1C, 0x00, b"\x00")
    await rig.run(3.0)
    rig.sent.clear()
    await rig.run(3.0)
    assert rig.swr_reads() == 0
    with pytest.raises(KeyError):
        rig.store.snapshot().field(SWR)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory",
    [pytest.param(_lan("X6200"), id="X6200"), pytest.param(_lan("X6100"), id="X6100")],
)
async def test_profiles_without_a_swr_scale_are_unchanged(
    factory: RadioFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _Rig(factory(), monkeypatch)
    rig.keyed = True
    await rig.receive(0x1C, 0x00, b"\x01")
    await rig.run(4.0)
    assert rig.swr_reads() == 0


# ── What the web state says about SWR (never a fake zero) ───────────────────


def _swr_state(model: str, snapshot: Any) -> ServerStatePublic:
    acquisition = get_radio_profile(model).state_acquisition
    assert acquisition is not None
    availability, declared = snapshot_field_status_inputs(acquisition, snapshot)
    payload = build_public_state_payload_from_snapshot(
        snapshot,
        radio=None,
        receiver_count=1,
        availability=availability,
        declared=declared,
    )
    return ServerStatePublic.model_validate(payload)


@pytest.mark.asyncio
async def test_g7250_swr_availability_follows_ptt_and_never_reads_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = _Rig(_lan("G7250")(), monkeypatch)
    await rig.receive(0x1C, 0x00, b"\x00")
    await rig.run(3.0)
    rx = _swr_state("G7250", rig.store.snapshot())
    # Declared, but not in use while receiving: unavailable, no value.
    assert rx.swrMeter is None
    assert rx.fieldStatus is not None
    assert rx.fieldStatus["swrMeter"].availability == "unavailable"

    rig.keyed = True
    await rig.receive(0x1C, 0x00, b"\x01")
    await rig.run(4.0)
    tx = _swr_state("G7250", rig.store.snapshot())
    assert tx.swrMeter == pytest.approx(1.5)
    assert tx.fieldStatus is not None
    assert tx.fieldStatus["swrMeter"].availability == "available"


@pytest.mark.asyncio
async def test_swr_classified_unknown_publishes_null_and_undeclared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing state for a meter the radio does not usefully report.

    A profile that lists ``global.meters.swr`` under ``unknown`` (what the
    G7250 entry reverts to if a keyed bench run shows no usable SWR) is never
    polled and reads null / ``undeclared``, never zero.
    """

    rig = _Rig(_lan("X6200")(), monkeypatch)
    rig.keyed = True
    await rig.receive(0x1C, 0x00, b"\x01")
    await rig.run(4.0)
    state = _swr_state("X6200", rig.store.snapshot())
    assert state.swrMeter is None
    assert state.fieldStatus is not None
    assert state.fieldStatus["swrMeter"].availability == "undeclared"
    assert state.fieldStatus["swrMeter"].observed is False
