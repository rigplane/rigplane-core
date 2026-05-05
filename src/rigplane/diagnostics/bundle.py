"""Bundle assembler — orchestrates contributors, builds manifest, writes ZIP."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from icom_lan.diagnostics._discovery import discover
from icom_lan.diagnostics._manifest import _Manifest
from icom_lan.diagnostics.contributor import BundleContext

logger = logging.getLogger(__name__)


def build_bundle(ctx: BundleContext, output_path: Path) -> Path:
    """Collect contributions, assemble manifest, write a zip at ``output_path``.

    Per-contributor failures are isolated: a raising ``contribute()`` is logged
    to ``manifest.warnings`` and the bundle is still produced. Returns the
    absolute path of the created zip.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as staging:
        staging_dir = Path(staging)
        manifest = _Manifest(ctx)

        for contributor in discover():
            contributor_dir = staging_dir / contributor.name
            contributor_dir.mkdir(parents=True, exist_ok=True)
            try:
                contributor.contribute(ctx, contributor_dir)
                manifest.record_success(contributor, contributor_dir)
            except Exception as exc:  # noqa: BLE001 — per-contributor isolation
                logger.warning(
                    "diagnostics: contributor %s failed: %r", contributor.name, exc
                )
                manifest.record_warning(contributor, repr(exc))
                # Drop any partial files the contributor wrote before raising,
                # so the bundle does not archive half-written / inconsistent
                # output. Best-effort: cleanup failures are silent.
                try:
                    if contributor_dir.exists():
                        shutil.rmtree(contributor_dir)
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass

        manifest.write(staging_dir / "manifest.json")
        return _zip_directory(staging_dir, output_path)


def _zip_directory(source_dir: Path, output_path: Path) -> Path:
    """Atomic-write a deflated zip of source_dir's contents to output_path."""
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(tmp, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for entry in sorted(source_dir.rglob("*")):
                if entry.is_file():
                    zf.write(entry, arcname=entry.relative_to(source_dir).as_posix())
        os.replace(tmp, output_path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    return output_path.resolve()
