"""``rigplane diagnose`` subcommand — local-bundle generator with opt-in upload.

Privacy posture (spec §4.8):

* Default (no ``--upload``): build the bundle, save to ``--output``, print the
  path. Never prompt, never transmit.
* ``--upload`` on a TTY: collect any missing description/issue/contact fields
  via prompts, build the bundle, show a preview (file list, total size,
  endpoint URL), then ask a final ``[y/N]`` consent prompt where Enter saves
  locally without sending.
* ``--upload`` non-TTY: must also pass ``--no-confirm`` to actually transmit.
  Otherwise the command saves locally and prints a message explaining that
  confirmation cannot be obtained without a TTY.
* ``--upload --no-confirm``: fully scripted; no prompts of any kind.

Known limitation: ``--include`` / ``--exclude`` filtering is not yet
implemented in this version. The flags are accepted but emit a warning; all
registered contributors run regardless. A follow-up issue will wire the
include/exclude lists through ``build_bundle``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import platformdirs

# NOTE: ``rigplane.diagnostics`` re-exports ``upload_bundle`` from
# ``upload.py``, which imports ``aiohttp`` at module level. ``aiohttp`` is a
# dev-only / optional dependency, so importing it eagerly here would crash
# every CLI invocation (``rigplane status``, ``rigplane freq`` …) for users
# who installed only the runtime requirements. All ``rigplane.diagnostics``
# imports are therefore deferred into the helpers/coroutine that actually
# need them — the ``diagnose`` subcommand path. ``add_subparser`` stays
# argparse-only and never touches diagnostics symbols.
if TYPE_CHECKING:
    from rigplane.diagnostics import BundleContext, ReportSubmitted

logger = logging.getLogger(__name__)


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``diagnose`` subparser on ``sub`` (an ``_SubParsersAction``).

    Typed as ``Any`` because ``argparse._SubParsersAction`` is private and the
    surrounding parser code in ``cli/__init__.py`` follows the same convention.
    """
    p: argparse.ArgumentParser = sub.add_parser(
        "diagnose",
        help="Build (and optionally upload) a diagnostic report bundle.",
    )
    p.add_argument(
        "--upload",
        action="store_true",
        help="Upload the bundle after a confirmation prompt (default: save only).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path (default: ~/rigplane-report-<timestamp>.zip).",
    )
    p.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="CATEGORY",
        help="Contributor name to include (repeatable). NOTE: filtering not yet implemented.",
    )
    p.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="CATEGORY",
        help="Contributor name to exclude (repeatable). NOTE: filtering not yet implemented.",
    )
    p.add_argument(
        "--description",
        default=None,
        help="Free-text description of the issue.",
    )
    p.add_argument(
        "--issue-ref",
        dest="issue_ref",
        default=None,
        help="Related issue URL (e.g. https://github.com/.../issues/123).",
    )
    p.add_argument(
        "--email",
        default=None,
        help="Contact email (opt-in; sent only if explicitly provided).",
    )
    p.add_argument(
        "--callsign",
        default=None,
        help="Amateur-radio callsign (opt-in; sent only if explicitly provided).",
    )
    p.add_argument(
        "--endpoint",
        default=None,
        help="Upload endpoint URL (overrides RIGPLANE_REPORT_ENDPOINT).",
    )
    p.add_argument(
        "--no-confirm",
        dest="no_confirm",
        action="store_true",
        help="Skip all interactive prompts (required for non-TTY upload).",
    )
    p.add_argument(
        "--bundle-id",
        dest="bundle_id",
        default=None,
        metavar="UUID",
        help="Explicit submission_id for retry/dedup (default: random UUID).",
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_output_path() -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return Path.home() / f"rigplane-report-{ts}.zip"


def _default_log_dir() -> Path:
    # Defense in depth: the same call already runs inside
    # `configure_diagnostic_logging()` at `import rigplane` time, but a CLI
    # entry point that bypasses that path (or runs before logging init) still
    # benefits from the migration. The helper is idempotent.
    from rigplane._platformdirs_migration import migrate_legacy_platformdirs

    migrate_legacy_platformdirs()
    return Path(platformdirs.user_cache_path("rigplane")) / "logs"


def _default_config_dir() -> Path:
    from rigplane._platformdirs_migration import migrate_legacy_platformdirs

    migrate_legacy_platformdirs()
    return Path(platformdirs.user_config_path("rigplane"))


_FALLBACK_ENDPOINT = "https://reports.msmsoft.net/v1/diagnostics/upload"


def _resolve_endpoint(endpoint_arg: str | None) -> str:
    if endpoint_arg:
        return endpoint_arg
    env = os.environ.get("RIGPLANE_REPORT_ENDPOINT")
    if env:
        return env
    # Try the canonical constant from the diagnostics package. On installs that
    # omit ``aiohttp`` (a dev-only dep) the lazy ``__getattr__`` hook in
    # ``rigplane.diagnostics.__init__`` raises ``ImportError`` while loading
    # ``upload.py`` — silently fall back to the well-known default URL so
    # endpoint resolution still works in the preview / save-locally display
    # path. The actual upload (which DOES need aiohttp) will fail later with
    # a friendly message; the duplicated string is the price of decoupling
    # the CLI's display path from the upload module's import requirements.
    try:
        from rigplane.diagnostics import DEFAULT_ENDPOINT
    except ImportError:
        return _FALLBACK_ENDPOINT
    return str(DEFAULT_ENDPOINT)


def _is_tty() -> bool:
    """True when both stdin and stdout are attached to a terminal."""
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _prompt(prompt: str) -> str:
    """Wrapper around ``input()`` so tests can patch a single name."""
    return input(prompt)


def _make_context(args: argparse.Namespace) -> BundleContext:
    # Lazy import — see module-level NOTE about deferring diagnostics imports.
    from rigplane.diagnostics import BundleContext

    submission_id = args.bundle_id or str(uuid.uuid4())
    return BundleContext(
        radio=None,
        config_dir=_default_config_dir(),
        log_dir=_default_log_dir(),
        user_description=args.description,
        issue_ref=args.issue_ref,
        contact_email=args.email,
        contact_callsign=args.callsign,
        submission_id=submission_id,
        generated_at_unix=int(time.time()),
    )


def _read_manifest(bundle_path: Path) -> dict[str, Any]:
    """Extract ``manifest.json`` from the produced bundle and parse it.

    The manifest doubles as the upload metadata payload (schema-stable per
    spec §5.2). Falls back to a minimal payload if the manifest is missing.
    """
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            data = zf.read("manifest.json")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        logger.warning("diagnose: cannot read manifest from bundle: %r", exc)
        return {}
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        logger.warning("diagnose: manifest.json is not valid JSON: %r", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _print_preview(bundle_path: Path, endpoint: str) -> None:
    size_bytes = bundle_path.stat().st_size
    print("\nBundle preview:", file=sys.stderr)
    print(f"  Path:     {bundle_path}", file=sys.stderr)
    print(f"  Size:     {size_bytes} bytes", file=sys.stderr)
    print(f"  Endpoint: {endpoint}", file=sys.stderr)


def _interactive_collect(args: argparse.Namespace) -> None:
    """Fill in missing description/issue/contact fields via prompts.

    Mutates ``args`` in place. Only called on TTY when ``--no-confirm`` is
    absent. Each field is skipped if it is already set on ``args``.
    """
    if args.description is None:
        try:
            value = _prompt("Description (what went wrong?): ").strip()
        except EOFError:
            value = ""
        args.description = value or None
    if args.issue_ref is None:
        try:
            value = _prompt("Issue URL (optional): ").strip()
        except EOFError:
            value = ""
        args.issue_ref = value or None
    if args.email is None:
        try:
            value = _prompt("Contact email (optional, opt-in): ").strip()
        except EOFError:
            value = ""
        args.email = value or None
    if args.callsign is None:
        try:
            value = _prompt("Callsign (optional, opt-in): ").strip()
        except EOFError:
            value = ""
        args.callsign = value or None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def _run_async(args: argparse.Namespace) -> int:
    # Lazy imports — see module-level NOTE. ``build_bundle`` plus the typed
    # error classes live in non-aiohttp submodules, so importing them here is
    # cheap. ``upload_bundle`` (and the other ``upload``-module re-exports)
    # transitively imports ``aiohttp`` via the lazy hook in
    # ``rigplane.diagnostics.__init__``; that import is deferred into the
    # ``args.upload`` branch below so ``rigplane diagnose`` (save-only) works
    # for runtime-only installs without ``aiohttp``.
    # These names land in the local scope, so test fixtures must patch
    # ``rigplane.diagnostics`` (the source module) rather than
    # ``rigplane.cli._diagnose``.
    from rigplane.diagnostics import build_bundle

    if args.include or args.exclude:
        print(
            "warning: --include / --exclude filtering is not yet implemented; "
            "all registered contributors will run.",
            file=sys.stderr,
        )

    output_path = args.output or _default_output_path()

    # Default behaviour (no --upload): just build and save. No prompts ever.
    if not args.upload:
        ctx = _make_context(args)
        print("Building diagnostic bundle...", file=sys.stderr)
        bundle_path = build_bundle(ctx, output_path)
        print(f"Bundle saved to: {bundle_path}")
        return 0

    # --upload path. Decide whether we may prompt.
    is_tty = _is_tty()
    may_prompt = is_tty and not args.no_confirm

    # Non-TTY without --no-confirm → save locally, do not transmit.
    if not is_tty and not args.no_confirm:
        ctx = _make_context(args)
        print("Building diagnostic bundle...", file=sys.stderr)
        bundle_path = build_bundle(ctx, output_path)
        print(
            f"warning: --upload requires a TTY for confirmation, or pass "
            f"--no-confirm to skip the prompt. Saved locally to {bundle_path}.",
            file=sys.stderr,
        )
        return 0

    # Collect any missing fields interactively before building the bundle, so
    # they end up inside manifest.json (which doubles as upload metadata).
    if may_prompt:
        _interactive_collect(args)

    ctx = _make_context(args)
    print("Building diagnostic bundle...", file=sys.stderr)
    bundle_path = build_bundle(ctx, output_path)

    endpoint = _resolve_endpoint(args.endpoint)

    if may_prompt:
        _print_preview(bundle_path, endpoint)
        try:
            answer = _prompt(f"Send to {endpoint}? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print(
                f"Saved locally; not uploading. Bundle: {bundle_path}",
                file=sys.stderr,
            )
            return 0

    # Upload (either --no-confirm path or user typed y/yes after preview).
    # Lazy import — accessing ``upload_bundle`` is what triggers the aiohttp
    # import via the :pep:`562` hook in ``rigplane.diagnostics.__init__``.
    # Keeping it inside the ``args.upload`` branch lets ``rigplane diagnose``
    # (save-only) run on installs that omit aiohttp. The error classes are
    # imported alongside for symmetry; they live in non-aiohttp submodules
    # and would be cheap to hoist, but bundling them here keeps the upload
    # path self-contained.
    try:
        from rigplane.diagnostics import (
            BundleTooLarge,
            ForbiddenContent,
            MetadataInvalid,
            NetworkError,
            RateLimited,
            UploadFailed,
            upload_bundle,
        )
    except ImportError as exc:
        # `aiohttp` is a dev-only / optional dependency. Pip-install users who
        # try `--upload` without it get here. Don't show a Python traceback —
        # they need an actionable message + the local bundle path so they can
        # still attach it to a GitHub issue manually.
        if "aiohttp" in str(exc):
            print(
                "Upload requires the 'aiohttp' package, which is not installed.",
                file=sys.stderr,
            )
            print(
                "  Install it with:  pip install aiohttp",
                file=sys.stderr,
            )
            print(
                f"  Bundle saved locally: {bundle_path}",
                file=sys.stderr,
            )
            print(
                "  You can attach it to a GitHub issue manually, or re-run "
                "with `pip install aiohttp` to enable upload.",
                file=sys.stderr,
            )
            return 9
        raise

    metadata = _read_manifest(bundle_path)
    try:
        result: ReportSubmitted = await upload_bundle(
            bundle_path,
            metadata,
            endpoint=endpoint,
        )
    except RateLimited as exc:
        retry = exc.retry_after_seconds
        retry_str = f"{retry}s" if isinstance(retry, int) else "unknown"
        print(f"Rate limit exceeded. Try again in {retry_str}.", file=sys.stderr)
        return 4
    except BundleTooLarge:
        size_bytes = bundle_path.stat().st_size
        print(
            f"Bundle too large ({size_bytes} bytes). "
            f"Try `--exclude` to drop some categories.",
            file=sys.stderr,
        )
        return 5
    except ForbiddenContent as exc:
        pattern = getattr(exc, "pattern", None)
        suffix = f" (pattern: {pattern})" if pattern else ""
        print(
            f"Server rejected bundle (forbidden content detected){suffix}. "
            f"Review the manifest and contact support.",
            file=sys.stderr,
        )
        return 6
    except MetadataInvalid as exc:
        print(f"Metadata validation failed: {exc}", file=sys.stderr)
        return 7
    except (NetworkError, UploadFailed) as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 8

    print("Uploaded.")
    if result.support_url:
        print(f"Support URL: {result.support_url}")
    if result.report_id:
        print(f"Report ID:   {result.report_id}")
    return 0


def run(args: argparse.Namespace) -> int:
    return asyncio.run(_run_async(args))
