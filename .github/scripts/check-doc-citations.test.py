"""Pin how the doc link gate (check-doc-citations.sh --check-links) treats symlinks.

The repo root carries markdown symlinks (CHANGELOG.md -> docs/CHANGELOG.md).
Their relative links are written for the target's directory, so scanning the
symlink path as if it were a file at the root reports links that are fine.
The gate must scan each real file once, from the real file's own directory.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("check-doc-citations.sh")
EMPTY_LINK_BASELINE = "# fixture baseline: no grandfathered links\n"


def _env_bash_has_associative_arrays() -> bool:
    # The gate declares `declare -A`, so it needs bash 4+ (macOS ships 3.2).
    probe = subprocess.run(
        ("/usr/bin/env", "bash", "-c", "declare -A probe"),
        capture_output=True,
    )
    return probe.returncode == 0


@unittest.skipUnless(
    _env_bash_has_associative_arrays(),
    "the gate script needs bash 4+ as `bash` on PATH",
)
class DocLinkGateSymlinkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        scripts = self.repo / ".github" / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(SCRIPT, scripts / SCRIPT.name)
        (scripts / "doc-link-baseline.txt").write_text(
            EMPTY_LINK_BASELINE, encoding="utf-8"
        )
        (self.repo / "docs").mkdir()  # the gate insists on running from a repo root
        self.git("init", "-q")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(
            ("git", *args), cwd=self.repo, check=True, capture_output=True, text=True
        )

    def write(self, relative: str, text: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def gate(self) -> subprocess.CompletedProcess[str]:
        self.git("add", "-A")
        return subprocess.run(
            (str(self.repo / ".github" / "scripts" / SCRIPT.name), "--check-links"),
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def link_markdown_symlink(self) -> None:
        # Same shape as the real tree: CHANGELOG.md at the root -> docs/CHANGELOG.md.
        self.write("docs/guide/remote-operation.md", "# Remote operation\n")
        os.symlink("docs/CHANGELOG.md", self.repo / "CHANGELOG.md")

    def test_symlink_links_resolve_from_the_target_not_from_the_symlink(self) -> None:
        self.link_markdown_symlink()
        self.write(
            "docs/CHANGELOG.md", "See [remote](guide/remote-operation.md).\n"
        )

        result = self.gate()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", result.stdout)

    def test_broken_link_behind_a_symlink_is_reported_once_at_the_target(self) -> None:
        self.link_markdown_symlink()
        self.write("docs/CHANGELOG.md", "See [gone](guide/missing.md).\n")

        result = self.gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        reported = [
            line.split(":", 1)[0].strip()
            for line in result.stderr.splitlines()
            if "does not resolve" in line
        ]
        self.assertEqual(reported, ["docs/CHANGELOG.md"], result.stderr)

    def test_symlink_pointing_outside_the_tracked_markdown_set_is_still_scanned(
        self,
    ) -> None:
        # A symlink whose target is not a tracked *.md file has no real file
        # that would scan its links, so skipping it would hide them.
        self.write("notes/raw.txt", "See [gone](missing.md).\n")
        os.symlink("notes/raw.txt", self.repo / "README.md")

        result = self.gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("README.md:1: link to 'missing.md'", result.stderr)

    def test_regular_markdown_files_are_still_checked(self) -> None:
        self.write("docs/a.md", "See [gone](nope.md) and [ok](b.md).\n")
        self.write("docs/b.md", "# B\n")

        result = self.gate()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/a.md:1: link to 'nope.md'", result.stderr)
        self.assertNotIn("'b.md'", result.stderr)


if __name__ == "__main__":
    unittest.main()
