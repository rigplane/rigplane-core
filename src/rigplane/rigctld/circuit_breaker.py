"""Circuit breaker for CI-V command resilience.

Prevents cascading failures when the radio stops responding.
The breaker transitions through three states:

- CLOSED  — normal operation; commands pass through.
- OPEN    — after ``failure_threshold`` consecutive timeouts; commands fail
            instantly (no queue, no wait).
- HALF_OPEN — after ``recovery_timeout`` seconds in OPEN; one probe command
              is allowed.  Success → CLOSED, failure → OPEN again.
"""

from __future__ import annotations

import enum
import logging
import time

__all__ = ["CircuitBreaker", "CircuitState"]

logger = logging.getLogger(__name__)


class CircuitState(enum.Enum):
    """Possible states of a :class:`CircuitBreaker`."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """State-machine circuit breaker wrapping CI-V command execution.

    Args:
        failure_threshold: Consecutive failures before the circuit opens
            (default ``3``).
        recovery_timeout: Seconds to wait in OPEN state before attempting a
            probe (default ``5.0``).
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 5.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Current state.

        Accessing this property may trigger an OPEN → HALF_OPEN transition
        if ``recovery_timeout`` has elapsed since the circuit opened.
        """
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            logger.info("CircuitBreaker: OPEN → HALF_OPEN (recovery window elapsed)")
        return self._state

    def allow_request(self) -> bool:
        """Return ``True`` if a command may proceed.

        Returns ``False`` only when the circuit is OPEN (fast-fail).
        Both CLOSED and HALF_OPEN states allow the call through.
        """
        return self.state != CircuitState.OPEN

    def record_success(self) -> None:
        """Record a successful command result.

        Resets the consecutive-failure counter and closes the circuit.
        """
        prev = self._state
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        if prev != CircuitState.CLOSED:
            logger.info("CircuitBreaker: %s → CLOSED", prev.value)

    def record_failure(self) -> None:
        """Record a failed command result.

        Increments the failure counter (CLOSED) or re-opens the circuit
        (HALF_OPEN).  Has no effect if already OPEN (commands should not
        reach here when the circuit is open).
        """
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("CircuitBreaker: HALF_OPEN → OPEN (probe failed)")
        elif self._state == CircuitState.CLOSED:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "CircuitBreaker: CLOSED → OPEN (%d consecutive failures)",
                    self._consecutive_failures,
                )
        # If already OPEN: allow_request() should have blocked the command.

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failures since the last success."""
        return self._consecutive_failures

    @property
    def failure_threshold(self) -> int:
        """Failure count required to open the circuit."""
        return self._failure_threshold

    @property
    def recovery_timeout(self) -> float:
        """Seconds between OPEN and HALF_OPEN transitions."""
        return self._recovery_timeout
