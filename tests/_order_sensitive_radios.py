"""Shared order-sensitive radio stubs for start-order regression tests (MOR-566).

Promoted from the bespoke stubs in ``tests/test_audio_bridge.py`` (MOR-556 /
MOR-559 regression suites) so any test can exercise the stream start-order
bug class without hand-building stateful stubs. Order-insensitive doubles
(``FakeAudioBackend``-style) cannot catch this class — these stubs exist
precisely because the transition ORDER is the contract under test.

Both stubs expose the neutral ``AudioTransport``-ish surface the bridge
consumes (``start_rx`` / ``stop_rx`` / ``start_tx`` / ``stop_tx`` /
``push_tx``, ``audio_tx_codec``, ``audio_bus``) plus call-order tracking via
the ``calls`` list.

Declared transition graphs
--------------------------

``LanLikeRadio`` — single state field mirroring ``LanAudioStream``::

    idle --start_rx--> receiving
    receiving --start_tx--> transmitting
    idle --start_tx--> transmitting            (RX never armed!)
    transmitting --start_rx--> AudioAlreadyStartedError    <- MOR-556
    transmitting --stop_tx--> receiving (rx_callback set) | idle
    receiving --stop_rx--> idle

RX can only start from IDLE: arming radio TX before the AudioBus RX
subscribe kills RX entirely (regression from #1735).

``ExclusiveUsbRadio`` — two legs on ONE exclusive physical device
(``audio_duplex_mode == "exclusive"``, MOR-534)::

    rx_running --start_tx--> rx DEAD (silent!), tx_running  <- MOR-559
    tx_running --start_rx--> rx_running, tx_running          (clean)

Adding the TX playback leg to an already-running RX capture models the
macOS CoreAudio AUHAL ``paramErr -50``: no Python exception is raised —
the capture just dies, exactly as observed live (MOR-531 de-risk).
"""

from __future__ import annotations

from rigplane.audio.bus import AudioBus
from rigplane.audio.usb_driver import AudioAlreadyStartedError, AudioNotStartedError
from rigplane.core.types import AudioCodec


class LanLikeRadio:
    """Radio stub mirroring ``LanAudioStream``'s RX/TX state machine.

    The real LAN stream supports RX-then-TX only: ``start_rx`` requires
    IDLE state, while ``start_tx`` flips the single state field to
    "transmitting". Arming radio TX before the AudioBus RX subscribe
    therefore kills RX entirely (regression from #1735, MOR-556).
    See the module docstring for the declared transition graph.
    """

    def __init__(self) -> None:
        self.state = "idle"
        self.calls: list[str] = []
        self.rx_callback: object | None = None
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.audio_bus = AudioBus(self)

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        self.calls.append("start_rx")
        if self.state != "idle":
            raise AudioAlreadyStartedError(f"Cannot start RX in state {self.state}")
        self.rx_callback = callback
        self.state = "receiving"

    async def stop_rx(self) -> None:
        if self.state == "receiving":
            self.state = "idle"
        self.rx_callback = None

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        if self.state == "transmitting":
            raise AudioAlreadyStartedError("Already transmitting")
        self.state = "transmitting"

    async def stop_tx(self) -> None:
        if self.state != "transmitting":
            return
        self.state = "receiving" if self.rx_callback is not None else "idle"

    async def push_tx(self, audio_data: bytes) -> None:
        if self.state != "transmitting":
            raise AudioNotStartedError(f"Cannot push TX in state {self.state}")


class ExclusiveUsbRadio:
    """Radio stub mirroring the FTX-1 same-device USB CODEC (MOR-559).

    ``audio_duplex_mode == "exclusive"`` (MOR-534): RX and TX resolve to ONE
    physical macOS C-Media device. Per the MOR-531 live de-risk, adding the
    TX playback leg to an ALREADY-RUNNING RX capture triggers CoreAudio AUHAL
    paramErr -50 and silently kills the capture (no Python exception — live,
    the bridge then reported "started (RX+TX)" with dead RX). Adding RX to a
    running TX leg is clean. The stub reproduces the silent capture death so
    the wrong order fails the test the same way it failed live.
    See the module docstring for the declared transition graph.
    """

    audio_duplex_mode = "exclusive"

    def __init__(self) -> None:
        self.rx_running = False
        self.tx_running = False
        self.calls: list[str] = []
        self.rx_callback: object | None = None
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.audio_bus = AudioBus(self)

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        self.calls.append("start_rx")
        self.rx_callback = callback
        self.rx_running = True

    async def stop_rx(self) -> None:
        self.rx_running = False
        self.rx_callback = None

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        if self.rx_running:
            # ||PaMacCore (AUHAL)|| err='-50': the TX open nominally succeeds
            # but the device's running RX capture dies silently.
            self.rx_running = False
        self.tx_running = True

    async def stop_tx(self) -> None:
        self.tx_running = False

    async def push_tx(self, audio_data: bytes) -> None:
        if not self.tx_running:
            raise AudioNotStartedError("TX not armed")
