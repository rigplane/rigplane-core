"""Scripted transmit-state fakes for the transmit-authority conformance matrix.

ADR row 4, ``docs/plans/2026-08-20-transmit-authority.md`` §3.10 item 1: the
existing fake harnesses, extended with a **scripted transmit-state answer** so
the hazard gate's solicited read exercises the real code path rather than a
hand-written stub of it.

One vocabulary, three wires. Each backend family answers the same six scripted
answers over its own real read code:

======================  ==========================  ==============  ==============
answer                  CI-V (Icom)                 Yaesu CAT       rigctld client
======================  ==========================  ==============  ==============
``rx``                  directed ``1C 00`` + ``00`` ``TX0``         ``0``
``tx_cat``              directed ``1C 00`` + ``01`` ``TX1``         ``1``
``tx_other``            (Icom carries no            ``TX2``         (upstream
                        attribution — §3.7)                         carries none)
``silence``             no reply at all             read times out  delayed past
                                                                    the deadline
``refusal``             NAK ``FA``                  ``?;``          malformed line
``unmapped``            ``1C 00`` + ``00 00``       ``TX9``         ``9``
======================  ==========================  ==============  ==============

The CI-V column additionally scripts three shapes that must **never** satisfy a
read (INV-13): an ACK, a setter echo, and a mis-addressed frame. They are not
filtered here — they go onto the wire and are rejected by the shipped
directed-exact-reply discipline (``runtime/_civ_rx.py:1489-1492`` routing
guards and ``:2636-2650`` provenance narrowing), which is the whole point: the
fake proves the *product's* validation discriminates, not its own.

The ``unmapped`` CI-V answer is deliberately ``00 00`` rather than a junk byte.
Its first byte is ``0x00``, so if the shape check were ever loosened it would
decode as **receiving** — the fail-open direction. A junk byte like ``0x02``
would decode as transmitting and could not catch that class of regression.

No MagicMock anywhere (CLAUDE.md hard rule, restated for this row by §3.10
item 1): every fake here is a plain object with real methods.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer, FakeRigctldState
from test_radio import MockTransport

from _helpers import wrap_civ_in_udp
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic9700 import Ic9700SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.tx_authority import RADIO_READBACK_SOURCES, TxStateReading
from rigplane.radio import IcomRadio

# ---------------------------------------------------------------------------
# The scripted answer vocabulary
# ---------------------------------------------------------------------------

TxAnswer = Literal["rx", "tx_cat", "tx_other", "silence", "refusal", "unmapped"]

#: Explicit literal, never computed from a type or an enum — the
#: ``test_audio_transport_conformance.py:65-81`` rule.
TX_ANSWER_VOCABULARY: tuple[TxAnswer, ...] = (
    "rx",
    "tx_cat",
    "tx_other",
    "silence",
    "refusal",
    "unmapped",
)

#: The CI-V shapes that must never satisfy a read (INV-13). Also an explicit
#: literal.
CIV_NON_ANSWERS: tuple[str, ...] = ("ack", "setter_echo", "misaddressed")

#: Answers that mean "the radio is transmitting". Explicit, never derived.
TRANSMITTING_ANSWERS: tuple[TxAnswer, ...] = ("tx_cat", "tx_other")

#: Answers that must never resolve to *receiving* on any backend: the read
#: failed, the radio refused, or the value is outside the positive map (§3.7).
NON_RECEIVING_ANSWERS: tuple[TxAnswer, ...] = ("silence", "refusal", "unmapped")

_PTT_FIELD = "global.tx_state.ptt"

#: A CI-V address that is not any bench radio's, for the mis-addressed shape.
FOREIGN_CIV_ADDR = 0x88


def civ_transmit_state_reply(answer: str, radio_addr: int) -> bytes | None:
    """The CI-V frame the fake radio answers a ``1C 00`` read with.

    ``None`` means "answer nothing" — the silence row.
    """
    if answer == "rx":
        return build_civ_frame(
            CONTROLLER_ADDR, radio_addr, 0x1C, sub=0x00, data=b"\x00"
        )
    if answer in ("tx_cat", "tx_other"):
        # Icom reports no attribution at all, so both keyed answers are the
        # same byte on this wire; the attribution field stays honestly None.
        return build_civ_frame(
            CONTROLLER_ADDR, radio_addr, 0x1C, sub=0x00, data=b"\x01"
        )
    if answer == "unmapped":
        return build_civ_frame(
            CONTROLLER_ADDR, radio_addr, 0x1C, sub=0x00, data=b"\x00\x00"
        )
    if answer == "refusal":
        return build_civ_frame(CONTROLLER_ADDR, radio_addr, 0xFA)
    if answer == "ack":
        return build_civ_frame(CONTROLLER_ADDR, radio_addr, 0xFB)
    if answer == "setter_echo":
        # Addressed *to* the radio, exactly as our own ptt_off setter is.
        return build_civ_frame(
            radio_addr, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x00"
        )
    if answer == "misaddressed":
        return build_civ_frame(
            CONTROLLER_ADDR, FOREIGN_CIV_ADDR, 0x1C, sub=0x00, data=b"\x00"
        )
    if answer == "silence":
        return None
    raise AssertionError(f"unscripted transmit-state answer {answer!r}")


def _is_civ_transmit_state_read(payload: bytes) -> bool:
    """``FE FE <to> <from> 1C 00 FD`` — the bare GET, no data byte."""
    return len(payload) == 7 and payload[4] == 0x1C and payload[5] == 0x00


# ---------------------------------------------------------------------------
# Wires
# ---------------------------------------------------------------------------


class ScriptedCivLink:
    """A serial CI-V link that answers the transmit-state read by content.

    The shipped serial fake (``tests/test_icom7610_serial_radio.py:125``)
    scripts replies by *send ordinal*; a solicited read inside a gate has no
    stable ordinal, so this one matches on ``(command, sub)`` instead. Same
    duck-typed surface as the production ``SerialCivLink``.
    """

    def __init__(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False
        self.sent_frames: list[bytes] = []
        self.answer: str = "rx"
        self.radio_addr: int = 0x94
        self._replies: asyncio.Queue[bytes] = asyncio.Queue()

    def set_device(self, device: str) -> None:
        """Reconnection seam the serial base class calls."""

    async def connect(self) -> None:
        self.connected = self.ready = self.healthy = True

    async def disconnect(self) -> None:
        self.connected = self.ready = self.healthy = False

    async def send(self, frame: bytes) -> None:
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        payload = bytes(frame)
        self.sent_frames.append(payload)
        if _is_civ_transmit_state_read(payload):
            reply = civ_transmit_state_reply(self.answer, self.radio_addr)
            if reply is not None:
                self._replies.put_nowait(reply)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        if not self.connected:
            return None
        try:
            return await asyncio.wait_for(
                self._replies.get(), timeout=0.05 if timeout is None else timeout
            )
        except asyncio.TimeoutError:
            return None


class ScriptedLanTransport(MockTransport):
    """The LAN transport fake, answering the transmit-state read by content."""

    def __init__(self) -> None:
        super().__init__()
        self.answer: str = "rx"
        self.radio_addr: int = 0x98

    async def send_tracked(self, data: bytes) -> None:
        await super().send_tracked(data)
        payload = bytes(data)[0x15:]
        if _is_civ_transmit_state_read(payload):
            reply = civ_transmit_state_reply(self.answer, self.radio_addr)
            if reply is not None:
                self.queue_response(wrap_civ_in_udp(reply))

    def civ_wire(self) -> list[bytes]:
        """The CI-V payloads, unwrapped from their UDP envelopes."""
        return [bytes(pkt)[0x15:] for pkt in self.sent_packets if len(pkt) > 0x15]


class ScriptedCatTransport:
    """Yaesu CAT reads/writes, scripted by answer name.

    Installed over a real ``YaesuCatRadio``'s real transport object by
    replacing its two async entry points — the established house pattern
    (``tests/test_ftx1_radio.py:34-46``) minus the mock.
    """

    _READ_ANSWERS = {
        "rx": "TX0",
        "tx_cat": "TX1",
        "tx_other": "TX2",
        "unmapped": "TX9",
        "refusal": "?",
    }

    def __init__(self, radio: YaesuCatRadio) -> None:
        self.wire: list[str] = []
        self.answer: str = "rx"
        radio._transport._connected = True
        radio._transport.query = self.query  # type: ignore[method-assign]
        radio._transport.write = self.write  # type: ignore[method-assign]

    async def query(self, cmd: str, *args: Any, **kwargs: Any) -> str:
        self.wire.append(f"?{cmd}")
        if self.answer == "silence":
            raise asyncio.TimeoutError("no CAT answer")
        try:
            return self._READ_ANSWERS[self.answer]
        except KeyError:  # pragma: no cover - guards a typo in a new row
            raise AssertionError(
                f"unscripted transmit-state answer {self.answer!r}"
            ) from None

    async def write(self, cmd: str, *args: Any, **kwargs: Any) -> None:
        self.wire.append(f"!{cmd}")


# ---------------------------------------------------------------------------
# One harness row
# ---------------------------------------------------------------------------


@dataclass
class TxConformanceHarness:
    """One backend column of the matrix: a real radio on a scripted wire."""

    name: str
    radio: Any
    script: Callable[[str], None]
    read_transmit_state: Callable[[], Awaitable[TxStateReading]]
    wire: Callable[[], Sequence[str]]
    is_read: Callable[[str], bool]
    hazard_method: str
    hazard_args: tuple[Any, ...]
    hazard: Callable[[], Awaitable[None]]
    key: Callable[[], Awaitable[None]]
    unkey: Callable[[], Awaitable[None]]
    close: Callable[[], Awaitable[None]]
    #: rigctld-client answers from an upstream cache, never the radio (§3.7).
    verified_readback: bool = True
    #: Only the ``ObservationPollable`` backends have the queue ingress the
    #: facade design missed (the D1 lesson, §3.2 item 2).
    queue_command: Any | None = None
    queue_unkey: Any | None = None
    drain: Callable[[], Awaitable[None]] | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def writes(self) -> list[str]:
        return [entry for entry in self.wire() if not self.is_read(entry)]

    def reads(self) -> list[str]:
        return [entry for entry in self.wire() if self.is_read(entry)]


# ---------------------------------------------------------------------------
# Per-backend construction
# ---------------------------------------------------------------------------

_ICOM_SERIAL_CLASSES = {
    "icom7610-serial": Icom7610SerialRadio,
    "ic705-serial": Ic705SerialRadio,
    "ic7300-serial": Ic7300SerialRadio,
    "ic9700-serial": Ic9700SerialRadio,
}

#: Every shipping backend class, as an explicit literal. Mirrors
#: ``tests/contracts/test_audio_transport_conformance.py:83-90`` and adds the
#: rigctld-client backend, which ships no audio but does ship writes.
CONFORMANCE_BACKENDS: tuple[str, ...] = (
    "lan-icom",
    "icom7610-serial",
    "ic705-serial",
    "ic7300-serial",
    "ic9700-serial",
    "yaesu-ftx1",
    "rigctld-client",
)

#: The backends whose web write path is the ``create_observation_poller``
#: queue drain (``web_startup.py:105-193`` branch 1) — the ingress a wrapping
#: facade could never see.
QUEUE_PATH_BACKENDS: tuple[str, ...] = ("yaesu-ftx1", "rigctld-client")


async def _civ_reading(radio: Any, timeout: float) -> TxStateReading:
    """One solicited CI-V transmit-state read, validated as the product does.

    Row 5 will put this behind ``TransmitStateReadable``; until then the
    harness assembles it from shipped parts so the conformance rows exercise
    the real validation rather than a stand-in for it. Note which parts:
    ``CivRequestTracker`` matches ``(command, sub, receiver)`` with **no**
    address check (``core/civ.py:410-417``), so the discrimination comes from
    the RX pump's routing guards plus the provenance narrowing at
    ``_civ_rx.py:2636-2650`` — exactly the trap §3.4 row 5 names.
    """
    frame = build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0x00)
    try:
        reply = await radio._send_civ_expect(frame, label="tx-state", timeout=timeout)
    except asyncio.TimeoutError:
        return TxStateReading(value=None, failure="timeout")
    except CommandError:
        # No usable answer: nothing came back, or what came back was a NAK.
        return TxStateReading(value=None, failure="read-error")
    for observation in radio._civ_runtime._observations_from_frame(reply):
        if str(observation.path) != _PTT_FIELD:
            continue
        source = str(observation.source.source)
        if source in RADIO_READBACK_SOURCES:
            return TxStateReading(
                value=bool(observation.value),
                attributed=None,  # Icom reports no attribution (§3.7)
                source=source,
                verified_readback=True,
            )
    return TxStateReading(value=None, failure="unverifiable-provenance")


async def _build_lan_icom() -> TxConformanceHarness:
    transport = ScriptedLanTransport()
    radio = IcomRadio("192.168.99.1", timeout=0.2)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._radio_addr = transport.radio_addr
    radio._start_civ_rx_pump()
    await asyncio.sleep(0)

    async def close() -> None:
        radio._connected = False
        task = radio._civ_rx_task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        radio._civ_transport = None
        radio._ctrl_transport = None

    return TxConformanceHarness(
        name="lan-icom",
        radio=radio,
        script=lambda answer: setattr(transport, "answer", answer),
        read_transmit_state=lambda: _civ_reading(radio, 0.15),
        wire=lambda: [payload.hex() for payload in transport.civ_wire()],
        is_read=_is_hex_civ_read,
        hazard_method="set_tuner_status",
        hazard_args=(2,),
        hazard=lambda: radio.set_tuner_status(2),
        key=lambda: radio.set_ptt(True),
        unkey=lambda: radio.set_ptt(False),
        close=close,
    )


def _is_hex_civ_read(entry: str) -> bool:
    return _is_civ_transmit_state_read(bytes.fromhex(entry))


async def _build_icom_serial(name: str) -> TxConformanceHarness:
    link = ScriptedCivLink()
    radio = _ICOM_SERIAL_CLASSES[name](device="/dev/ttyUSB0", civ_link=link)
    await radio.connect()
    link.radio_addr = radio._radio_addr

    return TxConformanceHarness(
        name=name,
        radio=radio,
        script=lambda answer: setattr(link, "answer", answer),
        read_transmit_state=lambda: _civ_reading(radio, 0.15),
        wire=lambda: [payload.hex() for payload in link.sent_frames],
        is_read=_is_hex_civ_read,
        hazard_method="set_tuner_status",
        hazard_args=(2,),
        hazard=lambda: radio.set_tuner_status(2),
        key=lambda: radio.set_ptt(True),
        unkey=lambda: radio.set_ptt(False),
        close=radio.disconnect,
    )


async def _build_yaesu() -> TxConformanceHarness:
    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    cat = ScriptedCatTransport(radio)

    # ``read()`` below must exercise the *shipped* ``read_ptt`` predicate --
    # not reimplement it -- so a regression there (e.g. the MOR-1905
    # inversion) is caught by this matrix (MOR-1941 review, BLOCKED-2).
    # ``read_ptt`` calls ``read_ptt_token`` internally exactly once; this
    # wraps that call to capture the raw token for attribution, so the
    # attribution mapping rides the same single wire round-trip instead of
    # re-querying the radio. Same house pattern ``ScriptedCatTransport``
    # already uses to replace an instance's bound methods.
    last_token: dict[str, str | None] = {"value": None}
    _real_read_ptt_token = radio.read_ptt_token

    async def _capturing_read_ptt_token() -> str:
        token = await _real_read_ptt_token()
        last_token["value"] = token
        return token

    radio.read_ptt_token = _capturing_read_ptt_token  # type: ignore[method-assign]

    async def read() -> TxStateReading:
        try:
            value = await radio.read_ptt()
        except asyncio.TimeoutError:
            return TxStateReading(value=None, failure="timeout")
        except Exception:
            # ``?;`` fails the typed parse — the radio refused the read.
            return TxStateReading(value=None, failure="read-error")
        policy = radio.profile.tx_policy
        return TxStateReading(
            value=value,
            # MOR-1941: the three-valued answer routes through
            # ``tx_state_map`` into this field via ``TxPolicy.attribution``,
            # off the token ``read_ptt`` itself just consumed above.
            attributed=policy.attribution(last_token["value"] or ""),
            source="yaesu_poll_response",
            verified_readback=True,
        )

    from rigplane.runtime._poller_types import CommandQueue, PttOff, SetTunerStatus

    queue = CommandQueue()
    seen: list[Any] = []
    poller = radio.create_observation_poller(callback=seen.extend, command_queue=queue)

    async def close() -> None:
        return None

    return TxConformanceHarness(
        name="yaesu-ftx1",
        radio=radio,
        script=lambda answer: setattr(cat, "answer", answer),
        read_transmit_state=read,
        wire=lambda: list(cat.wire),
        is_read=lambda entry: entry.startswith("?"),
        # The alias chain's innermost body: ``set_tuner_status`` is a pure
        # alias onto ``set_tuner`` (§3.2), and the queue drain calls the
        # latter directly (``yaesu_cat/poller.py:1099-1100``).
        hazard_method="set_tuner",
        hazard_args=(1,),
        hazard=lambda: radio.set_tuner(1),
        key=lambda: radio.set_ptt(True),
        unkey=lambda: radio.set_ptt(False),
        close=close,
        queue_command=SetTunerStatus(1),
        queue_unkey=PttOff(),
        drain=poller._drain_commands,
        extras={"queue": queue, "poller": poller, "cat": cat, "observations": seen},
    )


async def _build_rigctld_client() -> TxConformanceHarness:
    server = FakeRigctldServer(state=FakeRigctldState(), behavior=FakeRigctldBehavior())
    await server.start()
    radio = RigctldClientRadio(host=server.host, port=server.port)
    await radio.connect()

    def script(answer: str) -> None:
        server.behavior.command_delays.pop("t", None)
        server.behavior.malformed_responses.pop("t", None)
        server.behavior.disconnect_commands.discard("t")
        if answer == "rx":
            server.state.ptt = 0
        elif answer in ("tx_cat", "tx_other"):
            server.state.ptt = 1
        elif answer == "silence":
            # Realised as the upstream dropping the connection rather than a
            # bare delay: this socket is shared with every later read, and a
            # late line arriving after the gate cancelled its ``wait_for``
            # would answer the *next* read. Same fail direction either way —
            # no answer, so the hazard gate refuses ``tx-truth-unavailable``.
            server.behavior.disconnect_commands.add("t")
        elif answer == "refusal":
            server.behavior.malformed_responses["t"] = b"RPRT -1\n"
        elif answer == "unmapped":
            server.behavior.malformed_responses["t"] = b"9\n"
        else:  # pragma: no cover - guards a typo in a new row
            raise AssertionError(f"unscripted transmit-state answer {answer!r}")

    async def read() -> TxStateReading:
        try:
            value = await radio.get_ptt()
        except asyncio.TimeoutError:
            return TxStateReading(value=None, failure="timeout")
        except Exception:
            return TxStateReading(value=None, failure="read-error")
        # Never radio truth: upstream answers from its own cache (§3.7), so
        # the gate must refuse every hazard on this backend.
        return TxStateReading(
            value=value,
            attributed=None,
            source="hamlib_response",
            verified_readback=False,
        )

    from rigplane.runtime._poller_types import CommandQueue, PttOff, SelectVfo

    queue = CommandQueue()
    seen: list[Any] = []
    poller = radio.create_observation_poller(callback=seen.extend, command_queue=queue)

    async def close() -> None:
        await radio.disconnect()
        await server.stop()

    return TxConformanceHarness(
        name="rigctld-client",
        radio=radio,
        script=script,
        read_transmit_state=read,
        wire=lambda: list(server.commands_seen),
        is_read=lambda entry: entry.split(" ")[0] in ("t", r"\get_ptt"),
        # External rigctld ships no tuner; VFO select is its hazard family.
        hazard_method="set_vfo_slot",
        hazard_args=("B",),
        hazard=lambda: radio.set_vfo_slot("B"),
        key=lambda: radio.set_ptt(True),
        unkey=lambda: radio.set_ptt(False),
        close=close,
        verified_readback=False,
        queue_command=SelectVfo("B"),
        queue_unkey=PttOff(),
        drain=poller._drain_commands,
        extras={
            "queue": queue,
            "poller": poller,
            "server": server,
            "observations": seen,
        },
    )


async def build_harness(name: str) -> TxConformanceHarness:
    """Build one backend column. Explicit dispatch, never a registry lookup."""
    if name == "lan-icom":
        return await _build_lan_icom()
    if name in _ICOM_SERIAL_CLASSES:
        return await _build_icom_serial(name)
    if name == "yaesu-ftx1":
        return await _build_yaesu()
    if name == "rigctld-client":
        return await _build_rigctld_client()
    raise AssertionError(f"no harness for backend column {name!r}")
