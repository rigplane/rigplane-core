"""Launcher and control endpoint for the standalone IC-7610 simulator.

``run_serve`` owns one asyncio loop: the simulator's UDP servers, the local
aiohttp control endpoint and every event handler — no threads. One JSON line
goes to stdout once both listen (logs to stderr); every accepted or rejected
control event, plus start and stop, lands in a bounded trace (optionally a
JSONL file). With the same launcher arguments (``--seed``, ``--bind``,
``--control``, ``--keepalive-hold-s``, ``--username``) and the same event
sequence, the trace is identical once the real-time ``t`` field is removed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import signal
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web

from rigplane_testkit.icom.faults import FaultPlan
from rigplane_testkit.icom.simulator import MockIcomRadio

logger = logging.getLogger("rigplane_testkit.sim")

DEFAULT_KEEPALIVE_HOLD_S = 85.0
TRACE_RECORD_LIMIT = 10_000
_STOP_DRAIN_S = 0.25


class InvalidArgs(Exception):
    """Missing or malformed control-event argument."""


def _is_num(value: object) -> bool:
    """A finite number: bool is not one, and NaN/Infinity are not finite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False  # an int too large for float conversion is rejected too


def _json_safe(value: object) -> object:
    """Replace non-finite floats with their string spellings — strict JSON
    cannot emit them as numbers. Nesting is bounded by Python recursion;
    past the limit the event caller rejects the body (RecursionError)."""
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value == math.inf:
            return "Infinity"
        if value == -math.inf:
            return "-Infinity"
        return value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _dumps(value: object) -> str:
    """Strict JSON for HTTP responses: a non-finite float raises here."""
    return json.dumps(value, allow_nan=False)


def _ev_set_frequency(radio: MockIcomRadio, body: dict) -> dict:
    hz = body.get("hz")
    # 9_999_999_999 is the 10-digit BCD ceiling of the CI-V freq encoding.
    if not isinstance(hz, int) or isinstance(hz, bool) or not 1 <= hz <= 9_999_999_999:
        raise InvalidArgs("hz must be an integer between 1 and 9999999999")
    radio.set_frequency(hz)
    return {"frequency_hz": hz}


def _ev_session_drop(radio: MockIcomRadio, body: dict) -> dict:
    notice = body.get("notice", True)
    if not isinstance(notice, bool):
        raise InvalidArgs("notice must be a boolean")
    radio.drop_session(at=0.0, notice=notice)
    return {"notice": notice}


def _ev_power_cycle(radio: MockIcomRadio, body: dict) -> dict:
    down_for_s = body.get("down_for_s", 5.0)
    if not _is_num(down_for_s) or down_for_s < 0:
        raise InvalidArgs("down_for_s must be a finite number >= 0")
    notice = body.get("notice", False)
    if not isinstance(notice, bool):
        raise InvalidArgs("notice must be a boolean")
    radio.reboot(at=0.0, down_for=down_for_s, notice=notice)
    return {"down_for_s": down_for_s, "notice": notice}


def _ev_keepalive_silence(radio: MockIcomRadio, body: dict) -> dict:
    for_s = body.get("for_s")
    if not _is_num(for_s) or for_s <= 0:
        raise InvalidArgs("for_s must be a finite number > 0")
    radio.keepalive_silence(at=0.0, for_seconds=for_s)
    return {"for_s": for_s}


# Event name -> handler(radio, body) -> extra response fields (at=0, InvalidArgs on bad args).
_EVENTS: dict[str, Callable[[MockIcomRadio, dict], dict]] = {
    "set_frequency": _ev_set_frequency,
    "session_drop": _ev_session_drop,
    "power_cycle": _ev_power_cycle,
    "keepalive_silence": _ev_keepalive_silence,
}


class TraceLog:
    """Bounded in-memory event trace with an optional JSONL file. Records are
    ``{"n", "t", "kind", "name", "args", "result"}``: ``t`` (real seconds
    since construction) is the only time-dependent field; ``args``/``result``
    are sanitised — non-finite floats become string spellings.
    """

    def __init__(self, path: str | None = None) -> None:
        self._records: deque[dict] = deque(maxlen=TRACE_RECORD_LIMIT)
        self._next = 0
        self._t0 = time.monotonic()
        self._file = open(path, "a", encoding="utf-8") if path else None

    def append(self, kind: str, name: str, args: dict, result: dict) -> dict:
        record = {
            "n": self._next,
            "t": round(time.monotonic() - self._t0, 3),
            "kind": kind,
            "name": name,
            "args": _json_safe(args),
            "result": _json_safe(result),
        }
        self._next += 1
        self._records.append(record)
        if self._file is not None:
            line = json.dumps(
                record, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            self._file.write(line + "\n")
            self._file.flush()
        return record

    def snapshot(self, since: int) -> dict:
        return {
            "events": [record for record in self._records if record["n"] >= since],
            "next": self._next,
        }

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


# aiohttp 3.13 AppKeys instead of plain string keys (NotAppKeyWarning).
_RADIO_KEY: web.AppKey[MockIcomRadio] = web.AppKey("radio", MockIcomRadio)
_TRACE_KEY: web.AppKey[TraceLog] = web.AppKey("trace", TraceLog)
_STOP_EVENT_KEY: web.AppKey[asyncio.Event] = web.AppKey("stop_event", asyncio.Event)


@dataclass
class ServeStack:
    """The objects one serve process is made of (no sockets before start();
    a --trace file, when given, opens at construction)."""

    radio: MockIcomRadio
    trace: TraceLog
    app: web.Application
    stop_event: asyncio.Event


def build_radio(args: argparse.Namespace) -> MockIcomRadio:
    """The IC-7610 simulator with the serve defaults (MOR-3593): the recorded
    real dead-session hold (83.4-85.9 s), not the 0.5 s fast-test default."""
    # None = do not pass the kwarg: MockIcomRadio's own default applies.
    pairs = (("username", args.username), ("password", args.password))
    credentials = {key: value for key, value in pairs if value is not None}
    return MockIcomRadio(
        host=args.bind,
        port=0,
        civ_port=0,
        single_owner=True,
        keepalive_hold_s=args.keepalive_hold_s,
        fault_plan=FaultPlan(seed=args.seed),
        enable_audio=True,
        dual_receiver=True,
        stateful_ptt=True,
        **credentials,
    )


def build_stack(args: argparse.Namespace) -> ServeStack:
    """Build the radio, trace, stop event and control app (no sockets)."""
    radio = build_radio(args)
    trace = TraceLog(args.trace)
    stop_event = asyncio.Event()
    app = web.Application()
    app[_RADIO_KEY] = radio
    app[_TRACE_KEY] = trace
    app[_STOP_EVENT_KEY] = stop_event
    app.router.add_get("/state", _handle_state)
    app.router.add_post("/event", _handle_event)
    app.router.add_get("/trace", _handle_trace)
    app.router.add_post("/stop", _handle_stop)
    control_host, control_port = args.control
    # Deterministic args only: the bound (ephemeral) port never appears.
    start_args = {
        "radio": args.radio,
        "seed": args.seed,
        "bind": args.bind,
        "control": f"{control_host}:{control_port}",
        "keepalive_hold_s": args.keepalive_hold_s,
    }
    trace.append("start", "serve", start_args, {"ok": True})
    return ServeStack(radio=radio, trace=trace, app=app, stop_event=stop_event)


def format_control_host(host: str) -> str:
    """Bracket an IPv6 literal for URL interpolation; IPv4/hostname as-is."""
    return f"[{host}]" if ":" in host else host


def startup_line(
    radio: MockIcomRadio, args: argparse.Namespace, http_port: int
) -> dict:
    # _username is the accepted login; the password never appears in output.
    return {
        "sim": "rigplane-sim/1",
        "radio": "IC-7610",
        "fidelity": "A",
        "evidence": "simulated",
        "transport": "lan",
        "host": args.bind,
        "control_port": radio.control_port,
        "username": radio._username,
        "control": f"http://{format_control_host(args.control[0])}:{http_port}",
        "seed": args.seed,
    }


async def _handle_state(request: web.Request) -> web.Response:
    radio: MockIcomRadio = request.app[_RADIO_KEY]
    # Setters but no accessors: the private attributes are what the wire reads.
    return web.json_response(
        {
            "frequency_hz": radio._frequency,
            "mode": radio._mode,
            "ptt": radio.ptt_on,
            "session_held": radio.session_held,
            "login_count": radio.login_count,
        },
        dumps=_dumps,
    )


def _reject(trace: TraceLog, name: str, args: dict, result: dict) -> web.Response:
    """Trace a rejected event (``args`` already sanitised) and answer 400."""
    trace.append("event", name, args, result)
    return web.json_response(result, status=400, dumps=_dumps)


async def _handle_event(request: web.Request) -> web.Response:
    radio: MockIcomRadio = request.app[_RADIO_KEY]
    trace: TraceLog = request.app[_TRACE_KEY]
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        body = None
    name = body.get("event") if isinstance(body, dict) else None
    if not isinstance(name, str):
        return _reject(trace, "", {}, {"ok": False, "error": "invalid_args"})
    args = {key: value for key, value in body.items() if key != "event"}
    try:
        safe_args = _json_safe(args)  # every branch below records sanitised args
    except RecursionError:  # unrecordably nested: reject, no handler runs
        return _reject(
            trace, name, {}, {"ok": False, "error": "invalid_args", "event": name}
        )
    handler = _EVENTS.get(name)
    if handler is None:
        return _reject(
            trace, name, safe_args, {"ok": False, "error": "unsupported", "event": name}
        )
    try:
        applied = handler(radio, body)
        result = {"ok": True, "event": name, **applied}
        trace.append("event", name, safe_args, result)
    except InvalidArgs as exc:
        return _reject(
            trace,
            name,
            safe_args,
            {"ok": False, "error": "invalid_args", "event": name, "detail": str(exc)},
        )
    return web.json_response(result, dumps=_dumps)


async def _handle_trace(request: web.Request) -> web.Response:
    trace: TraceLog = request.app[_TRACE_KEY]
    try:
        since = int(request.query.get("since", "0"))
    except ValueError:
        since = 0
    return web.json_response(trace.snapshot(max(0, since)), dumps=_dumps)


async def _handle_stop(request: web.Request) -> web.Response:
    stop_event: asyncio.Event = request.app[_STOP_EVENT_KEY]
    # Flush the response before teardown; a repeat POST only re-arms the idempotent set.
    asyncio.get_running_loop().call_later(_STOP_DRAIN_S, stop_event.set)
    return web.json_response({"ok": True}, dumps=_dumps)


async def run_serve(args: argparse.Namespace) -> int:
    """Run one serve process until SIGINT/SIGTERM/POST /stop; return 0."""
    stack = build_stack(args)
    control_host, control_port_hint = args.control
    runner = web.AppRunner(stack.app, access_log=None)
    try:
        await stack.radio.start()
        await runner.setup()
        await web.TCPSite(runner, control_host, control_port_hint).start()
        http_port = runner.addresses[0][1]

        print(json.dumps(startup_line(stack.radio, args, http_port)), flush=True)
        logger.info(
            "sim serve: radio seed=%d control=http://%s:%d trace=%s",
            args.seed,
            format_control_host(control_host),
            http_port,
            args.trace or "off",
        )

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stack.stop_event.set)
            except NotImplementedError:
                break  # Windows: __main__ falls back to KeyboardInterrupt
        await stack.stop_event.wait()
        return 0
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
        try:
            await stack.radio.stop()
        finally:
            try:
                stack.trace.append("stop", "shutdown", {}, {"ok": True})
            finally:
                stack.trace.close()
