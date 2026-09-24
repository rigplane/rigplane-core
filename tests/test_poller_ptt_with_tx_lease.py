"""MOR-2563: web PTT while another holder has the TX audio leg live.

Pinned behaviour (filed as the falsifier for the bug, now the regression
suite): the poller's ``PttOn``/``PttOff`` arm through
``AudioSession.acquire_tx("ptt")`` / lease release (ADR
``docs/plans/2026-06-09-target-audio-architecture.md`` §3.3 item 3), not
``radio.start_tx()``/``radio.stop_tx()``. A session TX lease can already
hold the transport TX leg live — the audio bridge ``rigplane web``
auto-starts in RX+TX goes through ``AudioSession.acquire_tx``, and so does
RigPlane Pro's ``audio_start direction: tx``. Before MOR-2563 a second
``start_tx`` raised the transport's already-started error
(``UsbAudioDriver._start_tx_exclusive``/``start_tx`` — "TX stream already
started."; the LAN ``AudioStream.start_tx`` — "Already transmitting"), the
poller arm treated any exception as a failed arm and refused the key
(MOR-1178), and ``_stop_tx_audio_leg`` stopped TX regardless of other
lease holders.

With RX subscribed and a foreign ``bridge`` lease holding the TX leg, PTT
ON must be admitted (no ``CommandError``, the key write reaches the rig)
while the lease holder's TX leg stays live and its frames keep reaching
the output (b) and RX keeps reaching the subscriber (c); PTT OFF must
unkey (d) without stopping TX for the other lease holder and (e) leave RX
flowing. Case 5 pins that a GENUINE arm failure through the session still
refuses the key without touching the foreign lease; case 6 pins that a
repeated PTT ON reuses the held "ptt" lease.

The stack is as real as the existing helpers allow: the real
``RadioPoller`` dispatching ``PttOn``/``PttOff`` to a real ``YaesuCatRadio``
over a real ``UsbAudioDriver``/``AudioBus``/``AudioSession`` on the repo's
``FakeAudioBackend`` (USB cases, duplex policy pinned the way
``test_audio_duplex`` pins it), and the shared ``LanLikeRadio`` stub from
``_order_sensitive_radios`` whose ``start_tx`` raises the LAN stream's
"Already transmitting" (LAN case). CAT is an in-process recorder — no
serial port, no radio, no hardware anywhere. The MOR-1879 interlock
premise (radio observed in RX before keying) is stated via the shared
``observed_rx_dispatch_premise`` conftest fixture, exactly as
``test_web_ptt_arm_failure`` does; the managed-TX seat is not involved
(both radios are unmanaged, so the key takes the raw ``set_ptt`` write).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from _order_sensitive_radios import LanLikeRadio
from test_audio_duplex import DUPLEX_DEVICE, SEPARATE_RX_DEVICE

from rigplane.audio import AudioPacket
from rigplane.audio.backend import FakeAudioBackend
from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.capabilities import CAP_AUDIO
from rigplane.core.exceptions import CommandError
from rigplane.profiles import resolve_radio_profile
from rigplane.web.radio_poller import CommandQueue, PttOff, PttOn, RadioPoller

# MOR-1879: dispatch-direct suites must state the RF premise for the
# interlock seat at ``_execute`` — see the conftest fixture.
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")

# rigs/ftx1.toml: set_ptt = { cat = { write = "TX{state};" } }
_KEYED, _UNKEYED = "TX1;", "TX0;"
_LEASE_FRAME = b"tx-lease"


# ── The behaviour under test ────────────────────────────────────────────────


async def _drive_ptt_cycle(
    poller: RadioPoller,
    *,
    wrote_key: Callable[[], bool],
    wrote_unkey: Callable[[], bool],
    tx_live: Callable[[], bool],
    push_ok: Callable[[], Awaitable[bool]],
    rx_ok: Callable[[], Awaitable[bool]],
) -> list[str]:
    """Key and unkey through the poller; return the violated expectations.

    Observations are collected, not asserted on the spot, so one run
    reports the full (a)–(e) picture instead of stopping at the first
    failure — this is a falsifier: the failures ARE the result. TX liveness
    is read passively, because a lease ``push`` on a dead leg would re-arm
    it (``AudioSession._converge_for_push``) and mask the failure.
    """
    problems: list[str] = []

    # (a) The key is admitted: no refusal, and the key write ran.
    on_error: Exception | None = None
    try:
        await poller._execute(PttOn(), command_id="ptt-on", session_id="ws-1")
    except Exception as exc:
        on_error = exc
    if on_error is not None:
        problems.append(f"(a) PTT ON refused: {type(on_error).__name__}: {on_error}")
    if not wrote_key():
        problems.append("(a) no key write reached the rig")

    # (b) The lease holder's TX leg survives the key.
    live_after_on = tx_live()
    if not live_after_on:
        problems.append("(b) the lease holder's TX leg is not live after PTT ON")
    elif not await push_ok():
        problems.append("(b) lease push frames no longer reach the output")

    # (c) RX keeps flowing to the subscriber.
    if not await rx_ok():
        problems.append("(c) RX frames no longer reach the subscriber after PTT ON")

    # PTT OFF: unkey — but TX held for another lease holder must survive it.
    off_error: Exception | None = None
    try:
        await poller._execute(PttOff(), command_id="ptt-off", session_id="ws-1")
    except Exception as exc:
        off_error = exc
    if off_error is not None:
        problems.append(f"(d) PTT OFF failed: {type(off_error).__name__}: {off_error}")
    if not wrote_unkey():
        problems.append("(d) no unkey write reached the rig")

    # (d) The lease holder's TX leg is STILL live. Liveness read passively;
    # (e) is probed before any push that could re-arm the leg.
    live_after_off = tx_live()
    if not live_after_off:
        problems.append("(d) PTT OFF stopped the lease holder's TX leg")
    # (e) RX still flows.
    if not await rx_ok():
        problems.append("(e) RX frames no longer reach the subscriber after PTT OFF")
    if live_after_off and not await push_ok():
        problems.append(
            "(d) lease push frames no longer reach the output after PTT OFF"
        )

    return problems


# ── The USB stack: cases 1 (exclusive), 2 (full), 4 (control) ───────────────


def _fake_cat(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Offline CAT: the transport reads connected and records every write."""
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connected",
        True,
    )
    writes: list[str] = []

    # Class-level monkeypatch: the bound call passes the transport instance
    # as the first positional argument, so ``self`` comes before the command.
    async def _record(self: Any, cmd: str, *args: object, **kwargs: object) -> None:
        writes.append(cmd)

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.write",
        _record,
    )
    return writes


def _usb_stack(
    monkeypatch: pytest.MonkeyPatch, *, exclusive: bool
) -> tuple[YaesuCatRadio, FakeAudioBackend, RadioPoller, list[str]]:
    """Real FTX-1-shaped stack: ``RadioPoller`` → ``YaesuCatRadio`` →
    ``UsbAudioDriver`` → ``FakeAudioBackend``, with the real ``AudioBus``
    and ``AudioSession`` reached through ``radio.audio_session``.

    ``exclusive=True`` pins the macOS same-device duplex policy the way
    ``test_audio_duplex`` does (the FTX-1 shape); ``False`` keeps the real
    resolver on two separate fake devices (the ``full`` policy).
    """
    if exclusive:
        monkeypatch.setattr(
            "rigplane.audio.usb_driver.resolve_usb_duplex_mode",
            lambda _rx, _tx: "exclusive",
        )
        backend = FakeAudioBackend(
            devices=[DUPLEX_DEVICE], strict_device_exclusive=True
        )
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="USB Audio CODEC",
            backend=backend,
            rx_audio_channel="left",
        )
    else:
        backend = FakeAudioBackend(
            devices=[DUPLEX_DEVICE, SEPARATE_RX_DEVICE],
            strict_device_exclusive=True,
        )
        driver = UsbAudioDriver(
            rx_device="USB Audio CODEC",
            tx_device="BlackHole 2ch",
            backend=backend,
        )
    writes = _fake_cat(monkeypatch)
    radio = YaesuCatRadio(device="/dev/cu.fake", audio_driver=driver)
    assert (radio.audio_duplex_mode == "exclusive") is exclusive
    return radio, backend, RadioPoller(radio, CommandQueue()), writes


def _running_capture(backend: FakeAudioBackend) -> Any:
    """The live capture stream: the duplex one when it carries RX, else the
    newest running plain RX stream."""
    for stream in reversed(backend.duplex_streams):
        if stream.running:
            return stream
    for stream in reversed(backend.rx_streams):
        if stream.running:
            return stream
    return None


def _last_tx_frame(backend: FakeAudioBackend) -> bytes | None:
    """The newest frame any TX-bearing fake stream has played."""
    for streams in (backend.duplex_streams, backend.tx_streams):
        for stream in reversed(streams):
            if stream.written_frames:
                return stream.written_frames[-1]
    return None


@pytest.mark.parametrize(
    "exclusive",
    [True, False],
    ids=["exclusive-same-device", "full-separate-devices"],
)
async def test_usb_ptt_with_a_live_foreign_tx_lease(
    monkeypatch: pytest.MonkeyPatch, exclusive: bool
) -> None:
    """Cases 1 and 2: RX subscribed, a ``bridge`` lease holds the transport
    TX leg live, then the poller keys and unkeys."""
    radio, backend, poller, writes = _usb_stack(monkeypatch, exclusive=exclusive)
    session = radio.audio_session
    sub = await session.subscribe_rx("web-audio")
    lease = await session.acquire_tx("bridge")
    notes: list[str] = []
    rx_seq = 0

    def tx_live() -> bool:
        return bool(radio._audio_driver.tx_running)

    async def rx_ok() -> bool:
        nonlocal rx_seq
        rx_seq += 1
        frame = f"usb-rx-{rx_seq}".encode()
        target = _running_capture(backend)
        if target is None:
            notes.append("rx probe: no running capture stream to inject into")
            return False
        target.inject_frame(frame)
        pkt = await sub.get(timeout=1.0)
        return pkt is not None and pkt.data == frame

    async def push_ok() -> bool:
        try:
            await lease.push(_LEASE_FRAME)
        except Exception as exc:
            notes.append(f"lease.push raised {type(exc).__name__}: {exc}")
            return False
        return _last_tx_frame(backend) == _LEASE_FRAME

    try:
        # Harness premise, not the behaviour under test: with RX demand and
        # a TX lease the session converged to RX_TX — the lease holds the
        # transport TX leg live, RX frames flow, lease pushes land.
        assert session.state is AudioSessionState.RX_TX
        assert tx_live()
        assert await rx_ok()
        assert await push_ok()

        problems = await _drive_ptt_cycle(
            poller,
            wrote_key=lambda: _KEYED in writes,
            wrote_unkey=lambda: _UNKEYED in writes,
            tx_live=tx_live,
            push_ok=push_ok,
            rx_ok=rx_ok,
        )
        assert not problems, "\n".join([*problems, *notes])
    finally:
        await lease.release()
        await sub.release()


# ── Case 3: LAN ─────────────────────────────────────────────────────────────


class _PttLanRadio(LanLikeRadio):
    """The shared LAN-shaped stub plus the surface ``RadioPoller`` dispatch
    needs (profile, capabilities, ``set_ptt``) and a TX frame recorder.

    ``start_tx``/``stop_tx`` keep the stub's transition graph — including
    the LAN stream's ``AudioAlreadyStartedError("Already transmitting")``
    on a second ``start_tx`` (lan_stream.py ``AudioStream.start_tx``).

    ``audio_session`` mirrors the production radios' lazy radio-owned
    singleton (``runtime/radio.py``, ``backends/yaesu_cat/radio.py``) — the
    property the poller's session path (MOR-2563) reaches for.
    """

    def __init__(self) -> None:
        super().__init__()
        self.profile = resolve_radio_profile(model="IC-7610")
        self.capabilities = {CAP_AUDIO}
        self.ptt_writes: list[bool] = []
        self.tx_frames: list[bytes] = []
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session

    async def set_ptt(self, on: bool) -> None:
        self.ptt_writes.append(on)

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)
        self.tx_frames.append(audio_data)


async def test_lan_ptt_with_a_live_foreign_tx_lease() -> None:
    """Case 3: the same sequence on the LAN-shaped stub, whose ``start_tx``
    raises the LAN stream's "Already transmitting" on a second start."""
    radio = _PttLanRadio()
    session = radio.audio_session
    poller = RadioPoller(radio, CommandQueue())
    sub = await session.subscribe_rx("web-audio")
    lease = await session.acquire_tx("bridge")
    notes: list[str] = []
    rx_seq = 0

    def tx_live() -> bool:
        return radio.state == "transmitting"

    async def rx_ok() -> bool:
        nonlocal rx_seq
        rx_seq += 1
        packet = AudioPacket(
            ident=0x0080, send_seq=rx_seq, data=f"lan-rx-{rx_seq}".encode()
        )
        if radio.rx_callback is None:
            notes.append("rx probe: the radio RX callback is gone")
            return False
        radio.rx_callback(packet)
        pkt = await sub.get(timeout=1.0)
        return pkt is not None and pkt.data == packet.data

    async def push_ok() -> bool:
        try:
            await lease.push(_LEASE_FRAME)
        except Exception as exc:
            notes.append(f"lease.push raised {type(exc).__name__}: {exc}")
            return False
        return bool(radio.tx_frames) and radio.tx_frames[-1] == _LEASE_FRAME

    try:
        # Harness premise: the lease holds the stream in "transmitting".
        assert session.state is AudioSessionState.RX_TX
        assert tx_live()
        assert await rx_ok()
        assert await push_ok()

        problems = await _drive_ptt_cycle(
            poller,
            wrote_key=lambda: True in radio.ptt_writes,
            wrote_unkey=lambda: False in radio.ptt_writes,
            tx_live=tx_live,
            push_ok=push_ok,
            rx_ok=rx_ok,
        )
        assert not problems, "\n".join([*problems, *notes])
    finally:
        await lease.release()
        await sub.release()


# ── Case 4: control — no other lease holder ─────────────────────────────────


async def test_control_ptt_without_any_other_lease_keys_and_unkeys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: with NO other lease holder the same harness must key and
    unkey normally. It must pass today — it proves the stack, the dispatch
    and the probes work, so the failures in the cases above are the
    product behaviour, not a broken harness."""
    radio, backend, poller, writes = _usb_stack(monkeypatch, exclusive=True)
    session = radio.audio_session
    sub = await session.subscribe_rx("web-audio")
    rx_seq = 0

    async def rx_ok() -> bool:
        nonlocal rx_seq
        rx_seq += 1
        frame = f"ctl-rx-{rx_seq}".encode()
        target = _running_capture(backend)
        if target is None:
            return False
        target.inject_frame(frame)
        pkt = await sub.get(timeout=1.0)
        return pkt is not None and pkt.data == frame

    try:
        assert session.state is AudioSessionState.RX_ONLY

        await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
        assert _KEYED in writes
        assert radio._audio_driver.tx_running
        # The exclusive duplex arm keeps RX flowing mid-key.
        assert await rx_ok()

        await poller._execute(PttOff(), command_id="c2", session_id="ws-1")
        assert _UNKEYED in writes
        assert not radio._audio_driver.tx_running
        assert await rx_ok()
    finally:
        await sub.release()


# ── Case 5: genuine arm failure through the session (MOR-1178 preserved) ────


async def test_lan_session_arm_failure_refuses_key_and_keeps_foreign_lease() -> None:
    """A session that cannot make TX live still refuses the key (MOR-1178) —
    no key write, no leaked "ptt" demand — and the refusal must not touch
    the other owner's lease or call ``radio.stop_tx()`` (which on this stub
    would drop the shared stream out from under the foreign lease).
    """
    radio = _PttLanRadio()
    session = radio.audio_session
    poller = RadioPoller(radio, CommandQueue())
    sub = await session.subscribe_rx("web-audio")
    lease = await session.acquire_tx("bridge")
    arm_fails = True

    async def _start_tx() -> None:
        radio.calls.append("start_tx")
        if arm_fails:
            raise RuntimeError("TX audio device unavailable")
        await LanLikeRadio.start_tx(radio)

    try:
        assert session.state is AudioSessionState.RX_TX
        assert radio.state == "transmitting"

        # The foreign lease's TX leg drops silently (transport hiccup) and the
        # re-arm now fails: the session cannot make TX live for "ptt".
        radio.state = "receiving"
        radio.start_tx = _start_tx  # type: ignore[method-assign]
        calls_before = len(radio.calls)

        with pytest.raises(CommandError, match="TX audio failed to arm"):
            await poller._execute(PttOn(), command_id="ptt-on", session_id="ws-1")

        # No key write reached the rig; no "ptt" demand leaked.
        assert radio.ptt_writes == []
        assert poller._ptt_tx_lease is None  # noqa: SLF001
        assert session.tx_demand == 1  # only the foreign "bridge" lease
        assert not lease.released
        # The refusal ran exactly the failed arm attempt — no stop_tx at the
        # other owner's leg, no retry.
        assert radio.calls[calls_before:] == ["start_tx"]

        # The foreign lease is fully functional once the transport recovers.
        arm_fails = False
        await lease.push(_LEASE_FRAME)
        assert radio.state == "transmitting"
        assert radio.tx_frames[-1] == _LEASE_FRAME
    finally:
        await lease.release()
        await sub.release()


# ── Case 6: repeated PTT ON reuses the held lease ────────────────────────────


async def test_repeated_ptt_on_reuses_the_held_ptt_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second PTT ON while the "ptt" lease is held takes no second lease
    and leaks none: exactly one TX demand from the poller across the whole
    keyed period, released once by PTT OFF.
    """
    radio, backend, poller, writes = _usb_stack(monkeypatch, exclusive=True)
    session = radio.audio_session
    sub = await session.subscribe_rx("web-audio")
    try:
        await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
        lease = poller._ptt_tx_lease  # noqa: SLF001
        assert lease is not None and not lease.released
        assert session.tx_demand == 1
        assert radio._audio_driver.tx_running

        await poller._execute(PttOn(), command_id="c2", session_id="ws-1")
        assert poller._ptt_tx_lease is lease  # noqa: SLF001 — reused
        assert session.tx_demand == 1
        assert writes.count(_KEYED) == 2  # both keys still reach the rig

        await poller._execute(PttOff(), command_id="c3", session_id="ws-1")
        assert poller._ptt_tx_lease is None  # noqa: SLF001
        assert lease.released
        assert session.tx_demand == 0
        assert not radio._audio_driver.tx_running
        assert _UNKEYED in writes
    finally:
        await sub.release()
