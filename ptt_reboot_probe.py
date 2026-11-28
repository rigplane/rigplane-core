"""Measurement-only pytest plugin for MOR-4930 (not committed to product branches).

Raises the reboot PTT-off budget to 90 s and prints, per run, one JSON line with
the elapsed time to PTT off, the simulator's PTT transitions, and the Core
warnings (session loss, reconnect, each managed TX release attempt) with their
monotonic timestamps relative to the key-on.
"""

from __future__ import annotations

import json
import os
import logging
import time

import pytest

_BUDGET_S = 90.0


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[tuple[float, str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        name = record.name
        if not name.startswith("rigplane"):
            return
        self.records.append((time.monotonic(), name.rsplit(".", 1)[-1], record.getMessage()[:160]))


@pytest.fixture(autouse=True)
def _ptt_reboot_probe(request, monkeypatch):
    if "reboot" not in request.node.name:
        yield
        return
    mod = request.module
    monkeypatch.setitem(mod._BUDGETS, "reboot", _BUDGET_S)
    original = mod._wait_ptt
    probe: dict = {"test": request.node.name, "budget_s": _BUDGET_S}
    cap = _Capture()
    logging.getLogger("rigplane").addHandler(cap)
    sims: list = []

    async def wait_ptt(sim, expected, deadline_s):
        sims.append(sim)
        start = time.monotonic()
        try:
            elapsed = await original(sim, expected, deadline_s)
        except AssertionError:
            probe.setdefault("waits", []).append(
                {"expected": expected, "elapsed_s": None, "deadline_s": deadline_s}
            )
            raise
        probe.setdefault("waits", []).append(
            {"expected": expected, "elapsed_s": round(time.monotonic() - start, 3)}
        )
        if expected:
            probe["key_on_t"] = time.monotonic()
        return elapsed

    monkeypatch.setattr(mod, "_wait_ptt", wait_ptt)
    try:
        yield
    finally:
        logging.getLogger("rigplane").removeHandler(cap)
        t0 = probe.get("key_on_t", cap.records[0][0] if cap.records else 0.0)
        if sims:
            probe["transitions"] = [
                (round(t - t0, 3) if isinstance(t, float) else t, v)
                for t, v in getattr(sims[-1], "ptt_transitions", [])
            ]
        probe["events"] = [
            (round(t - t0, 3), who, msg)
            for t, who, msg in cap.records
            if any(
                k in msg
                for k in (
                    "release attempt",
                    "reconnect",
                    "session free",
                    "OpenClose",
                    "discovery",
                    "no CI-V data",
                    "recover",
                    "Connected",
                    "login",
                )
            )
        ][:80]
        line = "PTTPROBE " + json.dumps(probe, default=str)
        with open(os.environ.get("PTTPROBE_OUT", "/tmp/pttprobe.jsonl"), "a") as fh:
            fh.write(line + "\n")

