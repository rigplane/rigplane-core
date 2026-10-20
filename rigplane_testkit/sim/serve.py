"""Launcher and control endpoint for the standalone IC-7610 simulator.

``run_serve`` owns one asyncio loop: the simulator's UDP servers, the local
aiohttp control endpoint and every event handler run on it — no threads. The
process prints exactly one JSON line on stdout once both are listening; all
logs go to stderr. Every accepted or rejected control event, plus process
start and stop, lands in a bounded in-memory trace (optionally appended to a
JSONL file); the end of a ``lan_drop`` window appends its own
``lan_drop_end`` recovery marker. Same seed + same event sequence =>
identical trace records once the real-time ``t`` field is removed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from aiohttp import web

from rigplane_testkit.icom.faults import FaultPlan, FaultRule
from rigplane_testkit.icom.simulator import MockIcomRadio

logger = logging.getLogger("rigplane_testkit.sim")

DEFAULT_KEEPALIVE_HOLD_S = 85.0
TRACE_RECORD_LIMIT = 10_000
_STOP_DRAIN_S = 0.25

# The four (port, direction) streams a lan_drop blackouts (see FaultPlan).
_LAN_DROP_STREAMS = (
    "control_to_client",
    "control_from_client",
    "civ_to_client",
    "civ_from_client",
)


@dataclass
class LanDropState:
    """Serve-side bookkeeping for the exact-N-second lan_drop window.

    ``saved`` holds the pre-blackout rules of the ACTIVE window (across an
    overlap it stays the very first, pre-blackout set); ``timer`` is the
    pending restore callback. Both live here, not on the plan, because the
    window is a serve-layer concept: the plan only ever sees whole rules.
    """

    trace: TraceLog
    saved: dict[str, FaultRule] | None = None
    timer: asyncio.TimerHandle | None = None

    def cancel(self) -> None:
        """Shutdown: forget the pending restore so nothing fires later."""
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.saved = None


@dataclass
class EventContext:
    """Per-serve objects an event handler may need beyond the radio."""

    trace: TraceLog
    lan_drop: LanDropState


# Event name -> handler(radio, ctx, body) -> extra response fields.
# Every handler takes effect immediately (at=0) and raises InvalidArgs on a
# bad argument; unknown names are the caller's "unsupported" branch.
EventFn = Callable[[MockIcomRadio, EventContext, dict], dict]


class InvalidArgs(Exception):
    """A control event carried a missing or malformed argument."""


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ev_set_frequency(radio: MockIcomRadio, ctx: EventContext, body: dict) -> dict:
    hz = body.get("hz")
    # 9_999_999_999 is the 10-digit BCD ceiling of the CI-V freq encoding.
    if not isinstance(hz, int) or isinstance(hz, bool) or not 1 <= hz <= 9_999_999_999:
        raise InvalidArgs("hz must be an integer between 1 and 9999999999")
    radio.set_frequency(hz)
    return {"frequency_hz": hz}


def _restore_lan_drop_rules(radio: MockIcomRadio, state: LanDropState) -> None:
    """End the blackout: put the saved rules back and trace the recovery."""
    state.timer = None
    plan = radio.fault_plan
    if plan is not None and state.saved is not None:
        for stream, rule in state.saved.items():
            setattr(plan, stream, rule)
    state.saved = None
    state.trace.append("event", "lan_drop_end", {}, {"ok": True})


def _ev_lan_drop(radio: MockIcomRadio, ctx: EventContext, body: dict) -> dict:
    for_s = body.get("for_s")
    if not _is_num(for_s) or for_s <= 0:
        raise InvalidArgs("for_s must be a number > 0")
    plan = radio.fault_plan
    if plan is None:
        plan = FaultPlan()
        radio.fault_plan = plan
    state = ctx.lan_drop
    if state.timer is not None:
        # Overlap: forget the pending restore but keep the ORIGINAL saved
        # rules (not the blackout rules) for the new deadline.
        state.timer.cancel()
    else:
        state.saved = {stream: getattr(plan, stream) for stream in _LAN_DROP_STREAMS}
    # No duration_s on the rules: FaultRule.duration_s counts from the first
    # matched packet, which would stagger the two directions into up to 2N.
    # serve.py owns the window and restores the saved rules itself.
    for stream in _LAN_DROP_STREAMS:
        setattr(plan, stream, FaultRule(drop_probability=1.0))
    if radio.enable_audio:
        radio.audio_port_silence(0.0, for_s)
    state.timer = asyncio.get_running_loop().call_later(
        for_s, _restore_lan_drop_rules, radio, state
    )
    return {"for_s": for_s}


def _ev_session_drop(radio: MockIcomRadio, ctx: EventContext, body: dict) -> dict:
    notice = body.get("notice", True)
    if not isinstance(notice, bool):
        raise InvalidArgs("notice must be a boolean")
    radio.drop_session(at=0.0, notice=notice)
    return {"notice": notice}


def _ev_power_cycle(radio: MockIcomRadio, ctx: EventContext, body: dict) -> dict:
    down_for_s = body.get("down_for_s", 5.0)
    if not _is_num(down_for_s) or down_for_s < 0:
        raise InvalidArgs("down_for_s must be a number >= 0")
    notice = body.get("notice", False)
    if not isinstance(notice, bool):
        raise InvalidArgs("notice must be a boolean")
    radio.reboot(at=0.0, down_for=down_for_s, notice=notice)
    return {"down_for_s": down_for_s, "notice": notice}


def _ev_keepalive_silence(radio: MockIcomRadio, ctx: EventContext, body: dict) -> dict:
    for_s = body.get("for_s")
    if not _is_num(for_s) or for_s <= 0:
        raise InvalidArgs("for_s must be a number > 0")
    radio.keepalive_silence(at=0.0, for_seconds=for_s)
    return {"for_s": for_s}


_EVENTS: dict[str, EventFn] = {
    "set_frequency": _ev_set_frequency,
    "lan_drop": _ev_lan_drop,
    "session_drop": _ev_session_drop,
    "power_cycle": _ev_power_cycle,
    "keepalive_silence": _ev_keepalive_silence,
}


class TraceLog:
    """Bounded in-memory event trace with an optional JSONL file.

    Records are ``{"n", "t", "kind", "name", "args", "result"}``; ``n`` is a
    global counter, ``t`` real seconds since construction (the only
    non-deterministic field, kept for scorecard recovery timings). With a
    path, each record is appended as one ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` line and flushed immediately.
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
            "args": args,
            "result": result,
        }
        self._next += 1
        self._records.append(record)
        if self._file is not None:
            self._file.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )
            self._file.flush()
        return record

    def since(self, marker: int) -> list[dict]:
        return [record for record in self._records if record["n"] >= marker]

    @property
    def next_marker(self) -> int:
        return self._next

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


# aiohttp 3.13 AppKeys instead of plain string keys (NotAppKeyWarning);
# defined after the classes they name because the constructor takes the
# class object.
_RADIO_KEY: web.AppKey[MockIcomRadio] = web.AppKey("radio", MockIcomRadio)
_TRACE_KEY: web.AppKey[TraceLog] = web.AppKey("trace", TraceLog)
_STOP_EVENT_KEY: web.AppKey[asyncio.Event] = web.AppKey("stop_event", asyncio.Event)
_EVENT_CTX_KEY: web.AppKey[EventContext] = web.AppKey("event_ctx", EventContext)


@dataclass
class ServeStack:
    """The objects one serve process is made of (no I/O before start())."""

    radio: MockIcomRadio
    trace: TraceLog
    app: web.Application
    stop_event: asyncio.Event
    lan_drop: LanDropState


def build_radio(args: argparse.Namespace) -> MockIcomRadio:
    """The IC-7610 simulator with the serve-process defaults (MOR-3593).

    ``keepalive_hold_s`` defaults to the recorded real IC-7610 dead-session
    hold (83.4-85.9 s, see ``_DEFAULT_KEEPALIVE_HOLD_S`` in
    ``rigplane_testkit/icom/simulator.py``), not the 0.5 s fast-test default.
    """
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
    )


async def _handle_state(request: web.Request) -> web.Response:
    radio: MockIcomRadio = request.app[_RADIO_KEY]
    # The simulator exposes setters but no frequency/mode accessors; the
    # private attributes are the state the wire reads (no public property).
    return web.json_response(
        {
            "frequency_hz": radio._frequency,
            "mode": radio._mode,
            "ptt": radio.ptt_on,
            "session_held": radio.session_held,
            "login_count": radio.login_count,
        }
    )


def _event_args(body: dict) -> dict:
    return {key: value for key, value in body.items() if key != "event"}


async def _handle_event(request: web.Request) -> web.Response:
    radio: MockIcomRadio = request.app[_RADIO_KEY]
    ctx: EventContext = request.app[_EVENT_CTX_KEY]
    trace = ctx.trace
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        body = None
    name = body.get("event") if isinstance(body, dict) else None
    if not isinstance(name, str):
        result = {"ok": False, "error": "invalid_args"}
        trace.append("event", "", {}, result)
        return web.json_response(result, status=400)
    args = _event_args(body)
    handler = _EVENTS.get(name)
    if handler is None:
        result = {"ok": False, "error": "unsupported", "event": name}
        trace.append("event", name, args, result)
        return web.json_response(result, status=400)
    try:
        applied = handler(radio, ctx, body)
    except InvalidArgs as exc:
        result = {
            "ok": False,
            "error": "invalid_args",
            "event": name,
            "detail": str(exc),
        }
        trace.append("event", name, args, result)
        return web.json_response(result, status=400)
    result = {"ok": True, "event": name, **applied}
    trace.append("event", name, args, result)
    return web.json_response(result)


async def _handle_trace(request: web.Request) -> web.Response:
    trace: TraceLog = request.app[_TRACE_KEY]
    try:
        since = int(request.query.get("since", "0"))
    except ValueError:
        since = 0
    return web.json_response(
        {"events": trace.since(max(0, since)), "next": trace.next_marker}
    )


async def _handle_stop(request: web.Request) -> web.Response:
    stop_event: asyncio.Event = request.app[_STOP_EVENT_KEY]
    # Let the response flush before run_serve tears the site down.
    asyncio.get_running_loop().call_later(_STOP_DRAIN_S, stop_event.set)
    return web.json_response({"ok": True})


def build_control_app(
    radio: MockIcomRadio,
    trace: TraceLog,
    stop_event: asyncio.Event,
    lan_drop: LanDropState,
) -> web.Application:
    app = web.Application()
    app[_RADIO_KEY] = radio
    app[_TRACE_KEY] = trace
    app[_STOP_EVENT_KEY] = stop_event
    app[_EVENT_CTX_KEY] = EventContext(trace=trace, lan_drop=lan_drop)
    app.router.add_get("/state", _handle_state)
    app.router.add_post("/event", _handle_event)
    app.router.add_get("/trace", _handle_trace)
    app.router.add_post("/stop", _handle_stop)
    return app


def build_stack(args: argparse.Namespace) -> ServeStack:
    """Build the radio, trace, stop event and control app (no sockets)."""
    radio = build_radio(args)
    trace = TraceLog(args.trace)
    stop_event = asyncio.Event()
    lan_drop = LanDropState(trace=trace)
    app = build_control_app(radio, trace, stop_event, lan_drop)
    trace.append("start", "serve", _start_trace_args(args), {"ok": True})
    return ServeStack(
        radio=radio,
        trace=trace,
        app=app,
        stop_event=stop_event,
        lan_drop=lan_drop,
    )


def startup_line(
    radio: MockIcomRadio, args: argparse.Namespace, http_port: int
) -> dict:
    control_host, _ = args.control
    # _username/_password are the constructor's fixed test credentials; the
    # simulator exposes no public properties for them (nor for frequency).
    return {
        "sim": "rigplane-sim/1",
        "radio": "IC-7610",
        "fidelity": "A",
        "evidence": "simulated",
        "transport": "lan",
        "host": args.bind,
        "control_port": radio.control_port,
        "username": radio._username,
        "password": radio._password,
        # Bracket IPv6 literals so the value parses as a URL.
        "control": f"http://{format_control_host(control_host)}:{http_port}",
        "seed": args.seed,
    }


def format_control_host(host: str) -> str:
    """Bracket an IPv6 literal for URL interpolation; IPv4/hostname as-is."""
    return f"[{host}]" if ":" in host else host


def _start_trace_args(args: argparse.Namespace) -> dict:
    control_host, control_port = args.control
    # Deterministic content only: no ephemeral ports (trace determinism).
    return {
        "radio": args.radio,
        "seed": args.seed,
        "bind": args.bind,
        "control": f"{control_host}:{control_port}",
        "keepalive_hold_s": args.keepalive_hold_s,
    }


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, on_signal: Callable[[], None]
) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, on_signal)
        except NotImplementedError:
            # Platforms without asyncio signal handlers (Windows) fall back
            # to KeyboardInterrupt handling in __main__.main().
            return


async def run_serve(
    args: argparse.Namespace, ready: asyncio.Event | None = None
) -> int:
    """Run one serve process until SIGINT/SIGTERM/POST /stop; return 0."""
    stack = build_stack(args)
    control_host, control_port_hint = args.control
    runner = web.AppRunner(stack.app, access_log=None)
    try:
        await stack.radio.start()
        await runner.setup()
        site = web.TCPSite(runner, control_host, control_port_hint)
        await site.start()
        http_port = runner.addresses[0][1]

        print(json.dumps(startup_line(stack.radio, args, http_port)), flush=True)
        logger.info(
            "sim serve: radio seed=%d control=http://%s:%d trace=%s",
            args.seed,
            control_host,
            http_port,
            args.trace or "off",
        )
        if ready is not None:
            ready.set()

        _install_signal_handlers(asyncio.get_running_loop(), stack.stop_event.set)
        await stack.stop_event.wait()
        return 0
    finally:
        # No lan_drop restore may fire after the radio stops.
        stack.lan_drop.cancel()
        with contextlib.suppress(Exception):
            await runner.cleanup()
        try:
            await stack.radio.stop()
        finally:
            stack.trace.append("stop", "shutdown", {}, {"ok": True})
            stack.trace.close()
