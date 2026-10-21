"""Server-API capture session for a USB CI-V radio, receive side only.

Starts, in this process and a child:

* the guarded recording bridge (``bridge.make_guard``: profile GETs and the
  scope switches only) on the real serial port, in-process;
* the Core web server (``rigplane ... web``) pointed at the bridge, which
  runs the production polling, identity gate and scope session logic;

then drives the server only through its HTTP/WebSocket API: ``/api/v1/info``,
``/api/v1/state`` (sampled for ``--state-seconds``), ``/api/v1/capabilities``
and the ``/api/v1/scope`` WebSocket (frames counted for ``--scope-seconds``).
Closing the scope socket makes the server restore the scope state it read.
It sends no command. The output summary and the raw CI-V recording (every
byte, ``blocked`` rows included) go to ``--out-dir``.

The server's scope guardrail needs ``allow_low_baud_scope`` below 115200
baud; this script sets the environment switch for that and records it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import winshim  # noqa: E402

winshim.apply()

import aiohttp  # noqa: E402
from bridge import make_guard  # noqa: E402

from rigplane_testkit.serial import SerialCivRecorder, civ_frames  # noqa: E402

_SERVER_BOOT = (
    "import sys; sys.path.insert(0, {here!r}); import winshim; winshim.apply(); "
    "from rigplane.cli import main; sys.argv = ['rigplane'] + {argv!r}; main()"
)


def _say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _get(session: aiohttp.ClientSession, base: str, path: str) -> object:
    async with session.get(
        base + path, timeout=aiohttp.ClientTimeout(total=10)
    ) as resp:
        return await resp.json()


async def _wait_ready(
    session: aiohttp.ClientSession, base: str, seconds: float
) -> object:
    deadline = time.monotonic() + seconds
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await _get(session, base, "/api/v1/info")
        except Exception as exc:  # server not listening yet
            last = exc
            await asyncio.sleep(0.5)
    raise TimeoutError(f"server not ready: {last!r}")


def _stable(node: object) -> object:
    """State without the keys that move on their own (meters, clock readings)."""
    if isinstance(node, dict):
        return {
            k: _stable(v)
            for k, v in node.items()
            if not any(
                w in k.lower()
                for w in (
                    "meter",
                    "time",
                    "clock",
                    "seq",
                    "revision",
                    "updatedat",
                    "fieldstatus",
                    "radiohealth",
                )
            )
        }
    if isinstance(node, list):
        return [_stable(v) for v in node]
    return node


def _intervals(stamps: list[float]) -> dict:
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]))
    if not gaps:
        return {}
    return {
        "median_ms": round(statistics.median(gaps) * 1000, 1),
        "p95_ms": round(gaps[min(len(gaps) - 1, int(len(gaps) * 0.95))] * 1000, 1),
        "max_ms": round(gaps[-1] * 1000, 1),
    }


async def run(args: argparse.Namespace, *, opener: object = None) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rec_path = out_dir / f"ic7300-server-{stamp}.civ.jsonl"
    log_path = out_dir / f"ic7300-server-{stamp}.server.log"
    summary: dict = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "baud": args.baud,
    }

    rec = SerialCivRecorder(
        args.device,
        rec_path,
        baudrate=args.baud,
        opener=opener,
        client_filter=make_guard(args.model, args.radio_addr).new_filter,
    )
    await rec.start()
    summary["civ_t0_wall_utc"] = datetime.fromtimestamp(
        time.time() - (time.monotonic() - rec._t0), timezone.utc
    ).isoformat()
    argv = [
        "--model", args.model,
        "--backend", "serial",
        "--serial-port", f"socket://127.0.0.1:{rec.listen_port}",
        "--serial-baud", str(args.baud),
        "web", "--listen", "127.0.0.1", "--port", str(args.web_port),
        "--no-rigctld", "--no-discovery",
    ]  # fmt: skip
    env = dict(os.environ, ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD="1")
    code = _SERVER_BOOT.format(here=str(Path(__file__).resolve().parent), argv=argv)
    base = f"http://127.0.0.1:{args.web_port}"
    exit_code = 0
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-c", code], stdout=log, stderr=subprocess.STDOUT, env=env
        )
        try:
            async with aiohttp.ClientSession() as session:
                _say("waiting for the server")
                summary["info"] = await _wait_ready(session, base, 40)
                _say("server up")
                await asyncio.sleep(8)  # let the first full poll cycle land
                summary["capabilities_keys"] = sorted(
                    (await _get(session, base, "/api/v1/capabilities")).keys()  # type: ignore[union-attr]
                )
                state_before = await _get(session, base, "/api/v1/state")
                summary["state_before"] = state_before
                changes = 0
                prev = json.dumps(_stable(state_before), sort_keys=True)
                end = time.monotonic() + args.state_seconds
                while time.monotonic() < end:
                    await asyncio.sleep(1.0)
                    cur = json.dumps(
                        _stable(await _get(session, base, "/api/v1/state")),
                        sort_keys=True,
                    )
                    changes += cur != prev
                    prev = cur
                summary["state_samples_changed"] = changes

                stamps: list[float] = []
                sizes: list[int] = []
                _say("opening the scope socket")
                async with session.ws_connect(
                    base + "/api/v1/scope", timeout=aiohttp.ClientWSTimeout(ws_close=10)
                ) as ws:
                    summary["scope_ws_open_wall_utc"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    scope_end = time.monotonic() + args.scope_seconds
                    while time.monotonic() < scope_end:
                        try:
                            msg = await asyncio.wait_for(
                                ws.receive(), max(0.1, scope_end - time.monotonic())
                            )
                        except asyncio.TimeoutError:
                            break
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            stamps.append(time.monotonic())
                            sizes.append(len(msg.data))
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                _say(f"scope socket closed after {len(stamps)} frames")
                await asyncio.sleep(3)  # server restores the scope state on disconnect
                state_after = await _get(session, base, "/api/v1/state")
                sa, sb = _stable(state_after), _stable(state_before)
                summary["state_after_equal"] = sa == sb
                summary["state_diff_keys"] = sorted(
                    k
                    for k in set(sa) | set(sb)
                    if sa.get(k) != sb.get(k)  # type: ignore[union-attr]
                )
                span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0
                summary["scope_ws"] = {
                    "frames": len(stamps),
                    "fps": round((len(stamps) - 1) / span, 3) if span > 0 else None,
                    "interval": _intervals(stamps),
                    "frame_bytes": sorted(set(sizes))[:5],
                }
        except Exception as exc:
            summary["error"] = f"{type(exc).__name__}: {exc}"
            exit_code = 3
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
            await rec.stop()
    summary["scope_switch_trace"] = [
        {"t": round(t, 3), "dir": d, "frame": f.hex()}
        for t, d, f in civ_frames(rec_path)
        if f[4] == 0x27 and len(f) > 5 and f[5] in (0x10, 0x11)
    ]
    summary["blocked_chunks"] = rec.blocked_lines
    summary["records"] = rec.records_written
    (out_dir / f"ic7300-server-{stamp}.summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                k: summary.get(k)
                for k in (
                    "error",
                    "scope_ws",
                    "state_after_equal",
                    "state_diff_keys",
                    "blocked_chunks",
                    "records",
                )
            }
        )
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--device", required=True)
    parser.add_argument("--baud", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="IC-7300")
    parser.add_argument("--radio-addr", type=lambda s: int(s, 0), default=0x94)
    parser.add_argument("--web-port", type=int, default=18099)
    parser.add_argument("--state-seconds", type=float, default=20.0)
    parser.add_argument("--scope-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    budget = args.state_seconds + args.scope_seconds + 120

    async def bounded() -> int:
        return await asyncio.wait_for(run(args), timeout=budget)

    return asyncio.run(bounded())


if __name__ == "__main__":
    sys.exit(main())
