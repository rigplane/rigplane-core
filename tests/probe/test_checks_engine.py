"""Write-check engine behaviour (MOR-4464 B1, RED first).

The engine runs after the read pass inside the same run: preconditions,
a stack of originals with rehearsal, witness snapshots, retry-once
restore, cancel restore-first, ask timeouts, and a journal contract.
The wire below is scripted; the fakes sit behind the CLI seam tests.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from rigplane.profiles.probe_checks import (
    ProbeCheck,
    ProbeCheckStep,
    ProbePrecondition,
    ProbeRestoreItem,
)

FA_HOME = b"FA014074000;"
FA_NEW = b"FA007074000;"
MODE_HOME = b"MD02;"
MODE_NEW = b"MD01;"


def _freq_mode_check(**overrides) -> ProbeCheck:
    fields = dict(
        id="synth-freq-mode",
        title="Synthetic frequency and mode",
        explain_what="Sets VFO-A to 7.074 MHz for a moment, then reads it back.",
        explain_after="Puts frequency and mode back exactly as they are now.",
        warnings=(),
        preconditions=(),
        witness=("get_freq", "get_mode"),
        steps=(
            ProbeCheckStep(
                kind="write", command="set_freq", values=(("freq", 7074000),)
            ),
            ProbeCheckStep(kind="read", command="get_freq"),
        ),
        restore=(
            ProbeRestoreItem(read="get_freq", write="set_freq"),
            ProbeRestoreItem(read="get_mode", write="set_mode"),
        ),
    )
    fields.update(overrides)
    return ProbeCheck(**fields)  # type: ignore[arg-type]


def _cat_config(check: ProbeCheck):
    from rigplane.profiles.rig_loader import load_rig

    rigs = Path(__file__).resolve().parents[2] / "rigs"
    base = load_rig(rigs / "ftx1.toml")
    return dataclasses.replace(base, probe_checks=(check,))


class ScriptedWire:
    """One reply queue per request body; empty queue means silence."""

    def __init__(self, script: dict[bytes, list]) -> None:
        self._script = {body: list(queue) for body, queue in script.items()}
        self.sent: list[bytes] = []

    def _queued(self, request: bytes):
        queue = self._script.get(request, [])
        if not queue:
            return None
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def __call__(self, request: bytes | None, timeout: float):
        if request is None:
            return None
        self.sent.append(bytes(request))
        return self._queued(request)


class StatefulScriptedWire(ScriptedWire):
    """Models FA/MD0 state like a radio: writes apply, reads answer it.

    Queued outcomes still decide write answers (``?;`` refuses without
    applying, ``None`` applies silently); an explicitly emptied queue
    silences the read. Optional *clamp* maps a write line to the canned
    line the radio stores instead.
    """

    def __init__(
        self,
        script: dict[bytes, list],
        *,
        freq: int = 14074000,
        mode: str = "2",
        clamp: dict[str, str] | None = None,
    ) -> None:
        super().__init__(script)
        self._freq = freq
        self._mode = mode
        self._clamp = clamp or {}

    async def __call__(self, request: bytes | None, timeout: float):
        if request is None:
            return None
        self.sent.append(bytes(request))
        try:
            line = request.decode("ascii")
        except UnicodeDecodeError:
            return self._queued(request)
        if line in ("FA;", "MD0;"):
            queue = self._script.get(request, [])
            if request in self._script and not queue:
                return None
            if line == "FA;":
                return f"FA{self._freq:09d};".encode("ascii")
            return f"MD0{self._mode};".encode("ascii")
        if line.startswith("FA") and line.endswith(";") and len(line) > 3:
            queued = self._queued(request)
            if queued is not None:
                return queued
            stored = self._clamp.get(line, line)
            self._freq = int(stored[2:-1])
            return None
        if line.startswith("MD0") and line.endswith(";") and len(line) > 4:
            queued = self._queued(request)
            if queued is not None:
                return queued
            self._mode = line[3:-1]
            return None
        return self._queued(request)


def _standard_script() -> dict[bytes, list]:
    # Queues cover every read the engine sends: witness-before, originals,
    # rehearsal re-read, step reads, witness-after, restore re-reads.
    return {
        b"FA;": [FA_HOME] * 6,
        b"MD0;": [MODE_HOME] * 5,
        b"TX;": [b"TX0;", b"TX0;"],
        b"FA007074000;": [None],
        b"FA014074000;": [None, None],
        b"MD02;": [None, None],
    }


async def _run(
    check: ProbeCheck,
    script: dict[bytes, list],
    tmp_path: Path,
    *,
    consent: str = "y",
    answers: list[str | None] | None = None,
    on_event=None,
    ask_timeout_s: float = 60.0,
    clamp: dict[str, str] | None = None,
):
    from rigplane.probe.checks import run_checks

    wire = StatefulScriptedWire(script, clamp=clamp)
    emitted: list[str] = []
    answer_iter = iter(answers or [])

    async def ask(prompt: str):
        return next(answer_iter)

    (result, _) = await run_checks_and_wire(
        check, wire, tmp_path, consent, ask, emitted, on_event, ask_timeout_s
    )
    return result, wire, emitted


async def run_checks_and_wire(
    check, wire, tmp_path, consent, ask, emitted, on_event, ask_timeout_s
):
    from rigplane.probe.checks import run_checks

    journal = tmp_path / "probe.json.checks-journal.json"
    result = await run_checks(
        config=_cat_config(check),
        exchange=wire,
        gap_s=0.0,
        timeout_s=0.5,
        journal_path=journal,
        consent=lambda prompt: consent,
        ask=ask,
        emit=emitted.append,
        on_event=on_event,
        ask_timeout_s=ask_timeout_s,
    )
    return result, journal


def test_stack_order_of_restore(tmp_path: Path) -> None:
    async def scenario() -> None:
        result, wire, _ = await _run(_freq_mode_check(), _standard_script(), tmp_path)
        (record,) = result.records
        assert record.restored is True
        assert record.verified is True
        assert result.exit_code == 0
        assert wire.sent == [
            b"FA;",
            b"MD0;",
            b"FA;",
            b"MD0;",
            b"FA014074000;",
            b"FA;",
            b"MD02;",
            b"MD0;",
            b"FA007074000;",
            b"FA;",
            b"FA;",
            b"MD0;",
            b"MD02;",
            b"MD0;",
            b"FA014074000;",
            b"FA;",
        ]

    asyncio.run(scenario())


def test_rehearsal_refusal_aborts_with_nothing_changed(tmp_path: Path) -> None:
    async def scenario() -> None:
        script = _standard_script()
        script[b"FA014074000;"] = [b"?;"]
        result, wire, _ = await _run(_freq_mode_check(), script, tmp_path)
        (record,) = result.records
        assert record.verified is True
        assert record.restored is True
        assert b"FA007074000;" not in wire.sent
        assert result.exit_code == 0

    asyncio.run(scenario())


def test_unanswered_restore_read_writes_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        script = _standard_script()
        script[b"FA;"] = []
        result, wire, _ = await _run(
            _freq_mode_check(witness=()), script, tmp_path
        )
        (record,) = result.records
        assert wire.sent == [b"FA;"]
        assert record.error is not None
        assert result.exit_code == 0

    asyncio.run(scenario())


def test_precondition_abort_names_vfo_mode(tmp_path: Path) -> None:
    async def scenario() -> None:
        check = _freq_mode_check(
            preconditions=(ProbePrecondition(read="get_ptt", equals="TX0;"),)
        )
        script = _standard_script()
        script[b"TX;"] = [b"TX1;"]
        result, wire, _ = await _run(check, script, tmp_path)
        (record,) = result.records
        assert record.preconditions_ok is False
        assert record.error == (
            "Switch the radio to VFO mode (not a memory channel) "
            "and run the check again. Nothing was changed."
        )
        assert wire.sent == [b"TX;"]

    asyncio.run(scenario())


def test_witness_difference_marks_unverified_naming_field(tmp_path: Path) -> None:
    async def scenario() -> None:
        check = _freq_mode_check(witness=("get_freq", "get_mode", "get_ptt"))
        script = _standard_script()
        script[b"TX;"] = [b"TX0;", b"TX1;"]
        result, _wire, _ = await _run(check, script, tmp_path)
        (record,) = result.records
        assert record.verified is False
        assert "get_ptt" in (record.error or "")

    asyncio.run(scenario())


def test_refused_restore_write_retried_once_then_warning_exit_4(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        script = _standard_script()
        script[b"FA014074000;"] = [None, b"?;", b"?;"]
        result, wire, emitted = await _run(_freq_mode_check(), script, tmp_path)
        (record,) = result.records
        assert wire.sent.count(b"FA014074000;") == 3
        assert record.restored is False
        assert record.verified is False
        assert result.exit_code == 4
        assert any("WARNING" in line for line in emitted)
        journal = tmp_path / "probe.json.checks-journal.json"
        assert journal.exists()

    asyncio.run(scenario())


def test_clamped_write_is_unverified_with_both_values_in_file(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        script = _standard_script()
        result, _wire, _ = await _run(
            _freq_mode_check(),
            script,
            tmp_path,
            clamp={"FA007074000;": "FA005000000;"},
        )
        (record,) = result.records
        assert record.verified is False
        assert record.restored is True
        assert result.exit_code == 0
        step = next(
            row for row in record.steps if row.request_hex == b"FA007074000;".hex()
        )
        assert step.reply_text == "FA014074000;"

    asyncio.run(scenario())


def test_ask_timeout_recorded_as_skipped_restore_still_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        check = _freq_mode_check(
            steps=(
                ProbeCheckStep(
                    kind="write", command="set_freq", values=(("freq", 7074000),)
                ),
                ProbeCheckStep(kind="ask", question="Look at the radio?"),
            )
        )

        async def hang(prompt: str):
            await asyncio.Event().wait()

        from rigplane.probe.checks import run_checks

        wire = ScriptedWire(_standard_script())
        emitted: list[str] = []
        journal = tmp_path / "probe.json.checks-journal.json"
        result = await run_checks(
            config=_cat_config(check),
            exchange=wire,
            gap_s=0.0,
            timeout_s=0.5,
            journal_path=journal,
            consent=lambda prompt: "y",
            ask=hang,
            emit=emitted.append,
            ask_timeout_s=0.05,
        )
        (record,) = result.records
        assert record.answers[0].skipped is True
        assert record.answers[0].answer is None
        assert record.restored is True
        assert record.verified is True
        assert not journal.exists()

    asyncio.run(scenario())


def test_ctrl_c_during_check_restores_first(tmp_path: Path) -> None:
    async def scenario() -> None:
        check = _freq_mode_check(
            steps=(
                ProbeCheckStep(
                    kind="write", command="set_freq", values=(("freq", 7074000),)
                ),
                ProbeCheckStep(
                    kind="write", command="set_mode", values=(("mode", "1"),)
                ),
            )
        )
        seen: list[str] = []

        def hook(point: str) -> None:
            seen.append(point)
            if point == "step_write_begin" and seen.count("step_write_begin") == 2:
                raise KeyboardInterrupt()

        result, wire, _ = await _run(
            check, _standard_script(), tmp_path, on_event=hook
        )
        (record,) = result.records
        assert record.restored is True
        assert b"FA014074000;" in wire.sent
        assert "cancel" in (record.error or "").lower()

    asyncio.run(scenario())


def test_second_ctrl_c_during_restore_is_ignored(tmp_path: Path) -> None:
    async def scenario() -> None:
        def hook(point: str) -> None:
            if point == "restore_begin":
                raise KeyboardInterrupt()

        result, _wire, _ = await _run(
            _freq_mode_check(), _standard_script(), tmp_path, on_event=hook
        )
        (record,) = result.records
        assert record.restored is True
        assert record.verified is True
        assert result.exit_code == 0

    asyncio.run(scenario())


def test_link_dies_mid_check_leaves_journal_and_exit_4(tmp_path: Path) -> None:
    async def scenario() -> None:
        boom = OSError("link died")
        script = _standard_script()
        script[b"FA007074000;"] = [boom]
        inner = StatefulScriptedWire(script)

        class BrokenWire:
            def __init__(self) -> None:
                self.sent = inner.sent
                self._broken = False

            async def __call__(self, request: bytes | None, timeout: float):
                if self._broken:
                    raise OSError("link died")
                try:
                    return await inner(request, timeout)
                except OSError:
                    self._broken = True
                    raise

        from rigplane.probe.checks import run_checks

        emitted: list[str] = []
        journal = tmp_path / "probe.json.checks-journal.json"

        async def ask(prompt: str):
            return None

        result = await run_checks(
            config=_cat_config(_freq_mode_check()),
            exchange=BrokenWire(),
            gap_s=0.0,
            timeout_s=0.5,
            journal_path=journal,
            consent=lambda prompt: "y",
            ask=ask,
            emit=emitted.append,
        )
        (record,) = result.records
        assert record.restored is False
        assert result.exit_code == 4
        assert journal.exists()

    asyncio.run(scenario())


def test_verified_restore_deletes_the_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        await _run(_freq_mode_check(), _standard_script(), tmp_path)
        journal = tmp_path / "probe.json.checks-journal.json"
        assert not journal.exists()

    asyncio.run(scenario())


def test_sigterm_during_check_restores_first(tmp_path: Path) -> None:
    import os
    import signal

    class SigtermWire(StatefulScriptedWire):
        def __init__(self, script: dict[bytes, list]) -> None:
            super().__init__(script)
            self._killed = False

        async def __call__(self, request: bytes | None, timeout: float):
            if (
                request is not None
                and len(self.sent) == 1
                and not self._killed
            ):
                self._killed = True
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.Event().wait()
            return await super().__call__(request, timeout)

    async def scenario() -> None:
        from rigplane.probe.checks import run_checks

        wire = SigtermWire(_standard_script())
        emitted: list[str] = []
        journal = tmp_path / "probe.json.checks-journal.json"

        async def ask(prompt: str):
            return None

        result = await run_checks(
            config=_cat_config(_freq_mode_check()),
            exchange=wire,
            gap_s=0.0,
            timeout_s=0.5,
            journal_path=journal,
            consent=lambda prompt: "y",
            ask=ask,
            emit=emitted.append,
        )
        (record,) = result.records
        assert record.restored is True
        assert record.verified is True
        assert "cancel" in (record.error or "").lower()

    if os.name != "posix":
        pytest.skip("POSIX signals only")
    asyncio.run(scenario())
