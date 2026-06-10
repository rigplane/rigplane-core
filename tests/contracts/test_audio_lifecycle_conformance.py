"""Audio lifecycle conformance suite across all shipping backends (MOR-567).

ADR §3.9.2, capstone of the MOR-562 de-risking tranche: one scenario
matrix, run for every shipping transport against its fake driver/link,
that CATCHES the MOR-556/MOR-559 start-order bug class instead of
pinning a single happy path.

Backend rows (all hardware-free):

- ``lan-icom``        — LAN :class:`IcomRadio` driving the REAL
  :class:`~rigplane.audio.lan_stream.AudioStream` state machine (the
  actual MOR-556 culprit) over a fake UDP transport.
- ``icom7610/ic705/ic7300/ic9700-serial`` — the real serial classes
  (X6200 ships via the IC-705 class) over ``_FakeSerialCivLink`` with
  the REAL :class:`UsbAudioDriver` on a strict-exclusive
  :class:`FakeAudioBackend` (separate RX/TX devices → ``"full"`` duplex
  on every host platform, deterministic).
- ``yaesu-ftx1``      — :class:`YaesuCatRadio` with the same real
  driver/backend pair and a patched CAT transport.
- ``lan-graph-stub`` / ``exclusive-graph-stub`` — the shared
  order-sensitive stubs (MOR-566) with declared transition graphs;
  bridge round-trip rows only.

Documented gap: the FTX-1 *same-device* exclusive path cannot run
against the strict fake — live CoreAudio kills asymmetrically (RX onto
running TX is clean, TX onto running RX dies with AUHAL -50) while
``strict_device_exclusive`` rejects any second stream symmetrically.
That graph is therefore exercised via ``ExclusiveUsbRadio`` (declared
from the MOR-531 live de-risk), not a real backend row.

Known shipping bug surfaced by this suite (kept as a strict xfail, not
fixed here — MOR-567 is tests-only): ``AudioBridge.stop()`` drops the
RX demand BEFORE stopping radio TX, and ``LanAudioStream.stop_rx``
early-returns while TRANSMITTING — so on the LAN backend a bridge stop
with TX armed leaks the running RX stream (state ends RECEIVING with a
live ``_rx_task`` and zero bus subscribers). Stop-side instance of the
MOR-556 ordering class.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import pytest
from _order_sensitive_radios import ExclusiveUsbRadio, LanLikeRadio
from test_icom7610_serial_radio import _FakeSerialCivLink

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bridge import AudioBridge, BridgeState
from rigplane.audio.lan_stream import AudioState, AudioStream
from rigplane.audio.usb_driver import AudioAlreadyStartedError, UsbAudioDriver
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic9700 import Ic9700SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.runtime.radio import IcomRadio

_FRAME = b"\x01\x00" * 960  # one 20 ms s16le mono frame @ 48 kHz

_LOOPBACK = AudioDeviceInfo(
    id=AudioDeviceId(1), name="BlackHole 2ch", input_channels=2, output_channels=2
)
_RX_DEV = AudioDeviceInfo(id=AudioDeviceId(11), name="Rig CODEC In", input_channels=2)
_TX_DEV = AudioDeviceInfo(id=AudioDeviceId(12), name="Rig CODEC Out", output_channels=2)


class _FakeLanAudioTransport:
    """Minimal IcomTransport stand-in feeding the REAL LanAudioStream."""

    my_id = 0x0101
    remote_id = 0x0202

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def receive_packet(self, timeout: float = 1.0) -> bytes:
        await asyncio.sleep(3600)  # cancelled by stop_rx / test teardown
        return b""

    async def send_tracked(self, data: bytes) -> None:
        self.sent.append(bytes(data))


@dataclass
class _Harness:
    """One backend row: the radio plus uniform liveness/leak probes."""

    radio: Any
    rx_live: Callable[[], bool]
    tx_live: Callable[[], bool]
    # Force the NEXT radio RX start to fail (MOR-560 generalization).
    break_rx: Callable[[], None] = lambda: None
    # Open radio-side device streams (0 == nothing leaked).
    open_streams: Callable[[], int] = lambda: 0
    close: Callable[[], Awaitable[None]] | None = None
    # LAN single-state stream: RX can only start from IDLE (MOR-556 edge).
    rx_while_tx_raises: bool = False
    marks: list[str] = field(default_factory=list)


def _lan_harness() -> _Harness:
    from unittest.mock import MagicMock

    radio = IcomRadio("192.168.99.1")
    radio._connected = True
    radio._civ_transport = MagicMock()
    radio._audio_stream = AudioStream(_FakeLanAudioTransport())

    def _stream() -> AudioStream | None:
        return radio._audio_stream

    async def _close() -> None:
        radio._connected = False  # quiet the GC "active connection" warning

    return _Harness(
        radio=radio,
        rx_live=lambda: _stream() is not None and _stream()._rx_callback is not None,
        tx_live=lambda: (
            _stream() is not None and _stream().state is AudioState.TRANSMITTING
        ),
        # No audio transport negotiated -> start_rx raises ConnectionError.
        break_rx=lambda: setattr(radio, "_audio_stream", None),
        open_streams=lambda: int(
            _stream() is not None
            and _stream()._rx_task is not None
            and not _stream()._rx_task.done()
        ),
        close=_close,
        rx_while_tx_raises=True,
        marks=["lan-bridge-stop-leak"],
    )


def _usb_harness(make_radio: Callable[[UsbAudioDriver], Any]) -> _Harness:
    backend = FakeAudioBackend([_RX_DEV, _TX_DEV], strict_device_exclusive=True)
    driver = UsbAudioDriver(
        rx_device="Rig CODEC In", tx_device="Rig CODEC Out", backend=backend
    )
    radio = make_radio(driver)
    return _Harness(
        radio=radio,
        rx_live=lambda: driver.rx_running,
        tx_live=lambda: driver.tx_running,
        break_rx=backend.remove_devices,  # selection fails -> RX start fails
        open_streams=lambda: sum(
            s.running
            for s in backend.rx_streams + backend.tx_streams + backend.duplex_streams
        ),
    )


_SERIAL_CLASSES: dict[str, type] = {
    "icom7610-serial": Icom7610SerialRadio,
    "ic705-serial": Ic705SerialRadio,  # X6200 ships through this class
    "ic7300-serial": Ic7300SerialRadio,
    "ic9700-serial": Ic9700SerialRadio,
}

BACKENDS = ["lan-icom", *_SERIAL_CLASSES, "yaesu-ftx1"]


async def _make_harness(case: str, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    if case == "lan-icom":
        return _lan_harness()
    if case == "yaesu-ftx1":
        monkeypatch.setattr(
            "rigplane.backends.yaesu_cat.transport.YaesuCatTransport.connected", True
        )
        return _usb_harness(
            lambda driver: YaesuCatRadio(device="/dev/cu.fake", audio_driver=driver)
        )
    cls = _SERIAL_CLASSES[case]
    harness = _usb_harness(
        lambda driver: cls(
            device="/dev/ttyUSB-fake",
            civ_link=_FakeSerialCivLink(),
            audio_driver=driver,
        )
    )
    await harness.radio.connect()
    harness.close = harness.radio.disconnect
    return harness


@pytest.fixture(params=BACKENDS)
async def harness(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    h = await _make_harness(request.param, monkeypatch)
    yield h
    for stop in (h.radio.stop_tx, h.radio.stop_rx):  # safety net on failure
        try:
            await stop()
        except Exception:
            pass
    if h.close is not None:
        await h.close()


# ---------------------------------------------------------------------------
# Scenario matrix (every shipping backend)
# ---------------------------------------------------------------------------


async def test_rx_then_tx_then_ptt_cycle_then_stop(harness: _Harness) -> None:
    """Full lifecycle ends clean: zero bus subscribers, zero open streams."""
    radio = harness.radio
    sub = radio.audio_bus.subscribe(name="conformance")
    await sub.start()
    assert radio.audio_bus.rx_active and harness.rx_live()
    for _ in range(2):  # two PTT cycles over a live RX
        await radio.start_tx()
        await radio.push_tx(_FRAME)
        await radio.stop_tx()
        assert harness.rx_live() and not harness.tx_live()
    await sub.aclose()
    assert radio.audio_bus.subscriber_count == 0
    assert radio.audio_bus.rx_active is False
    assert not harness.rx_live() and not harness.tx_live()
    assert harness.open_streams() == 0


async def test_tx_while_rx_keeps_rx_alive(harness: _Harness) -> None:
    """Arming TX over a running RX must not kill the capture (MOR-559 class)."""
    radio = harness.radio
    sub = radio.audio_bus.subscribe(name="conformance")
    await sub.start()
    await radio.start_tx()
    assert harness.tx_live()
    assert harness.rx_live(), "TX arm silently killed the running RX capture"
    await radio.stop_tx()
    await sub.aclose()
    assert radio.audio_bus.subscriber_count == 0 and not harness.rx_live()


async def test_rx_while_tx_follows_declared_transition(harness: _Harness) -> None:
    """Late RX demand on a running TX: typed raise (LAN) or clean start."""
    radio = harness.radio
    await radio.start_tx()
    if harness.rx_while_tx_raises:
        with pytest.raises(AudioAlreadyStartedError):
            await radio.start_rx(lambda _p: None)
        assert harness.tx_live() and not harness.rx_live()
    else:
        await radio.start_rx(lambda _p: None)
        assert harness.tx_live() and harness.rx_live()
        await radio.stop_rx()
    await radio.stop_tx()
    assert not harness.tx_live() and not harness.rx_live()
    assert harness.open_streams() == 0


async def test_double_start_raises_typed_error(harness: _Harness) -> None:
    """Double start raises AudioAlreadyStartedError — never a bare RuntimeError."""
    radio = harness.radio
    await radio.start_rx(lambda _p: None)
    with pytest.raises(AudioAlreadyStartedError):
        await radio.start_rx(lambda _p: None)
    await radio.start_tx()
    with pytest.raises(AudioAlreadyStartedError):
        await radio.start_tx()
    await radio.stop_tx()
    await radio.stop_rx()
    assert not harness.rx_live() and not harness.tx_live()


async def test_start_failure_leaves_no_leaked_subscription(harness: _Harness) -> None:
    """MOR-560 generalized: a failed RX start must not leak a bus subscriber."""
    harness.break_rx()
    bridge = AudioBridge(
        harness.radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=FakeAudioBackend([_LOOPBACK]),
    )
    with pytest.raises(RuntimeError, match="radio RX failed to start"):
        await bridge.start()
    assert bridge.bridge_state is BridgeState.IDLE
    assert harness.radio.audio_bus.subscriber_count == 0
    assert not harness.rx_live()
    assert harness.open_streams() == 0


# ---------------------------------------------------------------------------
# Bridge round-trip — the consumer-side order declared by audio_duplex_mode
# ---------------------------------------------------------------------------


async def _assert_bridge_roundtrip(h: _Harness) -> None:
    bridge = AudioBridge(
        h.radio,
        device_name="BlackHole",
        tx_enabled=True,
        backend=FakeAudioBackend([_LOOPBACK]),
    )
    await bridge.start()
    assert h.radio.audio_bus.rx_active, "bridge started with dead radio RX"
    assert h.rx_live(), "bridge start left radio RX capture dead (MOR-556/559)"
    assert h.tx_live(), "bridge start left radio TX unarmed"
    await bridge.stop()
    assert h.radio.audio_bus.subscriber_count == 0
    assert not h.tx_live()
    if "lan-bridge-stop-leak" in h.marks:
        # Known shipping bug (see module docstring): bridge.stop() drops the
        # RX demand while the LAN state is TRANSMITTING, LanAudioStream
        # .stop_rx early-returns, and the RX loop leaks. Encoded, not fixed.
        assert h.rx_live() and h.open_streams() == 1
        pytest.xfail("LAN bridge.stop() leaks the running RX stream")
    assert not h.rx_live()
    assert h.open_streams() == 0


async def test_bridge_roundtrip_respects_declared_setup_order(
    harness: _Harness,
) -> None:
    """Bridge start must leave RX ALIVE and TX armed on EVERY transport.

    A tx-first revert (MOR-556) fails the LAN row of this test; the
    rx-first-on-exclusive half (MOR-559) is pinned by the stub-graph
    variant below.
    """
    await _assert_bridge_roundtrip(harness)


@pytest.mark.parametrize("stub_cls", [LanLikeRadio, ExclusiveUsbRadio])
async def test_bridge_roundtrip_on_declared_transition_graphs(stub_cls: type) -> None:
    """Same round-trip over the order-sensitive stub graphs (MOR-566).

    The exclusive row is the MOR-559 trap: an rx-first revert silently
    kills the running RX capture and fails the rx_live assertion.
    """
    radio: Any = stub_cls()
    # ExclusiveUsbRadio keeps rx_callback set across the silent kill, so its
    # rx_running flag is the authoritative liveness probe; LanLikeRadio has
    # no rx_running/tx_running fields and probes its single state field.
    await _assert_bridge_roundtrip(
        _Harness(
            radio=radio,
            rx_live=lambda: (
                radio.rx_running
                if hasattr(radio, "rx_running")
                else radio.rx_callback is not None
            ),
            tx_live=lambda: (
                radio.tx_running
                if hasattr(radio, "tx_running")
                else radio.state == "transmitting"
            ),
        )
    )


# ---------------------------------------------------------------------------
# Order-insensitivity: every demand-arrival permutation converges (stubs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("first", ["rx", "tx"])
async def test_lan_graph_all_arrival_orders_converge(first: str) -> None:
    """LanLikeRadio: both orders reach the same end state; the illegal edge
    raises the TYPED error (declared graph), never a silent divergence."""
    radio = LanLikeRadio()
    if first == "rx":
        await radio.start_rx(lambda _p: None)
        await radio.start_tx()
    else:
        await radio.start_tx()
        with pytest.raises(AudioAlreadyStartedError):
            await radio.start_rx(lambda _p: None)
        await radio.stop_tx()  # declared recovery: back through IDLE
        await radio.start_rx(lambda _p: None)
        await radio.start_tx()
    assert radio.state == "transmitting" and radio.rx_callback is not None
    await radio.stop_tx()
    await radio.stop_rx()
    assert radio.state == "idle" and radio.rx_callback is None


@pytest.mark.parametrize("first", ["rx", "tx"])
async def test_exclusive_graph_all_arrival_orders_converge(first: str) -> None:
    """ExclusiveUsbRadio: tx→rx is the clean edge; rx→tx hits the declared
    silent kill and must be re-armed — both converge to RX+TX running."""
    radio = ExclusiveUsbRadio()
    if first == "tx":
        await radio.start_tx()
        await radio.start_rx(lambda _p: None)
    else:
        await radio.start_rx(lambda _p: None)
        await radio.start_tx()
        assert not radio.rx_running, "graph declares the -50 silent RX kill"
        await radio.start_rx(lambda _p: None)  # recovery edge: RX onto live TX
    assert radio.rx_running and radio.tx_running
    await radio.stop_tx()
    await radio.stop_rx()
    assert not radio.rx_running and not radio.tx_running
