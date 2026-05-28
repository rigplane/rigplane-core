"""MkDocs hook: drop pages flagged robots: noindex from sitemap.xml.

mkdocs-material always includes every published page in sitemap.xml, regardless
of page-level `robots: noindex` frontmatter. Search engines therefore see the
URL in the sitemap, fetch it, see the noindex meta — and may still flag the
page in tooling like Bing Webmaster Tools (e.g. as a duplicate of the site-
wide description fallback).

This hook runs at on_post_build:
  1. Collect every page from the build whose effective robots meta contains
     "noindex".
  2. Rewrite site/sitemap.xml (and the gzipped variant) to drop those URLs.
"""
from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ET.register_namespace("", NS["sm"])



def on_post_build(config, **kwargs):  # noqa: D401 — mkdocs hook signature
    site_dir = Path(config["site_dir"])
    sitemap_path = site_dir / "sitemap.xml"
    sitemap_gz = site_dir / "sitemap.xml.gz"
    if not sitemap_path.exists():
        return

    # Source of truth is the rendered HTML: every page advertises its final
    # robots meta. Walk site_dir, find the noindex pages, map to canonical URL.
    noindex_paths: set[str] = set()
    site_url = (config.get("site_url") or "").rstrip("/")
    pattern = re.compile(
        rb"<meta\s+name=\"robots\"\s+content=\"[^\"]*noindex[^\"]*\"",
        re.IGNORECASE,
    )
    for html in site_dir.rglob("*.html"):
        try:
            head = html.read_bytes()[:8192]
        except OSError:
            continue
        if pattern.search(head):
            rel = html.relative_to(site_dir).as_posix()
            if rel.endswith("/index.html"):
                rel = rel[: -len("index.html")]
            elif rel == "index.html":
                rel = ""
            noindex_paths.add(f"{site_url}/{rel}")

    if not noindex_paths:
        return

    tree = ET.parse(sitemap_path)
    root = tree.getroot()
    removed = 0
    for url in list(root.findall("sm:url", NS)):
        loc_el = url.find("sm:loc", NS)
        if loc_el is None or loc_el.text is None:
            continue
        loc = loc_el.text.strip()
        if loc in noindex_paths:
            root.remove(url)
            removed += 1

    tree.write(sitemap_path, encoding="utf-8", xml_declaration=True)
    if sitemap_gz.exists():
        with open(sitemap_path, "rb") as f_in, gzip.open(sitemap_gz, "wb") as f_out:
            f_out.write(f_in.read())

    print(f"sitemap_noindex hook: removed {removed} noindex URL(s) from sitemap.xml")
