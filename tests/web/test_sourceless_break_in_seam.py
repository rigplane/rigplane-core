"""The sourceless break-in seam document (MOR-4473).

``frontend/src/lib/transport/__tests__/fixtures/sourceless-break-in-seam.json``
is the ONE shared document for the exchange the CW-keyer wiring spec pins
(its header names this file as the Python companion): the browser's exact WS
``cmd`` envelope for ``set_break_in {mode: 2}`` (FULL), the real local
handler's reply, the exact CI-V SET bytes the backend emits, and the
resulting public ``breakIn`` state.

The profile is a synthetic sourceless IC-7300: shipped ``rigs/ic7300.toml``
— alone among the faked-hardware-capable profiles declaring the 3-valued
break-in domain (FTX-1 is binary-only and its sim refuses BI SETs; G7250 and
X6200 lack the command) — with ``vfo_readback`` replaced to ``"none"``. With
no VFO-identity source the production TX-target derivation yields
``unsupported``: the sourceless class the frontend permit exception arms
break-in for. ``test_committed_seam_document_is_current`` is the drift check
(like the narrow-width seam); ``test_sourceless_break_in_seam_replay``
drives the committed envelope through the REAL handler entry
(``_handle_text``: envelope parse, routing, capability gate, enqueue) over a
REAL ``Ic7300SerialRadio`` on the testkit ``CivSerialFake`` (bench
``tests/fixtures/ic7300/civ_pairs.jsonl`` recording), executes the queued
command through ``poller._execute_queued_entry`` the way the consumer loop
does, and asserts the reply ok plus the PUBLIC ``breakIn`` through
``ServerStatePublic``. The break-in state arrives through the PRODUCTION
publication plumbing only: ``_station`` attaches the profile's real
``AcquisitionScheduler`` to the radio exactly like
``WebServer._bootstrap_state_acquisition`` (so the ``SetBreakIn`` arm's
``_request_post_write_readback`` queues a real USER readback instead of
no-opping), the command store IS the radio's own store, adopted exactly
like ``WebServer`` adopts it (a post-connect swap would orphan the pump's
bound generation), the seam pumps the production send seam (``_send_scheduler_requests`` — the call
the poller ``_run`` loop makes), and the bench fake's FULL-readback answer
is decoded and applied by the production ``runtime/_civ_rx.py`` 0x47 branch.
No hand-written observation, no direct getter read: if post-write
acquisition or publication breaks, the payload has no ``breakIn``. One stated
limit: the fake serves the bench-recorded FULL readback statically (it
models no 0x16/0x47 register; the CI-V SET is fire-and-forget), so this
proves the handler accepts, enqueues, emits the wire SET and publishes the
radio's reported state — not that the fake applied the SET.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
)
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import UnknownTxTarget
from rigplane.profiles.rig_loader import load_rig
from rigplane.testing.timing import await_until
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetBreakIn
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot
from rigplane.web.state_schema import ServerStatePublic
from rigplane_testkit.serial import CivSerialFake
from support.command_consumer import run_command_consumer

ROOT = Path(__file__).resolve().parents[2]
RIG_PATH = ROOT / "rigs/ic7300.toml"
SEAM_PATH = (
    ROOT / "frontend/src/lib/transport/__tests__/fixtures/sourceless-break-in-seam.json"
)
_RENDER_COMMAND = "see tests/web/test_sourceless_break_in_seam.py::render_seam_document"

_ENVELOPE_ID = "sourceless-break-in-1"

_BREAK_IN_PATH = FieldPath.global_("operator_controls", "break_in")


def _synthetic_profile():
    """Shipped IC-7300 with the VFO-identity source removed (sourceless)."""
    return replace(load_rig(RIG_PATH), vfo_readback="none").to_profile()


class _FrameRecorder:
    """The WS egress the handler writes reply frames to (plumbing only)."""

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


@contextlib.asynccontextmanager
async def _station():
    """Real serial IC-7300 over the bench-recording fake + real handler."""
    profile = _synthetic_profile()
    fake = CivSerialFake.from_profile(ROOT, reply_latency_s=0.0)
    link = SerialCivLink(
        device="/dev/seam-fake", open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device="/dev/seam-fake",
        civ_link=link,
        profile=profile,
        _enumerate_serial_ports_fn=lambda: [],
    )
    # Production acquisition plumbing, mirroring ``WebServer``: the server
    # adopts the radio's own store as the command store (it never swaps in
    # a fresh one after connect — the RX pump binds the store generation at
    # ``connect()``) and ``_bootstrap_state_acquisition`` rides the
    # profile's real scheduler on the radio, so the ``SetBreakIn`` arm's
    # ``_request_post_write_readback`` queues a genuine USER readback.
    store = radio.state_store
    assert isinstance(store, StateStore)
    acquisition_profile = profile.state_acquisition
    assert acquisition_profile is not None, "synthetic profile lost state_acquisition"
    radio._acquisition_scheduler = AcquisitionScheduler(  # noqa: SLF001
        profile=acquisition_profile
    )
    scheduler = radio._acquisition_scheduler
    assert isinstance(scheduler, AcquisitionScheduler)
    await radio.connect()
    queue: CommandQueue = CommandQueue()
    poller = RadioPoller(radio, queue, state_store=store)
    ws = _FrameRecorder()
    handler = ControlHandler(
        ws,
        radio,
        "test",
        profile.model,
        server=SimpleNamespace(command_queue=queue, command_state_store=store),
    )
    entries: list[Any] = []
    executed = asyncio.Event()
    consumer = asyncio.create_task(
        run_command_consumer(
            queue, poller._execute_queued_entry, entries=entries, executed=executed
        )
    )
    try:
        yield SimpleNamespace(
            fake=fake,
            radio=radio,
            store=store,
            poller=poller,
            scheduler=scheduler,
            ws=ws,
            handler=handler,
            entries=entries,
            executed=executed,
        )
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await radio.disconnect()


def _published_break_in(store: StateStore) -> int | None:
    """The store's break-in value, or None while production unpublished it."""
    try:
        value = store.snapshot().field(_BREAK_IN_PATH).value
    except KeyError:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _set_wire_hex(fake: CivSerialFake) -> str:
    """The exact CI-V break-in SET bytes the backend emitted on the fake."""
    hits = [
        frame
        for frame in fake.commands
        if frame.command == 0x16 and frame.sub == 0x47 and frame.data == b"\x02"
    ]
    assert len(hits) == 1, f"expected one break-in SET on the wire, saw {len(hits)}"
    frame = hits[0]
    raw = (
        b"\xfe\xfe"
        + bytes([frame.to_addr, frame.from_addr, frame.command, frame.sub or 0])
        + frame.data
        + b"\xfd"
    )
    return raw.hex()


async def _run_seam() -> dict[str, Any]:
    """The full exchange against the real handler and faked hardware."""
    profile = _synthetic_profile()
    envelope = {
        "id": _ENVELOPE_ID,
        "name": "set_break_in",
        "params": {"mode": 2},
        "type": "cmd",
    }
    async with _station() as station:
        await station.handler._handle_text(json.dumps(envelope))  # noqa: SLF001
        assert station.ws.frames, "no response frame was sent"
        reply = station.ws.frames[-1]
        await await_until(
            station.executed.is_set, description="queued break-in execution"
        )
        queued = [entry.command for entry in station.entries]
        assert SetBreakIn(mode=2) in queued, f"set_break_in dropped: {queued!r}"
        wire = _set_wire_hex(station.fake)
        # The SET arm's production post-write readback: the scheduler must
        # hold a USER readback for exactly the break-in path — queued by
        # ``_request_post_write_readback``, never by this test. If the arm
        # stops requesting it, this fails here, before any publication wait.
        readbacks = [
            request
            for request in station.scheduler.pending_requests()
            if request.reason == "post_write_readback"
        ]
        assert [request.paths for request in readbacks] == [(_BREAK_IN_PATH,)]
        assert {request.priority for request in readbacks} == {AcquisitionPriority.USER}
        # The production send seam — the call the poller ``_run`` loop makes
        # every pass. The executor resolves the path through the profile's
        # own query map (``get_break_in``), transmits over the real link, and
        # the bench fake answers with the recorded FULL readback.
        await station.poller._send_scheduler_requests()  # noqa: SLF001
        # The production CI-V RX path (``runtime/_civ_rx.py`` 0x47 branch)
        # decodes that answer into the shared command store. No observation
        # is written by hand: a broken readback or publication times out
        # here instead of being papered over.
        await await_until(
            lambda: _published_break_in(station.store) == 2,
            description="production break-in publication",
        )
        payload = build_public_state_payload_from_snapshot(
            station.store.snapshot(),
            radio=station.radio,
            receiver_count=profile.receiver_count,
        )
        ServerStatePublic.model_validate(payload)
        target = station.poller._compute_tx_target()  # noqa: SLF001
        assert target == UnknownTxTarget(reason="unsupported")
    return {
        "envelope": envelope,
        "profile": "rigs/ic7300.toml",
        "recording": "tests/fixtures/ic7300/civ_pairs.jsonl",
        "reply": reply,
        "request": {"name": "set_break_in", "params": {"mode": 2}},
        "state": {"breakIn": payload["breakIn"]},
        "synthetic": {"vfo_readback": "none"},
        "txTarget": {"reason": "unsupported", "status": "unknown"},
        "wire": [wire],
        "format": 1,
    }


def render_seam_document() -> str:
    """Canonical JSON for the committed seam document."""
    return json.dumps(asyncio.run(_run_seam()), indent=2, sort_keys=True) + "\n"


def test_committed_seam_document_is_current() -> None:
    # Fails on any producer drift (reply, wire, readback, public state).
    committed = SEAM_PATH.read_text(encoding="utf-8")
    assert committed == render_seam_document(), (
        f"sourceless-break-in-seam.json is stale; {_RENDER_COMMAND}"
    )


async def test_sourceless_break_in_seam_replay() -> None:
    # The committed request through the real handler: accepted, enqueued and
    # executed — never rejected, dropped, or unpublished.
    doc = json.loads(SEAM_PATH.read_text(encoding="utf-8"))
    result = await _run_seam()
    assert result["reply"] == doc["reply"]
    assert result["reply"]["ok"] is True
    assert result["reply"]["result"] == {"mode": 2}
    assert result["wire"] == doc["wire"]
    assert result["state"] == doc["state"] == {"breakIn": 2}
    assert result["txTarget"] == doc["txTarget"]
