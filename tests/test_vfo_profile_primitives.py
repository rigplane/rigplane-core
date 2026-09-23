"""Profile-declared Web VFO primitive admission and routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    VfoEqualize,
    VfoSwap,
)

# MOR-1884: this suite drives ``RadioPoller._execute`` directly to exercise
# dispatch bodies; the interlock seat now lives at its head, so the RF
# premise is stated once here (see the fixture docstring in conftest.py).
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


_SUPPORTED = (
    ("IC-705", "ab"),
    ("IC-7300", "ab"),
    ("IC-7610", "main_sub"),
    ("IC-9700", "main_sub"),
)
_UNSUPPORTED = (
    ("TX-500", "ab"),
    ("X6100", "ab"),
    ("X6200", "ab"),
)
# FTX-1 (MOR-2531): the ``ab_shared`` scheme declares the MAIN↔SUB
# primitives as native CAT commands (``vfo_swap`` = SV;, ``vfo_equalize`` =
# AB;, OM 2508-C) instead of CI-V opcode fields. The web handler admits the
# intents and ``YaesuCatPoller`` dispatches them; ``RadioPoller`` (the CI-V
# dispatcher) never serves an ab_shared profile and still refuses.
_CAT_DECLARED = (("FTX-1", "ab_shared"),)


def _radio(model: str) -> SimpleNamespace:
    profile = resolve_radio_profile(model=model)
    return SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        swap_vfo_ab=AsyncMock(),
        equalize_vfo_ab=AsyncMock(),
        swap_main_sub=AsyncMock(),
        equalize_main_sub=AsyncMock(),
    )


def _handler(radio: SimpleNamespace, queue: CommandQueue) -> ControlHandler:
    return ControlHandler(
        ws=SimpleNamespace(send_text=AsyncMock()),
        radio=radio,
        server_version="test",
        radio_model=radio.model,
        server=SimpleNamespace(
            command_queue=queue,
            command_state_store=StateStore(),
        ),
    )


@pytest.mark.parametrize(("model", "family"), _SUPPORTED + _UNSUPPORTED + _CAT_DECLARED)
def test_shipped_profiles_pin_exact_vfo_primitive_families(
    model: str,
    family: str,
) -> None:
    profile = resolve_radio_profile(model=model)

    assert profile.vfo_scheme == family
    if (model, family) in _SUPPORTED:
        if family == "ab":
            assert profile.swap_ab_code is not None
            assert profile.equal_ab_code is not None
            assert profile.swap_main_sub_code is None
            assert profile.equal_main_sub_code is None
        else:
            assert profile.swap_main_sub_code is not None
            assert profile.equal_main_sub_code is not None
            assert profile.swap_ab_code is None
            assert profile.equal_ab_code is None
    elif (model, family) in _CAT_DECLARED:
        # ab_shared declares no CI-V opcode fields; the primitives are the
        # native CAT commands and the capability tags ride the profile.
        assert profile.swap_ab_code is None
        assert profile.equal_ab_code is None
        assert profile.swap_main_sub_code is None
        assert profile.equal_main_sub_code is None
        assert profile.supports_command("vfo_swap")
        assert profile.supports_command("vfo_equalize")
        assert {"vfo_swap", "vfo_equalize"} <= profile.capabilities
    else:
        assert profile.swap_ab_code is None
        assert profile.equal_ab_code is None
        assert profile.swap_main_sub_code is None
        assert profile.equal_main_sub_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "command", "expected_method", "expected_args"),
    [
        (model, command, method, args)
        for model, family in _SUPPORTED
        for command, method, args in (
            (
                "vfo_swap",
                "swap_vfo_ab" if family == "ab" else "swap_main_sub",
                (0,) if family == "ab" else (),
            ),
            (
                "vfo_equalize",
                "equalize_vfo_ab" if family == "ab" else "equalize_main_sub",
                (0,) if family == "ab" else (),
            ),
        )
    ],
)
async def test_declared_vfo_primitives_admit_and_route_exact_family(
    model: str,
    command: str,
    expected_method: str,
    expected_args: tuple[int, ...],
) -> None:
    radio = _radio(model)
    queue = CommandQueue()
    handler = _handler(radio, queue)

    assert await handler._enqueue_command(command, {}) == {}  # noqa: SLF001
    entries = queue.drain_entries()
    assert len(entries) == 1

    poller = RadioPoller(radio, queue)
    await poller._execute(entries[0].command)  # noqa: SLF001

    getattr(radio, expected_method).assert_awaited_once_with(*expected_args)
    for method_name in (
        "swap_vfo_ab",
        "equalize_vfo_ab",
        "swap_main_sub",
        "equalize_main_sub",
    ):
        if method_name != expected_method:
            getattr(radio, method_name).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [model for model, _family in _UNSUPPORTED])
@pytest.mark.parametrize("command", ("vfo_swap", "vfo_equalize"))
async def test_absent_vfo_primitive_fails_before_queue_admission(
    model: str,
    command: str,
) -> None:
    radio = _radio(model)
    queue = CommandQueue()
    handler = _handler(radio, queue)

    with pytest.raises(ValueError, match="not supported by active profile"):
        await handler._enqueue_command(command, {})  # noqa: SLF001

    assert queue.drain_entries() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [model for model, _family in _UNSUPPORTED])
@pytest.mark.parametrize(
    "queued_command",
    (VfoSwap(), VfoEqualize()),
    ids=("swap", "equalize"),
)
async def test_absent_vfo_primitive_direct_poller_command_fails_closed(
    model: str,
    queued_command: VfoSwap | VfoEqualize,
) -> None:
    radio = _radio(model)
    poller = RadioPoller(radio, CommandQueue())

    with pytest.raises(NotImplementedError, match="profile declares no matching"):
        await poller._execute(queued_command)  # noqa: SLF001

    radio.swap_vfo_ab.assert_not_awaited()
    radio.equalize_vfo_ab.assert_not_awaited()
    radio.swap_main_sub.assert_not_awaited()
    radio.equalize_main_sub.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [model for model, _family in _CAT_DECLARED])
async def test_cat_declared_vfo_primitives_admit_at_the_handler(model: str) -> None:
    """ab_shared profiles admit via their declared CAT commands (MOR-2531)."""
    radio = _radio(model)
    queue = CommandQueue()
    handler = _handler(radio, queue)

    assert await handler._enqueue_command("vfo_swap", {}) == {}  # noqa: SLF001
    assert await handler._enqueue_command("vfo_equalize", {}) == {}  # noqa: SLF001
    entries = queue.drain_entries()
    assert [type(entry.command) for entry in entries] == [VfoSwap, VfoEqualize]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [model for model, _family in _CAT_DECLARED])
@pytest.mark.parametrize(
    "queued_command",
    (VfoSwap(), VfoEqualize()),
    ids=("swap", "equalize"),
)
async def test_cat_declared_profiles_fail_closed_on_the_civ_poller(
    model: str,
    queued_command: VfoSwap | VfoEqualize,
) -> None:
    """RadioPoller is the CI-V dispatcher and never serves an ab_shared
    (Yaesu CAT) profile in production — YaesuCatPoller does — so it refuses
    the command rather than guessing a route."""
    radio = _radio(model)
    poller = RadioPoller(radio, CommandQueue())

    with pytest.raises(NotImplementedError, match="profile declares no matching"):
        await poller._execute(queued_command)  # noqa: SLF001

    radio.swap_vfo_ab.assert_not_awaited()
    radio.equalize_vfo_ab.assert_not_awaited()
    radio.swap_main_sub.assert_not_awaited()
    radio.equalize_main_sub.assert_not_awaited()
