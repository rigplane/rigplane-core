"""Shared PTT-reread-answering test doubles for rigctld integration fixtures.

MOR-1900 fixture repair. Since ``6bdb5846`` (MOR-1900), ``RigctldHandler``'s
DEFER write gate (``_defer_write_gate`` in ``rigplane/rigctld/handler.py``)
only lets a MODE/BAND/VFO_SELECT/VFO_TOPOLOGY/MEMORY write through once
``_resolve_rigctld_rf_state`` has seen a *fresh*, provider-generation-matched
``Observation`` at ``global.tx_state.ptt`` in the canonical ``StateStore``.
That is honest — a real radio supplies this by answering
``RigctldServer._run_ptt_reread``'s periodic ``0x1C/0x00`` CI-V read
(MOR-1903). This module gives each fixture shape used under
``tests/integration/`` the ability to answer that same read, without
weakening the resolver or the gate it feeds:

* ``PttAnsweringSerialMockRadio`` — for the hand-rolled
  :class:`serial_stub.SerialMockRadio`, which has no CI-V ingest pipeline at
  all (``send_civ`` is a documented no-op). It owns a private, real
  ``StateStore`` (making it ``StateStoreCapable``, so ``RigctldServer`` uses
  it directly instead of an empty fallback store) and answers a
  ``(0x1C, 0x00)`` read the same way the real CI-V ingress
  (``_civ_rx.py: _CivRuntime._observations_from_frame``) would: one
  ``Observation`` at ``FieldPath.global_("tx_state", "ptt")``, bounded by the
  same TTL, reflecting the mock's own current ``_ptt`` flag.
* ``answer_ptt_reread_with_rx`` — for a real :class:`rigplane.radio.IcomRadio`
  fixture (``RecordingLanRadio`` in ``test_rigctld_audio_pipeline.py``).
  Reuses the exact technique already proven in
  ``tests/test_rigctld_ptt_reread.py`` (MOR-1903's own verification of this
  mechanism): construct the CI-V reply frame a real radio would send, and
  route it through the real, unmodified
  ``_CivRuntime._route_civ_frame``/``_observations_from_frame`` ingestion —
  the same production code path a live CI-V RX stream drives — skipping only
  the outer transport byte/header decode the fixture never sets up.

Neither helper touches ``_defer_write_gate``, the TX-interlock family
classification, or any production module; both only make a test double
capable of answering a read the gate already requires.

Answering the read is not instantaneous, though: ``RigctldServer._accept_client``
fires one ``0x1C/0x00`` immediately on the zero-to-one client transition
(closing the tick-vs-connect race), but that first send still needs a round
trip before the reply lands, and ``_run_ptt_reread``'s own scheduled cadence
only runs once a client is connected at all. A real hamlib client that issues
a DEFER-classified write inside that round trip sees exactly the same
``RPRT -9`` an unpatched fixture does; that is the resolver working as
designed, not a bug to route around. ``wait_for_known_rf_state`` below waits
that round trip out — the way a well-behaved client would — instead of
racing it.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from serial_stub import SerialMockRadio

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.runtime import tx_interlock
from rigplane.runtime._civ_rx import _OBSERVATION_MAX_AGE_SECONDS
from rigplane.types import CivFrame

if TYPE_CHECKING:
    from rigplane.radio import IcomRadio
    from rigplane.rigctld.server import RigctldServer

_PTT_PATH = FieldPath.global_("tx_state", "ptt")
_PTT_TTL_SECONDS = _OBSERVATION_MAX_AGE_SECONDS[("global", "tx_state", "ptt")]


class PttAnsweringSerialMockRadio(SerialMockRadio):
    """SerialMockRadio that answers rigctld's ``0x1C/0x00`` PTT re-read.

    Every other command still goes through the unmodified base class no-op.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ptt_state_store = StateStore()

    @property
    def state_store(self) -> StateStore:
        return self._ptt_state_store

    async def send_civ(
        self,
        command: int,
        sub: int | None = None,
        data: bytes | None = None,
        *,
        wait_response: bool = True,
        **_ignored_rigctld_send_kwargs: Any,
    ) -> None:
        # ``_run_ptt_reread`` calls with ``priority=``/``wait_dispatch=``,
        # which the base ``SerialMockRadio.send_civ`` does not accept —
        # absorbed here so the cadence's request no longer raises.
        if command == 0x1C and sub == 0x00:
            self._ptt_state_store.apply_current(
                Observation(
                    path=_PTT_PATH,
                    value=self._ptt,
                    source=SourceMetadata(
                        source="poll_response",
                        provider="serial_mock",
                        native_id="ptt_reread",
                    ),
                    timestamp_monotonic=time.monotonic(),
                    max_age=_PTT_TTL_SECONDS,
                )
            )
            return
        await super().send_civ(command, sub, data, wait_response=wait_response)


def civ_ptt_reply_frame(radio: "IcomRadio", *, transmitting: bool) -> CivFrame:
    """The CI-V reply a real radio would send to a ``0x1C/0x00`` read."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=radio._radio_addr,  # noqa: SLF001
        command=0x1C,
        sub=0x00,
        data=bytes([1 if transmitting else 0]),
        receiver=None,
    )


async def answer_ptt_reread_with_rx(radio: "IcomRadio") -> None:
    """Deliver an RX-state ``0x1C/0x00`` reply through real CI-V ingress."""
    frame = civ_ptt_reply_frame(radio, transmitting=False)
    await radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=radio._civ_epoch,  # noqa: SLF001
    )


async def wait_for_known_rf_state(
    server: "RigctldServer", *, timeout: float = 2.0
) -> None:
    """Wait out the startup window before ``_resolve_rigctld_rf_state`` knows RX.

    Requires a client already connected (``server._client_count > 0``) so
    ``_run_ptt_reread`` actually sends; otherwise this always times out —
    correctly, since an idle server never converges either.
    """
    handler = server._rig_handler  # noqa: SLF001
    resolve = getattr(handler, "_resolve_rigctld_rf_state", None)
    assert callable(resolve), "server._rig_handler not initialised — call after start()"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if resolve() is not tx_interlock.RfState.UNKNOWN:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        "RF state stayed UNKNOWN past the PTT re-read window — "
        "is a client connected (server._client_count > 0)?"
    )
