"""MOR-1544: ``set_digisel_shift`` must gate on its own capability tag.

``web/handlers/control.py`` and ``web/radio_poller.py`` both gated
``set_digisel_shift`` on the shared ``"digisel"`` capability (the 0x16/0x4E
DIGI-SEL on/off toggle). After MOR-1540 removed the over-declared "digisel"
tag from ``rigs/ic705.toml`` (IC-705 has DIGI-SEL *Shift*, 0x14/0x13, but not
the 0x16/0x4E toggle), a web/API client calling ``set_digisel_shift`` on
IC-705 was rejected at the web layer even though
``CoreRadio.set_digisel_shift`` itself has no such capability check — it
only requires the cmd29 route for 0x14/0x13, which IC-705 has.

Fix: a distinct ``"digisel_shift"`` capability tag, gating
``set_digisel_shift`` at both the control-handler (web-socket command
dispatch) and radio-poller (command execution) layers.

MOR-2917 then removed DIGI-SEL shift from ``rigs/ic705.toml``: neither
edition of the IC-705 CI-V Reference Guide has a 0x14 0x13 row, so the
IC-705 is now rejected like the IC-7300. No shipped profile declares the
shift without the toggle any more, so the MOR-1544 case (shift passes, toggle
refused) is exercised on the IC-7610's own capabilities minus ``"digisel"``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.state_cache import StateCache
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetDigiselShift

# MOR-1884: this suite drives ``RadioPoller._execute`` directly to exercise
# dispatch bodies; the interlock seat now lives at its head, so the RF
# premise is stated once here (see the fixture docstring in conftest.py).
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _profile_radio(
    model: str, *, without: frozenset[str] = frozenset()
) -> SimpleNamespace:
    """A radio double whose capabilities come straight from the real TOML
    profile — not a hand-picked test fixture — so this exercises exactly
    what ships to /api/v1/capabilities, less any tags named in ``without``.
    """
    profile = resolve_radio_profile(model=model)
    return SimpleNamespace(
        capabilities=set(profile.capabilities) - without,
        profile=profile,
        set_digisel=AsyncMock(),
        set_digisel_shift=AsyncMock(),
    )


def _shift_without_toggle_radio() -> SimpleNamespace:
    """The IC-7610's capabilities without the 0x16 0x4E toggle tag."""
    return _profile_radio("IC-7610", without=frozenset({"digisel"}))


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
    async def test_shift_without_toggle_passes_web_gate(self) -> None:
        """The web gate keys off "digisel_shift", not "digisel" (MOR-1544)."""
        srv, q = _server()
        h = _handler(_shift_without_toggle_radio(), srv)
        result = await h._enqueue_command("set_digisel_shift", {"level": 128})
        assert result == {"level": 128, "receiver": 0}
        assert len(q.items) == 1
        assert isinstance(q.items[0], SetDigiselShift)
        assert q.items[0].level == 128

    @pytest.mark.asyncio
    async def test_ic705_set_digisel_shift_rejected(self) -> None:
        """MOR-2917: the IC-705 declares no DIGI-SEL shift."""
        srv, q = _server()
        h = _handler(_profile_radio("IC-705"), srv)
        with pytest.raises(ValueError, match="digisel_shift"):
            await h._enqueue_command("set_digisel_shift", {"level": 128})
        assert q.items == []

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
    async def test_shift_without_toggle_poller_executes_digisel_shift(self) -> None:
        radio = _shift_without_toggle_radio()
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        await poller._execute(SetDigiselShift(level=200, receiver=0))  # noqa: SLF001
        radio.set_digisel_shift.assert_awaited_once_with(200, receiver=0)

    @pytest.mark.asyncio
    async def test_ic705_poller_skips_digisel_shift(self) -> None:
        """MOR-2917: the IC-705 declares no DIGI-SEL shift."""
        radio = _profile_radio("IC-705")
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        await poller._execute(SetDigiselShift(level=200, receiver=0))  # noqa: SLF001
        radio.set_digisel_shift.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ic7300_poller_skips_digisel_shift(self) -> None:
        """IC-7300 lacks digisel_shift — the poller must not forward the
        command to the radio backend at all."""
        radio = _profile_radio("IC-7300")
        poller = RadioPoller(radio, StateCache(), CommandQueue())
        await poller._execute(SetDigiselShift(level=200, receiver=0))  # noqa: SLF001
        radio.set_digisel_shift.assert_not_awaited()
