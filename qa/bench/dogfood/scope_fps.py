"""Measure explicit scope subscriptions against Core or its loopback testkit simulator.

Run ``uv run python qa/bench/dogfood/scope_fps.py --radio simulator --transport sim --both``.
Sequence gaps are expected under rate limiting; repeats and backwards movement fail.
PASS requires usable pixel frames at least 80% of the granted frame rate.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

# The simulator is a repository testkit, not part of the installed SDK wheel.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from rigplane.scope.models import FollowView, ScopeWireFrame, Subscription
from rigplane.web.protocol import (  # noqa: TID251
    decode_scope_frame_v2,
    decode_scope_granted,
    encode_scope_subscription,
)

MIN_PIXEL_DELIVERY_RATIO = 0.80


def sequence_step(previous: int, current: int) -> int:
    delta = (current - previous) & 0xFFFFFFFF
    if not 0 < delta < 0x80000000:
        raise ValueError("repeated or backwards scope sequence")
    return delta


def validate_frame(
    frame: ScopeWireFrame, source_id: str, receiver: int | None, width: int
) -> None:
    if (frame.source_id, frame.receiver, frame.width) != (source_id, receiver, width):
        raise ValueError("frame does not match granted subscription")


def summarize(
    arrivals: list[float], byte_count: int, duration: float
) -> dict[str, Any]:
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    return {
        "frames": len(arrivals),
        "fps": len(arrivals) / duration,
        "bytes_per_second": byte_count / duration,
        "median_gap_ms": statistics.median(gaps) * 1000 if gaps else None,
        "longest_gap_ms": max(gaps) * 1000 if gaps else None,
    }


@asynccontextmanager
async def simulator() -> AsyncIterator[tuple[str, float]]:
    from rigplane.commands import parse_civ_frame
    from rigplane.runtime.radio import IcomRadio
    from rigplane.scope import ScopeAssembler
    from rigplane.web.server import WebConfig, WebServer  # noqa: TID251
    from rigplane_testkit.icom import simulator as testkit

    previous_gaps = testkit._SCOPE_GAP_S
    testkit._SCOPE_GAP_S = {speed: 1 / 30 for speed in previous_gaps}
    fake = testkit.MockIcomRadio(scope_stream=True, enable_audio=True)
    receiver, templates = next(iter(fake._scope_templates.items()))
    wave = parse_civ_frame(templates[0])
    native = ScopeAssembler().feed(wave.data[1:], receiver)
    if native is None:
        raise ValueError("simulator fixture has no complete scope frame")
    fake.set_frequency((native.start_freq_hz + native.end_freq_hz) // 2)
    radio = None
    server = None
    try:
        await fake.start()
        radio = IcomRadio(
            host="127.0.0.1",
            port=fake.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        await radio.connect()
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        assert server._server is not None
        port = server._server.sockets[0].getsockname()[1]
        print(
            json.dumps(
                {
                    "event": "ready",
                    "pid": os.getpid(),
                    "port": port,
                    "testkit_scope_fps": 30,
                }
            ),
            flush=True,
        )
        yield f"http://127.0.0.1:{port}", native.end_freq_hz - native.start_freq_hz
    finally:
        try:
            if server is not None:
                await server.stop()
        finally:
            try:
                if radio is not None:
                    await radio.disconnect()
            finally:
                await fake.stop()
                testkit._SCOPE_GAP_S = previous_gaps


async def collect(
    session: aiohttp.ClientSession,
    base_url: str,
    protocols: tuple[str, ...],
    request: Subscription,
    duration: float,
) -> dict[str, Any]:
    async with session.ws_connect(
        base_url + "/api/v1/scope", protocols=protocols
    ) as ws:
        await ws.send_str(encode_scope_subscription(request))
        message = await ws.receive(timeout=15)
        if message.type is not aiohttp.WSMsgType.TEXT:
            raise ValueError("scope did not grant subscription")
        try:
            granted = decode_scope_granted(message.data)
        except ValueError as error:
            raise ValueError(f"scope subscription refused: {message.data}") from error
        # The hub grants at most the source native width: a request wider
        # than native is granted at native resolution, never upsampled.
        if (granted.source_id, granted.receiver) != (
            request.source_id,
            request.receiver,
        ) or not 1 <= granted.width <= request.width:
            raise ValueError("unexpected granted subscription")
        arrivals: list[float] = []
        byte_count = 0
        previous: int | None = None
        native_steps = 0
        pixel_frames = null_frames = out_of_range_frames = 0
        started = time.monotonic()
        while (remaining := duration - (time.monotonic() - started)) > 0:
            try:
                message = await ws.receive(timeout=remaining)
            except TimeoutError:
                break
            if message.type is aiohttp.WSMsgType.BINARY:
                frame = decode_scope_frame_v2(message.data)
                validate_frame(
                    frame, granted.source_id, granted.receiver, granted.width
                )
                if previous is not None:
                    native_steps += sequence_step(previous, frame.seq)
                previous = frame.seq
                pixel_frames += frame.pixels is not None and not frame.out_of_range
                null_frames += frame.pixels is None
                out_of_range_frames += frame.out_of_range
                arrivals.append(time.monotonic() - started)
                byte_count += len(message.data)
            elif message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise ValueError("scope socket closed before measurement ended")
        if not arrivals or not pixel_frames:
            raise ValueError("no in-range pixel frames received")
        if granted.max_fps <= 0:
            raise ValueError("a paused grant cannot measure delivery")
        delivery_ratio = pixel_frames / (duration * granted.max_fps)
        if delivery_ratio < MIN_PIXEL_DELIVERY_RATIO:
            raise ValueError(
                f"delivery below 80% of granted fps: {delivery_ratio:.1%} "
                f"({pixel_frames} usable frames in {duration:g}s at {granted.max_fps:g} fps)"
            )
        return {
            "source_id": granted.source_id,
            "width": granted.width,
            "max_fps": granted.max_fps,
            "native_sequence_steps": native_steps,
            "pixel_frames": pixel_frames,
            "null_frames": null_frames,
            "out_of_range_frames": out_of_range_frames,
            "pixel_delivery_ratio": delivery_ratio,
            **summarize(arrivals, byte_count, duration),
        }


async def measure(base_url: str, args: argparse.Namespace) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            base_url + "/api/v1/controller", json={"kind": "browser"}
        ) as response:
            response.raise_for_status()
            key = (await response.json())["controller_key"]
        protocols = (f"rigplane-controller-v1.{key}",)
        try:
            async with session.ws_connect(
                base_url + "/api/v1/ws", protocols=protocols
            ) as control:
                await control.send_json({"type": "subscribe", "streams": ["state"]})
                updates = 0

                async def watch_control() -> None:
                    nonlocal updates
                    async for message in control:
                        if message.type is aiohttp.WSMsgType.TEXT:
                            if json.loads(message.data).get("type") == "state_update":
                                updates += 1

                async def heartbeat() -> None:
                    while True:
                        await control.send_json({"type": "controller_heartbeat"})
                        await asyncio.sleep(2)

                watcher = asyncio.create_task(watch_control())
                pulse = asyncio.create_task(heartbeat())
                try:
                    profiles = (
                        [(1024, 10), (2800, 20)]
                        if args.both
                        else [(args.width, args.max_fps)]
                    )
                    receiver = 0 if args.source_id == "radio" else None
                    results = await asyncio.gather(
                        *(
                            collect(
                                session,
                                base_url,
                                protocols,
                                Subscription(
                                    args.source_id,
                                    receiver,
                                    FollowView("vfo", args.span_hz),
                                    width,
                                    fps,
                                ),
                                args.duration,
                            )
                            for width, fps in profiles
                        )
                    )
                    return {
                        "duration_s": args.duration,
                        "clients": results,
                        "control_state_updates": updates,
                        "control_state_updates_per_second": updates / args.duration,
                    }
                finally:
                    watcher.cancel()
                    pulse.cancel()
                    await asyncio.gather(watcher, pulse, return_exceptions=True)
        finally:
            async with session.delete(
                base_url + "/api/v1/controller", headers={"X-RigPlane-Controller": key}
            ):
                pass


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--radio", choices=["simulator"], default="simulator")
    result.add_argument("--transport", choices=["sim"], default="sim")
    result.add_argument(
        "--base-url",
        help="measure a running Core server instead of starting the simulator",
    )
    result.add_argument(
        "--source-id", choices=["radio", "audio_fft", "iq_fft"], default="radio"
    )
    result.add_argument("--width", type=int, default=1024)
    result.add_argument("--max-fps", type=positive, default=10)
    result.add_argument("--duration", type=positive, default=30)
    result.add_argument("--span-hz", type=positive)
    result.add_argument(
        "--both",
        action="store_true",
        help="concurrent RigDeck 1024@10 and desktop 2800@20",
    )
    return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.base_url:
        args.span_hz = args.span_hz or 100000
        return await measure(args.base_url.rstrip("/"), args)
    async with simulator() as (base_url, recorded_span):
        args.span_hz = args.span_hz or recorded_span
        return await measure(base_url, args)


def main() -> None:
    try:
        receipt = asyncio.run(run(parser().parse_args()))
    except (ValueError, aiohttp.ClientError, TimeoutError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), flush=True)
        raise SystemExit(1) from error
    print(json.dumps({"status": "PASS", **receipt}), flush=True)


if __name__ == "__main__":
    main()
