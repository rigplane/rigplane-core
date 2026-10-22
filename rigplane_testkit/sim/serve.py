"""Launcher and control endpoint for the standalone radio simulators.

``run_serve`` owns one asyncio loop: the simulator's servers, the local
aiohttp control endpoint and every event handler — no threads. One JSON line
goes to stdout once everything listens (logs to stderr); every accepted or
rejected control event, plus start and stop, lands in a bounded trace
(optionally a JSONL file). ``t`` (real elapsed seconds) is the only time
field; the trace is a log of what happened, not a replay script (a restore
timer's ``usb_reappear`` record appears only if the timer fires).

Three radio kinds: ``ic7610`` (LAN, MOR-3593), ``ic7300`` (serial CI-V,
MOR-3594) and ``ftx1`` (Yaesu CAT serial, MOR-3602) — each on real
endpoints; :class:`RadioBackend` is the seam between them.
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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from rigplane.profiles.rig_loader import RigLoadError

from rigplane_testkit.icom.faults import FaultPlan
from rigplane_testkit.icom.simulator import MockIcomRadio
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.serial.endpoints import PtyEndpoint, TcpEndpoint, pty_supported
from rigplane_testkit.sim.adapters.yaesu_cat import (
    YaesuCatSerialFake,
    fidelity_for_banner,
)

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


def _parse_hz(body: dict) -> int:
    """The shared set_frequency argument rule (both radio kinds)."""
    hz = body.get("hz")
    # 9_999_999_999 is the 10-digit BCD ceiling of the CI-V freq encoding.
    if not isinstance(hz, int) or isinstance(hz, bool) or not 1 <= hz <= 9_999_999_999:
        raise InvalidArgs("hz must be an integer between 1 and 9999999999")
    return hz


def _parse_for_s(body: dict) -> float:
    """The shared positive-window argument rule (both radio kinds)."""
    for_s = body.get("for_s")
    if not _is_num(for_s) or for_s <= 0:
        raise InvalidArgs("for_s must be a finite number > 0")
    return for_s


def _ev_set_frequency(radio: MockIcomRadio, body: dict) -> dict:
    hz = _parse_hz(body)
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
    for_s = _parse_for_s(body)
    radio.keepalive_silence(at=0.0, for_seconds=for_s)
    return {"for_s": for_s}


def _ev_serial_set_frequency(radio: CivSerialFake, body: dict) -> dict:
    hz = _parse_hz(body)
    radio.frequency_hz = hz
    return {"frequency_hz": hz}


def _ev_serial_silence(radio: CivSerialFake, body: dict) -> dict:
    for_s = _parse_for_s(body)
    radio.silence(for_s)
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


@dataclass
class RadioBackend:
    """One radio kind's seam: radio, lifecycle, startup fields (key order
    is the contract), /state body and event handlers."""

    radio: Any
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]
    startup_fields: Callable[[], dict]
    state: Callable[[], dict]
    events: dict[str, Callable[[Any, dict], dict]]
    vanish: _VanishController | None = None  # serial: restore-timer seam


# aiohttp 3.13 AppKeys instead of plain string keys (NotAppKeyWarning).
_BACKEND_KEY: web.AppKey[RadioBackend] = web.AppKey("backend", RadioBackend)
_TRACE_KEY: web.AppKey[TraceLog] = web.AppKey("trace", TraceLog)
_STOP_EVENT_KEY: web.AppKey[asyncio.Event] = web.AppKey("stop_event", asyncio.Event)


class _VanishController:
    """Serve-owned usb_vanish/reappear bookkeeping: the fake has no public
    vanished-flag readback, serve tracks it for ``GET /state``, and a
    scheduled reappear is cancellable on shutdown and traced on fire."""

    def __init__(self, fake: CivSerialFake, trace: TraceLog) -> None:
        self._fake = fake
        self._trace = trace
        self.vanished = False
        self._reappear: asyncio.TimerHandle | None = None

    def vanish(self, for_s: float | None) -> None:
        if self._reappear is not None:
            self._reappear.cancel()
            self._reappear = None
        self._fake.usb_vanish(at=0.0)
        self.vanished = True
        if for_s is not None:
            self._reappear = asyncio.get_running_loop().call_later(
                for_s, self.reappear, True
            )

    def reappear(self, traced: bool = False) -> None:
        if self._reappear is not None:
            self._reappear.cancel()
            self._reappear = None
        self._fake.usb_reappear(after=0.0)
        self.vanished = False
        if traced:  # only the scheduled reappear traces its own record
            self._trace.append("event", "usb_reappear", {}, {"ok": True})

    def cancel(self) -> None:
        """Shutdown: drop a scheduled reappear without running it."""
        if self._reappear is not None:
            self._reappear.cancel()
            self._reappear = None


@dataclass
class ServeStack:
    """The objects one serve process is made of (no sockets before start();
    a --trace file, when given, opens at construction)."""

    backend: RadioBackend
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


def _ic7610_backend(args: argparse.Namespace) -> RadioBackend:
    """The LAN simulator behind real UDP servers (MOR-3593 behaviour)."""
    radio = build_radio(args)

    def _startup_fields() -> dict:
        # _username is the accepted login; the password never appears in output.
        return {
            "radio": "IC-7610",
            "fidelity": "A",
            "evidence": "simulated",
            "transport": "lan",
            "host": args.bind,
            "control_port": radio.control_port,
            "username": radio._username,
        }

    def _state() -> dict:
        # Setters but no accessors: the private attributes are what the wire reads.
        return {
            "frequency_hz": radio._frequency,
            "mode": radio._mode,
            "ptt": radio.ptt_on,
            "session_held": radio.session_held,
            "login_count": radio.login_count,
        }

    return RadioBackend(
        radio, radio.start, radio.stop, _startup_fields, _state, _EVENTS
    )


# Profiles, rigs/ and fixtures live under the repo root (see the FTX-1 note below).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _ic7300_backend(args: argparse.Namespace, trace: TraceLog) -> RadioBackend:
    """The IC-7300 simulation profile (MOR-3597) on a TCP and (where supported) PTY."""
    fake = CivSerialFake.from_profile(_REPO_ROOT)
    sim = fake.sim_profile
    assert sim is not None  # from_profile always keeps the profile it loaded
    tcp = TcpEndpoint(fake, host=args.bind)
    pty = PtyEndpoint(fake) if pty_supported() else None
    vanish = _VanishController(fake, trace)

    def _ev_usb_vanish(radio: CivSerialFake, body: dict) -> dict:
        for_s = _parse_for_s(body) if "for_s" in body else None
        vanish.vanish(for_s)
        return {"for_s": for_s} if for_s is not None else {}

    def _ev_usb_reappear(radio: CivSerialFake, body: dict) -> dict:
        vanish.reappear()
        return {}

    async def _start() -> None:
        await tcp.start()
        if pty is not None:
            await pty.start()

    async def _stop() -> None:
        vanish.cancel()  # a scheduled reappear must not fire into a shutdown
        if pty is not None:
            await pty.stop()
        await tcp.stop()

    def _startup_fields() -> dict:
        return {
            "radio": "IC-7300",
            # The profile's tier: tests/testkit/test_ic7300_conformance.py fails
            # on a divergence outside the (empty) ledger, so A is kept honest.
            "fidelity": sim.sim.fidelity,
            "evidence": "simulated",
            "transport": "serial",
            "device": tcp.device_url,
            "pty": pty.device if pty is not None else None,
        }

    def _state() -> dict:
        return {
            "frequency_hz": fake.frequency_hz,
            "mode": fake.mode,
            "ptt": fake.ptt,
            "s_meter": fake.s_meter,
            "open_count": fake.open_count,
            "vanished": vanish.vanished,
        }

    events = {
        "set_frequency": _ev_serial_set_frequency,
        "usb_vanish": _ev_usb_vanish,
        "usb_reappear": _ev_usb_reappear,
        "silence": _ev_serial_silence,
    }
    return RadioBackend(fake, _start, _stop, _startup_fields, _state, events, vanish)


# The FTX-1 sim profile, rigs/ftx1.toml and the fixtures live under the repo
# root; a repo-relative constant here is the known ADR section 11 extraction
# blocker, because serve has no --fixtures flag yet.
_FTX1_REPO_ROOT = _REPO_ROOT
_FTX1_LEDGER = (
    _FTX1_REPO_ROOT / "tests" / "fixtures" / "ftx1" / "model_conformance_ledger.json"
)


def _ftx1_backend(args: argparse.Namespace, trace: TraceLog) -> RadioBackend:
    """The FTX-1 Yaesu CAT fake on a TCP and (where supported) PTY (MOR-3602)."""

    def _on_not_modelled(line: str) -> None:
        trace.append(
            "event",
            "not_modelled",
            {"request": line},
            {"ok": False, "outcome": "unsupported"},
        )

    fake = YaesuCatSerialFake.from_profile(
        _FTX1_REPO_ROOT, seed=args.seed, on_not_modelled=_on_not_modelled
    )
    fidelity = fidelity_for_banner(fake, _FTX1_LEDGER)
    tcp = TcpEndpoint(fake, host=args.bind)
    pty = PtyEndpoint(fake) if pty_supported() else None
    vanish = _VanishController(fake, trace)

    def _ev_ftx1_set_frequency(radio: YaesuCatSerialFake, body: dict) -> dict:
        hz = _parse_hz(body)
        try:
            # The engine bounds the value to the FTX-1's ranges; the setter
            # bypasses the wire's MEMORY-mode refusal on purpose.
            radio.frequency_hz = hz
        except ValueError as exc:
            raise InvalidArgs(str(exc)) from exc
        return {"frequency_hz": hz}

    def _ev_usb_vanish(radio: YaesuCatSerialFake, body: dict) -> dict:
        for_s = _parse_for_s(body) if "for_s" in body else None
        vanish.vanish(for_s)
        return {"for_s": for_s} if for_s is not None else {}

    def _ev_usb_reappear(radio: YaesuCatSerialFake, body: dict) -> dict:
        vanish.reappear()
        return {}

    async def _start() -> None:
        await tcp.start()
        if pty is not None:
            await pty.start()

    async def _stop() -> None:
        vanish.cancel()  # a scheduled reappear must not fire into a shutdown
        if pty is not None:
            await pty.stop()
        await tcp.stop()

    def _startup_fields() -> dict:
        return {
            "radio": "FTX-1",
            "fidelity": fidelity,
            "evidence": "simulated",
            "transport": "serial",
            "device": tcp.device_url,
            "pty": pty.device if pty is not None else None,
        }

    def _state() -> dict:
        return {
            "frequency_hz": fake.frequency_hz,
            "mode": fake.mode,
            "ptt": fake.ptt,
            "s_meter": fake.s_meter,
            "open_count": fake.open_count,
            "vanished": vanish.vanished,
        }

    events = {
        "set_frequency": _ev_ftx1_set_frequency,
        "usb_vanish": _ev_usb_vanish,
        "usb_reappear": _ev_usb_reappear,
        "silence": _ev_serial_silence,
    }
    return RadioBackend(fake, _start, _stop, _startup_fields, _state, events, vanish)


def build_stack(args: argparse.Namespace) -> ServeStack:
    """Build the radio backend, trace, stop event and control app (no sockets)."""
    trace = TraceLog(args.trace)
    stop_event = asyncio.Event()
    try:
        if args.radio == "ic7300":
            backend = _ic7300_backend(args, trace)
        elif args.radio == "ftx1":
            backend = _ftx1_backend(args, trace)
        else:
            backend = _ic7610_backend(args)
    except (OSError, ValueError, RigLoadError) as exc:
        # A missing/unreadable profile, bad TOML or a bad fixture is a
        # startup error (SystemExit prints the message, no traceback), not
        # a crash from deep inside a loader. No sockets exist yet; close
        # the one side effect, a --trace file opened above.
        trace.close()
        raise SystemExit(f"serve --radio {args.radio}: {exc}") from exc
    app = web.Application()
    app[_BACKEND_KEY] = backend
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
    return ServeStack(backend=backend, trace=trace, app=app, stop_event=stop_event)


def format_control_host(host: str) -> str:
    """Bracket an IPv6 literal for URL interpolation; IPv4/hostname as-is."""
    return f"[{host}]" if ":" in host else host


def startup_line(
    backend: RadioBackend, args: argparse.Namespace, http_port: int
) -> dict:
    return {
        "sim": "rigplane-sim/1",
        **backend.startup_fields(),
        "control": f"http://{format_control_host(args.control[0])}:{http_port}",
        "seed": args.seed,
    }


async def _handle_state(request: web.Request) -> web.Response:
    backend: RadioBackend = request.app[_BACKEND_KEY]
    return web.json_response(backend.state(), dumps=_dumps)


def _reject(trace: TraceLog, name: str, args: dict, result: dict) -> web.Response:
    """Trace a rejected event (``args`` already sanitised) and answer 400."""
    trace.append("event", name, args, result)
    return web.json_response(result, status=400, dumps=_dumps)


async def _handle_event(request: web.Request) -> web.Response:
    backend: RadioBackend = request.app[_BACKEND_KEY]
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
    handler = backend.events.get(name)
    if handler is None:
        return _reject(
            trace, name, safe_args, {"ok": False, "error": "unsupported", "event": name}
        )
    try:
        applied = handler(backend.radio, body)
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
    # Signal handlers before anything listens or prints: a caller may read
    # the startup line and signal at once; the default action would kill
    # the process (-SIGTERM) before the traced shutdown could run.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stack.stop_event.set)
        except NotImplementedError:
            break  # Windows: __main__ falls back to KeyboardInterrupt
    control_host, control_port_hint = args.control
    runner = web.AppRunner(stack.app, access_log=None)
    try:
        await stack.backend.start()
        await runner.setup()
        await web.TCPSite(runner, control_host, control_port_hint).start()
        http_port = runner.addresses[0][1]

        print(json.dumps(startup_line(stack.backend, args, http_port)), flush=True)
        logger.info(
            "sim serve: radio seed=%d control=http://%s:%d trace=%s",
            args.seed,
            format_control_host(control_host),
            http_port,
            args.trace or "off",
        )

        await stack.stop_event.wait()
        return 0
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
        try:
            await stack.backend.stop()
        finally:
            try:
                stack.trace.append("stop", "shutdown", {}, {"ok": True})
            finally:
                stack.trace.close()
