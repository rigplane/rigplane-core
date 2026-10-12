"""Shared helpers for tests that exercise the bounded PortAudio pool."""

from __future__ import annotations

import asyncio

from rigplane.audio.usb_driver import bounded_portaudio_pool


async def drain_pool() -> None:
    """Wait until the shared pool has settled every tracked operation.

    Releasing a blocking gate and then sleeping a fixed 0.05 s races the
    loop-side settle of the pool future: the worker may have finished while
    the done-callbacks that uncount ``inflight`` and drop the retained
    stream have not run yet, leaking state bound to this test's closing
    event loop into the module singleton where the next test inherits it.
    ``_BoundedPortAudioPool.submit_tracked`` registers the ``inflight``
    uncount on each submission's future before the caller adds its own
    bookkeeping callback (``settled`` in ``open_stream_bounded`` and
    ``_submit_stop``, which drop the retained stream), and asyncio runs a
    future's done-callbacks in registration order, so once ``inflight``
    reads zero here the pool's bookkeeping for every settled operation has
    run too.
    """
    async with asyncio.timeout(5):
        while bounded_portaudio_pool.inflight:
            await asyncio.sleep(0.005)
