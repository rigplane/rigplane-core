"""One resident process per radio per computer (single-instance lock).

Resident CLI commands (``web``, ``station``, ``serve``) hold the radio's
session for the life of the process. Two such processes on one computer
against one radio fight over the radio's single LAN session, so a second
start for the same radio is refused here, before any packet is sent to the
radio and before any listener binds.

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
    "lock_file_for_identity",
    "radio_lock_identity",
]

#: Commands that hold the radio's session for the life of the process.
RESIDENT_COMMANDS: frozenset[str] = frozenset({"web", "station", "serve"})


class ResidentAlreadyRunningError(Exception):
    """Another resident process already serves this radio on this computer."""

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


def radio_lock_identity(config: BackendConfig) -> str:
    """Return the stable lock identity for a backend config.

    LAN and rigctld radios are keyed by ``host:port`` — the radio's network
    endpoint, which is what owns the radio's single LAN session. Login
    credentials are deliberately excluded: different credentials still reach
    the same radio session. Serial radios are keyed by the device path as
    configured; the path is used verbatim (not canonicalized) because the
    CLI passes it through to the transport unchanged, so identical resident
    invocations — the misconfiguration this guards — always map to the same
    identity.
    """
    if isinstance(config, LanBackendConfig):
        return f"lan:{config.host.strip().lower()}:{config.port}"
    if isinstance(config, RigctldBackendConfig):
        return f"rigctld:{config.host.strip().lower()}:{config.port}"
    if isinstance(config, (SerialBackendConfig, YaesuCatBackendConfig)):
        return f"serial:{config.device.strip()}"
    raise TypeError(f"unsupported backend config: {type(config).__name__}")


def lock_file_for_identity(
    identity: str, *, cache_dir: str | Path | None = None
) -> Path:
    """Return the lock-file path for a radio identity.

    The file lives under ``platformdirs.user_cache_path("rigplane")`` in a
    ``locks/`` subdirectory, named from the SHA-256 digest of the identity
    (endpoint strings can contain path-unsafe characters, so the raw
    identity is never used as a file name).
    """
    base = (
        Path(cache_dir)
        if cache_dir is not None
        else Path(platformdirs.user_cache_path("rigplane"))
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return base / "locks" / f"{digest}.lock"


def _read_holder_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        return int(text.split()[0])
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
        handle: BinaryIO = open(path, "a+b")
    except OSError as exc:
        logger.warning(
            "single-instance guard unavailable (%s); starting without it", exc
        )
        return None
    try:
        _lock_nonblocking(handle)
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
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    lock = ResidentLock(identity, path, handle)
    _HELD[str(path)] = lock
    return lock


def _lock_nonblocking(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ResidentAlreadyRunningError(_read_holder_pid_for(handle)) from exc
            raise
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise ResidentAlreadyRunningError(_read_holder_pid_for(handle)) from exc
        raise


def _read_holder_pid_for(handle: BinaryIO) -> int | None:
    try:
        name = getattr(handle, "name", None)
        if name is None:
            return None
        return _read_holder_pid(Path(str(name)))
    except (OSError, ValueError):
        return None
