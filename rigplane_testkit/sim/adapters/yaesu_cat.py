"""Yaesu CAT serial adapter (MOR-3602): the FTX-1 on an in-memory duplex.

:class:`YaesuCatSerialFake` mounts behind the shared
:class:`~rigplane_testkit.serial.endpoints.TcpEndpoint` /
:class:`~rigplane_testkit.serial.endpoints.PtyEndpoint` and serve's
``_VanishController`` through the same duck-typed surface those use on
``CivSerialFake``: ``open_serial_connection()``, ``silence()``,
``usb_vanish()``/``usb_reappear()`` and ``open_count``.

Wire rules (this module's own deliberately dumb splitter; no production
parser runs here): host bytes split on ``;`` and a partial line is held
until its terminator. A buffered partial run longer than 64 bytes without
a ``;`` is answered with ``?;`` once, then discarded up to and including the next
``;``; bytes after that ``;`` start a fresh line. Each complete line goes
to ``YaesuCatModel.handle``; a returned reply line is delivered to that
connection as ASCII bytes, ``None`` delivers nothing. Echo is OFF. While
silenced, newly accepted lines are consumed without answering or applying;
already scheduled replies remain scheduled. While
vanished, opens raise ``OSError(ENODEV)`` and live connections fail like
the CI-V fake's.

Replies use the profile's reply_latency_ms, in request order per connection;
zero or absent latency delivers synchronously.

The ``frequency_hz`` setter is the control-event path, not the wire: it
applies ``SetFrequency`` directly on the engine, bypassing the wire's
MEMORY-mode FA refusal on purpose (it is a test control), and raises
``ValueError`` when the engine's outcome is not ``Ok``.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import math
import time
import weakref
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.sim.engine import ManualClock, Ok, SetFrequency
from rigplane_testkit.sim.model.yaesu_cat import (
    YaesuCatModel,
    build_ftx1_engine,
    seed_from_recording,
)
from rigplane_testkit.sim.profiles import SimProfile, load_sim_profile
from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

if TYPE_CHECKING:
    from rigplane.profiles.rig_loader import RigConfig

__all__ = [
    "YaesuCatSerialFake",
    "fidelity_for_banner",
    "recorded_divergences",
    "unledgered_divergences",
]

logger = logging.getLogger("rigplane_testkit.sim.adapters.yaesu_cat")

# Errno a disappeared USB-serial device raises on the next read; mirrors
# civ_serial.py (serial_open.py maps it to serial_not_found).
_VANISH_ERRNO = errno.ENODEV
_MAX_RUN = 64  # bytes without a ';'; a longer run answers '?;' once, then drops
_REFUSAL_BYTES = b"?;"

_PROFILE_REL = Path("rigplane_testkit/sim/profiles/ftx1.toml")
_RIG_REL = Path("rigs/ftx1.toml")


def _recorded_table(root: Path, profile: SimProfile) -> RecordedCatAnswerTable:
    """Load the first [answers] recording; require at least one paired reply."""
    if not profile.answers.recorded:
        raise ValueError(f"{profile.sim.id}: an [answers] recorded entry is required")
    file, _transport = profile.answers.recorded[0]
    path = root / file
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    table = RecordedCatAnswerTable.from_cat_pairs(rows)
    if not any(
        (answer := table.lookup(request)) is not None and answer.reply is not None
        for request in table.requests()
    ):
        raise ValueError(f"{path}: no request/reply evidence")
    return table


def _build_seeded_model(
    profile: SimProfile,
    rig: RigConfig,
    recorded: RecordedCatAnswerTable,
    *,
    clock,
    seed: int,
    on_not_modelled: Callable[[str], None] | None = None,
) -> YaesuCatModel:
    """A fresh engine + model seeded from the recording's own replies."""
    if not rig.expected_identity_ids:
        raise ValueError(f"{rig.model!r}: [identity].expected_ids is empty")
    engine = build_ftx1_engine(profile, rig, clock=clock, seed=seed)
    model = YaesuCatModel(
        engine,
        identity=rig.expected_identity_ids[0],
        recorded=recorded,
        on_not_modelled=on_not_modelled,
    )
    seed_from_recording(model, recorded)
    return model


def recorded_divergences(
    model: YaesuCatModel, table: RecordedCatAnswerTable
) -> dict[str, dict[str, str | None]]:
    """Replay every recorded request against *model*; return the mismatches
    as ``{request: {"recorded": ..., "model": ...}}``.

    Raise ``ValueError`` if no recorded requests can be compared.
    """
    live: dict[str, dict[str, str | None]] = {}
    compared = 0
    for request in table.requests():
        recorded = table.lookup(request)
        if recorded is None:
            continue
        got = model.handle(request)
        compared += 1
        if got != recorded.reply:
            live[request] = {"recorded": recorded.reply, "model": got}
    if not compared:
        raise ValueError("recorded answer table has no request/reply evidence")
    return live


def unledgered_divergences(
    model_factory: Callable[[RecordedCatAnswerTable], YaesuCatModel],
    table: RecordedCatAnswerTable,
    ledger_path: Path,
) -> dict[str, dict[str, str | None] | None]:
    """Live divergences not exactly ledgered, plus stale ledger keys as ``None``.

    *model_factory* takes the recorded table and returns a fresh model
    seeded from it, so one replay can never bleed state into the next.
    Both the recorded and model replies must match the entry in *ledger_path*.
    Every ledger key must still diverge in the fresh replay.
    """
    live = recorded_divergences(model_factory(table), table)
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    mismatches: dict[str, dict[str, str | None] | None] = {
        request: diff
        for request, diff in live.items()
        if request not in ledger
        or any(
            key not in ledger[request] or diff[key] != ledger[request][key]
            for key in ("recorded", "model")
        )
    }
    mismatches.update(dict.fromkeys(ledger.keys() - live.keys()))
    return mismatches


def _fresh_seeded_model(
    fake: YaesuCatSerialFake, table: RecordedCatAnswerTable
) -> YaesuCatModel:
    """The conformance replica: a fresh ManualClock engine seeded from *table*."""
    return _build_seeded_model(
        fake._profile, fake._rig, table, clock=ManualClock(), seed=fake._seed
    )


def fidelity_for_banner(fake: YaesuCatSerialFake, ledger_path: Path) -> str:
    """Cap fidelity at B with declared oracle evidence, otherwise C,
    on an unledgered divergence, stale ledger key, or empty table (MOR-3598).
    Never exceed the profile's configured tier.
    """
    tier = fake._profile.sim.fidelity
    fallback = tier
    if tier == "A":
        golden = (fake._root / "tests" / "golden" / "wire_oracle").resolve()
        has_oracle = any(
            (fake._root / entry).resolve().is_relative_to(golden)
            for entry in fake._profile.sim.evidence
        )
        fallback = "B" if has_oracle else "C"
    if not fake._recorded.requests():
        return fallback
    divergences = unledgered_divergences(
        lambda table: _fresh_seeded_model(fake, table), fake._recorded, ledger_path
    )
    return tier if not divergences else fallback


def _finite_delay(value: float, field: str, *, clamp_negative: bool = False) -> float:
    message = f"{field}: must be a finite number >= 0, got {value!r}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(message)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (value < 0 and not clamp_negative):
        raise ValueError(message)
    return max(0.0, value)


class YaesuCatSerialFake:
    """A stateful Yaesu CAT radio on the far end of an in-memory serial duplex.

    Owns a :class:`YaesuCatModel` on a :class:`SimEngine`; state changes
    only through the wire or the ``frequency_hz`` control setter. Every
    fault is scripted through the methods below. Build with
    :meth:`from_profile`.
    """

    def __init__(
        self,
        model: YaesuCatModel,
        *,
        profile: SimProfile,
        rig: RigConfig,
        recorded: RecordedCatAnswerTable,
        seed: int = 0,
        root: Path | None = None,
    ) -> None:
        latency = profile.timing.reply_latency_ms
        self._reply_latency = (
            _finite_delay(
                0.0 if latency is None else latency, "profile timing.reply_latency_ms"
            )
            / 1000.0
        )
        self._model = model
        self._profile = profile
        self._rig = rig
        self._recorded = recorded
        self._seed = seed
        self._root = (Path.cwd() if root is None else Path(root)).resolve()
        self._silent_until = 0.0
        self._vanished = False
        self._open_count = 0
        self._connections: list[_CatConnection] = []

    @classmethod
    def from_profile(
        cls,
        root: Path,
        profile_path: Path | None = None,
        *,
        seed: int = 0,
        on_not_modelled: Callable[[str], None] | None = None,
    ) -> YaesuCatSerialFake:
        """Build the FTX-1 fake from the sim profile under *root*.

        Loads ``rigplane_testkit/sim/profiles/ftx1.toml`` (or
        *profile_path*) with ``load_sim_profile``, the capability profile
        from ``rigs/ftx1.toml`` (identity from its
        ``[identity].expected_ids``, never hardcoded) and the recorded
        answer table named in ``[answers] recorded``, and seeds the model
        from the recording. The engine clock is ``time.monotonic``;
        *on_not_modelled* is forwarded to the model as best-effort
        coverage-gap instrumentation.
        """
        root = Path(root)
        path = Path(profile_path) if profile_path is not None else root / _PROFILE_REL
        profile = load_sim_profile(path, root=root)
        rig = load_rig(root / _RIG_REL)
        recorded = _recorded_table(root, profile)
        model = _build_seeded_model(
            profile,
            rig,
            recorded,
            clock=time.monotonic,
            seed=seed,
            on_not_modelled=on_not_modelled,
        )
        return cls(
            model, profile=profile, rig=rig, recorded=recorded, seed=seed, root=root
        )

    # ------------------------------------------------------------------
    # Identity / state surface
    # ------------------------------------------------------------------

    @property
    def model(self) -> YaesuCatModel:
        return self._model

    @property
    def frequency_hz(self) -> int:
        return self._model.engine.state.receivers[0].frequency_hz

    @frequency_hz.setter
    def frequency_hz(self, value: int) -> None:
        # Control-event path: applies on the engine directly, bypassing the
        # wire's MEMORY-mode FA refusal on purpose (this is a test control,
        # not a CAT SET). A non-Ok outcome (e.g. out of range) raises.
        outcome = self._model.engine.apply(SetFrequency(0, int(value)))
        if not isinstance(outcome, Ok):
            raise ValueError(f"engine refused frequency_hz={value!r}: {outcome}")

    @property
    def mode(self) -> str:
        """The CAT mode NAME of the MAIN receiver (``"FM"``, ...)."""
        return self._model.engine.state.receivers[0].mode

    @property
    def ptt(self) -> bool:
        return self._model.engine.state.ptt

    @property
    def s_meter(self) -> int:
        """The MAIN receiver's raw meter (``s_meter_main``). The AGC-OFF
        pinning to 0 is a wire-level rule of the model, not of this read."""
        value = self._model.engine.state.meters.get("s_meter_main", 0)
        return value if type(value) is int else 0

    @property
    def open_count(self) -> int:
        """How many times the port has been (re)opened."""
        return self._open_count

    # ------------------------------------------------------------------
    # Scriptable faults
    # ------------------------------------------------------------------

    def silence(self, for_seconds: float) -> None:
        """Suppress new wire lines, preserving already scheduled replies."""
        duration = _finite_delay(
            for_seconds, "silence for_seconds", clamp_negative=True
        )
        self._silent_until = time.monotonic() + duration

    def usb_vanish(self, at: float = 0.0) -> None:
        """Make the device disappear *at* seconds from now (needs a running loop)."""
        delay = _finite_delay(at, "usb_vanish at", clamp_negative=True)
        asyncio.get_running_loop().call_later(delay, self._vanish_now)

    def usb_reappear(self, after: float = 0.0) -> None:
        """Make the device openable again *after* seconds from now."""
        delay = _finite_delay(after, "usb_reappear after", clamp_negative=True)
        asyncio.get_running_loop().call_later(delay, self._reappear_now)

    def _vanish_now(self) -> None:
        self._vanished = True
        error = OSError(_VANISH_ERRNO, "Yaesu CAT fake: USB device disappeared")
        for connection in tuple(self._connections):
            connection.fail(error)

    def _reappear_now(self) -> None:
        self._vanished = False

    # ------------------------------------------------------------------
    # Mounting seam
    # ------------------------------------------------------------------

    async def open_serial_connection(
        self,
    ) -> tuple[asyncio.StreamReader, _FakeCatWriter]:
        """Zero-argument opener for the TCP/PTY endpoints."""
        if self._vanished:
            raise OSError(_VANISH_ERRNO, "Yaesu CAT fake: USB device is gone")
        self._open_count += 1
        connection = _CatConnection(self, self._open_count)
        self._connections.append(connection)
        return connection.reader, connection.writer

    # ------------------------------------------------------------------
    # Wire handling (called by _FakeCatWriter only)
    # ------------------------------------------------------------------

    def _muted(self) -> bool:
        return self._vanished or time.monotonic() < self._silent_until

    def _on_wire_line(self, connection: _CatConnection, line: bytes) -> None:
        if self._muted():
            return  # a mute radio consumes the line and applies nothing
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            connection.deliver(_REFUSAL_BYTES)
            return
        try:
            reply = self._model.handle(text)
            data = None if reply is None else reply.encode("ascii")
        except Exception:
            # A model failure must not kill the connection task silently.
            logger.exception("Yaesu CAT model failed on %r; answering '?;'", text)
            data = _REFUSAL_BYTES
        if data is not None:
            connection.deliver(data)

    def _on_oversize_run(self, connection: _CatConnection) -> None:
        if self._muted():
            return
        connection.deliver(_REFUSAL_BYTES)


class _CatConnection:
    """One open session of the fake: an asyncio reader/writer pair."""

    def __init__(self, fake: YaesuCatSerialFake, ordinal: int) -> None:
        self._fake = weakref.ref(fake, self._fake_gone)
        self._loop = asyncio.get_running_loop()
        self._latency = fake._reply_latency
        self._pending: deque[tuple[float, bytes]] = deque()
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False
        self.ordinal = ordinal
        self.reader = asyncio.StreamReader()
        self.writer = _FakeCatWriter(fake, self)
        self.error: OSError | None = None

    def fail(self, error: OSError) -> None:
        """Fail an open session; leave an already retired session unchanged."""
        if not self._retire():
            return
        self.error = error
        self.reader.set_exception(error)

    def _fake_gone(self, _reference: weakref.ReferenceType[YaesuCatSerialFake]) -> None:
        self.close()

    def _retire(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._pending.clear()
        fake = self._fake()
        if fake is not None:
            fake._connections.remove(self)
        return True

    def close(self) -> None:
        if not self._retire():
            return
        if self.error is None:
            self.reader.feed_eof()

    def deliver(self, data: bytes) -> None:
        if self._closed:
            return
        if self._latency == 0:
            self.reader.feed_data(data)
            return
        self._pending.append((self._loop.time() + self._latency, data))
        if self._timer is None:
            self._timer = self._loop.call_at(self._pending[0][0], self._deliver_due)

    def _deliver_due(self) -> None:
        self._timer = None
        if self._closed:
            return
        while self._pending and self._pending[0][0] <= self._loop.time():
            _, data = self._pending.popleft()
            self.reader.feed_data(data)
        if self._pending:
            self._timer = self._loop.call_at(self._pending[0][0], self._deliver_due)


class _FakeCatWriter:
    """The write half of a fake CAT connection.

    De-frames host bytes with a deliberately dumb splitter (ringfence rule:
    no production parser), so a framing bug in the code under test stays
    visible here.
    """

    def __init__(self, fake: YaesuCatSerialFake, connection: _CatConnection) -> None:
        self._fake = weakref.ref(fake)
        self._connection = connection
        self._buf = bytearray()
        self._discard = False  # inside an oversize run: drop through the next ';'
        self.closed = False

    def write(self, data: bytes) -> None:
        if self._connection.error is not None:
            raise self._connection.error
        if self.closed:
            raise RuntimeError("write to a closed fake CAT writer")
        fake = self._fake()
        if fake is None:
            raise RuntimeError("write to a retired fake CAT connection")
        self._buf += bytes(data)
        while True:
            if self._discard:
                end = self._buf.find(b";")
                if end < 0:
                    self._buf.clear()
                    return
                del self._buf[: end + 1]
                self._discard = False
                continue
            end = self._buf.find(b";")
            if end < 0:
                if len(self._buf) > _MAX_RUN:
                    self._buf.clear()
                    self._discard = True
                    fake._on_oversize_run(self._connection)
                return
            line = bytes(self._buf[: end + 1])
            del self._buf[: end + 1]
            fake._on_wire_line(self._connection, line)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self._connection.close()

    async def wait_closed(self) -> None:
        pass
