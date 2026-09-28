"""Unit tests for scripts/build_versioned_docs.py.

The tests cover the site-root assembly and the overlay config on fake
per-version build trees; they run neither MkDocs nor git.
"""

from __future__ import annotations

import json
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
    overlay_config,
    page_dirs,
)

SITE_URL = "https://rigplane.dev/"
V3 = DocsVersion(id="3.0", title="3.0 (beta)", source=".")
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
        {"version": "3.0", "title": "3.0 (beta)", "aliases": []},
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
    ]
