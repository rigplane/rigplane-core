"""build_versioned_docs.py — build the versioned documentation site.

Every documentation version is a plain ``mkdocs build --strict`` of one source
tree into ``<out>/<version>/``. The source is the working tree (``.``) or a
git ref, exported with ``git archive`` and built in its own environment from
its own ``uv.lock``, so a released version renders its own docstrings with its
own toolchain. A throwaway overlay config (``INHERIT: mkdocs.yml``) sets the
version's ``site_url`` and turns on Material's version selector
(``extra.version.provider: mike``), so no source tree needs versioning
settings in its own ``mkdocs.yml``.

The site root then gets ``versions.json`` (read by the version selector), a
redirect page for every page path of every version (the unversioned URL goes
to the default version, or to the first listed version that has the page),
the default version's ``404.html``, a ``sitemap.xml`` index of the version
sitemaps, and the top-level non-Markdown files of this checkout's ``docs/``
(dotfiles excepted).

Usage (from the repository root; versions are listed newest first, which is
the selector's order):

    uv run python scripts/build_versioned_docs.py --out site --default 2.11 \\
        --version 3.0 "3.0 (beta)" . \\
        --version 2.11 2.11 v2.11.1

An exported version installs only its ``dev`` dependency group: mkdocstrings
reads the package source through ``PYTHONPATH``, so the project itself (and
the web UI build its install triggers) is never installed for it.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_NAME = ".mkdocs-versioned.yml"
WORKING_TREE = "."

REDIRECT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redirecting</title>
<link rel="canonical" href="{canonical}">
<meta http-equiv="refresh" content="0; url={href}">
<script>location.replace({href_js} + location.search + location.hash);</script>
</head>
<body>
<p>This page has moved to <a href="{href}">{canonical}</a>.</p>
</body>
</html>
"""


@dataclass(frozen=True)
class DocsVersion:
    """One documentation version: URL segment, selector title, source."""

    id: str
    title: str
    source: str


def overlay_config(site_url: str, version_id: str, default_id: str) -> str:
    """Return the MkDocs overlay that versions one build of ``mkdocs.yml``."""
    return (
        "INHERIT: mkdocs.yml\n"
        f"site_url: {site_url}{version_id}/\n"
        "extra:\n"
        "  version:\n"
        "    provider: mike\n"
        f"    default: '{default_id}'\n"
    )


def page_dirs(version_root: Path) -> set[str]:
    """Return the page paths of one built version, ``""`` for its home page."""
    pages = set()
    for index in version_root.rglob("index.html"):
        rel = index.parent.relative_to(version_root).as_posix()
        pages.add("" if rel == "." else rel)
    return pages


def _url_path(*parts: str) -> str:
    return "/".join(part for part in parts if part) + "/"


def _redirect_page(href: str, canonical: str) -> str:
    return REDIRECT_TEMPLATE.format(
        href=html.escape(href),
        canonical=html.escape(canonical),
        href_js=json.dumps(href),
    )


def _sitemap_index(site_url: str, version_ids: Sequence[str]) -> str:
    entries = "".join(
        f"  <sitemap>\n    <loc>{site_url}{version_id}/sitemap.xml</loc>\n  </sitemap>\n"
        for version_id in version_ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}</sitemapindex>\n"
    )


def assemble_root(
    out: Path,
    versions: Sequence[DocsVersion],
    default_id: str,
    site_url: str,
    static_dir: Path,
) -> None:
    """Write the site root around the built ``<out>/<version>/`` trees."""
    ids = [version.id for version in versions]
    if default_id not in ids:
        raise ValueError(f"default version {default_id!r} is not one of {ids}")

    pages = {version_id: page_dirs(out / version_id) for version_id in ids}
    for version_id, version_pages in pages.items():
        for page in version_pages:
            if page.split("/")[0] in ids:
                raise ValueError(
                    f"page {page!r} of version {version_id} would be shadowed "
                    "by a version directory"
                )

    base_path = urlsplit(site_url).path or "/"
    for page in sorted(set().union(*pages.values())):
        target = (
            default_id
            if page in pages[default_id]
            else next(version_id for version_id in ids if page in pages[version_id])
        )
        stub = out / page / "index.html" if page else out / "index.html"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(
            _redirect_page(
                href=base_path + _url_path(target, page),
                canonical=site_url + _url_path(target, page),
            )
        )

    shutil.copyfile(out / default_id / "404.html", out / "404.html")
    (out / "versions.json").write_text(
        json.dumps(
            [
                {"version": version.id, "title": version.title, "aliases": []}
                for version in versions
            ],
            indent=2,
        )
        + "\n"
    )
    (out / "sitemap.xml").write_text(_sitemap_index(site_url, ids))
    for path in sorted(static_dir.iterdir()):
        if path.is_file() and path.suffix != ".md" and not path.name.startswith("."):
            shutil.copyfile(path, out / path.name)


def _site_url(repo: Path) -> str:
    match = re.search(
        r"^site_url:\s*(\S+)\s*$", (repo / "mkdocs.yml").read_text(), re.MULTILINE
    )
    if match is None:
        raise ValueError("mkdocs.yml has no site_url")
    return match.group(1).rstrip("/") + "/"


def _export(repo: Path, ref: str, dest: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(dest, filter="data")


def _build_version(
    repo: Path,
    version: DocsVersion,
    default_id: str,
    site_url: str,
    out: Path,
    workdir: Path,
) -> None:
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    if version.source == WORKING_TREE:
        source = repo
        mkdocs = [sys.executable, "-m", "mkdocs"]
    else:
        source = workdir / version.id
        source.mkdir(parents=True)
        _export(repo, version.source, source)
        subprocess.run(
            ["uv", "sync", "--frozen", "--only-group", "dev", "--quiet"],
            cwd=source,
            env=env,
            check=True,
        )
        mkdocs = ["uv", "run", "--no-sync", "mkdocs"]
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(source / "src"), env.get("PYTHONPATH")])
    )

    overlay = source / OVERLAY_NAME
    overlay.write_text(overlay_config(site_url, version.id, default_id))
    try:
        subprocess.run(
            [
                *mkdocs,
                "build",
                "--strict",
                "--config-file",
                OVERLAY_NAME,
                "--site-dir",
                str(out / version.id),
            ],
            cwd=source,
            env=env,
            check=True,
        )
    finally:
        overlay.unlink()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the versioned docs site.")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    parser.add_argument(
        "--default", required=True, help="version the unversioned URLs redirect to"
    )
    parser.add_argument(
        "--version",
        nargs=3,
        action="append",
        required=True,
        metavar=("ID", "TITLE", "SOURCE"),
        help='URL segment, selector title, and "." (working tree) or a git ref',
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    versions = [DocsVersion(*fields) for fields in args.version]
    out = args.out.resolve()
    if out.exists():
        if not (out / "versions.json").is_file():
            print(f"refusing to replace {out}: not a previous output", file=sys.stderr)
            return 2
        shutil.rmtree(out)

    site_url = _site_url(REPO_ROOT)
    with tempfile.TemporaryDirectory(prefix="rigplane-docs-") as workdir:
        for version in versions:
            _build_version(
                REPO_ROOT, version, args.default, site_url, out, Path(workdir)
            )
    assemble_root(out, versions, args.default, site_url, REPO_ROOT / "docs")
    print(f"built {', '.join(v.id for v in versions)} into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
