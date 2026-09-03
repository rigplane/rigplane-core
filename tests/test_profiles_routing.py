"""Profile-driven routing and capability guard tests."""

from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane import IcomRadio
from rigplane.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.rigctld.state_cache import StateCache
from rigplane.web import radio_poller as radio_poller_module
from rigplane.web.handlers import ControlHandler
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetFreq, SetMode

# MOR-1884: this suite drives ``RadioPoller._execute`` directly to exercise
# dispatch bodies; the interlock seat now lives at its head, so the RF
# premise is stated once here (see the fixture docstring in conftest.py).
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


def _dual_radio_mock() -> MagicMock:
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    # The receiver=0-while-SUB-active branch restores MAIN via the public
    # select_receiver API (not a hand-built 0x07 CI-V frame); a bare
    # MagicMock attribute is not awaitable, so tests reaching that path
    # raise TypeError without this.
    radio.select_receiver = AsyncMock()
    return radio


def _single_radio_mock() -> MagicMock:
    profile = resolve_radio_profile(model="IC-7300")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    return radio


def test_radio_model_and_capabilities_are_profile_derived() -> None:
    radio = IcomRadio("127.0.0.1", model="IC-7300")
    assert radio.model == "IC-7300"
    assert "dual_rx" not in radio.capabilities


@pytest.mark.asyncio
async def test_single_profile_receiver_guard_is_explicit() -> None:
    radio = IcomRadio("127.0.0.1", model="IC-7300")
    radio._check_connected = lambda: None  # type: ignore[method-assign]

    with pytest.raises(CommandError, match="receiver=1"):
        await radio.set_freq(14_074_000, receiver=1)


@pytest.mark.asyncio
async def test_dual_profile_poller_delegates_sub_freq_to_core_radio() -> None:
    """receiver=1 (SUB) no longer gets a hand-rolled VFO switch in the poller.

    ``CoreRadio.set_freq`` already owns the cmd29-vs-VFO-switch decision for
    a non-MAIN receiver (runtime/radio.py), so the poller must pass the
    receiver through unconditionally and never touch ``send_civ`` itself.
    """
    radio = _dual_radio_mock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    await poller._execute(SetFreq(14_074_000, receiver=1))  # noqa: SLF001

    radio.set_freq.assert_awaited_once_with(14_074_000, receiver=1)
    radio.send_civ.assert_not_awaited()
    radio.select_receiver.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_profile_poller_rejects_sub_receiver() -> None:
    radio = _single_radio_mock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    with pytest.raises(CommandError, match="receiver=1"):
        await poller._execute(SetMode("USB", receiver=1))  # noqa: SLF001


async def test_control_handler_checks_capabilities_not_model_name() -> None:
    """IC-7300 has nb capability (from TOML) — verify it DOES NOT raise.

    Also test that a radio WITHOUT nb capability DOES raise."""
    profile = resolve_radio_profile(model="IC-7300")
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = SimpleNamespace(put=lambda _cmd: None)
    server = SimpleNamespace(command_queue=queue)
    radio = SimpleNamespace(capabilities=set(profile.capabilities))
    handler = ControlHandler(ws, radio, "1.0", profile.model, server=server)

    # IC-7300 has nb — should NOT raise
    await handler._enqueue_command("set_nb", {"on": True, "receiver": 0})

    # A radio without nb capability should raise
    radio_no_nb = SimpleNamespace(capabilities={"audio", "scope", "meters"})
    handler_no_nb = ControlHandler(ws, radio_no_nb, "1.0", "FAKE", server=server)
    with pytest.raises(ValueError, match="missing capability: nb"):
        await handler_no_nb._enqueue_command("set_nb", {"on": True, "receiver": 0})


@pytest.mark.asyncio
async def test_dual_profile_poller_routes_main_mode_via_select_receiver_when_active_sub() -> (
    None
):
    """receiver=0 (MAIN) while SUB is active still gets a switch-and-restore.

    There is no lower-layer equivalent for a VFO-aware MAIN write, so the
    poller keeps this dance — but it must go through the public
    ``select_receiver`` API instead of building the raw 0x07 CI-V frame
    itself, and in the right order: MAIN before the write, SUB after.
    """
    radio = _dual_radio_mock()
    radio._radio_state.active = "SUB"
    calls: list[str] = []
    radio.select_receiver = AsyncMock(
        side_effect=lambda which: calls.append(f"select_receiver({which})")
    )
    radio.set_mode = AsyncMock(side_effect=lambda *a, **k: calls.append("set_mode"))
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    await poller._execute(SetMode("USB", receiver=0))  # noqa: SLF001

    assert calls == ["select_receiver(0)", "set_mode", "select_receiver(1)"]
    radio.set_mode.assert_awaited_once_with("USB", None)
    radio.send_civ.assert_not_awaited()


def _count_self_civ_call_sites() -> int:
    """Count ``self._civ(...)`` call sites in ``radio_poller.py`` via ``ast``.

    Walking the parsed AST for ``Call`` nodes whose function is the
    attribute ``_civ`` on a ``Name`` node ``self`` means a comment or a
    string that happens to contain the same text cannot inflate the count
    the way a regex scan could.
    """
    source = inspect.getsource(radio_poller_module)
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "_civ"
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            count += 1
    return count


def test_radio_poller_raw_civ_call_count_is_pinned() -> None:
    """Ratchet: exactly 6 raw ``self._civ(...)`` sites remain.

    The 8 hand-rolled ``self._civ(0x07, ...)`` VFO-switch frames that used
    to live in ``SetFreq``/``SetMode`` (the ``receiver!=0`` fallback dance
    and the ``receiver=0``-while-SUB-active restore dance) were removed:
    the former now delegates to ``CoreRadio.set_freq``/``set_mode``, which
    already owns that decision; the latter now calls the public
    ``select_receiver`` API instead of building the raw frame itself.
    ``SwitchScopeReceiver`` was the 11th (MOR-2106): it now resolves
    ``set_scope_main_sub`` through ``_send_cmd`` instead of building
    ``0x27 0x12`` as a literal in ``_execute`` -- reusing ``_send_cmd``'s
    existing two call sites below rather than adding a new one.
    ``_send_one_state_query`` used to hold 5 of its own branches (cmd29
    wrap x2, the scope-receiver rewrite, and a sub-is-None/sub-is-set
    split that only differed in whether ``sub=None`` was spelled out) --
    those collapsed into the one call below when the wire-frame assembly
    moved into the shared ``runtime._state_queries.wire_parts_for_query``,
    also used by ``RigctldServer._send_one_state_query`` and
    ``runtime.radio_initial_state.fetch_initial_state``. The 6 that
    remain:

    - ``_send_cmd``: 2 — cmd29-wrapped vs. plain generic command dispatch.
    - ``_send_one_state_query``: 1 — dispatches whatever
      ``wire_parts_for_query`` resolved.
    - ``_execute``: 2 — the BSR band-switch stored-freq read and
      ``SelectVfo``'s scope-follow (0x27 0x12).
    - ``_send_query``: 1 — the meter poll read.

    Changing this literal deliberately means recounting the real call
    sites above, not just editing the number.
    """
    assert _count_self_civ_call_sites() == 6


# ---------------------------------------------------------------------------
# resolve_radio_profile fails closed (plan §8.1 Q5, MOR-2012)
# ---------------------------------------------------------------------------


class TestResolveRadioProfileFailsClosed:
    """The silent IC-7610/first-LAN-profile default fallback is removed:
    an unidentified radio must refuse, never guess."""

    def test_no_identifying_information_raises_a_clear_refusal(self) -> None:
        """No profile, no model, no radio_addr — resolve_radio_profile()
        must raise rather than silently return a default profile."""
        with pytest.raises(ValueError) as excinfo:
            resolve_radio_profile()

        # Must not be KeyError: get_radio_profile() already raises KeyError
        # for a *named* model that isn't found, so callers need to be able
        # to tell "nothing identified the radio" apart from "you named an
        # unknown one" by exception type alone.
        assert not isinstance(excinfo.value, KeyError)
        assert type(excinfo.value) is ValueError

    @pytest.mark.parametrize("blank_model", ["", "   "])
    def test_blank_model_raises_the_same_refusal(self, blank_model: str) -> None:
        """A blank or whitespace-only model counts as "nothing identifies
        the radio" — it must not silently resolve to a default profile."""
        with pytest.raises(ValueError) as excinfo:
            resolve_radio_profile(model=blank_model)

        assert not isinstance(excinfo.value, KeyError)
        assert type(excinfo.value) is ValueError

    def test_unmatched_radio_addr_alone_also_refuses(self) -> None:
        """A radio_addr that matches no loaded profile is also "nothing
        identifies the radio" -- 0xFF is not any shipped rig's civ_addr."""
        with pytest.raises(ValueError) as excinfo:
            resolve_radio_profile(radio_addr=0xFF)

        assert not isinstance(excinfo.value, KeyError)

    def test_explicit_model_override_still_resolves(self) -> None:
        """A profile/model passed explicitly by the caller remains the
        deliberate override it already was -- unchanged by this ruling."""
        profile = resolve_radio_profile(model="IC-7300")
        assert profile.model == "IC-7300"

        same_profile_object = resolve_radio_profile(profile=profile)
        assert same_profile_object is profile

        by_name = resolve_radio_profile(profile="IC-9700")
        assert by_name.model == "IC-9700"
