"""Queue pressure threshold for transport and poller coordination.

Neutral module shared by both transport and radio_poller to avoid circular dependencies.
"""

__all__ = ["PRESSURE_THRESHOLD"]

PRESSURE_THRESHOLD = 0.7
