"""Audio-port simulation for ``MockIcomRadio`` (the IC-7610 LAN audio port).

Recorded evidence — real IC-7610 LAN audio port, 10-minute idle RX window
(``tests/fixtures/ic7610/audio_stream.json`` and the first 50 headers in
``audio_rx_headers.jsonl``, 2026-10-11/12; pinned by
``tests/testkit/test_simulator_audio.py``):

* RX streams as bursts of THREE packets every 20 ms — two of 1388 bytes
  (0x18 header + 1364 payload) and one of 1136 (0x18 + 1112): 3840
  payload bytes per burst = 20 ms of 48 kHz stereo s16, the profile's rx
  codec. Header: tracked seq at 0x06 (step 1), a second sequence at 0x12
  (BE u16, step 1), the BE u16 at 0x10 marks the first packet of each
  burst (0x8126; continuations carry 0x8026), 0x14 = 0, payload length
  at 0x16 (BE u16).
* The stream starts at the client's audio OpenClose(open) — the packet
  ``runtime/_audio_runtime_mixin.py``: ``_ensure_audio_transport`` sends
  right after the transport handshake. The conninfo rx codec travels on
  the CONTROL port, so it cannot be the audio-port trigger.
* Across a 22 s outage with a CI-V-only soft reconnect, RX resumed the
  moment the link returned, gap-free, before any new audio OpenClose
  (MOR-3361/MOR-3358 comments): a kept radio session never pauses the
  stream.
* The radio sent ZERO retransmit requests on the audio port although the
  client's audio numbering broke twice (lost packets, then a restart at
  0).

NOT recorded on the bench (simulator choices, never claimed as radio
behaviour): the TX audio path — transmit is never actuated, so the sink
records the CLIENT's format as the only reference, and only while the
audio stream lives (its OpenClose(open) until the stream stops: audio
OpenClose(close), ``audio_stream_drop`` or the session end); whether the
radio applies an in-order rule to client audio packets (none applied
here); what stops the RX stream other than the session ending (here the
audio-port OpenClose(close), ``audio_stream_drop`` and the session-end
paths stop it; a re-open restarts it with continued numbering — the
stream epoch is the radio session, like the CI-V tracker's).
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Audio packets carry an 0x18-byte header (``src/rigplane/audio/lan_stream.py``:
# AUDIO_HEADER_SIZE; the recorded headers in audio_rx_headers.jsonl).
AUDIO_HEADER_SIZE = 0x18

# Recorded cadence (audio_stream.json: 150.01 packets/s): one burst of
# three packets every 20 ms, ~0.3 ms apart (the first recorded burst:
# 0.0/0.0003/0.0005).
_AUDIO_BURST_PERIOD_S = 0.020
_AUDIO_IN_BURST_OFFSETS_S = (0.0, 0.0003, 0.0005)

# Recorded per-burst payload split: 1364 + 1364 + 1112 = 3840 bytes =
# 20 ms of 48 kHz stereo s16 (the profile's PCM_2CH_16BIT rx codec).
_AUDIO_BURST_PAYLOADS = (1364, 1364, 1112)

# BE u16 at 0x10: 0x8126 on the first packet of each burst, 0x8026 on the
# continuations (audio_rx_headers.jsonl).
_RX_IDENT_FIRST_FRAGMENT = 0x8126
_RX_IDENT_CONTINUATION = 0x8026

# A byte ramp rotated by the inner sequence: the "known pattern" the tests
# decode (no on-air samples are committed).
_PCM_RAMP = bytes(range(256))


@dataclass(frozen=True, slots=True)
class ClientAudioFrame:
    """One client→radio audio DATA frame observed on the audio port.

    The TX audio path is unrecorded on the radio, so the client's format
    (``src/rigplane/audio/lan_stream.py``: ``build_audio_packet``) is the
    only reference: ``seq`` is the tracked header sequence (LE u16 at
    0x06), ``inner_seq`` the audio-level sequence (BE u16 at 0x12),
    ``payload_len`` the bytes after the header, ``received_at`` the
    arrival ``time.monotonic()``.
    """

    seq: int
    inner_seq: int
    payload_len: int
    received_at: float


class AudioPortMixin:
    """The audio-port half of ``MockIcomRadio`` (mixed into it).

    The listener reuses the CI-V port's transport paths (the same
    ``_MockProtocol``, the ``_handshake_reply`` replies and the reboot
    down-window gate) and adds the RX streamer, the TX sink and the
    silence/drop events. The declarative ``FaultPlan`` covers control and
    CI-V only (``faults.py``: ``Port``), so audio traffic bypasses it.
    State lives on the owning ``MockIcomRadio`` (initialized by
    ``_audio_init``); simulator.py's wire constants are imported inside
    the methods because simulator.py imports this module for the mixin.
    """

    # ------------------------------------------------------------------
    # Lifecycle (called from MockIcomRadio)
    # ------------------------------------------------------------------

    def _audio_init(self) -> None:
        """Initialize the audio-port state (idempotent per instance)."""
        self._audio_udp: asyncio.DatagramTransport | None = None
        self._audio_proto = None
        self._actual_audio_port: int = 0
        # RX stream state: the client that opened audio, its stream task
        # and both sequence counters (header 0x06 / inner 0x12), which
        # step 1 per packet and wrap at 0xFFFF.
        self._audio_client: tuple[tuple[str, int], int] | None = None
        self._audio_rx_task: asyncio.Task[None] | None = None
        self._audio_rx_seq: int = 0
        self._audio_rx_inner_seq: int = 0
        # audio_port_silence deadline (0.0 = inactive).
        self._audio_silent_until: float = 0.0
        # Observables (names fixed; Lane B tests depend on them).
        self._audio_rx_frames_sent: int = 0
        self.audio_tx_frames: list[ClientAudioFrame] = []
        self._audio_last_client_seq: int | None = None
        self._audio_retransmit_requests_received: int = 0

    async def _audio_start(self) -> None:
        """Bind the audio port at the CI-V port + 1 when free (else any).

        The status reply advertises the bound port, so the old dummy
        ``civ_port + 1`` becomes the bind hint.
        """
        from rigplane_testkit.icom.simulator import _MockProtocol

        loop = asyncio.get_running_loop()
        hint = self._actual_civ_port + 1 if self._actual_civ_port else 0
        try:
            transport, proto = await loop.create_datagram_endpoint(
                lambda: _MockProtocol(self, "audio"),
                local_addr=(self._host, hint),
            )
        except OSError:
            # The adjacent port is taken: bind any port; the status reply
            # advertises the actual bound port either way.
            transport, proto = await loop.create_datagram_endpoint(
                lambda: _MockProtocol(self, "audio"),
                local_addr=(self._host, 0),
            )
        self._audio_udp = transport
        self._audio_proto = proto
        self._actual_audio_port = transport.get_extra_info("sockname")[1]

    async def _audio_stop(self) -> None:
        """Cancel the RX stream and close the listener (idempotent)."""
        self._audio_end_session("stop")
        if self._audio_udp is not None:
            self._audio_udp.close()
            self._audio_udp = None
        self._audio_proto = None
        self._actual_audio_port = 0

    # ------------------------------------------------------------------
    # Observables (fixed names — Lane B depends on them)
    # ------------------------------------------------------------------

    @property
    def audio_port(self) -> int:
        """Bound audio-port number (0 when the listener is off or before
        :meth:`start`)."""
        return self._actual_audio_port

    @property
    def audio_rx_frames_sent(self) -> int:
        """RX audio packets actually sent on the audio port (cumulative).

        Emissions suppressed by a silence or reboot window step the
        packet sequences but are not counted here.
        """
        return self._audio_rx_frames_sent

    @property
    def audio_last_client_seq(self) -> int | None:
        """The client's last tracked audio-port number, per stream.

        Every tracked type-0 packet the client sends while the audio
        stream lives — idle, OpenClose, audio DATA — advances it (one
        numbering space, like the CI-V port's tracker; the audio
        OpenClose(open) itself is the first tracked number).
        ``None`` before the open and after the session ends
        (``_audio_end_session`` resets it; an audio OpenClose(close) or
        ``audio_stream_drop`` stops the stream but keeps the last number
        for assertions); a client that restarts its numbering (MOR-3366)
        shows as a lower or wrapped value. No in-order rule is applied
        (unrecorded).
        """
        return self._audio_last_client_seq

    @property
    def audio_retransmit_requests_received(self) -> int:
        """Retransmit requests the CLIENT sent on the audio port.

        Counted per packet (single and ranges forms alike) and never
        answered: the simulator keeps no sent-audio buffer, and nothing
        recorded says the radio re-sends audio on request.
        """
        return self._audio_retransmit_requests_received

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def audio_port_silence(self, at: float, for_seconds: float) -> None:
        """Schedule a window where the audio port answers NOTHING.

        No I-Am-Here, no ping reply, no RX audio, while the control and
        CI-V ports answer as usual. Both packet sequences keep stepping
        through the window while nothing is sent, so the client sees lost
        packets when it ends — the shape of the recorded 22 s outage
        (``audio_rx_frames_sent`` counts only sent packets). Call from
        inside the loop.
        """
        loop = asyncio.get_running_loop()
        loop.call_later(
            max(0.0, at),
            self._begin_audio_silence,
            max(0.0, for_seconds),
        )

    def _begin_audio_silence(self, for_seconds: float) -> None:
        self._audio_silent_until = time.monotonic() + for_seconds
        logger.debug("Mock: audio port silence for %.2fs", for_seconds)

    def audio_stream_drop(self) -> None:
        """Stop the RX audio stream while keeping the radio session.

        A later audio OpenClose(open) restarts it with continued
        numbering. What stops a real radio's RX stream besides the session
        ending is not recorded — this is a test seam, not recorded radio
        behaviour. Call from inside the loop.
        """
        self._audio_stop_rx("audio_stream_drop")

    # ------------------------------------------------------------------
    # Session hooks (called from MockIcomRadio's session lifecycle)
    # ------------------------------------------------------------------

    def _audio_end_session(self, reason: str) -> None:
        """End the audio stream epoch with the radio session.

        Stops the RX stream, forgets the audio client (which also stops
        TX-frame recording) and resets the client-numbering observable
        (a new session starts a new numbering, like the CI-V tracker
        reset). The cumulative counters (``audio_rx_frames_sent``,
        ``audio_tx_frames``, ``audio_retransmit_requests_received``)
        survive for assertions.
        """
        self._audio_stop_rx(reason)
        self._audio_last_client_seq = None

    def _audio_stop_rx(self, reason: str) -> None:
        """Cancel the RX stream task and forget the audio client."""
        if self._audio_rx_task is not None:
            self._audio_rx_task.cancel()
            self._audio_rx_task = None
        if self._audio_client is not None:
            self._audio_client = None
            logger.debug("Mock: audio RX stopped (%s)", reason)

    # ------------------------------------------------------------------
    # Inbound dispatch (called from MockIcomRadio._dispatch)
    # ------------------------------------------------------------------

    def _handle_audio_packet(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        proto,
    ) -> None:
        """Parse and dispatch one inbound audio-port datagram.

        The transport handshake reuses the CI-V port's replies
        (``MockIcomRadio._handshake_reply``); type-0 packets carry the
        client's tracked numbering; audio DATA frames land in the TX sink.
        """
        # simulator.py imports this module for the mixin, so its wire
        # constants are imported here, not at module level.
        import rigplane_testkit.icom.simulator as _sim

        if time.monotonic() < self._audio_silent_until:
            return  # audio_port_silence: the port answers nothing
        n = len(data)

        # Transport handshake: the CI-V port's own replies (same bytes),
        # sent through this port's funnel with its silence gate.
        reply = self._handshake_reply(data, ptype, sender_id)
        if reply is not None:
            self._audio_send(reply, addr)
            return

        # Client retransmit request: counted, never answered (see
        # ``audio_retransmit_requests_received``).
        if n >= _sim._HEADER_SIZE and ptype == _sim._PT_CONTROL:
            self._audio_retransmit_requests_received += 1
            return

        # Disconnect: ignored, like the CI-V port ignores it.
        if n == _sim._HEADER_SIZE and ptype == _sim._PT_DISCONNECT:
            return

        if ptype == _sim._PT_DATA:
            if n == _sim._HEADER_SIZE:
                # Idle keepalive: tracked, never answered — like the CI-V
                # port, which keeps its own cadence. Only while the audio
                # stream lives; before the open and after the stream or
                # session ended it draws no tracking.
                if self._audio_client is not None:
                    self._audio_last_client_seq = seq
                return
            if n == 0x16:
                # The production audio OpenClose (open starts the RX
                # stream; close stops it).
                if data[0x15] == _sim._OPENCLOSE_OPEN:
                    self._audio_start_rx(addr, sender_id)
                    # The open itself is the first tracked packet, like
                    # the CI-V port's open (``_civ_gap_ensure``).
                    self._audio_last_client_seq = seq
                else:
                    self._audio_stop_rx("audio_openclose_close")
                return
            if n > AUDIO_HEADER_SIZE:
                # Client audio DATA frame → the TX sink, recorded only
                # while the audio stream lives (its OpenClose(open) until
                # the stream stops: audio OpenClose(close),
                # ``audio_stream_drop`` or the session end). Whether and
                # how a real radio gates TX audio is unrecorded; the
                # scope of this sink is a simulator choice.
                if self._audio_client is not None:
                    self._audio_last_client_seq = seq
                    self.audio_tx_frames.append(
                        ClientAudioFrame(
                            seq=seq,
                            inner_seq=struct.unpack_from(">H", data, 0x12)[0],
                            payload_len=n - AUDIO_HEADER_SIZE,
                            received_at=time.monotonic(),
                        )
                    )
                return

    def _audio_start_rx(self, addr: tuple[str, int], sender_id: int) -> None:
        """Target the RX stream at this client; start the burst loop.

        The client is re-learned on every open, so a rebuilt audio socket
        (new addr) retargets the stream within the same session; the
        numbering never restarts on a re-open — the epoch is the session.
        """
        self._audio_client = (addr, sender_id)
        if self._audio_rx_task is None or self._audio_rx_task.done():
            self._audio_rx_task = asyncio.get_running_loop().create_task(
                self._audio_rx_loop()
            )

    # ------------------------------------------------------------------
    # Outbound funnel and RX streamer
    # ------------------------------------------------------------------

    def _audio_send(self, pkt: bytes, addr: tuple[str, int]) -> bool:
        """Single outbound funnel for audio-port datagrams.

        Applies the reboot down-window and the audio-port silence gate,
        then sends through the listener's transport. The declarative fault
        plan does not apply (control/CI-V ports only — ``faults.py``:
        ``Port``). Returns True only when the packet was actually sent,
        so the RX counter counts sends, not suppressed emissions.
        """
        if time.monotonic() < self._down_until:
            return False  # reboot window: the radio is "powered off"
        if time.monotonic() < self._audio_silent_until:
            return False  # audio_port_silence: the port answers nothing
        proto = self._audio_proto
        if proto is not None:
            proto.send(pkt, addr)
            return True
        return False

    async def _audio_rx_loop(self) -> None:
        """Emit the recorded RX stream: three packets every 20 ms."""
        try:
            while True:
                await asyncio.sleep(_AUDIO_BURST_PERIOD_S)
                client = self._audio_client
                if client is None or self._audio_udp is None:
                    continue
                addr, client_id = client
                loop = asyncio.get_running_loop()
                for position, offset in enumerate(_AUDIO_IN_BURST_OFFSETS_S):
                    loop.call_later(
                        offset,
                        self._audio_emit_rx_packet,
                        position,
                        addr,
                        client_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock: audio RX loop error")

    def _audio_emit_rx_packet(
        self, position: int, addr: tuple[str, int], client_id: int
    ) -> None:
        """Build and send one RX packet (dropped unless the stream lives).

        The emission slot still steps both sequences when the packet is
        suppressed (silence/reboot window), so the client sees the gap;
        only actually-sent packets advance ``audio_rx_frames_sent``.
        """
        if self._audio_client is None:
            return  # the stream stopped after this emission was scheduled
        pkt = self._audio_rx_packet(position, client_id)
        if self._audio_send(pkt, addr):
            self._audio_rx_frames_sent += 1

    def _audio_rx_packet(self, position: int, client_id: int) -> bytes:
        """Build one RX packet with the recorded header layout.

        ``position`` is the packet's place in its burst (0..2): the first
        packet of a burst carries the first-fragment ident. Both sequences
        step 1 per packet (wrap at 0xFFFF); the payload is the byte ramp
        seeded by the inner sequence. Building never counts: the caller
        counts only packets the funnel actually sent.
        """
        import rigplane_testkit.icom.simulator as _sim

        payload_len = _AUDIO_BURST_PAYLOADS[position]
        total = AUDIO_HEADER_SIZE + payload_len
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0x00, total)
        struct.pack_into("<H", pkt, 0x04, _sim._PT_DATA)
        struct.pack_into("<H", pkt, 0x06, self._audio_rx_seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        ident = _RX_IDENT_FIRST_FRAGMENT if position == 0 else _RX_IDENT_CONTINUATION
        struct.pack_into(">H", pkt, 0x10, ident)
        struct.pack_into(">H", pkt, 0x12, self._audio_rx_inner_seq)
        struct.pack_into(">H", pkt, 0x14, 0)  # recorded: always 0
        struct.pack_into(">H", pkt, 0x16, payload_len)
        start = self._audio_rx_inner_seq & 0xFF
        pkt[AUDIO_HEADER_SIZE:] = (_PCM_RAMP * (payload_len // 256 + 2))[
            start : start + payload_len
        ]
        self._audio_rx_seq = (self._audio_rx_seq + 1) & 0xFFFF
        self._audio_rx_inner_seq = (self._audio_rx_inner_seq + 1) & 0xFFFF
        return bytes(pkt)
