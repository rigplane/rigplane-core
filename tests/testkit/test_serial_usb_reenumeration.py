"""USB re-enumeration mid-session: the node is renamed, the radio answers (MOR-4645).

Bench (Mac mini, Core 3.3.0rc4): after a USB serial link-down the IC-705
connect sat in "identity not accepted ... holding the connect — the re-read
task keeps asking on the open link" for ~11 minutes until a replug.

Shape reproduced here, with the production link/code untouched:

1. CONNECTED on the old node; the port stays listed but goes silent
   (``CivSerialFake.silence``: opens fine, nothing answers, no errors —
   the stale handle never surfaces a failure).
2. The watchdog declares link-down; ``soft_reconnect`` reopens the still
   listed old node; the identity gate gets no answer and parks the connect
   in the hold — the background re-read retries ``19 00`` on the open
   (stale) link and never reopens the port.
3. USB re-enumeration: the old node disappears and the same adapter
   reappears under a NEW name (same USB ``serial_number``) with the radio
   answering on the new node.

Desired: Core notices the re-enumeration, adopts the new node, reopens the
port and latches CONNECTED. Predicted: the hold owns the open link, so the
watchdog skips (``_serial_civ_watchdog_loop``), ``soft_reconnect`` never
runs, rediscovery never runs, and the re-read keeps asking the stale
handle — the station never recovers.

REPRODUCTION ONLY (recovery files are frozen): the test pins the desired
behavior and is marked ``xfail(strict=True)`` while the stall reproduces.
No product code is changed by this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno

import pytest

from rigplane import RadioConnectionState
from rigplane.backends.discovery import SerialPortCandidate
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands.commander import Priority
from rigplane.core.radio_protocol import RadioIdentityStatus

from rigplane_testkit.serial import CivSerialFake
from rigplane.testing.timing import await_until

_ADAPTER_SERIAL = "IC705-ADAPTER-SN-4645"
_ADAPTER_VID = 0x0403
_ADAPTER_PID = 0x6001


class _ReenumerationStall(Exception):
    """Core never followed the re-enumerated node (MOR-4645).

    Raised ONLY when the post-rename recovery deadline expires. Every
    other failure — setup, phase barriers, unexpected identity states —
    stays an ordinary failure so the ``xfail`` below can only hide the
    reproduction itself, never a broken test.
    """


# Compressed cadence (same shape as test_serial_usb_silence_recovery.py):
# short answer window and watchdog tick so the silence evidence crosses the
# link-down threshold in well under a second, and a short identity re-read
# backoff so the hold engages quickly.
_ANSWER_WINDOW_S = 0.2
_WATCHDOG_TICK_S = 0.05


def _radio_for(
    model: str, old_fake: CivSerialFake, topology: dict, **kwargs
) -> Ic705SerialRadio | Ic7300SerialRadio:
    """The CLI's serial construction with a device-routed opener seam.

    ``SerialCivLink.open_serial_connection`` takes no arguments, so the
    fake alone cannot tell device nodes apart. The switching opener models
    what the production ``open_serial_port(url=device)`` does: opens follow
    the currently adopted device node, and a vanished node fails with
    ENODEV (which ``core/serial_open.py`` maps to ``serial_not_found``).
    """
    holder: dict = {}

    async def switching_opener():
        live = topology["live"]
        current = holder["radio"]._serial_device  # noqa: SLF001
        fake = live.get(current)
        if fake is None:
            raise OSError(errno.ENODEV, f"test: {current} is gone (MOR-4645 repro)")
        return await fake.open_serial_connection()

    link = SerialCivLink(
        device=kwargs["device"], open_serial_connection=switching_opener
    )
    cls = Ic705SerialRadio if model == "IC-705" else Ic7300SerialRadio
    radio = cls(
        device=kwargs["device"],
        civ_link=link,
        reconnect_glob=kwargs["reconnect_glob"],
        _enumerate_serial_ports_fn=kwargs["enumerate"],
        _civ_identity_probe=kwargs["probe"],
    )
    radio._civ_get_timeout = _ANSWER_WINDOW_S  # noqa: SLF001
    radio._SERIAL_WATCHDOG_INTERVAL_S = _WATCHDOG_TICK_S  # noqa: SLF001
    radio._SERIAL_WATCHDOG_RETRY_S = 0.05  # noqa: SLF001
    radio._SERIAL_WATCHDOG_RETRY_MAX_S = 0.1  # noqa: SLF001
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.05, 0.1)  # noqa: SLF001
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.1  # noqa: SLF001
    holder["radio"] = radio
    return radio


async def _poll_loop(radio, stop: asyncio.Event) -> None:
    """A live poller: fire-and-forget CI-V ``0x03`` reads, errors ignored."""
    while not stop.is_set():
        try:
            await radio.send_civ(
                0x03,
                wait_response=False,
                priority=Priority.BACKGROUND,
                wait_dispatch=False,
            )
        except Exception:
            pass
        await asyncio.sleep(0.02)


@pytest.mark.realtime(
    reason="link-down, hold and recovery budgets are real-clock timeouts on the live watchdog"
)
@pytest.mark.xfail(
    strict=True,
    raises=_ReenumerationStall,
    reason="MOR-4645: the identity hold keeps asking on the stale (renamed-away) "
    "link and never reopens the re-enumerated node",
)
@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
async def test_usb_reenumeration_renamed_node_recovers(model: str, tmp_path) -> None:
    """Link-down, then the adapter re-enumerates under a new name: Core must follow.

    ``CivSerialFake.silence`` models the bench failure's first half (the
    port stays listed, nothing answers — including the identity read); the
    old node file disappearing plus a new node with the SAME USB serial
    models the re-enumeration. Recovery must come through rediscovery
    (``_maybe_rediscover_serial_device``) adopting the new node and the
    identity gate answering there — the re-read task alone cannot do it,
    it never reopens the port.
    """
    old_path = tmp_path / "cu.usbserial-1420"
    old_path.write_text("")
    new_path = tmp_path / "cu.usbserial-9931"

    def _candidate(device: str) -> SerialPortCandidate:
        return SerialPortCandidate(
            device=device,
            description="",
            hwid=None,
            vid=_ADAPTER_VID,
            pid=_ADAPTER_PID,
            serial_number=_ADAPTER_SERIAL,
        )

    # Phase 1: the adapter lives on the old node.
    topology: dict = {
        "candidates": [_candidate(str(old_path))],
        "live": {},
    }
    if model == "IC-705":
        old_fake = CivSerialFake(model="IC-705", model_id=b"\xa4")
        new_fake = CivSerialFake(model="IC-705", model_id=b"\xa4")
    else:
        old_fake = CivSerialFake(model="IC-7300")
        new_fake = CivSerialFake(model="IC-7300")
    topology["live"][str(old_path)] = old_fake

    def _enumerate() -> list[SerialPortCandidate]:
        return list(topology["candidates"])

    async def _probe_forbidden(port: str) -> int | None:
        raise AssertionError(
            "CI-V probe must never run when the PRIMARY USB serial_number "
            f"identity is known (attempted on {port!r})"
        )

    radio = _radio_for(
        model,
        old_fake,
        topology,
        device=str(old_path),
        reconnect_glob=str(tmp_path / "cu.usbserial*"),
        enumerate=_enumerate,
        probe=_probe_forbidden,
    )

    await radio.connect()
    assert radio.connected
    assert radio.conn_state is RadioConnectionState.CONNECTED
    # The adapter fingerprint is captured for the PRIMARY rediscovery match.
    assert radio._serial_hw_identity == (  # noqa: SLF001
        _ADAPTER_SERIAL,
        _ADAPTER_VID,
        _ADAPTER_PID,
    )

    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_loop(radio, stop))
    try:
        assert await await_until(
            lambda: any(frame.command == 0x03 for frame in old_fake.commands),
            timeout=2.0,
            description="the poller never reached the wire",
        )

        # Phase 2: the port stays listed but goes silent. The watchdog
        # declares link-down; soft_reconnect reopens the still-listed old
        # node; the silent gate parks the connect in the identity hold.
        # The barrier waits for the COMPLETED hold — CONNECTING with the
        # unanswered gate returned and the background re-read driving —
        # not the in-flight gate: soft_reconnect runs the gate while still
        # RECONNECTING, so the bare ownership check is already true
        # during CHECKING and would let the topology change land before
        # the hold exists.
        old_fake.silence(3600.0)

        def _settled_in_identity_hold() -> bool:
            identity = radio.connection_identity
            reread = radio._serial_identity_reread_task  # noqa: SLF001
            return (
                radio.conn_state is RadioConnectionState.CONNECTING
                and identity is not None
                and identity.status is RadioIdentityStatus.NO_RESPONSE
                and not radio._serial_identity_gate_running  # noqa: SLF001
                and reread is not None
                and not reread.done()
                and radio._identity_phase_owns_open_link()  # noqa: SLF001
            )

        assert await await_until(
            _settled_in_identity_hold,
            timeout=10.0,
            description="the silent gate never parked the connect in the identity hold",
        ), "silent link never entered the identity hold"

        # Phase 3: USB re-enumeration — the old node disappears, the same
        # adapter (same USB serial) reappears under a new name, answering.
        # The stale old handle stays mute but never errors, exactly the
        # bench shape that held the connect for ~11 minutes.
        old_path.unlink()
        new_path.write_text("")
        topology["candidates"] = [_candidate(str(new_path))]
        del topology["live"][str(old_path)]
        topology["live"][str(new_path)] = new_fake

        # Desired: Core adopts the new node, reopens it, the gate answers.
        # Only THIS deadline may raise the expected stall: a timeout here
        # is the MOR-4645 reproduction, everything else stays loud.
        try:
            recovered = await await_until(
                lambda: (
                    radio.connected
                    and radio.conn_state is RadioConnectionState.CONNECTED
                    and radio._serial_device == str(new_path)  # noqa: SLF001
                ),
                timeout=20.0,
                description="station never followed the re-enumerated node",
            )
        except AssertionError as exc:
            raise _ReenumerationStall(
                "the running station did not reopen the re-enumerated port"
            ) from exc
        assert recovered, "the running station did not reopen the re-enumerated port"
        identity = radio.connection_identity
        assert identity is not None
        assert identity.status in (
            RadioIdentityStatus.VERIFIED,
            RadioIdentityStatus.UNVERIFIED,
        ), f"recovered without an answered identity: {identity.status!r}"
    finally:
        stop.set()
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
        old_fake.silence(0.0)
        with contextlib.suppress(Exception):
            await radio.disconnect()
