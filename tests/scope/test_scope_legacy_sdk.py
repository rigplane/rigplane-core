"""Deprecated scope SDK adapters share the callback publication path."""

import asyncio

import pytest

from rigplane.radio import IcomRadio
from rigplane.scope import ScopeFrame


@pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
async def test_deprecated_streams_preserve_primary_callback_and_cancel(
    model, virtual_clock
):
    radio = IcomRadio("127.0.0.1", model=model)
    radio._connected = True
    assert not hasattr(radio, "_scope_frame_queue")
    seen = []
    radio.on_scope_data(seen.append)
    streams = [radio.scope_stream(), radio.scope_stream()]
    with pytest.warns(DeprecationWarning, match="on_scope_data"):
        pending = [asyncio.create_task(anext(stream)) for stream in streams]
        await virtual_clock.elapse(0)
    frame = ScopeFrame(0, 0, 14_000_000, 14_100_000, b"\x01", False)
    radio._civ_runtime._publish_scope_frame(frame)
    assert await asyncio.gather(*pending) == [frame, frame]
    assert seen == [frame]
    await streams[0].aclose()
    changed = []
    radio.on_scope_data(changed.append)
    waiting = asyncio.create_task(anext(streams[1]))
    await virtual_clock.elapse(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    radio._civ_runtime._publish_scope_frame(frame)
    assert changed == [frame]
    assert seen == [frame]
    assert not radio._scope_stream_callbacks
    radio._connected = False


def test_duplicate_output_builders_removed_from_public_exports():
    import rigplane
    import rigplane.commands
    import rigplane.commands.scope

    for module in (rigplane, rigplane.commands, rigplane.commands.scope):
        for suffix in ("on", "off"):
            assert not hasattr(module, f"scope_data_output_{suffix}")
