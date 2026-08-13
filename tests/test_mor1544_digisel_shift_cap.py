"""MOR-1544: ``set_digisel_shift`` must gate on its own capability tag.

``web/handlers/control.py`` and ``web/radio_poller.py`` both gated
``set_digisel_shift`` on the shared ``"digisel"`` capability (the 0x16/0x4E
DIGI-SEL on/off toggle). After MOR-1540 removed the over-declared "digisel"
tag from ``rigs/ic705.toml`` (IC-705 has DIGI-SEL *Shift*, 0x14/0x13, but not
the 0x16/0x4E toggle), a web/API client calling ``set_digisel_shift`` on
IC-705 was rejected at the web layer even though
``CoreRadio.set_digisel_shift`` itself has no such capability check — it
only requires the cmd29 route for 0x14/0x13, which IC-705 has.

Fix: a distinct ``"digisel_shift"`` capability tag, declared in the TOML
profiles whose ``[commands]`` table actually carries
``get_digisel_shift``/``set_digisel_shift`` (IC-705, IC-7610), gating
``set_digisel_shift`` at both the control-handler (web-socket command
dispatch) and radio-poller (command execution) layers.

TDD red-first: written against the unfixed code (before this ticket's
edits to ``capabilities.py``, ``control.py``, ``radio_poller.py``,
``rigs/ic705.toml``), the IC-705-acceptance tests below fail:
``TestControlHandlerGate.test_ic705_set_digisel_shift_passes_web_gate`` and
``TestPollerExecutionGate.test_ic705_poller_executes_digisel_shift`` raise
``ValueError`` / silently skip because "digisel_shift" is not yet a known
capability and IC-705 does not declare "digisel". After the fix, they pass.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.state_cache import StateCache
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetDigiselShift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _profile_radio(model: str) -> SimpleNamespace:
    """A radio double whose capabilities come straight from the real TOML
    profile — not a hand-picked test fixture — so this exercises exactly
    what ships to /api/v1/capabilities.
    """
    profile = resolve_radio_profile(model=model)
    return SimpleNamespace(
        capabilities=set(profile.capabilities),
        profile=profile,
        set_digisel=AsyncMock(),
        set_digisel_shift=AsyncMock(),
    )


def _handler(radio: object, server: object) -> ControlHandler:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "test", server=server)


def _server() -> tuple[SimpleNamespace, _QueueRecorder]:
    q = _QueueRecorder()
    return SimpleNamespace(command_queue=q), q


# ---------------------------------------------------------------------------
# Web layer (ControlHandler._enqueue_command)
# ---------------------------------------------------------------------------


class TestControlHandlerGate:
    @pytest.mark.asyncio
    async def test_ic705_set_digisel_shift_passes_web_gate(self) -> None:
        """IC-705 has get/set_digisel_shift [0x14, 0x13] in [commands] but no
        digisel [0x16, 0x4E] toggle — the web gate must key off
        "digisel_shift", not "digisel" (MOR-1544)."""
        srv, q = _server()
        h = _handler(_profile_radio("IC-705"), srv)
        result = await h._enqueue_command("set_digisel_shift", {"level": 128})
        assert result == {"level": 128, "receiver": 0}
        assert len(q.items) == 1
        assert isinstance(q.items[0], SetDigiselShift)
        assert q.items[0].level == 128

    @pytest.mark.asyncio
    async def test_ic7300_set_digisel_shift_rejected(self) -> None:
        """IC-7300 has neither digisel nor digisel_shift commands — must
        still be rejected under the new capability key."""
        srv, _ = _server()
        h = _handler(_profile_radio("IC-7300"), srv)
        with pytest.raises(ValueError, match="digisel_shift"):
            await h._enqueue_command("set_digisel_shift", {"level": 100})

    @pytest.mark.asyncio
    async def test_ic705_set_digisel_toggle_still_rejected(self) -> None:
        """digisel (0x16/0x4E) gating is unchanged: IC-705 still has no
        "digisel" capability, so set_digisel must still be rejected."""
        srv, _ = _server()
        h = _handler(_profile_radio("IC-705"), srv)
        with pytest.raises(ValueError, match="digisel"):
            await h._enqueue_command("set_digisel", {"on": True})

    @pytest.mark.asyncio
    async def test_ic7610_set_digisel_and_shift_both_accepted(self) -> None:
        """Regression guard: IC-7610 declares both digisel and
        digisel_shift (it has both CI-V commands) — neither gate must
        regress for a radio that legitimately has both."""
        srv, q = _server()
        h = _handler(_profile_radio("IC-7610"), srv)
        await h._enqueue_command("set_digisel", {"on": True})
        await h._enqueue_command("set_digisel_shift", {"level": 50})
        assert len(q.items) == 2


# ---------------------------------------------------------------------------
# Execution layer (RadioPoller._execute)
# ---------------------------------------------------------------------------


class TestPollerExecutionGate:
    @pytest.mark.asyncio
    async def test_ic705_poller_executes_digisel_shift(self) -> None:
        radio = _profile_radio("IC-705")
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        await poller._execute(SetDigiselShift(level=200, receiver=0))  # noqa: SLF001
        radio.set_digisel_shift.assert_awaited_once_with(200, receiver=0)

    @pytest.mark.asyncio
    async def test_ic7300_poller_skips_digisel_shift(self) -> None:
        """IC-7300 lacks digisel_shift — the poller must not forward the
        command to the radio backend at all."""
        radio = _profile_radio("IC-7300")
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        await poller._execute(SetDigiselShift(level=200, receiver=0))  # noqa: SLF001
        radio.set_digisel_shift.assert_not_awaited()
