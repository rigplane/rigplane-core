"""Fixture-scoped pool phase observer for MOR-3777 (instrument-only).

Bounded in-memory chronology for the fake-fixture USB-audio ownership
tests: per submission ``submit`` -> worker ``start`` -> ``body_end`` ->
``settled`` markers, ``after_drain`` pool-state snapshots, and the
production INFO caller/native states captured at emission time by a
fixture-scoped logging handler.

Contract (MOR-3777 parent authorization): numeric/enumerated metadata
only — no callable reprs, exception texts, raw parameters or test
names; INFO states pass an exact message template and whitelist
(caller in {cancelled, timed_out}, native in {pending, completed,
failed}) and dynamic log args are never stringified; the INFO line
carries no pool identity, so those states stay a separate
UNCORRELATED list never joined to ring submissions; intake never
raises and never does IO, and ``flush()`` prints one bounded block
through captured stdout after the fixture's executor shutdown.
Wrapping adds one Python frame and one done-callback per submitted
call plus the marks; the future object is returned unchanged and a
failed submission re-raises the original exception unmodified. No
ordering, timeout or cancellation property is claimed beyond the
qualification test; observation overhead is nonzero and unmeasured
here, and surviving audio thread names alone are not ownership
evidence. The seam is the instance-level ``submit_tracked`` swap
already used by ``test_usb_audio_portaudio_off_loop_mor2892.py``,
restored by ``monkeypatch``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

# Captured at import: observer timestamps stay on the real monotonic
# clock even if a test later replaces time.monotonic.
_monotonic = time.monotonic

RING_MAX = 128
OP_STATES_MAX = 32
FLUSH_MARKER_MAX = 16
_OP_STATE_MSG = "usb-audio: PortAudio operation %d caller=%s native=%s"
_CALLER_STATES = frozenset({"cancelled", "timed_out"})
_NATIVE_STATES = frozenset({"pending", "completed", "failed"})

_current: "PoolPhaseObserver | None" = None


def set_current(observer: "PoolPhaseObserver | None") -> None:
    global _current
    _current = observer


def current_for(pool: Any) -> "PoolPhaseObserver | None":
    observer = _current
    if observer is not None and observer.pool is pool:
        return observer
    return None


class _OpStateHandler(logging.Handler):
    """Fixture-scoped capture at emission time (phase-correct, no IO)."""

    def __init__(self, observer: "PoolPhaseObserver") -> None:
        super().__init__(level=logging.INFO)
        self._observer = observer

    def emit(self, record: logging.LogRecord) -> None:
        self._observer._capture_op_state(record)


class PoolPhaseObserver:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._lock = threading.Lock()
        self._events: "deque[dict[str, object]]" = deque(maxlen=RING_MAX)
        self._op_states: "deque[tuple[int, str, str, int]]" = deque(
            maxlen=OP_STATES_MAX
        )
        self._t0 = _monotonic()
        self._seq = 0
        self._total = 0
        self._subs = 0
        self._logger: logging.Logger | None = None
        self._handler: _OpStateHandler | None = None

    @property
    def pool(self) -> Any:
        return self._pool

    def _ms(self, now: float | None = None) -> int:
        moment = _monotonic() if now is None else now
        return int(round((moment - self._t0) * 1000))

    def _mark(self, event: str, **fields: object) -> None:
        try:
            with self._lock:
                self._seq += 1
                self._total += 1
                self._events.append(
                    {"seq": self._seq, "ev": event, "t_ms": self._ms(), **fields}
                )
        except Exception:  # noqa: BLE001 — diagnostics must never break a run
            pass

    def attach(self, monkeypatch: Any, logger: logging.Logger) -> None:
        """Wrap instance-level ``submit_tracked`` and capture INFO states."""
        self._logger = logger
        self._handler = _OpStateHandler(self)
        logger.addHandler(self._handler)
        if not logger.isEnabledFor(logging.INFO):
            monkeypatch.setattr(logger, "level", logging.INFO)
        submit = self._pool.submit_tracked

        def observed_submit(fn: Callable[[], Any]) -> Any:
            inflight_before = self._pool.inflight

            def observed_fn() -> Any:
                t_start = _monotonic()
                self._mark(
                    "start",
                    sub=sub,
                    tid=threading.get_ident(),
                    queue_wait_ms=self._ms(t_start) - self._ms(t_submit),
                )
                try:
                    return fn()
                finally:
                    self._mark(
                        "body_end", sub=sub, body_ms=self._ms() - self._ms(t_start)
                    )

            def settled(done: Any) -> None:
                try:
                    if done.cancelled():
                        outcome = "cancelled"
                    else:
                        outcome = "ok" if done.exception() is None else "err"
                except Exception:  # noqa: BLE001
                    outcome = "unknown"
                self._mark("settled", sub=sub, outcome=outcome)

            # Counter + submit marker advance atomically BEFORE the real
            # submission, so a worker can never mark its own ``start``
            # ahead of the ``submit`` marker: chronology is real.
            with self._lock:
                self._subs += 1
                sub = self._subs
                self._seq += 1
                self._total += 1
                self._events.append(
                    {
                        "seq": self._seq,
                        "ev": "submit",
                        "t_ms": self._ms(),
                        "sub": sub,
                        "inflight_before": inflight_before,
                    }
                )
            t_submit = _monotonic()
            try:
                future = submit(observed_fn)
            except BaseException:
                # No future exists; the original exception propagates
                # unchanged and no settled mark is invented.
                self._mark("submit_failed", sub=sub)
                raise
            future.add_done_callback(settled)
            return future

        monkeypatch.setattr(self._pool, "submit_tracked", observed_submit)

    def close(self) -> None:
        """Always remove the fixture handler; idempotent, never raises."""
        handler, self._handler = self._handler, None
        logger, self._logger = self._logger, None
        try:
            if handler is not None and logger is not None:
                logger.removeHandler(handler)
        except Exception:  # noqa: BLE001
            pass

    def _capture_op_state(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno != logging.INFO or record.msg != _OP_STATE_MSG:
                return
            args = record.args
            if not isinstance(args, tuple) or len(args) != 3:
                return
            op, caller, native = args
            if (
                isinstance(op, int)
                and not isinstance(op, bool)
                and caller in _CALLER_STATES
                and native in _NATIVE_STATES
            ):
                with self._lock:
                    self._op_states.append((op, caller, native, self._ms()))
        except Exception:  # noqa: BLE001
            pass

    def mark_pool_state(self, label: str) -> None:
        """Snapshot selected counters (after drain / before replace)."""
        try:
            self._mark(
                label,
                inflight=self._pool.inflight,
                stops=len(self._pool._stops),
                opens=len(self._pool._opens),
                cleanups=len(self._pool._cleanup_streams),
            )
        except Exception:  # noqa: BLE001
            pass

    def events(self) -> "list[dict[str, object]]":
        with self._lock:
            return list(self._events)

    def op_states(self) -> "list[tuple[int, str, str, int]]":
        with self._lock:
            return list(self._op_states)

    def latest_sub(self) -> int:
        with self._lock:
            return self._subs

    def has(self, sub: int, event: str) -> bool:
        return any(
            m["ev"] == event and m.get("sub") == sub for m in self.events()
        )

    def order(self, sub: int, event: str) -> int:
        for m in self.events():
            if m["ev"] == event and m.get("sub") == sub:
                return int(m["seq"])
        return -1

    def outcome(self, sub: int) -> str:
        for m in self.events():
            if (
                m["ev"] == "settled"
                and m.get("sub") == sub
                and isinstance(m.get("outcome"), str)
            ):
                return str(m["outcome"])
        return "missing"

    def caller_states(self) -> "set[str]":
        return {state[1] for state in self.op_states()}

    def report(self) -> "list[str]":
        with self._lock:
            events, states = list(self._events), list(self._op_states)
            total, subs = self._total, self._subs
        lines = [
            f"mor-3777-obs ring={len(events)} dropped={max(0, total - len(events))}"
            f" total={total} subs={subs}"
        ]
        lines += [
            f"mor-3777-obs op_state_uncorrelated op={op} caller={caller}"
            f" native={native} t={t_ms}"
            for op, caller, native, t_ms in states
        ]
        lines += [
            "mor-3777-obs " + " ".join(f"{k}={v}" for k, v in m.items())
            for m in events[-FLUSH_MARKER_MAX:]
        ]
        return lines

    def flush(self) -> None:
        """Teardown-time only: bounded captured-stdout dump (never raises)."""
        try:
            for line in self.report():
                print(line)
        except Exception:  # noqa: BLE001
            pass
