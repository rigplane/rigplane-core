"""Route handlers for WebSocket channels and HTTP endpoints.

Each handler manages the lifecycle of one client connection on one channel:
- control (/api/v1/ws): JSON commands, events, state
- scope (/api/v1/scope): binary scope frames with backpressure
- audio (/api/v1/audio): placeholder for future audio streaming
"""

from .audio import AudioBroadcaster, AudioHandler  # noqa: TID251
from .control import ControlHandler  # noqa: TID251
from .diagnostics import DiagnosticsHandler  # noqa: TID251
from .scope import HIGH_WATERMARK, ScopeHandler  # noqa: TID251

__all__ = [
    "HIGH_WATERMARK",
    "ControlHandler",
    "DiagnosticsHandler",
    "ScopeHandler",
    "AudioBroadcaster",
    "AudioHandler",
]
