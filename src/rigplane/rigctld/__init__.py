"""Hamlib NET rigctld-compatible TCP server for icom-lan.

Provides a drop-in replacement for ``rigctld`` that bridges
rigctld's line-based TCP protocol to an ``IcomRadio`` instance.

Usage::

    async with IcomRadio("192.168.1.10") as radio:
        server = RigctldServer(radio, host="0.0.0.0", port=4532)
        await server.serve_forever()
"""

try:
    from .server import RigctldServer  # noqa: TID251

    __all__ = ["RigctldServer"]
except ImportError:
    __all__ = []
