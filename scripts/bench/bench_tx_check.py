#!/usr/bin/env python3
"""Owner-present HTTP PTT bench (MOR-3839 release gate live-TX evidence).

Run it from any directory on any OS with only the Python 3.11 standard
library: ``python3 scripts/bench/bench_tx_check.py`` from a Core checkout, or
copy this one file to a bench machine next to a ``rigs/`` directory (see
``--rigs-dir``). It never touches a serial port; it drives Core's HTTP API
only. The journal goes to ``./bench-out`` under the current directory.

Default invocation is READ ONLY. Armed runs additionally require, all at once:
``--tx-armed``, ``--checklist-confirmed``, ``--no-clients-confirmed`` and a
non-empty ``BENCH_OWNER_YES`` environment note (who confirmed what, when);
``--sim`` is the only unarmed-safe self-run and starts the existing isolated
IC-7610 production stack instead of talking to a radio.

Runs 1-3 key and rekey with 300 ms / 50 ms holds and demand positive keying
evidence. Run 4 is the in-flight force_off (MOR-3838 supersede path):
``transmit_on`` at t=0 and ``force_off`` at t=10 ms by client send order,
without awaiting replies, 10 cycles. Either side may win the race -- the key
may or may not be observed on the radio (on real IC-705/IC-7300 Core
supersedes it before the radio sees it; the simulator may show it keyed
briefly) -- and BOTH outcomes are valid. What must hold for PASS is the safety
outcome only: fresh radio RX observed within the 2 s OFF deadline,
``releaseRequired`` false and intent rx in the final snapshot, observed PTT
off, no stuck TX, radio ends in RX.

HTTP launch times are evidence of client requests, never of CI-V ACK ordering
or RF transitions. The watchdog can request OFF, not guarantee delivery over a
failed network. This tool does not inject faults, send audio, or emit raw CI-V.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import datetime as dt
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlsplit

HF_LIMIT_HZ = 30_000_000


STATE = "/api/v1/state"
MANAGED = "/api/v1/managed-transmit"
COMMAND = MANAGED + "/command"
TIMEOUT = 2.0
MAX_TOTAL = 10.0
RADIO_SOURCES = {"poll_response", "command_response", "civ_unsolicited"}

REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_rigs_dir():
    """Profile directory of the checkout this file lives in.

    Used when no --rigs-dir is given, so the tool also works from a bare copy
    next to a rigs/ directory on a bench machine without a full checkout.
    """
    return REPO_ROOT / "rigs"


class Unsafe(RuntimeError):
    pass


class ReadinessDeadline(Unsafe):
    pass


def stamp():
    return {"wall": dt.datetime.now(dt.UTC).isoformat(), "mono": time.monotonic()}


class Journal:
    def __init__(self, args):
        directory = Path.cwd() / "bench-out"
        directory.mkdir(exist_ok=True)
        label = re.sub(r"[^A-Za-z0-9_.-]", "_", args.label)
        now = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = directory / f"{label}-run{args.run}-{now}.jsonl"
        self.file = self.path.open("x", encoding="utf-8")
        self.lock = threading.Lock()

    def emit(self, kind, **values):
        with self.lock:
            self.file.write(json.dumps({"type": kind, **stamp(), **values},
                                       allow_nan=False, default=str) + "\n")
            self.file.flush()

    def close(self):
        self.file.close()


def refusal(value):
    """Do not treat an outer ok:true envelope as a successful radio write."""
    if isinstance(value, dict):
        if value.get("ok") is False or value.get("accepted") is False:
            return True
        if value.get("error") or value.get("result") in ("rejected", "refused"):
            return True
        return any(refusal(v) for v in value.values() if isinstance(v, (dict, list)))
    if isinstance(value, list):
        return any(refusal(v) for v in value)
    return False


class Client:
    def __init__(self, url, journal):
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in {"", "/"}):
            raise Unsafe("--url must be an HTTP(S) server origin without credentials")
        self.parsed, self.log = parsed, journal
        self.lock = threading.Lock()
        self.number = 0

    def request(self, method, path, body=None, *, cycle=None, action=None, on_sent=None,
                deadline=None):
        def timeout():
            if deadline is None:
                return TIMEOUT
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessDeadline(f"readiness deadline expired during {action or path}")
            return min(TIMEOUT, remaining)

        request_timeout = timeout()
        with self.lock:
            self.number += 1
            request_id = self.number
        begin = stamp()
        self.log.emit("http_request", request_id=request_id, cycle=cycle,
                      action=action, method=method, path=path, body=body,
                      request_at=begin)
        cls = (http.client.HTTPSConnection if self.parsed.scheme == "https"
               else http.client.HTTPConnection)
        connection = cls(self.parsed.hostname, self.parsed.port, timeout=request_timeout)
        try:
            encoded = None if body is None else json.dumps(body).encode()
            if deadline is not None:
                connection.connect()
                connection.sock.settimeout(timeout())
            connection.request(method, path, body=encoded, headers={
                "Content-Type": "application/json", "Connection": "close",
                "Cache-Control": "no-cache"})
            sent_at = stamp()
            self.log.emit("http_request_sent", request_id=request_id, cycle=cycle,
                          action=action, sent_at=sent_at)
            if on_sent is not None:
                on_sent(sent_at)
            if deadline is not None:
                response_socket = connection.sock
                response_socket.settimeout(timeout())
            response = connection.getresponse()
            if deadline is None:
                raw = response.read(2_000_001)
            else:
                chunks, size = [], 0
                while size < 2_000_001 and not response.isclosed():
                    response_socket.settimeout(timeout())
                    chunk = response.read1(min(65_536, 2_000_001 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                raw = b"".join(chunks)
                timeout()
            if len(raw) > 2_000_000:
                raise Unsafe("oversized HTTP response")
            server_text = raw.decode("utf-8", errors="replace")
            try:
                value = json.loads(server_text)
            except ValueError as exc:
                self.log.emit("http_response", request_id=request_id, cycle=cycle,
                              action=action, status=response.status, text=server_text,
                              request_at=begin, accepted=False)
                raise Unsafe("non-JSON HTTP response") from exc
            rejected = refusal(value)
            accepted = 200 <= response.status < 300 and not rejected
            result = {"status": response.status, "body": value,
                      "accepted": accepted, "refused": rejected,
                      "request_at": begin, "response_at": stamp()}
            self.log.emit("http_response", request_id=request_id, cycle=cycle,
                          action=action, text=server_text, **result)
            # Preserve supported experiment refusals as results, including an
            # explicit rejection in a successful HTTP envelope. Other errors abort.
            managed_refusal = (path == COMMAND and isinstance(body, dict)
                               and body.get("operation") in {"transmit_on", "force_off"}
                               and isinstance(value, dict)
                               and value.get("result") == "rejected")
            power_refusal = (path == "/api/v1/commands" and action == "same_power_write"
                             and isinstance(value, dict) and value.get("ok") is False
                             and value.get("error") == "radio_nak")
            if (rejected and (200 <= response.status < 300 or response.status == 409)
                    and (managed_refusal or power_refusal)):
                return result
            if not 200 <= response.status < 300 or rejected:
                raise Unsafe(f"HTTP/write failure for {action or path}: "
                             f"status={response.status}; see JSONL response")
            if not isinstance(value, dict):
                raise Unsafe("HTTP response must be an object")
            return result
        except Exception as exc:
            self.log.emit("http_error", request_id=request_id, cycle=cycle,
                          action=action, error=f"{type(exc).__name__}: {exc}",
                          request_at=begin)
            if (deadline is not None and isinstance(exc, TimeoutError)
                    and time.monotonic() >= deadline):
                raise ReadinessDeadline(
                    f"readiness deadline expired during {action or path}") from exc
            raise
        finally:
            connection.close()


def field(state, path):
    value = state
    for name in path.split("."):
        value = value.get(name) if isinstance(value, dict) else None
    status = state.get("fieldStatus", {}).get(path, {})
    return value, status


def observed(state, path, expected=None, *, exact=False):
    value, status = field(state, path)
    source = status.get("source") or {}
    when = status.get("lastObservedMonotonic")
    good = (value is not None
            and status.get("observed") is True and status.get("freshness") == "fresh"
            and status.get("availability") == "available"
            and source.get("provider") == "icom_civ"
            and source.get("source") in RADIO_SOURCES
            and type(when) in {int, float} and math.isfinite(when))
    if exact:
        good = good and type(value) is type(expected) and value == expected
    return good, value, when, status


def sample_observed_after(sample, observed_when, after):
    """True when the radio observation itself is at least `after` (bench clock).

    The state read carries the SERVER monotonic clock in lastObservedMonotonic; both
    clocks are CLOCK_MONOTONIC on this host.  A fresh flag can serve a sample taken
    before the OFF (KEYING TTL 1.2 s), so the observation time decides, never the time
    of the GET.  The offset per sample is received_mono minus the newest fresh
    observation in the same snapshot (an upper bound of the server-to-bench skew).
    """
    newest = [s.get("lastObservedMonotonic") for s in sample["state"].get("fieldStatus", {}).values()
              if isinstance(s, dict) and s.get("freshness") == "fresh"
              and type(s.get("lastObservedMonotonic")) in {int, float}]
    if not newest or observed_when is None:
        return False
    offset = sample["received_mono"] - max(newest)
    return observed_when + offset >= after - 0.15


def debt_free(managed):
    view = managed.get("managedTransmit", {})
    return (view.get("status") == "available"
            and view.get("releaseRequired") is False
            and view.get("intent", {}).get("kind") == "rx")


def verdict(managed):
    view = managed.get("managedTransmit", {})
    actuation = view.get("lastActuation") or {}
    if actuation.get("operation") != "force_receive":
        return "uncertain"
    if debt_free(managed) and actuation.get("result") == "accepted":
        return "released"
    result = actuation.get("result")
    return "refused" if result == "rejected" else "uncertain"


class Bench:
    def __init__(self, args, journal):
        self.args, self.log = args, journal
        self.client = Client(args.url, journal)
        self.stop = threading.Event()
        self.poll_stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.cycle_id = None
        self.error = None
        self.armed = False
        self.workers = []
        self.rows = []
        self.total = 0.0
        self.key_intervals = []
        self.off_requests = []
        self.ptt_watermark = None
        self.last_positive = None
        self.uncertain_positive = False
        self.release_since = None
        self.generation = None
        self.sim_adaptations = []
        self.sim_vox_unobserved = False

    def possible_tx(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.total = sum(max(0, min(entry.get("rx_observed_mono") or now, now)
                                - entry["started"])
                             for entry in self.key_intervals)
            return self.total

    def register_off(self, entry):
        with self.lock:
            entry.update(baseline=None, rx_observed_mono=None,
                         rx_observed_ms=None, deadline_failed=False)
            self.off_requests.append(entry)
            for key in self.key_intervals:
                if key.get("off") is None:
                    key["off"] = entry
            if self.release_since is None:
                self.release_since = entry["started"]

    def capture_rx(self, response):
        good, value, when, _ = observed(response["body"], "ptt")
        if not good or type(value) is not bool:
            return
        received = response["response_at"]["mono"]
        with self.lock:
            if self.ptt_watermark is None or when > self.ptt_watermark:
                self.ptt_watermark = when
            for off in self.off_requests:
                sent = off.get("sent_at")
                if (off["rx_observed_mono"] is not None or sent is None
                        or response["request_at"]["mono"] < sent["mono"]):
                    continue
                if off["baseline"] is None:
                    # Early emergency may precede any usable PTT evidence.
                    # Establish a server baseline, then require advancement.
                    off["baseline"] = when
                    continue
                if value is False and when > off["baseline"]:
                    off["rx_observed_mono"] = received
                    off["rx_observed_ms"] = (received - off["started"]) * 1000
                    for key in self.key_intervals:
                        if key.get("off") is off:
                            key["rx_observed_mono"] = received
                    self.log.emit("unkey_rx_observed", cycle=off.get("cycle"),
                                  action=off["action"], observed_ms=off["rx_observed_ms"],
                                  within_2s=received <= off["started"] + 2.0)

    def watchdog(self):
        while not self.poll_stop.wait(0.005):
            now = time.monotonic()
            reason = None
            with self.lock:
                for key in self.key_intervals:
                    off = key.get("off")
                    hold_end = off.get("sent_at", {}).get("mono") if off else None
                    if (hold_end or now) - key["started"] > 0.600:
                        reason = "600ms key-down request-to-unkey request limit exceeded"
                        break
                for off in self.off_requests:
                    observed_at = off["rx_observed_mono"]
                    if (not off["deadline_failed"] and now >= off["started"] + 2.0
                            and (observed_at is None or observed_at > off["started"] + 2.0)):
                        off["deadline_failed"] = True
                        reason = f"{off['action']}: no fresh RX within 2s of OFF request"
                        break
            if self.possible_tx(now) > MAX_TOTAL:
                reason = "10s summed possible-TX budget exhausted"
            if reason is not None:
                self.fail(reason)

    def sim_vox_missing(self, state):
        value, status = field(state, "voxOn")
        return (self.args.sim and self.sim_vox_unobserved and value is None
                and status.get("availability") == "missing"
                and status.get("observed") is not True
                and status.get("lastObservedMonotonic") is None)

    def fail(self, reason):
        with self.lock:
            if self.error is None:
                self.error = str(reason)
                self.log.emit("safety_failure", cycle=self.cycle_id, error=self.error)
        self.stop.set()

    def read(self, cycle=None, *, deadline=None):
        state_response = self.client.request("GET", STATE, cycle=cycle, action="state",
                                             deadline=deadline)
        if (self.generation is not None
                and state_response["body"].get("providerGeneration") != self.generation):
            raise Unsafe("provider generation changed during bench")
        # Capture even a brief RX sample before another HTTP read can hide it.
        self.capture_rx(state_response)
        managed = self.client.request("GET", MANAGED, cycle=cycle,
                                      action="managed", deadline=deadline)["body"]
        sample = {"state": state_response["body"], "managed": managed,
                  "state_request_mono": state_response["request_at"]["mono"],
                  "received_mono": time.monotonic()}
        self.log.emit("snapshot", cycle=cycle, **sample)
        with self.lock:
            self.latest = sample
        return sample

    def require_hf(self, sample, where):
        """Owner constraint 2026-10-28: transmit on HF only (below 30 MHz); VHF/UHF never."""
        if self.args.sim:
            return
        good, value, _, status = observed(sample["state"], "main.freqHz")
        ok = (good and type(value) in {int, float} and math.isfinite(value)
              and 0 < value < HF_LIMIT_HZ)
        self.log.emit("hf_check", where=where, frequency_hz=value, accepted=ok,
                      observation=status)
        if not ok:
            raise Unsafe(f"{where}: radio frequency {value!r} is not a fresh read-back "
                         f"below {HF_LIMIT_HZ} Hz; refusing to key")

    def require_idle(self, sample, *, minimum=False):
        state, managed = sample["state"], sample["managed"]
        if (not observed(state, "ptt", False, exact=True)[0]
                or managed.get("txObservation", {}).get("observedPtt") != "off"
                or not debt_free(managed)):
            raise Unsafe("requires fresh radio RX, observed TX off, and no release owed")
        _, status = field(state, "voxOn")
        if (status.get("availability") not in {None, "undeclared", "unavailable"}
                and not self.sim_vox_missing(state)
                and not observed(state, "voxOn", False, exact=True)[0]):
            raise Unsafe("exposed VOX state must be fresh and off")
        clients = state.get("wsClients", {})
        if isinstance(clients, dict) and any(
                type(count) in {int, float} and count > 0 for count in clients.values()):
            raise Unsafe("public state exposes active WebSocket clients")
        if minimum:
            good, power, _, _ = observed(state, "powerLevel")
            if not good or type(power) not in {int, float} or power != self.minimum:
                raise Unsafe("requires fresh RF power at exact verified profile minimum")

    def wait_ready(self, action, *, power_after=None, minimum=False):
        """Wait for acquisition before safety decisions; never call while keyed."""
        deadline = time.monotonic() + 10.0
        pending = ["first complete state/managed snapshot"]
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise Unsafe(self.error or "interrupted")
            try:
                sample = self.read(self.cycle_id, deadline=deadline)
            except ReadinessDeadline as exc:
                pending.append(str(exc))
                break
            if self.stop.is_set():
                raise Unsafe(self.error or "interrupted")
            if time.monotonic() >= deadline:
                break
            state, managed = sample["state"], sample["managed"]
            paths = ("ptt", "powerLevel")
            vox_status = field(state, "voxOn")[1]
            if (vox_status.get("availability") not in {None, "undeclared", "unavailable"}
                    and not self.sim_vox_missing(state)):
                paths += ("voxOn",)
            pending = []
            for path in paths:
                good, value, when, status = observed(state, path)
                valid_type = (type(value) in {int, float} and math.isfinite(value)
                              if path == "powerLevel" else type(value) is bool)
                if not good or not valid_type:
                    pending.append(
                        f"{path}: value={value!r}, availability={status.get('availability')!r}, "
                        f"observed={status.get('observed')!r}, freshness={status.get('freshness')!r}, "
                        f"source={status.get('source')!r}, lastObservedMonotonic={when!r}"
                        + (" (simulator declares VOX but has no fresh radio observation)"
                           if self.args.sim and path == "voxOn" and not good else ""))
            view = managed.get("managedTransmit", {})
            prerequisites = (
                ("managedTransmit.status", view.get("status"), {"available"}),
                ("managedTransmit.intent.kind", view.get("intent", {}).get("kind"),
                 {"rx", "ptt", "transmit"}),
                ("txObservation.observedPtt", managed.get("txObservation", {}).get("observedPtt"),
                 {"off", "on"}))
            for name, value, allowed in prerequisites:
                if value not in allowed:
                    pending.append(f"{name}={value!r}; expected one of {sorted(allowed)!r}")
            if type(view.get("releaseRequired")) is not bool:
                pending.append(f"managedTransmit.releaseRequired={view.get('releaseRequired')!r}; "
                               "expected bool")
            if not pending:
                self.require_idle(sample)
                _, power, when, _ = observed(state, "powerLevel")
                if power_after is not None and when <= power_after:
                    pending.append(f"powerLevel observation has not advanced: {when!r} <= {power_after!r}")
                if minimum and power != self.minimum:
                    pending.append(f"powerLevel={power!r}; exact profile minimum={self.minimum!r}")
                if not pending:
                    return sample
            if self.stop.wait(min(0.250, max(0, deadline - time.monotonic()))):
                raise Unsafe(self.error or "interrupted")
        # Only a never-observed, missing VOX may be exempted, and only after
        # the full acquisition wait. Reacquire all other evidence afterwards;
        # stale/true/otherwise invalid VOX must still fail closed.
        if (self.args.sim and not self.sim_vox_unobserved
                and pending and all(item.startswith("voxOn:") for item in pending)):
            self.sim_vox_unobserved = True
            if self.sim_vox_missing(sample["state"]):
                marker = "SIM: VOX not simulated"
                print(marker)
                self.sim_adaptations.append(marker)
                self.log.emit("sim_adaptation", marker=marker,
                              observation=field(sample["state"], "voxOn"))
                return self.wait_ready(action, power_after=power_after, minimum=minimum)
            self.sim_vox_unobserved = False
        raise Unsafe(f"{action}: required fresh radio/managed observations unavailable within 10s"
                     + (" at exact profile minimum" if minimum else "")
                     + "; pending: " + "; ".join(pending))

    def poll(self):
        due = time.monotonic()
        while not self.poll_stop.is_set():
            try:
                sample = self.read(self.cycle_id)
                release_since = self.release_since
                good, value, ptt_when, _ = observed(sample["state"], "ptt")
                if (release_since is not None and good and value is True
                        and sample["state_request_mono"] >= release_since + 1.0
                        and sample_observed_after(sample, ptt_when, release_since + 1.0)):
                    self.fail("fresh radio PTT true 1s after OFF in current release epoch")
                    return
            except Exception as exc:
                self.fail(f"state poll: {type(exc).__name__}: {exc}")
                return
            due += 0.05
            # A slow network is recorded as missed cadence, never delays scheduler.
            if time.monotonic() > due + 0.05:
                self.log.emit("poll_late", cycle=self.cycle_id,
                              late_ms=(time.monotonic() - due) * 1000)
                due = time.monotonic()
            self.poll_stop.wait(max(0, due - time.monotonic()))

    def launch(self, operation, action, *, body=None, positive=False, target=None):
        entry = {"action": action, "started": time.monotonic(), "result": None,
                 "positive": positive, "done": threading.Event(),
                 "sent": threading.Event(), "cycle": self.cycle_id}
        cycle = self.cycle_id
        self.log.emit("command_launch", cycle=cycle, action=action,
                      scheduled_mono=target, actual_mono=entry["started"],
                      slip_ms=None if target is None else (entry["started"] - target) * 1000)
        if positive:
            if self.stop.is_set():
                raise Unsafe("positive command blocked after safety failure")
            with self.lock:
                self.last_positive = entry["started"]
                self.release_since = None
                self.key_intervals.append(entry)
        elif operation == "force_off":
            self.register_off(entry)

        def work():
            def on_sent(when):
                with self.lock:
                    entry["sent_at"] = when
                    if operation == "force_off":
                        entry["baseline"] = self.ptt_watermark
                entry["sent"].set()
            try:
                entry["result"] = self.client.request(
                    "POST", COMMAND if body is None else "/api/v1/commands",
                    {"operation": operation} if body is None else body,
                    cycle=cycle, action=action, on_sent=on_sent)
            except Exception as exc:
                if positive:
                    self.uncertain_positive = True
                self.fail(f"{action}: {type(exc).__name__}: {exc}")
            finally:
                entry["completed"] = time.monotonic()
                entry["done"].set()

        thread = threading.Thread(target=work, name=f"bench-{action}", daemon=False)
        entry["thread"] = thread
        self.workers.append(entry)
        thread.start()
        return entry

    def wait_until(self, due):
        if self.stop.wait(max(0, due - time.monotonic())):
            raise Unsafe(self.error or "interrupted")

    def join_requests(self, entries):
        for entry in entries:
            while not entry["done"].wait(0.02):
                if self.stop.is_set():
                    raise Unsafe(self.error or "interrupted")

    def wait_sent(self, entry):
        while not entry["sent"].wait(0.005):
            if self.stop.is_set() or entry["done"].is_set():
                raise Unsafe(self.error or f"{entry['action']} was not sent")

    def current(self):
        with self.lock:
            return self.latest

    def settle(self, after, baseline, deadline):
        """Require two advancing radio RX observations after the OFF request.

        Server monotonic clocks are compared only with other server observations,
        never with the client's monotonic clock or managed sampledAt timestamp.
        """
        first = None
        last_sample = None
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise Unsafe(self.error or "interrupted")
            sample = self.current()
            if sample is not None and sample is not last_sample:
                last_sample = sample
                good, value, when, _ = observed(sample["state"], "ptt")
                if (good and value is False
                        and sample["state_request_mono"] >= after
                        and (baseline is None or when > baseline)):
                    if first is None:
                        first = when
                    elif when > first and debt_free(sample["managed"]):
                        return sample
                # An older unkey epoch cannot flag an intentional later rekey.
                # This check runs only after the FINAL off; no key remains scheduled.
                if (time.monotonic() >= after + 1.0 and good and value is True
                        and sample["state_request_mono"] >= after + 1.0
                        and sample_observed_after(sample, when, after + 1.0)):
                    raise Unsafe("radio still reports fresh PTT true 1s after final OFF")
            time.sleep(0.01)
        raise Unsafe("no fresh radio RX plus cleared release debt within OFF budget")

    def wait_off_observed(self, entries):
        while any(entry["rx_observed_mono"] is None for entry in entries):
            self.wait_until(time.monotonic() + 0.005)
        if any(entry["rx_observed_ms"] > 2000 for entry in entries):
            raise Unsafe("fresh RX arrived beyond the 2s OFF deadline")

    def preflight(self):
        runtime = self.client.request("GET", "/api/v1/runtime", action="runtime")["body"]
        sample = self.wait_ready("initial preflight")
        state, managed = sample["state"], sample["managed"]
        self.generation = state.get("providerGeneration")
        profile_name = "ic7610" if self.args.sim else self.args.profile
        rigs_dir = Path(self.args.rigs_dir) if self.args.rigs_dir else repo_rigs_dir()
        profile_path = rigs_dir / f"{profile_name}.toml"
        with profile_path.open("rb") as source:
            profile = tomllib.load(source)
        control = profile["controls"]["rf_power"]
        raw_min, raw_max = control["raw_min"], control["raw_max"]
        if type(raw_min) is not int or type(raw_max) is not int or raw_max <= raw_min:
            raise Unsafe("unsupported RF profile range")
        # Public Icom level is raw/255. Read the native minimum from this checkout.
        if raw_max != 255:
            raise Unsafe("only the inspected Icom raw_255 normalization is supported")
        minimum = raw_min / 255.0
        self.minimum = minimum
        actual_model = runtime.get("radio", {}).get("model")
        if actual_model != profile["radio"]["model"]:
            raise Unsafe(f"server model {actual_model!r} does not match {profile_name}")
        if runtime.get("radio", {}).get("connected") is not True:
            raise Unsafe("radio is not connected")
        good, value, _, _ = observed(state, "ptt")
        if not good or value is not False or not debt_free(managed):
            raise Unsafe("preflight requires fresh radio PTT false and no release owed")
        vox, vox_status = field(state, "voxOn")
        vox_exposed = vox_status.get("availability") not in {None, "undeclared", "unavailable"}
        if (vox_exposed and not self.sim_vox_missing(state)
                and (vox is not False or not observed(state, "voxOn")[0])):
            raise Unsafe("exposed VOX state must be fresh and off")
        # Current runtime audioCapture reports only lastFailureCategory. It has no
        # active microphone indicator, so never infer an idle mic from that field.
        capture = runtime.get("audioCapture", {})
        mic_flag = capture.get("active")
        if mic_flag is True:
            raise Unsafe("runtime reports an active microphone")
        ws_clients = state.get("wsClients", {})
        if isinstance(ws_clients, dict) and any(
                type(count) in {int, float} and count > 0 for count in ws_clients.values()):
            raise Unsafe("public state exposes active WebSocket clients")
        bridge = runtime.get("bridge", {})
        if bridge.get("enabled") is True or bridge.get("running") is True:
            raise Unsafe("runtime exposes an enabled/running audio bridge")
        playback = runtime.get("audioPlayback", {})
        if (type(playback.get("activeClients")) in {int, float}
                and playback["activeClients"] > 0):
            raise Unsafe("runtime exposes active audio WebSocket clients")
        session = runtime.get("audioSession", {})
        if session.get("enabled") is True and session.get("state") not in {None, "idle", "stopped"}:
            raise Unsafe("runtime exposes an active audio session")
        for section in (runtime, runtime.get("process", {}), runtime.get("audioBus", {}).get("stats", {})):
            for name in ("ws_clients", "wsClients", "controlClients", "audioClients", "txClients"):
                if type(section.get(name)) in {int, float} and section[name] > 0:
                    raise Unsafe(f"runtime exposes active {name}")
        if self.armed and not self.args.sim and not self.args.no_clients_confirmed:
            raise Unsafe("runtime cannot prove absence of live WebSocket/mic clients; "
                         "--no-clients-confirmed is required")
        if self.args.sim and mic_flag is not False:
            marker = "SIM: client microphone not observable; isolated stack creates no mic clients"
            print(marker)
            self.sim_adaptations.append(marker)
        self.log.emit("preflight", runtime=runtime, profile=profile_name,
                      profile_path=str(profile_path), raw_minimum=raw_min,
                      normalized_minimum=minimum, live=self.armed and not self.args.sim,
                      vox_exposed=vox_exposed, mic_observability="no active mic field",
                      sim_adaptations=list(self.sim_adaptations),
                      current_power=field(state, "powerLevel"),
                      minimum_advisory_only=not self.armed)
        if self.armed:
            if self.stop.is_set():
                raise Unsafe(self.error or "interrupted")
            sample = self.wait_ready("before minimum-power write")
            self.require_hf(sample, "preflight")
            baseline = observed(sample["state"], "powerLevel")[2]
            self.client.request("POST", "/api/v1/commands", {
                "name": "set_rf_power", "params": {"level": float(minimum),
                                                       "level_unit": "normalized"}},
                action="set_profile_minimum")
            sample = self.wait_ready("minimum-power readback", power_after=baseline,
                                     minimum=True)
            observation = field(sample["state"], "powerLevel")
            self.log.emit("minimum_verified", profile=profile_name, raw_minimum=raw_min,
                          normalized_minimum=minimum, observed_value=observation[0],
                          observation=observation)
            if self.args.sim:
                marker = "SIM: min-power check adapted"
                print(f"{marker}: IC-7610 profile {profile_path}; "
                      f"raw minimum={raw_min}, normalized minimum={minimum}, "
                      f"radio-sourced observed value={observation[0]}; "
                      f"requested live profile {self.args.profile} was not validated.")
                self.log.emit("sim_minimum_adaptation", marker=marker,
                              profile=profile_name, requested_live_profile=self.args.profile,
                              raw_minimum=raw_min, normalized_minimum=minimum,
                              observed_value=observation[0], observation=observation)

    def cycle(self, number, extra):
        self.cycle_id = number
        if self.args.run == 4:
            return self.cycle_inflight(number)
        delay = 0.0 if self.args.run == 2 else 0.150
        reserve = 0.300 + delay + 0.050 + 0.200
        if MAX_TOTAL - self.possible_tx() < reserve:
            raise Unsafe("conservative 10s total budget cannot accommodate another cycle")
        snapshot = self.wait_ready("before key-down")
        self.require_idle(snapshot, minimum=True)
        self.require_hf(snapshot, f"cycle {number} before key-down")
        good, value, baseline, _ = observed(snapshot["state"], "ptt")
        if not good or value is not False or not debt_free(snapshot["managed"]):
            raise Unsafe("next cycle requires fresh radio RX and no release owed")
        extra_body = None
        if extra:
            good_power, power, _, _ = observed(snapshot["state"], "powerLevel")
            if not good_power or type(power) not in {int, float} or power != self.minimum:
                raise Unsafe("run3 requires fresh RF power at verified profile minimum")
            extra_body = {"name": "set_rf_power", "params": {
                "level": float(power), "level_unit": "normalized"}}
        started = time.monotonic()
        entries = []
        first_off = second_off = None
        row = None
        try:
            entries.append(self.launch("transmit_on", "key", positive=True, target=started))
            if extra:
                self.wait_until(started + 0.260)
                entries.append(self.launch(None, "same_power_write", body=extra_body,
                                           target=started + 0.260))
            self.wait_until(started + 0.300)
            first_off = self.launch("force_off", "unkey", target=started + 0.300)
            entries.append(first_off)
            self.wait_until(started + 0.300 + delay)
            # Establish client send ordering without awaiting OFF's HTTP reply.
            # Separate TCP connections still cannot prove server execution order.
            self.wait_sent(first_off)
            before_rekey = self.current()
            off_seen = first_off["rx_observed_mono"] is not None
            self.log.emit("first_unkey_epoch", cycle=number,
                          fresh_radio_rx_observed=off_seen,
                          verdict=(verdict(before_rekey["managed"]) if off_seen else "uncertain"),
                          note="Rekey is intentional even without RX; OFF's independent 2s deadline remains active.",
                          state=before_rekey["state"], managed=before_rekey["managed"])
            entries.append(self.launch("transmit_on", "rekey", positive=True,
                                       target=started + 0.300 + delay))
            self.wait_until(started + 0.350 + delay)
            good_baseline, _, final_baseline, _ = observed(self.current()["state"], "ptt")
            final_off = self.launch("force_off", "final_unkey", target=started + 0.350 + delay)
            second_off = final_off
            entries.append(final_off)
            if not good_baseline:
                raise Unsafe("PTT observation disappeared before final OFF; OFF requested")
            self.join_requests(entries)
            # A positive request finishing after final OFF could have registered
            # after its fence. Always close that HTTP-arrival race with a barrier.
            if any(e["positive"] and e["completed"] > final_off["started"] for e in entries):
                final_baseline = observed(self.current()["state"], "ptt")[2]
                final_off = self.launch("force_off", "positive_completion_barrier_off")
                entries.append(final_off)
                self.join_requests([final_off])
            self.wait_off_observed([entry for entry in entries if not entry["positive"]
                                    and "rx_observed_mono" in entry])
            sample = self.settle(final_off["started"], final_baseline,
                                 final_off["started"] + 8.0)
            if self.possible_tx() > MAX_TOTAL:
                raise Unsafe("10s summed possible-TX budget exhausted")
            elapsed = time.monotonic() - started
            keys = [e for e in entries if e["positive"]]
            ordered = [next(e for e in entries if e["action"] == action)
                       for action in ("key", "unkey", "rekey", "final_unkey")]
            sent_times = [e["sent_at"]["mono"] for e in ordered]
            first_hold_ms = (sent_times[1] - sent_times[0]) * 1000
            rekey_gap_ms = (sent_times[2] - sent_times[1]) * 1000
            second_hold_ms = (sent_times[3] - sent_times[2]) * 1000
            if any((off["sent_at"]["mono"] - key["started"]) > 0.600
                   for key, off in zip((ordered[0], ordered[2]), (ordered[1], ordered[3]))):
                raise Unsafe("600ms key-down request-to-unkey request limit exceeded")
            extra_gap_ms = (None if not extra else
                (sent_times[1] - next(e["sent_at"]["mono"] for e in entries
                                     if e["action"] == "same_power_write")) * 1000)
            sequence_valid = sent_times == sorted(sent_times)
            timing_valid = (sequence_valid and 275 <= first_hold_ms <= 325
                            and 25 <= second_hold_ms <= 75
                            and ((0 <= rekey_gap_ms <= 25) if delay == 0
                                 else (125 <= rekey_gap_ms <= 175))
                            and (not extra or 30 <= extra_gap_ms <= 50))
            row = {"cycle": number, "variant": "extra-write" if extra else "control",
                   "key": "accepted" if keys[0]["result"]["accepted"] else "refused",
                   "rekey": "accepted" if keys[1]["result"]["accepted"] else "refused",
                   "verdict": verdict(sample["managed"]),
                   "final_release_verdict": verdict(sample["managed"]),
                   "first_unkey_fresh_rx_observed": off_seen,
                   "off_ms": round((sample["received_mono"] - final_off["started"]) * 1000),
                   "cycle_span_ms": round(elapsed * 1000),
                   "ack_ambiguity": "not established by HTTP timing",
                   "client_send_sequence_valid": sequence_valid,
                   "timing_valid": timing_valid,
                   "timing_tolerances_ms": {"first_hold": [275, 325], "second_hold": [25, 75],
                                            "rekey_gap": [0, 25] if delay == 0 else [125, 175],
                                            "extra_before_unkey": [30, 50] if extra else None},
                   "client_sent_mono": dict(zip((e["action"] for e in ordered), sent_times)),
                   "client_unkey_rekey_gap_ms": rekey_gap_ms,
                   "client_key_first_unkey_hold_ms": first_hold_ms,
                   "client_second_hold_ms": second_hold_ms,
                   "client_send_order_note": "client byte submission only; server/CI-V execution order unproven",
                   "extra_write_before_unkey_ms": extra_gap_ms,
                   "state": sample["state"], "managed": sample["managed"]}
        finally:
            if row is None:
                keys = [entry for entry in entries if entry["positive"]]
                outcomes = [("accepted" if entry["result"]["accepted"] else "refused")
                            if entry["result"] is not None else "unknown" for entry in keys]
                row = {"cycle": number, "variant": "extra-write" if extra else "control",
                       "key": outcomes[0] if outcomes else "unknown",
                       "rekey": outcomes[1] if len(outcomes) > 1 else "not_sent",
                       "verdict": "uncertain", "timing_valid": False, "off_ms": None,
                       "aborted": True, "error": self.error,
                       "cycle_span_ms": round((time.monotonic() - started) * 1000)}
            row.update(first_unkey_rx_observed_ms=(None if first_off is None else
                                                   first_off["rx_observed_ms"]),
                       second_unkey_rx_observed_ms=(None if second_off is None else
                                                    second_off["rx_observed_ms"]),
                       total_possible_tx_ms=self.possible_tx() * 1000)
            self.rows.append(row)
            self.log.emit("cycle_summary", **row)

    def cycle_inflight(self, number):
        """Run 4: force_off while the key is still in flight (MOR-3838 supersede).

        transmit_on at t=0, force_off at t=10 ms by client send order, without
        awaiting replies. Either the accept or the supersede-reject of the key
        is valid evidence -- PASS requires only the safety outcome: fresh
        radio RX within the 2 s OFF deadline, releaseRequired false with
        intent rx, observed PTT off, no stuck TX. No rekey, no second hold.
        """
        if MAX_TOTAL - self.possible_tx() < 0.300:
            raise Unsafe("conservative 10s total budget cannot accommodate another cycle")
        snapshot = self.wait_ready("before key-down")
        self.require_idle(snapshot, minimum=True)
        self.require_hf(snapshot, f"cycle {number} before key-down")
        good, value, baseline, _ = observed(snapshot["state"], "ptt")
        if not good or value is not False or not debt_free(snapshot["managed"]):
            raise Unsafe("next cycle requires fresh radio RX and no release owed")
        started = time.monotonic()
        entries = []
        first_off = None
        off = None
        row = None
        try:
            key_entry = self.launch("transmit_on", "key", positive=True, target=started)
            entries.append(key_entry)
            self.wait_until(started + 0.010)
            first_off = self.launch("force_off", "unkey", target=started + 0.010)
            off = first_off
            entries.append(off)
            self.join_requests(entries)
            # A positive request finishing after the OFF could have registered
            # after its fence. Close that HTTP-arrival race with a barrier.
            if key_entry["completed"] > first_off["started"]:
                final_baseline = observed(self.current()["state"], "ptt")[2]
                off = self.launch("force_off", "positive_completion_barrier_off")
                entries.append(off)
                self.join_requests([off])
            else:
                final_baseline = observed(self.current()["state"], "ptt")[2]
            if final_baseline is None:
                raise Unsafe("PTT observation disappeared before OFF settled; OFF requested")
            self.wait_off_observed([off])
            sample = self.settle(off["started"], final_baseline, off["started"] + 8.0)
            if self.possible_tx() > MAX_TOTAL:
                raise Unsafe("10s summed possible-TX budget exhausted")
            elapsed = time.monotonic() - started
            sent_key = key_entry["sent_at"]["mono"]
            sent_off = first_off["sent_at"]["mono"]
            hold_ms = (sent_off - sent_key) * 1000
            sequence_valid = sent_off >= sent_key
            timing_valid = sequence_valid and 0 <= hold_ms <= 100
            row = {"cycle": number, "variant": "inflight-force-off",
                   "key": "accepted" if key_entry["result"]["accepted"] else "refused",
                   "rekey": "n/a",
                   "verdict": verdict(sample["managed"]),
                   "final_release_verdict": verdict(sample["managed"]),
                   "key_evidence_required": False,
                   "off_ms": round((sample["received_mono"] - off["started"]) * 1000),
                   "cycle_span_ms": round(elapsed * 1000),
                   "ack_ambiguity": "not established by HTTP timing",
                   "client_send_sequence_valid": sequence_valid,
                   "timing_valid": timing_valid,
                   "timing_tolerances_ms": {"key_to_unkey_hold": [0, 100]},
                   "client_sent_mono": {"key": sent_key, "unkey": sent_off},
                   "client_key_unkey_hold_ms": hold_ms,
                   "client_send_order_note": "client byte submission only; server/CI-V execution order unproven",
                   "state": sample["state"], "managed": sample["managed"]}
        finally:
            if row is None:
                key_entry = entries[0] if entries else None
                outcome = "unknown"
                if key_entry is not None and key_entry["result"] is not None:
                    outcome = ("accepted" if key_entry["result"]["accepted"] else "refused")
                row = {"cycle": number, "variant": "inflight-force-off",
                       "key": outcome, "rekey": "n/a",
                       "verdict": "uncertain", "timing_valid": False, "off_ms": None,
                       "aborted": True, "error": self.error,
                       "cycle_span_ms": round((time.monotonic() - started) * 1000)}
            row.update(first_unkey_rx_observed_ms=(None if first_off is None else
                                                  first_off["rx_observed_ms"]),
                       second_unkey_rx_observed_ms=None,
                       total_possible_tx_ms=self.possible_tx() * 1000)
            self.rows.append(row)
            self.log.emit("cycle_summary", **row)

    def emergency(self, reason):
        if not self.armed:
            return
        print(f"\n!!! BENCH ABORT: {reason}; requesting FORCE OFF !!!", file=sys.stderr)
        self.log.emit("emergency", cycle=self.cycle_id, reason=str(reason))
        # Stop every scheduled positive action. HTTP workers already in flight
        # cannot be cancelled safely; wait for completion and then fence again.
        self.stop.set()

        def retry_off(action):
            for attempt in range(3):
                off = self.launch("force_off", f"{action}_{attempt + 1}")
                deadline = off["started"] + 2.0
                first = None
                while time.monotonic() < deadline:
                    try:
                        sample = self.read(self.cycle_id, deadline=deadline)
                        good, value, when, _ = observed(sample["state"], "ptt")
                        if good and value is False and off["rx_observed_mono"] is not None:
                            if first is None:
                                first = when
                            elif (when > first and debt_free(sample["managed"])
                                  and sample["managed"].get("txObservation", {}).get("observedPtt") == "off"):
                                return sample
                    except Exception as exc:
                        self.log.emit("cleanup_error", action=off["action"], error=str(exc))
                    self.possible_tx()
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                self.log.emit("cleanup_off_unconfirmed", action=off["action"],
                              rx_observed_ms=off["rx_observed_ms"])
            return None

        retry_off("emergency_off")
        for entry in list(self.workers):
            entry["thread"].join()
        # Timed-out HTTP transmit_on can enter the server after earlier OFF.
        # Its production admission lifetime is 10s; continue OFF through it.
        horizon = ((self.last_positive or time.monotonic()) + 10.0
                   if self.uncertain_positive else time.monotonic())
        while True:
            sample = retry_off("cleanup_barrier_off")
            if time.monotonic() >= horizon:
                break
            time.sleep(0.200)
        if sample is not None:
            self.log.emit("emergency_rx_observed", cycle=self.cycle_id,
                          state=sample["state"], managed=sample["managed"])
            return
        print("!!! RX IS UNCONFIRMED: OWNER MUST VERIFY RADIO / MANUALLY UNKEY !!!",
              file=sys.stderr)
        self.log.emit("cleanup_rx_unconfirmed", cycle=self.cycle_id)

    def run(self):
        self.armed = bool(self.args.sim or (self.args.tx_armed
                          and bool(os.environ.get("BENCH_OWNER_YES", "").strip())
                          and self.args.checklist_confirmed))
        poller = watcher = None
        handlers = {}
        log_offset = None
        if self.args.server_log:
            log_offset = Path(self.args.server_log).stat().st_size

        def interrupted(signum, _frame):
            self.fail(f"signal {signum}")

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, interrupted)
            self.log.emit("meta", run=self.args.run, label=self.args.label,
                          server=self.args.url, sim=self.args.sim, armed=self.armed,
                          note="No owner environment value is recorded. HTTP is not CI-V timing.")
            self.preflight()
            self.log.emit("plan", cycles=self.args.cycles, hold_ms=300, second_hold_ms=50,
                          rekey_gap_ms=0 if self.args.run == 2 else 150,
                          cycle_gap_s=self.args.cycle_gap,
                          run3="alternating control and same-RF-minimum write at unkey-minus-40ms",
                          run4="in-flight force_off at key-plus-10ms, no rekey; "
                          "keying itself is not required evidence",
                          total_possible_on_budget_s=MAX_TOTAL)
            if not self.armed:
                print("DRY / READ ONLY: preflight and plan saved; no writes or TX.")
                return 0
            poller = threading.Thread(target=self.poll, name="bench-state-poll", daemon=False)
            watcher = threading.Thread(target=self.watchdog, name="bench-watchdog", daemon=False)
            watcher.start()
            poller.start()
            for number in range(1, self.args.cycles + 1):
                self.cycle(number, self.args.run == 3 and number % 2 == 0)
                if number < self.args.cycles:
                    self.wait_until(time.monotonic() + self.args.cycle_gap)
            return 0 if all(row["timing_valid"] for row in self.rows) else 1
        except BaseException as exc:
            self.fail(f"{type(exc).__name__}: {exc}")
            self.emergency(self.error)
            return 1
        finally:
            self.poll_stop.set()
            if poller is not None:
                poller.join()
            if watcher is not None:
                watcher.join()
            for entry in self.workers:
                entry["thread"].join()
            if self.args.server_log and log_offset is not None:
                try:
                    with Path(self.args.server_log).open("rb") as source:
                        source.seek(log_offset)
                        for line in source.read().decode("utf-8", errors="replace").splitlines():
                            if re.search(r"release.*attempt|not.confirmed|read.?back|uncertain", line, re.I):
                                self.log.emit("server_log", text=line,
                                              correlation="run window only; not a cycle attribution")
                except OSError as exc:
                    self.log.emit("server_log_error", error=str(exc))
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
            self.log.emit("summary", completed_cycles=len(self.rows),
                          total_possible_tx_ms=self.possible_tx() * 1000, error=self.error,
                          timing_invalid_cycles=[row["cycle"] for row in self.rows
                                                 if not row["timing_valid"]],
                          dry=not self.armed)
            print("cycle variant       key      rekey    verdict   timing final_off_ms")
            for row in self.rows:
                print(f"{row['cycle']:>5} {row['variant']:<13} {row['key']:<8} "
                      f"{row['rekey']:<8} {row['verdict']:<9} "
                      f"{'valid' if row['timing_valid'] else 'BAD':<6} {str(row['off_ms']):>12}")
            print(f"JSONL: {self.log.path}")


def simulated(args, journal):
    # Exactly the production-stack helpers used by the API regression. No
    # test-local shortened deadlines, fake clocks, or forged minimum evidence.
    root = REPO_ROOT
    for path in (root, root / "src", root / "tests", root / "tests" / "integration"):
        sys.path.insert(0, str(path))
    from integration.test_tx_under_fault_matrix import _stand_up_stack, _tear_down_stack

    ready = concurrent.futures.Future()

    async def serve():
        stack = None
        with tempfile.TemporaryDirectory(prefix="bench-tx-sim-") as temporary:
            try:
                stack = await _stand_up_stack(Path(temporary), loss="hold",
                                              tot_seconds=30, unsolicited=True)
                done = asyncio.Event()
                ready.set_result((stack.web.port, asyncio.get_running_loop(), done))
                await done.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    journal.emit("sim_error", error=f"{type(exc).__name__}: {exc}")
            finally:
                if stack is not None:
                    await _tear_down_stack(stack)

    thread = threading.Thread(target=lambda: asyncio.run(serve()),
                              name="bench-isolated-simulator", daemon=False)
    thread.start()
    try:
        port, loop, done = ready.result()
        args.url = f"http://127.0.0.1:{port}"
        print("SIMULATOR ONLY: isolated IC-7610 production stack, profile ic7610.")
        return run_bench(args, journal)
    finally:
        def shutdown(completed):
            if completed.exception() is None:
                _, loop, done = completed.result()
                loop.call_soon_threadsafe(done.set)
        ready.add_done_callback(shutdown)
        thread.join()


def run_bench(args, journal):
    return Bench(args, journal).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--label", required=True)
    parser.add_argument("--run", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--profile", choices=("ic705", "ic7300"), default="ic705")
    parser.add_argument("--rigs-dir", default=None,
                        help="profile directory; defaults to the rigs/ of the "
                        "checkout this file lives in")
    parser.add_argument("--cycle-gap", type=float, default=3.0)
    # Fewer cycles only (never more): the 10 s summed possible-TX budget is NOT widened.
    parser.add_argument("--cycles", type=int, choices=range(1, 11), default=10)
    parser.add_argument("--tx-armed", action="store_true")
    parser.add_argument("--checklist-confirmed", action="store_true")
    parser.add_argument("--no-clients-confirmed", action="store_true")
    parser.add_argument("--server-log")
    parser.add_argument("--sim", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.cycle_gap) or args.cycle_gap < 0:
        parser.error("--cycle-gap must be finite and nonnegative")
    if args.sim and args.url != "http://127.0.0.1:8080":
        parser.error("--sim owns an ephemeral localhost URL; do not pass --url")
    journal = Journal(args)
    try:
        return simulated(args, journal) if args.sim else run_bench(args, journal)
    except BaseException as exc:
        journal.emit("fatal", error=f"{type(exc).__name__}: {exc}")
        print(f"BENCH FAILED: {type(exc).__name__}: {exc}\nJSONL: {journal.path}", file=sys.stderr)
        return 1
    finally:
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
