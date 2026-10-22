"""Full-CI worker evidence (MOR-3407), adapted from qualified private observer.

Opt in with RIGPLANE_WORKER_DIAG_DIR and -p pytest_worker_diagnostics.
Snapshots contain filenames/functions/line numbers, never locals or source text.
Parameters, task names, environment values and exception text are not retained.
Latest 120/240-second snapshots replace older ones; append logs stop at 1 MiB.
Raw fatal/timeout files are capped after reap and again before artifact upload.
--finalize limits the upload to 32 MiB, 2 MiB/file and 256 known files, recording
truncation in bounds.json. Incomplete/truncated evidence is not a PASS verdict.
"""

from __future__ import annotations
import asyncio
import itertools
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
import re
from dataclasses import dataclass
import pytest

_MAX_DUMP_BYTES = 256 * 1024
_MAX_TASKS = 64
_MAX_TASK_FRAMES = 8
_MAX_THREAD_FRAMES = 128
_STATE = pytest.StashKey()


class _BoundedText:
    def __init__(self) -> None:
        self.data = bytearray()
        self.truncated = False
        self.marker = b"\n[diagnostic output truncated]\n"

    def add(self, text: str) -> None:
        if self.truncated:
            return
        encoded = text.encode("utf-8", errors="replace")
        remaining = _MAX_DUMP_BYTES - len(self.marker) - len(self.data)
        if len(encoded) > remaining:
            self.data.extend(encoded[:remaining])
            self.truncated = True
        else:
            self.data.extend(encoded)

    def finish(self) -> bytes:
        if self.truncated:
            self.data.extend(self.marker)
        return bytes(self.data)


@dataclass
class _Case:
    sequence: int
    nodeid: str
    started: float
    stop: threading.Event
    thread: threading.Thread | None = None


def _frame_line(frame) -> str:
    return (
        json.dumps(
            {
                "file": frame.f_code.co_filename,
                "function": frame.f_code.co_name,
                "line": frame.f_lineno,
            },
            ensure_ascii=True,
        )
        + "\n"
    )


def _discover_loop(main_frame):
    frame = main_frame
    try:
        for _ in range(_MAX_THREAD_FRAMES):
            if frame is None:
                break
            for key in ("_loop", "self"):
                try:
                    candidate = frame.f_locals.get(key)
                    if (
                        isinstance(candidate, asyncio.AbstractEventLoop)
                        and candidate.is_running()
                        and not candidate.is_closed()
                    ):
                        return candidate
                except BaseException:
                    pass
            frame = frame.f_back
    except BaseException:
        pass
    finally:
        del frame
    return None


class _Diagnostics:
    def __init__(self, directory: Path, worker: str) -> None:
        self.directory = directory
        self.worker = worker
        self.pid = os.getpid()
        self.run_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.sequence = 0
        self.current: _Case | None = None
        self.events_path = directory / f"events-{self.pid}.jsonl"

    def _event(self, kind: str, case: _Case) -> None:
        try:
            now = time.monotonic()
            record = {
                "resources": _own_resources(),
                "event": kind,
                "nodeid": case.nodeid,
                "worker": self.worker,
                "pid": self.pid,
                "sequence": case.sequence,
                "monotonic": now,
                "elapsed_seconds": now - case.started,
            }
            if kind == "END":
                record["duration_seconds"] = now - case.started
            line = json.dumps(record, ensure_ascii=True) + "\n"
            with self.lock:
                _append_bounded(self.events_path, line)
        except BaseException:
            pass

    def start(self, nodeid: str) -> _Case:
        with self.lock:
            self.sequence += 1
            case = _Case(
                self.sequence, _safe_nodeid(nodeid), time.monotonic(), threading.Event()
            )
            self.current = case
        self._event("START", case)
        try:
            case.thread = threading.Thread(
                target=self._watch,
                args=(case,),
                name="pytest-worker-observer",
                daemon=True,
            )
            case.thread.start()
        except BaseException:
            case.thread = None
        return case

    def finish(self, case: _Case) -> None:
        case.stop.set()
        self._event("END", case)
        with self.lock:
            if self.current is case:
                self.current = None
        try:
            if case.thread is not None:
                case.thread.join(timeout=0.05)
        except BaseException:
            pass

    def _watch(self, case: _Case) -> None:
        try:
            for threshold in (120, 240):
                delay = max(0.0, threshold - (time.monotonic() - case.started))
                if case.stop.wait(delay) or case.stop.is_set():
                    return
                self._snapshot(case, threshold)
        except BaseException:
            pass

    def _snapshot(self, case: _Case, threshold: int) -> None:
        output = _BoundedText()
        frames = main_frame = loop = None
        try:
            output.add(
                json.dumps(
                    {
                        "event": "SNAPSHOT",
                        "resources": _own_resources(),
                        "nodeid": case.nodeid,
                        "worker": self.worker,
                        "pid": self.pid,
                        "sequence": case.sequence,
                        "scheduled_elapsed_seconds": threshold,
                        "elapsed_seconds": time.monotonic() - case.started,
                        "test_end_signalled": case.stop.is_set(),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
            frames = sys._current_frames()
            main_ident = threading.main_thread().ident
            main_frame = frames.get(main_ident)
            for ident, top_frame in sorted(frames.items()):
                if output.truncated:
                    break
                output.add(f"\nTHREAD ident={ident} main={ident == main_ident}\n")
                frame = top_frame
                try:
                    for _ in range(_MAX_THREAD_FRAMES):
                        if frame is None or output.truncated:
                            break
                        output.add(_frame_line(frame))
                        frame = frame.f_back
                    if frame is not None and not output.truncated:
                        output.add("[thread frame limit reached]\n")
                except BaseException:
                    output.add("[thread stack unavailable]\n")
                finally:
                    del frame
            del top_frame
            loop = _discover_loop(main_frame)
            if loop is None:
                output.add("\nASYNCIO active main-thread loop unavailable\n")
            elif not output.truncated:
                output.add("\nASYNCIO best-effort task snapshot\n")
                try:
                    tasks = asyncio.all_tasks(loop)
                    output.add(f"observed_task_count={len(tasks)}\n")
                    for index, task in enumerate(itertools.islice(tasks, _MAX_TASKS)):
                        if output.truncated:
                            break
                        output.add(f"\nTASK index={index}\n")
                        task_frames = None
                        try:
                            output.add(
                                f"done={task.done()} cancelled={task.cancelled()}\n"
                            )
                            awaitable = task.get_coro()
                            for depth in range(24):
                                if awaitable is None or output.truncated:
                                    break
                                frame = (
                                    getattr(awaitable, "cr_frame", None)
                                    or getattr(awaitable, "gi_frame", None)
                                    or getattr(awaitable, "ag_frame", None)
                                )
                                output.add(
                                    f"AWAIT depth={depth} type={type(awaitable).__name__}\n"
                                )
                                if frame is not None:
                                    output.add(_frame_line(frame))
                                awaitable = (
                                    getattr(awaitable, "cr_await", None)
                                    or getattr(awaitable, "gi_yieldfrom", None)
                                    or getattr(awaitable, "ag_await", None)
                                )
                            if awaitable is not None:
                                output.add("[await-chain depth limit reached]\n")
                            awaitable = frame = None
                            task_frames = task.get_stack(limit=_MAX_TASK_FRAMES)
                            for task_frame in task_frames:
                                output.add(_frame_line(task_frame))
                            if not task_frames:
                                output.add("[no exposed task frames]\n")
                        except BaseException:
                            output.add("[task stack unavailable]\n")
                        finally:
                            task_frames = None
                    if len(tasks) > _MAX_TASKS:
                        output.add("[task limit reached]\n")
                    del tasks
                except BaseException:
                    output.add("[asyncio task snapshot unavailable]\n")
            path = self.directory / f"dump-{self.pid}-{threshold}.txt"
            with path.open("wb") as stream:
                stream.write(output.finish())
        except BaseException:
            pass
        finally:
            frames = main_frame = loop = None


def pytest_configure(config) -> None:
    if not os.environ.get("RIGPLANE_WORKER_DIAG_DIR"):
        return
    _configure_durable_exit(config)
    try:
        worker_input = getattr(config, "workerinput", None)
        if worker_input is None:
            return
        directory = Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        config.stash[_STATE] = _Diagnostics(
            directory, str(worker_input.get("workerid", "unknown"))
        )
    except BaseException:
        pass


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    diagnostics = case = None
    try:
        diagnostics = item.config.stash.get(_STATE, None)
        if diagnostics is not None:
            if item.nodeid.split("::", 1)[0].endswith("test_soak_driver.py"):
                _install_soak_phases()
            case = diagnostics.start(item.nodeid)
    except BaseException:
        pass
    try:
        return (yield)
    finally:
        try:
            if diagnostics is not None and case is not None:
                diagnostics.finish(case)
        except BaseException:
            pass


# Soak phase markers are installed when its test module starts.
def _own_resources():
    try:
        fields = {}
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.split(":", 1)[0] in ("VmRSS", "VmHWM", "Threads"):
                key, value = line.split(":", 1)
                fields[key] = value.strip()
        return fields
    except BaseException:
        return {"status": "UNKNOWN"}


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session):
    _enable_durable_fatal()


_PHASES_INSTALLED = False


def _install_soak_phases():
    global _PHASES_INSTALLED
    if _PHASES_INSTALLED:
        return
    _PHASES_INSTALLED = True
    import functools
    from scripts.soak import soak_driver as driver

    directory = Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"])
    path = directory / f"phases-{os.getpid()}.jsonl"

    def phase(kind, label):
        try:
            task = asyncio.current_task()
            record = {
                "kind": kind,
                "phase": label,
                "pid": os.getpid(),
                "monotonic": time.monotonic(),
                "resources": _own_resources(),
                "taskId": id(task) if task is not None else None,
            }
            _append_bounded(path, json.dumps(record) + "\n")
        except BaseException:
            pass

    def observe(cls, method, label):
        original = getattr(cls, method)

        @functools.wraps(original)
        async def wrapped(*args, **kwargs):
            phase("START", label)
            try:
                value = await original(*args, **kwargs)
            except BaseException as exc:
                phase(type(exc).__name__, label)
                raise
            else:
                phase("END", label)
                return value

        setattr(cls, method, wrapped)

    for cls, method, label in (
        (driver.SoakDriver, "_run_phases", "driver._run_phases"),
        (driver._SimulatorStack, "aclose", "stack.aclose"),
        (driver.RigctldServer, "stop", "rigctld.stop"),
        (driver.WebServer, "stop", "web.stop"),
        (driver.IcomRadio, "disconnect", "radio.disconnect"),
        (driver.MockIcomRadio, "stop", "sim.stop"),
    ):
        observe(cls, method, label)


# Tiny module-load delta: durable fatal/timeout output, available exit status.
_FATAL_STREAM = None


def _configure_durable_exit_impl(config):
    import faulthandler
    import pytest_timeout

    global _FATAL_STREAM
    directory = Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    worker = getattr(config, "workerinput", None)
    if worker is None:
        return
    worker_id = str(worker.get("workerid", "unknown"))
    _FATAL_STREAM = (directory / f"fatal-{worker_id}-{os.getpid()}.log").open("x")
    _FATAL_STREAM.write(
        json.dumps(
            {"event": "FATAL_WIRING_READY", "pid": os.getpid(), "worker": worker_id}
        )
        + "\n"
    )
    _FATAL_STREAM.flush()
    original = pytest_timeout.timeout_timer

    def durable_timeout(item, settings):
        try:
            path = directory / f"timeout-{worker_id}-{os.getpid()}.log"
            with path.open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "PYTEST_TIMEOUT_CALLBACK",
                            "nodeid": _safe_nodeid(item.nodeid),
                            "pid": os.getpid(),
                            "worker": worker_id,
                            "monotonic": time.monotonic(),
                            "resources": _own_resources(),
                        }
                    )
                    + "\n"
                )
                stream.flush()
                faulthandler.dump_traceback(file=stream, all_threads=True)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            pass
        return original(item, settings)

    pytest_timeout.timeout_timer = durable_timeout
    config.stash[_DURABLE_ORIGINAL] = original


_DURABLE_ORIGINAL = pytest.StashKey()


def _testnodedown_impl(node, error):
    directory = Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"])
    io = getattr(node.gateway, "_io", None)
    popen = getattr(io, "popen", None)
    code = getattr(popen, "returncode", None)
    record = {
        "event": "NODE_DOWN",
        "gateway": getattr(node.gateway, "id", None),
        "errorType": type(error).__name__ if error is not None else None,
        "controllerChildPid": getattr(popen, "pid", None),
        "availableReturncode": code,
        "signal": -code if isinstance(code, int) and code < 0 else None,
        "exitStatusEvidence": "AVAILABLE"
        if code is not None
        else "UNKNOWN_NOT_REAPED_BY_OBSERVER",
        "monotonic": time.monotonic(),
    }
    _append_bounded(directory / "controller-node-exit.jsonl", json.dumps(record) + "\n")


def _enable_durable_fatal_impl():
    if _FATAL_STREAM is None:
        return
    import faulthandler

    faulthandler.enable(file=_FATAL_STREAM, all_threads=True)
    _FATAL_STREAM.write(
        json.dumps({"event": "FATAL_SESSIONSTART_ENABLED", "pid": os.getpid()}) + "\n"
    )
    _FATAL_STREAM.flush()
    os.fsync(_FATAL_STREAM.fileno())


def pytest_xdist_newgateway(gateway):
    # Observe the existing execnet reaper; do not poll/wait/kill independently.
    io = getattr(gateway, "_io", None)
    if io is None or not hasattr(io, "wait"):
        return
    original_wait = io.wait

    def wait_and_record():
        try:
            code = original_wait()
        except BaseException:
            _record_reaped_exit(gateway, io, None, "ORIGINAL_WAIT_RAISED")
            raise
        _record_reaped_exit(gateway, io, code, "ORIGINAL_WAIT_RETURNED")
        return code

    io.wait = wait_and_record


def _record_reaped_exit(gateway, io, code, outcome):
    try:
        popen = getattr(io, "popen", None)
        record = {
            "event": "EXECNET_WAIT_COMPLETED",
            "gateway": gateway.id,
            "pid": getattr(popen, "pid", None),
            "returncode": code,
            "signal": -code if isinstance(code, int) and code < 0 else None,
            "waitOutcome": outcome,
            "evidence": "REAPED_AVAILABLE" if code is not None else "UNKNOWN",
            "monotonic": time.monotonic(),
        }
        path = (
            Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"])
            / "controller-reaped-exit.jsonl"
        )
        _append_bounded(path, json.dumps(record) + "\n")
        _cap_worker_raw_files(
            Path(os.environ["RIGPLANE_WORKER_DIAG_DIR"]), getattr(popen, "pid", None)
        )
    except BaseException:
        pass


def _configure_durable_exit(config):
    global _FATAL_STREAM
    try:
        _configure_durable_exit_impl(config)
    except BaseException:
        stream, _FATAL_STREAM = _FATAL_STREAM, None
        if stream is not None:
            try:
                stream.close()
            except BaseException:
                pass


def _enable_durable_fatal():
    try:
        _enable_durable_fatal_impl()
    except BaseException:
        pass


def pytest_testnodedown(node, error):
    try:
        _testnodedown_impl(node, error)
    except BaseException:
        pass


MAX_APPEND_BYTES = 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILES = 256
_KNOWN_FILE = re.compile(
    r"(?:events-|phases-|dump-|fatal-|timeout-|controller-)[A-Za-z0-9_.-]+\.(?:jsonl|txt|log)$"
)


def _safe_nodeid(nodeid):
    return nodeid.split("[", 1)[0][:512]


def _append_bounded(path, line):
    encoded = line.encode("utf-8", errors="replace")
    if len(encoded) > 8192:
        return
    marker = b'{"event":"TRUNCATED_APPEND_LOG"}\n'
    size = path.stat().st_size if path.exists() else 0
    if size > MAX_APPEND_BYTES - len(marker):
        return
    if size + len(encoded) > MAX_APPEND_BYTES - len(marker):
        encoded = marker
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _truncate(path, limit):
    marker = b"\n[TRUNCATED diagnostic evidence]\n"
    if path.stat().st_size > limit:
        with path.open("r+b") as stream:
            stream.truncate(limit)
            stream.seek(max(0, limit - len(marker)))
            stream.write(marker[:limit])
        return True
    return False


def _cap_worker_raw_files(directory, pid):
    if pid is not None:
        for pattern in (f"fatal-*-{pid}.log", f"timeout-*-{pid}.log"):
            for path in directory.glob(pattern):
                if not path.is_symlink():
                    _truncate(path, MAX_FILE_BYTES)


def finalize_directory(directory):
    """Bound known upload files; do not copy or inspect unrelated files."""
    directory = Path(directory)
    if not directory.is_dir():
        return
    records = []
    total = 0
    count = 0
    dropped = 0
    for path in sorted(directory.iterdir()):
        if (
            path.is_symlink()
            or not path.is_file()
            or not _KNOWN_FILE.fullmatch(path.name)
        ):
            continue
        if count >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            path.unlink()
            dropped += 1
            continue
        limit = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total)
        truncated = _truncate(path, limit)
        size = path.stat().st_size
        records.append({"file": path.name, "bytes": size, "truncated": truncated})
        total += size
        count += 1
    (directory / "bounds.json").write_text(
        json.dumps(
            {
                "files": records,
                "bytes": total,
                "droppedFiles": dropped,
                "limits": {
                    "file": MAX_FILE_BYTES,
                    "total": MAX_TOTAL_BYTES,
                    "count": MAX_FILES,
                },
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--finalize":
        try:
            finalize_directory(sys.argv[2])
        except BaseException:
            pass
