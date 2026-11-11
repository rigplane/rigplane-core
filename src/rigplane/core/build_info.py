"""Typed contract for the stamped build-info file (MOR-4408).

``scripts/release/stamp_build_info.py`` (the one producer, used by the Core
wheel workflow and by Pro's source-checkout freeze) writes
``_build_info.json`` next to this module. :class:`BuildInfo` is the
contract; :func:`load_build_info` is the single reader.

The reader never raises into the server: a missing or malformed file reports
unknown (:data:`UNKNOWN_BUILD_INFO`). There is deliberately no runtime
``git`` fallback — an installed package inside an unrelated git repository
would report the wrong repository (``diagnostics/_manifest.py``'s
``git describe`` build id is a separate, diagnostic-only mechanism).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_MAX_REF_LENGTH = 200

_BUILD_INFO_FILENAME = "_build_info.json"


@dataclass(frozen=True)
class BuildInfo:
    """Build provenance stamped into a checkout before packaging.

    ``git_sha`` is the 40-hex commit the artifact was built from;
    ``requested_ref`` is the ref the consumer asked for (tag, branch or SHA).
    Both are ``None`` for source checkouts and PyPI wheels, which are never
    stamped.
    """

    git_sha: str | None
    requested_ref: str | None


UNKNOWN_BUILD_INFO = BuildInfo(git_sha=None, requested_ref=None)


def render_build_info(info: BuildInfo) -> str:
    """Serialize ``info`` to the on-disk JSON form."""
    return json.dumps(
        {"git_sha": info.git_sha, "requested_ref": info.requested_ref},
        indent=2,
        sort_keys=True,
    )


def write_build_info(info: BuildInfo, path: Path) -> None:
    """Atomically write ``info`` to ``path``; refuse incomplete data loudly.

    The producer side is strict: a stamped file that would read back as
    unknown must fail the build, not pass silently.
    """
    _validate(info, strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(render_build_info(info))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def load_build_info(path: Path | None = None) -> BuildInfo:
    """Read the stamped build info; unknown on any malformed input."""
    target = (
        path if path is not None else Path(__file__).with_name(_BUILD_INFO_FILENAME)
    )
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError):
        return UNKNOWN_BUILD_INFO
    if not isinstance(data, dict):
        return UNKNOWN_BUILD_INFO
    info = BuildInfo(
        git_sha=data.get("git_sha"),
        requested_ref=data.get("requested_ref"),
    )
    try:
        _validate(info, strict=False)
    except ValueError:
        return UNKNOWN_BUILD_INFO
    return info


def _validate(info: BuildInfo, *, strict: bool) -> None:
    """Shared field rules: a valid sha is always required, ``strict``
    additionally requires a ref (the file must carry provenance to report)."""
    if not isinstance(info.git_sha, str) or not _SHA_RE.fullmatch(info.git_sha):
        raise ValueError(
            f"BuildInfo.git_sha must be exactly 40 lowercase hex characters: {info.git_sha!r}"
        )
    if info.requested_ref is None:
        if strict:
            raise ValueError("BuildInfo.requested_ref must name the requested ref")
        return
    if (
        not isinstance(info.requested_ref, str)
        or not info.requested_ref
        or "\n" in info.requested_ref
        or "\r" in info.requested_ref
        or len(info.requested_ref) > _MAX_REF_LENGTH
    ):
        raise ValueError(
            f"BuildInfo.requested_ref must be a short single-line ref name: {info.requested_ref!r}"
        )
