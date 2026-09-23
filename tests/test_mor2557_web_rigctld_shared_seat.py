"""MOR-2557: web + embedded rigctld over one Yaesu radio must share one seat.

At core v3.0.0b4 the embedded rigctld found existing acquisition services
only through the ``StateStoreCapable`` / ``StateModelCapable`` protocols.
``YaesuCatRadio`` carries neither, so ``RigctldServer`` built a fallback
``StateStore`` and overwrote the web seat's ``_state_store`` on the radio.
The Yaesu adapter then resolved ``available_when`` against that empty
fallback — clause sources unobserved, availability ``None`` — and silently
withheld the conditional ATT / manual-notch-frequency reads, while the web
startup gate, resolving the same clauses against the web store where freq
and mode ARE observed, waited on those three fields forever. Reproduced
live on plain core v3.0.0b4 (ticket MOR-2557 comment, 2026-09-23):
``web --no-rigctld`` gates in ~1 s, ``web --rigctld`` and ``station`` hang.

These tests drive the production servers over the real FTX-1 profile and a
mock CAT transport, in the CLI's construction order (``cli/__init__.py``:
``WebServer`` first, ``RigctldServer`` second).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from test_ftx1_sub_acquisition import _CAT_ANSWERS as _FTX1_BENCH_ANSWERS

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.rig_loader import load_rig
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.web.server import WebConfig, WebServer

_RIGS_DIR = Path(__file__).parents[1] / "rigs"

_MAIN_ATT = FieldPath.receiver("main", "operator_controls", "att")
_MAIN_NOTCH_FREQ = FieldPath.receiver("main", "operator_controls", "manual_notch_freq")
_SUB_NOTCH_FREQ = FieldPath.receiver("sub", "operator_controls", "manual_notch_freq")

# The bench answer table of test_ftx1_sub_acquisition.py serves the FTX-1
# slow-controls lane. The web poller also runs the tx-controls lane
# (``poll_tx_controls``: power, mic gain, compressor, VOX, split,
# clarifier RIT/XIT, tuner, dial lock, the CW keyer family), whose reads
# that table never needed — a missing answer makes the radio answer ``?;``
# against a declared path, which aborts the startup gate as a declared
# command defect instead of letting the conditional fields be observed.
# MAIN 14.0432 MHz USB / SUB 144.5 MHz USB keeps the profile clauses
# permissive: freq <= 60 MHz and modes outside the FM family, so the gate
# requires exactly the three conditional fields to be observed.
_CAT_ANSWERS = {
    **_FTX1_BENCH_ANSWERS,
    "RA0;": "RA00",
    "BP01;": "BP01035",
    "PC;": "PC0050",
    "MG;": "MG050",
    "PR0;": "PR00",
    "PL;": "PL050",
    "VX;": "VX0",
    "ST;": "ST0",
    "CF000;": "CF0000000",
    "CF001;": "CF001+0000",
    "AC;": "AC000",
    "LK;": "LK0",
    "KS;": "KS020",
    "KP;": "KP00",
    "BI;": "BI0",
    "SD;": "SD0100",
}

# Gate completion must beat this even under CI load; at origin/main the
# gate never completes, so the timeout is what turns the hang into a
# failure (the same wait_for pattern test_startup_state_gate.py uses).
_GATE_TIMEOUT_SECONDS = 20.0


class _FakeSocket:
    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 4532)


class _FakeAsyncServer:
    def __init__(self) -> None:
        self.sockets = [_FakeSocket()]

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _ftx1_radio(unanswered: list[str]) -> YaesuCatRadio:
    """A connected FTX-1-profile YaesuCatRadio on a mock CAT transport."""
    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True

    async def _query(command: str, *, timeout: float | None = None) -> str:
        answer = _CAT_ANSWERS.get(command)
        if answer is None:
            unanswered.append(command)
            return "?;"
        return answer

    radio._transport.query = AsyncMock(side_effect=_query)  # type: ignore[method-assign]
    return radio


async def test_embedded_rigctld_keeps_the_web_seat_and_the_gate_completes() -> None:
    unanswered: list[str] = []
    radio = _ftx1_radio(unanswered)
    web = WebServer(
        radio,
        WebConfig(
            host="127.0.0.1",
            port=0,
            discovery=False,
            await_initial_state=True,
        ),
    )
    # The web seat attached its store onto the non-protocol radio.
    assert radio._state_store is web.command_state_store

    rigctld = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0))
    fake_listener = AsyncMock(return_value=_FakeAsyncServer())

    async def _web_bind(*_args: object, **_kwargs: object) -> _FakeAsyncServer:
        return _FakeAsyncServer()

    with (
        patch("rigplane.rigctld.server.asyncio.start_server", new=fake_listener),
        patch("rigplane.rigctld.handler.RigctldHandler"),
        patch("rigplane.web.web_startup.asyncio.start_server", new=_web_bind),
    ):
        await rigctld.start()
        try:
            # At origin/main this bootstrap replaces the web seat's
            # services with an empty fallback store; with the fix the web
            # seat stays authoritative on the radio.
            assert radio._state_store is web.command_state_store

            try:
                await asyncio.wait_for(web.start(), timeout=_GATE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                pytest.fail(
                    "startup gate did not complete with the embedded rigctld "
                    f"over the FTX-1; unanswered CAT commands: {sorted(set(unanswered))}"
                )
        finally:
            await web.stop()
            await rigctld.stop()

    observed = {field.path for field in web.command_state_store.snapshot().fields}
    assert _MAIN_ATT in observed, "conditional MAIN att was never observed"
    assert _MAIN_NOTCH_FREQ in observed, "conditional MAIN notch freq never observed"
    assert _SUB_NOTCH_FREQ in observed, "conditional SUB notch freq never observed"
