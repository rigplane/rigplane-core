"""Unit tests for scripts/build_versioned_docs.py.

Most tests cover the site-root assembly and the overlay config on fake
per-version build trees; they run neither MkDocs nor git. The version-warning
tests build a one-page site with the real theme overrides.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Make the scripts/ directory importable.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_versioned_docs import (  # noqa: E402
    DocsVersion,
    assemble_root,
    OVERLAY_TEMPLATES,
    install_overlay_templates,
    overlay_config,
    page_dirs,
)

SITE_URL = "https://rigplane.dev/"
V3 = DocsVersion(id="3.0", title="3.0", source=".")
V2 = DocsVersion(id="2.11", title="2.11", source="v2.11.1")


def _fake_build(root: Path, pages: list[str]) -> None:
    """Write a minimal MkDocs-like output tree with the given page dirs."""
    for page in pages:
        target = root / page / "index.html" if page else root / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"<html>{root.name}:{page}</html>")
    (root / "404.html").write_text(f"<html>404 of {root.name}</html>")
    (root / "sitemap.xml").write_text("<urlset/>")
    (root / "assets").mkdir()
    (root / "assets" / "app.js").write_text("")


@pytest.fixture
def site(tmp_path: Path) -> Path:
    out = tmp_path / "site"
    _fake_build(out / "3.0", ["", "guide/cli", "guide/web-ui", "release-notes/beta"])
    _fake_build(out / "2.11", ["", "guide/cli", "guide/web-ui"])
    return out


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "robots.txt").write_text("User-agent: *\n")
    (docs / "CNAME").write_text("rigplane.dev\n")
    (docs / "index.md").write_text("# Home\n")
    (docs / ".hidden").write_text("x")
    (docs / "guide").mkdir()
    return docs


def _redirect_target(page: Path) -> str:
    html = page.read_text()
    return html.split('http-equiv="refresh" content="0; url=')[1].split('"')[0]


def test_page_dirs_lists_every_page_and_skips_assets(site: Path) -> None:
    assert page_dirs(site / "3.0") == {
        "",
        "guide/cli",
        "guide/web-ui",
        "release-notes/beta",
    }


def test_versions_json_keeps_the_given_order(site: Path, static_dir: Path) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    assert json.loads((site / "versions.json").read_text()) == [
        {"version": "3.0", "title": "3.0", "aliases": []},
        {"version": "2.11", "title": "2.11", "aliases": []},
    ]


def test_unversioned_urls_redirect_to_the_default_version(
    site: Path, static_dir: Path
) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    assert _redirect_target(site / "index.html") == "/2.11/"
    assert _redirect_target(site / "guide" / "cli" / "index.html") == "/2.11/guide/cli/"


def test_page_missing_from_the_default_redirects_to_the_version_that_has_it(
    site: Path, static_dir: Path
) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    stub = site / "release-notes" / "beta" / "index.html"
    assert _redirect_target(stub) == "/3.0/release-notes/beta/"


def test_redirect_carries_canonical_url_and_keeps_anchor_and_query(
    site: Path, static_dir: Path
) -> None:
    assemble_root(site, [V3, V2], "3.0", SITE_URL, static_dir)

    html = (site / "guide" / "cli" / "index.html").read_text()
    assert '<link rel="canonical" href="https://rigplane.dev/3.0/guide/cli/">' in html
    assert (
        'location.replace("/3.0/guide/cli/" + location.search + location.hash)' in html
    )


def test_root_404_is_the_default_versions_404(site: Path, static_dir: Path) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    assert (site / "404.html").read_text() == "<html>404 of 2.11</html>"


def test_root_sitemap_is_an_index_of_the_version_sitemaps(
    site: Path, static_dir: Path
) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    sitemap = (site / "sitemap.xml").read_text()
    assert "<sitemapindex" in sitemap
    assert "<loc>https://rigplane.dev/3.0/sitemap.xml</loc>" in sitemap
    assert "<loc>https://rigplane.dev/2.11/sitemap.xml</loc>" in sitemap


def test_static_top_level_docs_files_are_copied_to_the_root(
    site: Path, static_dir: Path
) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    assert (site / "robots.txt").read_text() == "User-agent: *\n"
    assert (site / "CNAME").read_text() == "rigplane.dev\n"
    assert not (site / "index.md").exists()
    assert not (site / ".hidden").exists()


def test_versioned_trees_are_left_untouched(site: Path, static_dir: Path) -> None:
    assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)

    assert (site / "2.11" / "guide" / "cli" / "index.html").read_text() == (
        "<html>2.11:guide/cli</html>"
    )


def test_unknown_default_version_is_rejected(site: Path, static_dir: Path) -> None:
    with pytest.raises(ValueError, match="default version"):
        assemble_root(site, [V3, V2], "2.10", SITE_URL, static_dir)


def test_page_dir_that_shadows_a_version_directory_is_rejected(
    site: Path, static_dir: Path
) -> None:
    (site / "3.0" / "2.11").mkdir()
    (site / "3.0" / "2.11" / "index.html").write_text("")

    with pytest.raises(ValueError, match="2.11"):
        assemble_root(site, [V3, V2], "2.11", SITE_URL, static_dir)


def test_overlay_points_site_url_at_the_version_and_enables_the_selector() -> None:
    config = overlay_config(SITE_URL, "3.0", "2.11")

    assert config.splitlines() == [
        "INHERIT: mkdocs.yml",
        "site_url: https://rigplane.dev/3.0/",
        "extra:",
        "  version:",
        "    provider: mike",
        "    default: '2.11'",
        "  analytics:",
        "    provider: custom",
    ]


def _fake_repo(root: Path) -> None:
    for template in OVERLAY_TEMPLATES:
        (root / template).parent.mkdir(parents=True, exist_ok=True)
        (root / template).write_text(f"current {template.name}")


def test_exported_version_gets_this_checkouts_templates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _fake_repo(repo)
    source = tmp_path / "exported"
    stale = source / OVERLAY_TEMPLATES[0]
    stale.parent.mkdir(parents=True)
    stale.write_text("stale snippet")

    install_overlay_templates(repo, source)

    assert stale.read_text() == f"current {OVERLAY_TEMPLATES[0].name}"


def test_exported_version_without_overrides_still_gets_the_templates(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _fake_repo(repo)
    source = tmp_path / "exported"
    source.mkdir()

    install_overlay_templates(repo, source)

    assert {t.name for t in OVERLAY_TEMPLATES} == {
        "custom.html",
        "main.html",
        "outdated.html",
    }
    for template in OVERLAY_TEMPLATES:
        assert (source / template).read_text() == f"current {template.name}"


REPO_ROOT = Path(__file__).resolve().parents[1]
BANNER_TEXT = "Read the documentation for the current release."


def _build_page(source: Path, version_id: str, default_id: str, out: Path) -> str:
    """Build the one-page site at ``source`` the way ``_build_version`` does."""
    overlay = source / ".mkdocs-versioned.yml"
    overlay.write_text(overlay_config(SITE_URL, version_id, default_id))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "--config-file",
            overlay.name,
            "--site-dir",
            str(out),
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    return (out / "index.html").read_text()


def _version_site(tmp_path: Path, name: str, main_html: str | None) -> Path:
    """Return a one-page source tree using the theme overrides of this checkout.

    ``main_html`` replaces ``main.html``, standing for a released tag whose
    override predates the version warning; ``None`` keeps this checkout's.
    """
    source = tmp_path / name
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "index.md").write_text("# Home\n")
    (source / "mkdocs.yml").write_text(
        f"site_name: Fixture\nsite_url: {SITE_URL}\n"
        "theme:\n  name: material\n  custom_dir: docs/overrides\n"
        "extra:\n  social_image: https://example.invalid/og.png\n"
    )
    shutil.copytree(REPO_ROOT / "docs" / "overrides", source / "docs" / "overrides")
    if main_html is not None:
        (source / "docs" / "overrides" / "main.html").write_text(main_html)
        (
            source / "docs" / "overrides" / "partials" / "javascripts" / "outdated.html"
        ).unlink()
    return source


def _outdated_banner(page: str) -> str | None:
    """Return the version-warning container of a built page, if any."""
    match = re.search(
        r'<div [^>]*data-md-component="outdated"[^>]*>.*?</aside>\s*</div>',
        page,
        re.DOTALL,
    )
    return match.group(0) if match else None


def _version_default(page: str) -> str:
    config = re.search(
        r'<script id="__config" type="application/json">(.*?)</script>', page
    )
    assert config is not None
    return json.loads(config.group(1))["version"]["default"]


def test_exported_older_version_shows_the_warning_and_current_release_link(
    tmp_path: Path,
) -> None:
    # v2.11.1's main.html has no ``outdated`` block and it has no outdated partial.
    source = _version_site(tmp_path, "v2.11", '{% extends "base.html" %}\n')
    install_overlay_templates(REPO_ROOT, source)

    page = _build_page(source, "2.11", "3.0", tmp_path / "out-2.11")

    banner = _outdated_banner(page)
    assert banner is not None
    assert "This is the documentation for an older version of RigPlane." in banner
    assert f"{BANNER_TEXT}</strong>" in banner
    assert re.search(r'<a href="\.\./[^"]*">\s*<strong>' + BANNER_TEXT, banner)
    # The corrected partial resolves the relative base_url against the page.
    assert 'base=new URL(".",location)' in banner
    assert _version_default(page) == "3.0"


def test_default_version_keeps_the_warning_hidden(tmp_path: Path) -> None:
    source = _version_site(tmp_path, "working-tree", None)

    page = _build_page(source, "3.0", "3.0", tmp_path / "out-3.0")

    banner = _outdated_banner(page)
    assert banner is not None
    # Material unhides the banner in the browser only when the page's version
    # (3.0) is not the configured default, which is 3.0 here.
    assert (
        '<div data-md-color-scheme="default" data-md-component="outdated" hidden>'
        in banner
    )
    assert _version_default(page) == "3.0"
