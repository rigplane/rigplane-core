"""MOR-3072 regression: a managed-TX lifecycle exit must not undo the tuner.

The operator enables the antenna tuner's persistent ON; a normal managed-TX
lifecycle exit -- an explicit release (``force_off``), a normal shutdown, an
owner disconnect, or the transport detach behind disconnect/soft-disconnect
and reconnect switches (``transport_unavailable``) -- must not switch that
tuner off. Every implicit full-force abort family dispatches FORCE_RECEIVE
and STOP_CW only, and no lifecycle route reads the tuner to decide anything.

Expected RED on the current tree: the implicit full-force family is
dispatched per operation (``managed_tx_authority.py:
ManagedTxAuthority._execute`` iterates ``AbortOperation``), so STOP_TUNE
reaches both vendor actuators and lands on the wire as a persistent tuner-OFF
write (``runtime/radio.py: CoreRadio.actuate`` -> ``set_tuner_status(0)``
-> CI-V ``1C 01 00``; ``yaesu_cat/radio.py: YaesuCatRadio.actuate`` ->
``set_tuner`` state 0), read at those symbols on the base 6fad8de tree.

The wire assertions are deliberately independent of cached telemetry: the
persistent tuner-OFF write is refused whether the tuner is observed ON (1),
observed OFF (0), or never observed at all, and no route sends a tuner
read. The observed value is not fabricated or rewritten by the lifecycle.
Explicit low-level STOP_TUNE actuation is a separate effect-lane contract
and stays untouched.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.parser import format_command
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.command_spec import CatCommandSpec
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.rig_loader import load_rig
from test_icom7610_serial_radio import _FakeSerialCivLink

# The canonical decoded tuner observation both vendors publish: the Icom
# CI-V decode (``runtime/_civ_rx.py``, ``1C 01`` reply) and the Yaesu
# adapter (``yaesu_cat/observations.py``, ``get_tuner_status``) both write
# this typed int -- 0=off, 1=on, 2=tuning.
_TUNER_PATH = FieldPath.global_("operator_controls", "tuner_status")
_TUNER_ON = 1
_TUNER_OFF = 0
_SOURCE = SourceMetadata(source="poll_response", provider="tests")

_ROUTES = ("force_off", "shutdown", "owner_disconnect", "transport_unavailable")


def _observe_tuner(store: StateStore, value: int) -> None:
    """Apply one typed, current-generation tuner observation."""
    store.apply_current(
        Observation(
            path=_TUNER_PATH,
            value=value,
            source=_SOURCE,
            timestamp_monotonic=time.monotonic(),
        )
    )


async def _force_off(composition: ManagedTxComposition) -> None:
    submission = await composition.authority.submit_force_off()
    await submission.wait_settlement()


async def _run_route(
    composition: ManagedTxComposition, route: str, identity: object
) -> None:
    if route == "force_off":
        await _force_off(composition)
    elif route == "shutdown":
        assert await composition.shutdown(asyncio.Event()) is ShutdownResult.DRAINED
    elif route == "owner_disconnect":
        authority = composition.authority
        assert await authority.ptt_down("owner-a") is ManagedTxOutcome.ACCEPTED
        assert await authority.owner_disconnect("owner-a") is ManagedTxOutcome.ACCEPTED
    else:
        await composition.transport_unavailable(identity)


# ---------------------------------------------------------------------------
# Icom: the real serial radio over the house fake CI-V link.
# ---------------------------------------------------------------------------


class _QuietAbortCivLink(_FakeSerialCivLink):
    """The house fake, minus answers to the fire-and-forget abort writes.

    The parent answers every managed write with FB, and FB names no
    command (MOR-2860), so with the unkey and both abort writes in flight
    at once the unkey's settlement would depend on which FB the RX pump
    delivers first. Leaving ``17 FF`` (stop CW) and ``1C 01 00`` (tuner
    off) unanswered lets the unkey settle deterministically on its own
    answer while every write is still recorded on the wire sink.
    """

    _UNANSWERED = frozenset({b"\x17\xff", b"\x1c\x01\x00"})

    async def send(self, frame: bytes) -> None:
        payload = bytes(frame)
        if payload[4:-1] not in self._UNANSWERED:
            await super().send(frame)
            return
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        if self.lifecycle_events is not None:
            self.lifecycle_events.append(("send", payload))
        self.sent_frames.append(payload)
        for response in self._responses_by_send.pop(len(self.sent_frames), []):
            self._responses.put_nowait(response)


async def _icom_lifecycle_exit(
    tmp_path: Path, route: str, observed: int | None
) -> tuple[Icom7610SerialRadio, list[bytes], StateStore]:
    link = _QuietAbortCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    composition = ManagedTxComposition(
        radio, config_path=tmp_path / "icom-mor3072-tot.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    try:
        await composition.transport_ready(radio)
        await composition.bind_state_store(store)
        if observed is not None:
            _observe_tuner(store, observed)
        await _run_route(composition, route, radio)
        return radio, list(link.sent_frames), store
    finally:
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize(
    "observed",
    [_TUNER_ON, _TUNER_OFF, None],
    ids=["tuner-on", "tuner-off", "tuner-unknown"],
)
async def test_icom_lifecycle_exit_never_disables_the_persistent_tuner(
    tmp_path: Path, route: str, observed: int | None
) -> None:
    radio, frames, store = await _icom_lifecycle_exit(tmp_path, route, observed)

    # RED today: STOP_TUNE is dispatched unconditionally, so the persistent
    # tuner-OFF frame (CI-V 1C 01 00) reaches the wire on every route.
    assert (
        bytes(radio._commands.set_tuner_status(0, to_addr=radio._radio_addr))
        not in frames
    )
    # No lifecycle route reads the tuner to decide anything.
    assert (
        bytes(radio._commands.get_tuner_status(to_addr=radio._radio_addr)) not in frames
    )
    # FORCE_RECEIVE and STOP_CW still happen.
    assert bytes(radio._commands.ptt_off(to_addr=radio._radio_addr)) in frames
    assert bytes(radio._commands.stop_cw(to_addr=radio._radio_addr)) in frames
    if observed is not None:
        assert store.snapshot().field(_TUNER_PATH).value == observed


# ---------------------------------------------------------------------------
# Yaesu: the real profile radio over a recorded CAT transport.
# ---------------------------------------------------------------------------

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_PROFILE_TEMPLATES = {
    "set_ptt": CatCommandSpec(write="ZP{state};"),
    "send_cw": CatCommandSpec(write="ZC{type}{mem};"),
    "set_tuner": CatCommandSpec(write="ZT{src}{type}{state};"),
}
_TUNER_READ = "AC;"


def _yaesu_profile_radio() -> YaesuCatRadio:
    config = load_rig(_RIGS_DIR / "ftx1.toml")
    commands = dict(config.commands)
    commands.update(_PROFILE_TEMPLATES)
    radio = YaesuCatRadio("/dev/null", profile=replace(config, commands=commands))
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    # The unkey's confirming read-back sees RX, as a radio that obeyed it.
    radio._transport.query = AsyncMock(return_value="TX0")
    return radio


def _tuner_off_command() -> str:
    return format_command(
        _PROFILE_TEMPLATES["set_tuner"].write, src="0", type="0", state="0"
    )


async def _yaesu_lifecycle_exit(
    tmp_path: Path, route: str, observed: int | None
) -> tuple[YaesuCatRadio, list[str], list[str], StateStore]:
    radio = _yaesu_profile_radio()
    composition = ManagedTxComposition(
        radio, config_path=tmp_path / "yaesu-mor3072-tot.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    try:
        await composition.transport_ready(radio)
        await composition.bind_state_store(store)
        if observed is not None:
            _observe_tuner(store, observed)
        await _run_route(composition, route, radio)
        return (
            radio,
            [call.args[0] for call in radio._transport.write.await_args_list],
            [call.args[0] for call in radio._transport.query.await_args_list],
            store,
        )
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "observed"),
    [(route, _TUNER_ON) for route in _ROUTES]
    + [("force_off", _TUNER_OFF), ("force_off", None)],
    ids=[f"{route}-tuner-on" for route in _ROUTES]
    + [
        "force_off-tuner-off",
        "force_off-tuner-unknown",
    ],
)
async def test_yaesu_lifecycle_exit_never_disables_the_persistent_tuner(
    tmp_path: Path, route: str, observed: int | None
) -> None:
    _radio, written, queried, store = await _yaesu_lifecycle_exit(
        tmp_path, route, observed
    )

    # RED today: the unconditional STOP_TUNE writes ``set_tuner`` state 0
    # on every full-force route.
    assert _tuner_off_command() not in written
    # No lifecycle route reads the tuner to decide anything.
    assert _TUNER_READ not in queried
    # FORCE_RECEIVE and STOP_CW still happen.
    assert format_command(_PROFILE_TEMPLATES["set_ptt"].write, state="0") in written
    assert (
        format_command(_PROFILE_TEMPLATES["send_cw"].write, type=" ", mem="") in written
    )
    if observed is not None:
        assert store.snapshot().field(_TUNER_PATH).value == observed
