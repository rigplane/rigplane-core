"""MOR-4352: a managed-release abort the radio cannot execute is not applicable.

Live failure (real Xiegu G7250, Mac mini, 2026-11-10 11:54Z, Core main
faf0f7109): a Pro TX smoke ran ``transmit_on`` then full-force
``force_off``; the rig was back in RX within 392 ms, yet
``GET /api/v1/managed-transmit`` reported
``abortErrors: [{"operation": "stop_cw", "error": "rejected"}]`` and the
server log warned ``stop_cw is not declared by profile G7250 and not
recorded as absent -- refusing rather than guessing``. The shared
managed-release path scheduled STOP_CW for every radio; on a profile
without CI-V CW keying the bound-command refusal normalized into a
rejected abort, and the Pro tool reads any abortError as an unsafe
managed write.

The contract pinned here, through the production composition over radios
built from the REAL rig profiles on the house fake CI-V link:

- a full-force release on a profile that does not declare the abort's
  command (G7250, X6200: no ``send_cw``/``stop_cw``) never schedules
  STOP_CW -- no 0x17 frame on the wire -- and reports
  ``abort_errors == ()``;
- a profile that declares ``stop_cw`` (IC-705, ``stop_cw = [0x17]``)
  still sends STOP_CW on full force, exactly as before, and a
  fire-and-forget abort the radio's profile can build reports no
  abortError;
- the normal (non-full-force) unkey path schedules no abort at all, so
  it cannot grow the same defect;
- the effect lane itself never dispatches an abort the actuator declares
  inapplicable.

Expected RED on the current tree: ``ManagedTxAuthority._execute``
schedules ``AbortOperation.STOP_CW`` for every full-force pass, the
actuator's bound-command refusal settles as ``AbortFailed`` on G7250 and
X6200, ``abort_applicable`` does not exist yet, and the Xiegu profiles
record no CW-keying absence.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.backends.ic705.serial import Ic705SerialRadio, XieguSerialRadio
from rigplane.commands import parse_civ_frame
from rigplane.core.state_store import StateStore
from rigplane.profiles import get_radio_profile
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_icom7610_serial_radio import _FakeSerialCivLink

_TMP_CONFIG = "mor4352-tot.json"


class _ManagedReleaseCivLink(_FakeSerialCivLink):
    """The house fake as a managed full-force release needs it.

    The managed fire-and-forget writes (PTT ON ``1C 00 01``, STOP_CW
    ``17 FF``, tuner off ``1C 01 00``) are recorded on the wire sink but
    left unanswered -- the ``_QuietAbortCivLink`` discipline from
    test_managed_tx_tuner_preservation, so no stray FB can name the wrong
    write (MOR-2860). The bare ``1C 00`` PTT read IS answered with RX:
    every fire-and-forget send stamps the request tracker's
    unclaimed-ack clock at send time, so the unkey immediately after a
    key-down always verifies through that read instead of its own FB;
    answering it settles the release deterministically the way the live
    G7250 did (back in RX, release confirmed).
    """

    _UNANSWERED_WRITES = frozenset({b"\x1c\x00\x01", b"\x17\xff", b"\x1c\x01\x00"})

    async def send(self, frame: bytes) -> None:
        payload = bytes(frame)
        body = payload[4:-1]
        if body in self._UNANSWERED_WRITES or body == b"\x1c\x00":
            if not self.connected:
                raise ConnectionError("Serial CI-V link is disconnected.")
            self.sent_frames.append(payload)
            for response in self._responses_by_send.pop(len(self.sent_frames), []):
                self._responses.put_nowait(response)
            if body == b"\x1c\x00":
                self._responses.put_nowait(
                    bytes((0xFE, 0xFE, payload[3], payload[2], 0x1C, 0x00, 0x00, 0xFD))
                )
            return
        await super().send(frame)


def _stop_cw_frames(frames: list[bytes]) -> list[bytes]:
    """The CI-V 0x17 CW-keying frames among *frames* (command 0x17 only)."""
    return [frame for frame in frames if parse_civ_frame(frame).command == 0x17]


async def _released_projection(
    radio: _IcomSerialRadioBase, tmp_path: Path
) -> ManagedTxProjection:
    """``transmit_on`` then full-force ``force_off`` through the authority."""
    composition = ManagedTxComposition(radio, config_path=tmp_path / _TMP_CONFIG)
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    on = await composition.authority.submit_transmit_on()
    await on.wait_settlement()
    off = await composition.authority.submit_force_off()
    await off.wait_settlement()
    try:
        return await composition.authority.snapshot()
    finally:
        await composition.shutdown(asyncio.Event())


# ---------------------------------------------------------------------------
# Full-force release: radios without CI-V CW keying
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["G7250", "X6200"])
async def test_full_force_release_reports_no_abort_error(
    tmp_path: Path, model: str
) -> None:
    link = _ManagedReleaseCivLink()
    radio = XieguSerialRadio(device="/dev/ttyUSB0", model=model, civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    try:
        projection = await _released_projection(radio, tmp_path)
    finally:
        await radio.disconnect()

    assert projection.state.intent.kind is ManagedTxIntentKind.RX
    # RED today: STOP_CW is scheduled for every radio; on a profile with
    # no CI-V CW keying the bound-command refusal settles as
    # AbortFailed(stop_cw, "rejected") in abort_errors.
    assert projection.state.abort_errors == ()
    # STOP_CW never reaches the wire either (the refusal happens before
    # any write; the fix must keep it that way by never scheduling it).
    assert _stop_cw_frames(list(link.sent_frames)) == []


# ---------------------------------------------------------------------------
# Full-force release: a radio that declares stop_cw keeps the abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ic705_full_force_release_still_sends_stop_cw(
    tmp_path: Path,
) -> None:
    link = _ManagedReleaseCivLink()
    radio = Ic705SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    try:
        projection = await _released_projection(radio, tmp_path)
    finally:
        await radio.disconnect()

    assert projection.state.intent.kind is ManagedTxIntentKind.RX
    # The IC-705 profile declares stop_cw = [0x17]; full force still puts
    # exactly those profile bytes on the wire.
    assert bytes(radio._commands.stop_cw(to_addr=radio._radio_addr)) in link.sent_frames
    # A fire-and-forget abort the profile can build is accepted, not an
    # abortError.
    assert projection.state.abort_errors == ()


# ---------------------------------------------------------------------------
# The normal (non-full-force) unkey path schedules no abort at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g7250_normal_unkey_never_schedules_an_abort(
    tmp_path: Path,
) -> None:
    link = _ManagedReleaseCivLink()
    radio = XieguSerialRadio(device="/dev/ttyUSB0", model="G7250", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    composition = ManagedTxComposition(radio, config_path=tmp_path / _TMP_CONFIG)
    store = StateStore()
    store.begin_provider_generation()
    try:
        await composition.transport_ready(radio)
        await composition.bind_state_store(store)
        assert (
            await composition.authority.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
        )
        assert await composition.authority.ptt_up("owner") is ManagedTxOutcome.ACCEPTED
        projection = await composition.authority.snapshot()
    finally:
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()

    assert projection.state.intent.kind is ManagedTxIntentKind.RX
    assert projection.state.abort_errors == ()
    assert _stop_cw_frames(list(link.sent_frames)) == []


# ---------------------------------------------------------------------------
# The effect lane gate itself
# ---------------------------------------------------------------------------


class _NoCWKeyingActuator:
    """Records every dispatch; declares STOP_CW inapplicable."""

    def __init__(self) -> None:
        self.calls: list[AbortOperation] = []

    def abort_applicable(self, operation: AbortOperation) -> bool:
        return operation is not AbortOperation.STOP_CW

    async def actuate(
        self,
        token: EffectToken,
        operation: Any,
        *,
        is_current: Any,
    ) -> ActuationResult:
        self.calls.append(operation)
        return (
            ActuationResult.REJECTED
            if operation is AbortOperation.STOP_CW
            else ActuationResult.ACCEPTED
        )


@pytest.mark.asyncio
async def test_settle_abort_never_dispatches_an_inapplicable_abort() -> None:
    actuator = _NoCWKeyingActuator()
    lane = ManagedTxEffectLane(actuator, clock=lambda: 20.0)

    failed = await lane.settle_abort(
        EffectToken(1, 1, "mor4352"),
        AbortOperation.STOP_CW,
        deadline_monotonic=21.0,
    )

    # Unsupported is not failed: no AbortFailed, and the radio was never
    # asked. RED today: the lane dispatches STOP_CW and the rejection
    # comes back as AbortFailed.
    assert failed is None
    assert actuator.calls == []


# ---------------------------------------------------------------------------
# Actuator and profile declarations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["G7250", "X6200"])
def test_xiegu_actuator_declares_stop_cw_not_applicable(model: str) -> None:
    radio = XieguSerialRadio(device="/dev/null", model=model)
    assert radio.abort_applicable(AbortOperation.STOP_CW) is False


def test_ic705_actuator_declares_stop_cw_applicable() -> None:
    radio = Ic705SerialRadio(device="/dev/null")
    assert radio.abort_applicable(AbortOperation.STOP_CW) is True


@pytest.mark.parametrize("model", ["G7250", "X6200"])
def test_xiegu_profiles_record_ci_v_cw_keying_absent(model: str) -> None:
    """Both directions of CI-V CW keying are formally absent (D1 state 2).

    ``supports_command`` answers False for the undeclared name alone, so
    the managed gate needs no absence record; the record is the D1
    release contract -- "neither declared nor declared absent" must not
    exist at release -- and names the source that established it.
    """
    profile = get_radio_profile(model)
    assert {"send_cw", "stop_cw"} <= profile.absent_command_names
    assert not {"send_cw", "stop_cw"} & profile.command_names


def test_g7250_absent_source_is_the_live_release_observation() -> None:
    source = get_radio_profile("G7250").absent_command_sources["stop_cw"]
    assert "LIVE 2026-11-10" in source
    assert "MOR-4352" in source


def test_x6200_absent_source_is_the_vendor_command_table() -> None:
    source = get_radio_profile("X6200").absent_command_sources["stop_cw"]
    assert "Radioddity X6200 CI-V V1.0.6" in source


def test_ic705_still_declares_the_cw_keying_commands() -> None:
    profile = get_radio_profile("IC-705")
    assert profile.supports_command("send_cw")
    assert profile.supports_command("stop_cw")
