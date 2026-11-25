#!/usr/bin/env python3
"""Stamp ``src/rigplane/core/_build_info.json`` into a Core checkout (MOR-4408).

The one producer of the build-info file, called by two consumers of the
contract:

- ``core-wheel.yml``'s build job, on the throwaway checkout of the resolved
  SHA, before ``uv build``;
- Pro's CI, which freezes Core from a sibling SOURCE checkout (editable
  install, no wheel) and calls this script from that checkout before
  packaging — see ``docs/internals/core-wheel-workflow.md``.

``--verify-file PATH --expect-sha SHA`` verifies an already-stamped file —
including one inside an installed wheel — through the same contract, and is
how the wheel workflow's installed-artifact smoke proves provenance for a
Core ref of any age: refs older than this contract have no
``rigplane.core.build_info`` module of their own, and the controls carry it
for them.

The schema is owned by ``src/rigplane/core/build_info.py``; this script
loads that module by file path so it stays importable under a bare
``python3`` with no installed rigplane package (``rigplane/__init__.py``
eagerly imports the backend stack).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Sequence

_SHA_RE = re.compile(r"[0-9a-f]{40}")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MODULE_PATH = _REPO_ROOT / "src" / "rigplane" / "core" / "build_info.py"


def _load_contract_module() -> ModuleType:
    # Always the contract that ships next to this script (the controls'
    # tree), never the stamped checkout's copy: the wheel workflow runs the
    # script from `control/` against a `core/` tree that may differ.
    # Registered in sys.modules before exec: dataclass processing looks the
    # module up there by __module__.
    spec = importlib.util.spec_from_file_location("rigplane_build_info", _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load build-info contract module: {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fail(message: str) -> int:
    print(f"stamp_build_info FAILED: {message}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", help="40-hex commit SHA being built (stamp mode)")
    parser.add_argument(
        "--requested-ref",
        help="the ref the consumer asked for (stamp mode)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="checkout root to stamp (default: this script's repository)",
    )
    parser.add_argument(
        "--verify-file",
        type=Path,
        help="verify this stamped file (e.g. inside an installed wheel) instead of stamping",
    )
    parser.add_argument(
        "--expect-sha",
        help="40-hex commit SHA the verified file must report",
    )
    args = parser.parse_args(argv)

    if args.verify_file is not None:
        if not args.expect_sha or not _SHA_RE.fullmatch(args.expect_sha):
            return _fail("--expect-sha must be 40 lowercase hex")
        info = _load_contract_module().load_build_info(args.verify_file)
        if info.git_sha != args.expect_sha:
            return _fail(
                f"{args.verify_file} reports git_sha {info.git_sha!r}, "
                f"expected {args.expect_sha!r}"
            )
        return 0

    if not args.sha or not _SHA_RE.fullmatch(args.sha):
        return _fail(f"--sha must be 40 lowercase hex: {args.sha!r}")
    if (
        not args.requested_ref
        or "\n" in args.requested_ref
        or "\r" in args.requested_ref
    ):
        return _fail("--requested-ref must be a non-empty single-line ref")
    contract = _load_contract_module()
    target = args.root.resolve() / "src" / "rigplane" / "core" / "_build_info.json"
    contract.write_build_info(
        contract.BuildInfo(git_sha=args.sha, requested_ref=args.requested_ref),
        target,
    )
    print(target.relative_to(args.root.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
