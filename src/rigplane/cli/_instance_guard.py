"""One rigplane process per radio per computer (single-instance lock).

CLI commands with a LAN or local-serial radio hold the radio's exclusive
session — resident commands (``web``, ``station``, ``serve``) for the life
of the process, one-shot commands for the run. Two such processes on one
computer against one radio fight over that single session, so a second
process for the same explicitly named radio (``--host``/``--serial-port``)
is refused here, before the radio is opened and before any listener binds.
When the radio is not named explicitly, discovery broadcasts or serial
probes run first — the lock identity is unknown until discovery answers —
and the guard is taken once the backend config is built. The rigctld
backend is never guarded: an external rigctld accepts several clients, so
there is no exclusive session to protect.

The OS file lock is the authority: it is released by the OS when the holder
exits, including SIGKILL, so a restart after a crash is never blocked. The
pid written into the lock file is only a hint for the refusal message — a
stale pid is never treated as authority, and no lock file is ever deleted.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

import platformdirs

from rigplane.backends.config import (
    BackendConfig,
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

__all__ = [
    "RESIDENT_COMMANDS",
    "ResidentAlreadyRunningError",
    "ResidentLock",
    "acquire_resident_lock",
    "guards_backend",
    "lock_file_for_identity",
    "radio_lock_identity",
]

#: Commands that hold the radio's session for the life of the process.
RESIDENT_COMMANDS: frozenset[str] = frozenset({"web", "station", "serve"})


class ResidentAlreadyRunningError(Exception):
    """Another rigplane process already holds this radio on this computer."""

    def __init__(self, pid: int | None) -> None:
        self.holder_pid = pid
        if pid is None:
            super().__init__(
                "another rigplane process is already serving this radio "
                "on this computer; stop it first"
            )
        else:
            super().__init__(
                f"another rigplane process (pid {pid}) is already serving "
                "this radio on this computer; stop it first"
            )


def guards_backend(config: BackendConfig) -> bool:
    """Whether commands with this backend take the single-instance guard.

    Only backends that hold an exclusive radio session — LAN Icom and local
    serial radios (including yaesu-cat) — are guarded. The rigctld backend
    talks to an external rigctld that accepts several clients, so there is
    no session conflict to prevent.
    """
    return isinstance(
        config, (LanBackendConfig, SerialBackendConfig, YaesuCatBackendConfig)
    )


def radio_lock_identity(config: BackendConfig) -> str:
    """Return the stable lock identity for a backend config.

    LAN radios are keyed by ``host:port`` — the radio's network endpoint,
    which is what owns the radio's single LAN session. Login credentials
    are deliberately excluded: different credentials still reach the same
    radio session. Serial radios are keyed by the real path of the device
    (``os.path.realpath``), so a ``/dev/serial/by-id/…`` symlink and its
    target map to the same lock. Raises ``TypeError`` for backends without
    an exclusive session (rigctld) and for unknown configs.
    """
    if isinstance(config, LanBackendConfig):
        return f"lan:{config.host.strip().lower()}:{config.port}"
    if isinstance(config, (SerialBackendConfig, YaesuCatBackendConfig)):
        return f"serial:{os.path.realpath(config.device.strip())}"
    if isinstance(config, RigctldBackendConfig):
        raise TypeError(
            "rigctld backend takes no resident lock: "
            "an external rigctld accepts several clients"
        )
    raise TypeError(f"unsupported backend config: {type(config).__name__}")


def _default_lock_dir() -> Path:
    """Default lock directory (test seam: tests redirect this per test)."""
    return Path(platformdirs.user_cache_path("rigplane"))


def lock_file_for_identity(
    identity: str, *, cache_dir: str | Path | None = None
) -> Path:
    """Return the lock-file path for a radio identity.

    The file lives under ``platformdirs.user_cache_path("rigplane")`` in a
    ``locks/`` subdirectory, named from the SHA-256 digest of the identity
    (endpoint strings can contain path-unsafe characters, so the raw
    identity is never used as a file name).
    """
    base = Path(cache_dir) if cache_dir is not None else _default_lock_dir()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return base / "locks" / f"{digest}.lock"


#: Offset of the single guarded byte. The pid hint is written starting at
#: _PID_OFFSET so a refused process can read it without touching the byte
#: another process locked (on Windows, reading a locked byte fails).
_LOCK_BYTE_OFFSET = 0
_PID_OFFSET = 1


def _read_holder_pid(path: Path) -> int | None:
    try:
        with open(path, "rb") as stream:
            stream.seek(_PID_OFFSET)
            raw = stream.read()
    except OSError:
        return None
    try:
        return int(raw.decode("utf-8", errors="replace").strip().split()[0])
    except (ValueError, IndexError):
        return None


class ResidentLock:
    """An acquired per-radio resident lock, held until closed or exit."""

    def __init__(self, identity: str, path: Path, handle: BinaryIO) -> None:
        self.identity = identity
        self.path = path
        self.pid = os.getpid()
        self._handle = handle
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if _HELD.get(str(self.path)) is self:
                del _HELD[str(self.path)]
            try:
                self._handle.close()
            except OSError:
                pass

    def __enter__(self) -> ResidentLock:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


_HELD: dict[str, ResidentLock] = {}


def acquire_resident_lock(
    identity: str, *, cache_dir: str | Path | None = None
) -> ResidentLock | None:
    """Take the exclusive non-blocking lock for *identity*.

    Returns the held lock, or ``None`` (after logging one warning) when the
    lock directory or file cannot be created — the guard is a safety net,
    not a gate on a working setup. Raises ResidentAlreadyRunningError when
    another process holds the lock.

    The guard is per process: if this process already holds the lock for the
    same lock file, the held lock is returned instead of opening a second
    file description (which the OS would refuse). Acquired locks are kept in
    a module-level registry for the process lifetime; the OS still releases
    the underlying lock on exit, including SIGKILL.
    """
    path = lock_file_for_identity(identity, cache_dir=cache_dir)
    held = _HELD.get(str(path))
    if held is not None and not held.closed:
        return held
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_CREAT without O_TRUNC (never "w+b"): when two processes start
        # against a radio for the first time at once, the loser must open
        # the winner's just-created file — not truncate it. No placeholder
        # byte is written before locking: the fixed lock byte is guarded
        # first, and locking past end-of-file is allowed (on Windows the
        # locked region may extend beyond EOF).
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o666)
        try:
            handle: BinaryIO = os.fdopen(fd, "r+b")
        except OSError:
            os.close(fd)
            raise
    except OSError as exc:
        logger.warning(
            "single-instance guard unavailable (%s); starting without it", exc
        )
        return None
    try:
        _lock_nonblocking(handle, path)
    except ResidentAlreadyRunningError:
        try:
            handle.close()
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            handle.close()
        except OSError:
            pass
        logger.warning(
            "single-instance guard unavailable (%s); starting without it", exc
        )
        return None
    try:
        # The pid hint lives outside the locked byte so a refused process
        # can still read it.
        handle.seek(_PID_OFFSET)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
    except OSError as exc:
        # The pid is only a hint for the refusal message; the lock itself
        # is already held, so keep it and start with a warning.
        logger.warning(
            "single-instance guard pid hint unwritable (%s); holding the lock",
            exc,
        )
    lock = ResidentLock(identity, path, handle)
    _HELD[str(path)] = lock
    return lock


def _lock_nonblocking(handle: BinaryIO, path: Path) -> None:
    # The lock-file path is passed explicitly: a handle opened with
    # os.fdopen reports an integer fd as its name, not the path, so the
    # holder pid is read from the known path instead.
    if sys.platform == "win32":
        # Lock one byte at a FIXED offset: every process guards the same
        # byte however long the pid hint after it grows.
        handle.seek(_LOCK_BYTE_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ResidentAlreadyRunningError(_read_holder_pid(path)) from exc
            raise
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise ResidentAlreadyRunningError(_read_holder_pid(path)) from exc
        raise
