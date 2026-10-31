"""Pin the three environment entries for every job that launches a Playwright browser.

This is a text scanner for ordinary edits, not a YAML parser. It does not look for
anchors, step-level overrides, GITHUB_ENV writes or local actions.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"
BROWSER_ENV = {
    "DBUS_SESSION_BUS_ADDRESS": "'disabled:'",
    "FC_FONTATIONS": "'1'",
    "VK_ICD_FILENAMES": "''",
}
EXPECTED_BROWSER_JOBS = {
    ("quick.yml", "quick-frontend"),
    ("full.yml", "capture-harness"),
    ("visual.yml", "visual"),
}
BROWSER_ENTRY_POINTS = (
    "playwright test", "test:e2e", "capture.mjs", "capture-ptt.mjs",
    "verify:component-kit-api", "verify-package.mjs", "quick-v2-worker-v1.sh",
)


class WorkflowFormatError(ValueError):
    """A workflow's jobs or a job's steps do not have the expected block form."""


def _jobs(workflow: str, text: str) -> dict[str, tuple[str, list[str]]]:
    markers = list(re.finditer(r"(?m)^jobs:[ \t]*(?:#.*)?$", text))
    if len(markers) != 1:
        raise WorkflowFormatError(f"{workflow}: expected one block jobs: mapping")
    body = re.split(r"(?m)^[^\s#]", text[markers[0].end() :], maxsplit=1)[0]
    headers = list(re.finditer(r"(?m)^  ([\w-]+):[ \t]*(?:#.*)?$", body))
    if not headers:
        raise WorkflowFormatError(f"{workflow}: expected jobs in the jobs: mapping")
    jobs = {}
    for index, header in enumerate(headers):
        name = header.group(1)
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        block = body[header.end() : end]
        # A job that calls a workflow of this repository has no steps of its
        # own; the called file is scanned on its own (MOR-3878).
        if re.search(r"(?m)^    uses:[ \t]*\./\.github/workflows/[\w.-]+\.ya?ml[ \t]*$", block):
            jobs[name] = block, []
            continue
        markers = list(re.finditer(r"(?m)^    steps:[ \t]*(?:#.*)?$", block))
        if len(markers) != 1:
            raise WorkflowFormatError(f"{workflow}:{name}: expected one block steps: sequence")
        marker = markers[0]
        rest = block[marker.end() :]
        following = re.search(r"(?m)^    (?!#)\S", rest)
        end = following.start() if following else len(rest)
        steps = re.split(r"(?m)^      -[ \t]+", rest[:end])[1:]
        settings = block[: marker.start()] + rest[end:]
        jobs[name] = settings, ["        " + step for step in steps]
    return jobs


def _run(step: str) -> str:
    match = re.search(r"(?m)^        run[ \t]*:[ \t]*(.*)$", step)
    if match is None:
        return ""
    value = match.group(1).strip()
    if not value.startswith(("|", ">")):
        return value
    return re.split(r"(?m)^        \S", step[match.end() :], maxsplit=1)[0]


def _launches_browser(step: str) -> bool:
    run = "\n".join(
        line for line in _run(step).splitlines() if not line.lstrip().startswith("#")
    )
    run = " ".join(re.sub(r"\\\r?\n", "", run).split())
    return any(token in run for token in BROWSER_ENTRY_POINTS)


def _job_env_errors(label: str, settings: str) -> list[str]:
    envs = list(re.finditer(r"(?m)^    env[ \t]*:[ \t]*(.*)$", settings))
    if len(envs) != 1 or envs[0].group(1).strip():
        return [f"{label}: expected one block job-level env: mapping"]
    body = re.split(r"(?m)^    \S", settings[envs[0].end() :], maxsplit=1)[0]
    errors = []
    for name, expected in BROWSER_ENV.items():
        values = re.findall(rf"(?m)^      {name}:[ \t]*(.*)$", body)
        if values != [expected]:
            errors.append(f"{label}: {name} must appear once as {expected}, found {values}")
    return errors


def workflow_errors(
    workflows: dict[str, str], expected: set[tuple[str, str]] = EXPECTED_BROWSER_JOBS
) -> list[str]:
    errors = []
    browser_jobs = {}
    for workflow, text in sorted(workflows.items()):
        try:
            jobs = _jobs(workflow, text)
        except WorkflowFormatError as exc:
            errors.append(str(exc))
            continue
        for job, (settings, steps) in jobs.items():
            if any(_launches_browser(step) for step in steps):
                browser_jobs[workflow, job] = settings
    for description, names in (
        ("Unexpected browser jobs", browser_jobs.keys() - expected),
        ("Missing browser jobs", expected - browser_jobs.keys()),
    ):
        if names:
            errors.append(f"{description}: " + ", ".join(f"{w}:{j}" for w, j in sorted(names)))
    for (workflow, job), settings in browser_jobs.items():
        errors.extend(_job_env_errors(f"{workflow}:{job}", settings))
    return errors


class RepositoryBrowserEnvironmentTest(unittest.TestCase):
    def test_workflows_obey_browser_environment_contract(self) -> None:
        paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
        self.assertTrue(paths, "No workflow files discovered")
        errors = workflow_errors({path.name: path.read_text(encoding="utf-8") for path in paths})
        self.assertEqual(errors, [], "\n".join(errors))


class BrowserEnvironmentParserTest(unittest.TestCase):
    @staticmethod
    def workflow(run: str = "node frontend/fixtures/capture.mjs", *, env: bool = True) -> str:
        settings = "    env:\n" if env else ""
        if env:
            settings += "".join(f"      {name}: {value}\n" for name, value in BROWSER_ENV.items())
        return f"jobs:\n  browser:\n{settings}    steps:\n      - run: {run}\n"

    def errors(self, text: str) -> list[str]:
        return workflow_errors({"fixture.yml": text}, {("fixture.yml", "browser")})

    def test_contract_membership_is_pinned(self) -> None:
        commands = {
            "playwright test", "test:e2e", "capture.mjs", "capture-ptt.mjs",
            "verify:component-kit-api", "verify-package.mjs",
            "quick-v2-worker-v1.sh",
        }
        self.assertEqual(set(BROWSER_ENTRY_POINTS), commands)
        self.assertEqual(BROWSER_ENV, {
            "DBUS_SESSION_BUS_ADDRESS": "'disabled:'",
            "FC_FONTATIONS": "'1'",
            "VK_ICD_FILENAMES": "''",
        })

    def test_correct_job_passes(self) -> None:
        for run in (
            "npx playwright test",
            "|\n          npx playwright test",
            ">\n          npx playwright test",
        ):
            with self.subTest(run=run):
                self.assertEqual(self.errors(self.workflow(run)), [])

    def test_new_workflow_reports_unexpected_browser_job(self) -> None:
        errors = workflow_errors({"new-workflow.yaml": self.workflow()}, set())
        self.assertEqual(errors, ["Unexpected browser jobs: new-workflow.yaml:browser"])

    def test_browser_jobs_without_environment_are_rejected(self) -> None:
        for token in (
            *BROWSER_ENTRY_POINTS,
            "npx playwright install chromium && npx playwright test", "npx playwright   test",
            "|\n          npx playwright \\\n            test",
            "|\n          npx playwright\\\n            test",
        ):
            with self.subTest(token=token):
                errors = self.errors(self.workflow(token, env=False))
                message = "fixture.yml:browser: expected one block job-level env: mapping"
                self.assertEqual(errors, [message])

    def test_install_only_step_is_not_a_browser_job(self) -> None:
        text = self.workflow("npx playwright install chromium", env=False)
        self.assertEqual(workflow_errors({"install.yml": text}, set()), [])

    def test_changed_missing_and_duplicate_job_values_are_rejected(self) -> None:
        for name, value in BROWSER_ENV.items():
            original = f"      {name}: {value}\n"
            for replacement in (f"      {name}: 'wrong'\n", "", original * 2):
                with self.subTest(name=name, replacement=replacement):
                    errors = self.errors(self.workflow().replace(original, replacement))
                    self.assertTrue(any(f"{name} must appear once" in error for error in errors))

    def test_local_reusable_workflow_calls_do_not_hide_browser_jobs(self) -> None:
        text = (
            "jobs:\n  call:\n    needs: fresh\n    uses: ./.github/workflows/gate.yml\n"
            + self.workflow().removeprefix("jobs:\n")
        )
        self.assertEqual(self.errors(text), [])
        # The ordinary job next to the call is still inspected.
        broken = (
            "jobs:\n  call:\n    uses: ./.github/workflows/gate.yml\n"
            + self.workflow(env=False).removeprefix("jobs:\n")
        )
        self.assertEqual(
            self.errors(broken),
            ["fixture.yml:browser: expected one block job-level env: mapping"],
        )

    def test_missing_jobs_or_steps_fail_with_context(self) -> None:
        for text, message in (
            ("name: no jobs\n", "fixture.yml: expected one block jobs: mapping"),
            (
                "jobs:\n  browser:\n    uses: reusable.yml\n",
                "fixture.yml:browser: expected one block steps",
            ),
        ):
            with self.subTest(text=text):
                self.assertTrue(any(message in error for error in self.errors(text)))


if __name__ == "__main__":
    unittest.main()
