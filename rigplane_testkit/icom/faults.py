"""Declarative fault-injection plans for the Icom LAN simulator.

A :class:`FaultPlan` attaches one :class:`FaultRule` to each
(port, direction) stream of ``MockIcomRadio``:

  * ports: ``control`` (auth/ping/token) and ``civ`` (CI-V data);
  * directions: ``to_client`` (radio replies) and ``from_client``
    (client requests, faulted before the simulator parses them).

Each rule can drop, duplicate, delay, reorder, corrupt or truncate the
packets on its stream. Each stream draws from its own ``random.Random``,
derived from :attr:`FaultPlan.seed` as ``f"{seed}:{port}:{direction}"``,
so a stream's decisions never depend on how the other streams' packets
interleave (including OS-dependent interleaves of the two UDP ports).

A rule can be bounded with ``max_packets`` ("lose the next 5 control
packets") and/or ``duration_s`` (measured from the first packet the rule
acts on); once a bound is exhausted the stream passes through untouched.

The plan only decides — it never performs I/O. :meth:`FaultPlan.decide`
returns the planned emissions for one packet as ``(delay_s, payload)``
pairs, and the simulator schedules them on its asyncio loop. This keeps
the decision layer unit-testable without sockets or an event loop.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Literal

Port = Literal["control", "civ"]
Direction = Literal["to_client", "from_client"]

# Same constant the simulator uses for its legacy RNG, so a plan built with
# defaults is exactly as deterministic as the pre-existing drop/reorder knobs.
_DEFAULT_SEED = 0xC0FFEE

# One planned emission: (delay in seconds before sending, payload to send).
Emission = tuple[float, bytes]


@dataclass
class FaultRule:
    """Faults applied to one (port, direction) packet stream.

    All probabilities are independent draws in [0, 1]. Effects combine in
    a fixed order per packet: drop → corrupt → truncate → delay →
    duplicate → reorder.

    Attributes:
        drop_probability: probability of dropping the packet.
        drop_every: drop every Nth matched packet (0 = off); combines with
            ``drop_probability`` by OR.
        duplicate_probability: probability of sending a second copy.
        duplicate_delay_s: extra delay of the second copy.
        delay_s: fixed delay added to every matched packet.
        jitter_s: uniform random extra delay in [0, jitter_s].
        reorder_window: buffer N emissions, shuffle, then flush (0/1 = off).
        reorder_flush_s: a held emission is released after at most this long
            even if the window never fills (the simulator drives this on
            idle); the buffer also flushes when a bound deactivates the rule.
        corrupt_probability: probability of bit-flipping bytes.
        corrupt_bytes: how many distinct byte positions to flip.
        truncate_probability: probability of cutting the packet short.
        truncate_to: byte length the packet is cut to.
        max_packets: stop matching after this many packets (None = unbounded).
        duration_s: stop matching this long after the first matched packet
            (None = unbounded); the clock starts on the first match.
    """

    drop_probability: float = 0.0
    drop_every: int = 0
    duplicate_probability: float = 0.0
    duplicate_delay_s: float = 0.01
    delay_s: float = 0.0
    jitter_s: float = 0.0
    reorder_window: int = 0
    reorder_flush_s: float = 0.05
    corrupt_probability: float = 0.0
    corrupt_bytes: int = 1
    truncate_probability: float = 0.0
    truncate_to: int = 8
    max_packets: int | None = None
    duration_s: float | None = None

    # Mutable runtime state — not part of construction or comparison.
    _matched: int = field(default=0, init=False, compare=False)
    _dropped: int = field(default=0, init=False, compare=False)
    _started_at: float | None = field(default=None, init=False, compare=False)
    _reorder_buf: list[Emission] = field(
        default_factory=list, init=False, compare=False
    )
    _reorder_since: float | None = field(default=None, init=False, compare=False)

    def is_noop(self) -> bool:
        """True when no fault knob is set — the fast path for the simulator."""
        return (
            self.drop_probability == 0.0
            and self.drop_every == 0
            and self.duplicate_probability == 0.0
            and self.delay_s == 0.0
            and self.jitter_s == 0.0
            and self.reorder_window <= 1
            and self.corrupt_probability == 0.0
            and self.truncate_probability == 0.0
        )

    @property
    def matched(self) -> int:
        """Packets this rule has acted on so far (bound accounting)."""
        return self._matched

    @property
    def dropped(self) -> int:
        """Packets this rule has dropped so far."""
        return self._dropped

    @property
    def held(self) -> int:
        """Emissions currently buffered by the reorder window."""
        return len(self._reorder_buf)

    def _active(self, now: float) -> bool:
        if self.max_packets is not None and self._matched >= self.max_packets:
            return False
        if (
            self.duration_s is not None
            and self._started_at is not None
            and now - self._started_at >= self.duration_s
        ):
            return False
        return True

    def decide(
        self, packet: bytes, rng: random.Random, now: float
    ) -> list[Emission] | None:
        """Plan the emissions for one packet.

        Returns a list of ``(delay_s, payload)`` pairs (empty list = the
        packet is dropped), or ``None`` when the packet was swallowed into
        the reorder buffer and nothing is emitted yet.
        """
        if not self._active(now):
            # A bound ran out: release anything the reorder window still
            # holds, then pass the packet through untouched.
            return self.flush(rng) + [(0.0, packet)]
        if self._started_at is None:
            self._started_at = now
        self._matched += 1

        # drop
        if (self.drop_every > 0 and self._matched % self.drop_every == 0) or (
            self.drop_probability > 0.0 and rng.random() < self.drop_probability
        ):
            self._dropped += 1
            return []

        payload = packet

        # corrupt: bit-flip distinct random byte positions.
        if (
            self.corrupt_probability > 0.0
            and len(payload) > 0
            and rng.random() < self.corrupt_probability
        ):
            buf = bytearray(payload)
            positions = rng.sample(range(len(buf)), min(self.corrupt_bytes, len(buf)))
            for pos in positions:
                buf[pos] ^= 0xFF
            payload = bytes(buf)

        # truncate
        if self.truncate_probability > 0.0 and rng.random() < self.truncate_probability:
            payload = payload[: max(0, self.truncate_to)]

        # delay
        delay = self.delay_s
        if self.jitter_s > 0.0:
            delay += rng.uniform(0.0, self.jitter_s)

        emissions: list[Emission] = [(delay, payload)]

        # duplicate
        if (
            self.duplicate_probability > 0.0
            and rng.random() < self.duplicate_probability
        ):
            emissions.append((delay + max(0.0, self.duplicate_delay_s), payload))

        # reorder: buffer, then flush shuffled once the window fills or the
        # flush deadline passes — a held packet is never held forever.
        if self.reorder_window > 1:
            if self._reorder_since is None:
                self._reorder_since = now
            self._reorder_buf.extend(emissions)
            if len(self._reorder_buf) >= self.reorder_window:
                return self.flush(rng)
            if now - self._reorder_since >= self.reorder_flush_s:
                return self.flush(rng)
            return None

        return emissions

    def flush(self, rng: random.Random) -> list[Emission]:
        """Release everything the reorder window holds (shuffled); [] if empty."""
        if not self._reorder_buf:
            return []
        batch = self._reorder_buf[:]
        self._reorder_buf.clear()
        self._reorder_since = None
        rng.shuffle(batch)
        return batch

    def flush_due_in(self, now: float) -> float | None:
        """Seconds until the reorder deadline, or None when nothing is held."""
        if self._reorder_since is None:
            return None
        return max(0.0, self._reorder_since + self.reorder_flush_s - now)


# All four (port, direction) streams — each gets its own derived RNG.
_STREAMS: tuple[tuple[Port, Direction], ...] = (
    ("control", "to_client"),
    ("control", "from_client"),
    ("civ", "to_client"),
    ("civ", "from_client"),
)


@dataclass
class FaultPlan:
    """One :class:`FaultRule` per (port, direction) stream of the simulator.

    ``seed`` feeds one derived RNG per stream —
    ``random.Random(f"{seed}:{port}:{direction}")`` — so a stream's
    decisions never depend on how the other streams' packets interleave,
    and the whole plan still replays identically for a fixed packet
    sequence per stream.
    """

    control_to_client: FaultRule = field(default_factory=FaultRule)
    control_from_client: FaultRule = field(default_factory=FaultRule)
    civ_to_client: FaultRule = field(default_factory=FaultRule)
    civ_from_client: FaultRule = field(default_factory=FaultRule)
    seed: int = _DEFAULT_SEED

    _rngs: dict[tuple[Port, Direction], random.Random] = field(
        init=False, compare=False
    )

    def __post_init__(self) -> None:
        self._rngs = {
            (port, direction): random.Random(f"{self.seed}:{port}:{direction}")
            for port, direction in _STREAMS
        }

    def rule(self, port: Port, direction: Direction) -> FaultRule:
        """Return the rule for one (port, direction) stream."""
        if port == "control":
            return (
                self.control_to_client
                if direction == "to_client"
                else self.control_from_client
            )
        return self.civ_to_client if direction == "to_client" else self.civ_from_client

    def is_noop(self, port: Port, direction: Direction) -> bool:
        """True when the stream's rule has no fault knobs set."""
        return self.rule(port, direction).is_noop()

    def decide(
        self,
        port: Port,
        direction: Direction,
        packet: bytes,
        *,
        now: float | None = None,
    ) -> list[Emission] | None:
        """Plan the emissions for one packet on one stream.

        Returns ``(delay_s, payload)`` pairs ([] = dropped) or ``None``
        (held in the reorder buffer). With all-default rules this always
        returns ``[(0.0, packet)]`` and consumes no randomness.
        """
        return self.rule(port, direction).decide(
            packet,
            self._rngs[(port, direction)],
            time.monotonic() if now is None else now,
        )

    def flush(self, port: Port, direction: Direction) -> list[Emission]:
        """Release whatever the stream's reorder buffer holds (shuffled).

        The simulator calls this when the reorder deadline fires, so a burst
        smaller than the window is still emitted on idle.
        """
        return self.rule(port, direction).flush(self._rngs[(port, direction)])

    def flush_due_in(
        self, port: Port, direction: Direction, *, now: float | None = None
    ) -> float | None:
        """Seconds until the stream's reorder deadline (None = nothing held)."""
        return self.rule(port, direction).flush_due_in(
            time.monotonic() if now is None else now
        )
