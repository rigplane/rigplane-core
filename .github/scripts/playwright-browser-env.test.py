"""Pin Chromium startup environment using only stdlib text parsing and unittest."""

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
    ("quick.yml", "quick"),
    ("full.yml", "capture-harness"),
    ("visual.yml", "visual"),
}
BROWSER_ENTRY_POINTS = (
    "playwright test",  # Playwright CLI; frontend/playwright.config.ts and sibling configs.
    "test:e2e",  # frontend/package.json maps this family to the Playwright CLI.
    "capture.mjs",  # frontend/fixtures/capture.mjs launches Chromium.
    "capture-ptt.mjs",  # frontend/fixtures/capture-ptt.mjs launches Chromium.
    "verify:component-kit-api",  # frontend/component-kit-api/verify-package.mjs launches Chromium.
    "verify-package.mjs",  # frontend/component-kit-api/verify-package.mjs (direct invocation).
)


class WorkflowFormatError(ValueError):
    """The text parser cannot inspect a workflow's jobs or steps safely."""


def _jobs(workflow: str, text: str) -> dict[str, tuple[str, list[str]]]:
    if re.search(r"(^|[ :])[&*][A-Za-z_]|<<:", text, re.MULTILINE):
        raise WorkflowFormatError(f"{workflow}: YAML anchors, aliases and merges are unsupported")
    markers = list(re.finditer(r"(?m)^jobs:[ \t]*(?:#.*)?$", text))
    if len(markers) != 1:
        raise WorkflowFormatError(f"{workflow}: expected one block jobs: mapping")
    body = re.split(r"(?m)^[^\s#]", text[markers[0].end() :], maxsplit=1)[0]
    headers = list(re.finditer(r"(?m)^  (?!#)(\S[^\n]*)", body))
    if not headers:
        raise WorkflowFormatError(f"{workflow}: expected jobs in the jobs: mapping")
    jobs = {}
    for index, header in enumerate(headers):
        match = re.fullmatch(r"([\w-]+):[ \t]*(?:#.*)?", header.group(1))
        if match is None:
            raise WorkflowFormatError(f"{workflow}: unsupported job header {header.group(1)!r}")
        name = match.group(1)
        if name in jobs:
            raise WorkflowFormatError(f"{workflow}:{name}: duplicate job")
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        block = body[header.end() : end]
        markers = list(re.finditer(r"(?m)^    steps:[ \t]*(?:#.*)?$", block))
        if len(markers) != 1:
            raise WorkflowFormatError(f"{workflow}:{name}: expected one block steps: sequence")
        marker = markers[0]
        rest = block[marker.end() :]
        following = re.search(r"(?m)^    (?!#)\S", rest)
        end = following.start() if following else len(rest)
        steps = re.split(r"(?m)^      -[ \t]+", rest[:end])[1:]
        if not steps or any(not re.match(r"[\w-]+[ \t]*:", step) for step in steps):
            raise WorkflowFormatError(f"{workflow}:{name}: unsupported or empty steps: sequence")
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


def _envs(text: str, indent: int) -> list[tuple[str, str]]:
    envs = []
    pattern = rf"(?m)^{' ' * indent}(?:env|'env'|\"env\")[ \t]*:[ \t]*(.*)$"
    for match in re.finditer(pattern, text):
        body = re.split(
            rf"(?m)^ {{0,{indent}}}[^\s#]", text[match.end() :], maxsplit=1
        )[0]
        value = match.group(1).strip()
        envs.append(("" if value.startswith("#") else value, body))
    return envs


def _job_env_errors(label: str, settings: str) -> list[str]:
    envs = _envs(settings, 4)
    if len(envs) != 1 or envs[0][0]:
        return [f"{label}: expected one block job-level env: mapping (no flow form)"]
    errors = []
    for name, expected in BROWSER_ENV.items():
        key = rf"(?:{name}|'{name}'|\"{name}\")"
        values = re.findall(rf"(?m)^      {key}:[ \t]*(.*)$", envs[0][1])
        if values != [expected]:
            errors.append(f"{label}: {name} must appear once as {expected}, found {values}")
    return errors


def _step_env_errors(label: str, step: str) -> list[str]:
    errors = []
    for name in BROWSER_ENV:
        if name in _run(step):
            errors.append(f"{label}: run mentions {name}; protected variables must stay job-level")
    for value, body in _envs(step, 8):
        visible = re.sub(r"\$\{\{.*?\}\}", "", value + "\n" + body, flags=re.DOTALL)
        content = "\n".join(
            line for line in visible.splitlines() if not line.lstrip().startswith("#")
        ).strip()
        if value or content.startswith("{"):
            for name in BROWSER_ENV:
                if re.search(rf"\b{name}\b", visible):
                    errors.append(f"{label}: step overrides {name} in inline env")
            if not content.startswith("{"):
                errors.append(f"{label}: unsupported step env: mapping {value!r}")
        else:
            for name in BROWSER_ENV:
                key = rf"(?:{name}|'{name}'|\"{name}\")"
                if re.search(rf"(?m)^ {{9,}}{key}:[ \t]*", visible):
                    errors.append(f"{label}: step overrides {name} in block env")
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
            label = f"{workflow}:{job}"
            for index, step in enumerate(steps, 1):
                if re.search(r"(?m)^        uses[ \t]*:[ \t]*['\"]?\./", step):
                    errors.append(
                        f"{label}: local actions are not scanned by this guard; "
                        "extend it before adding one"
                    )
                errors.extend(_step_env_errors(f"{label}:step {index}", step))
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
        }
        self.assertEqual(set(BROWSER_ENTRY_POINTS), commands)
        self.assertEqual(BROWSER_ENV, {
            "DBUS_SESSION_BUS_ADDRESS": "'disabled:'",
            "FC_FONTATIONS": "'1'",
            "VK_ICD_FILENAMES": "''",
        })

    def test_correct_job_passes(self) -> None:
        self.assertEqual(self.errors(self.workflow()), [])
        self.assertEqual(self.errors(self.workflow().replace("env:", "env :")), [])
        self.assertEqual(self.errors(self.workflow().replace("run:", "run :")), [])

    def test_new_workflow_reports_unexpected_browser_job(self) -> None:
        errors = workflow_errors({"new-workflow.yaml": self.workflow(env=False)}, set())
        self.assertIn("Unexpected browser jobs: new-workflow.yaml:browser", errors)
        self.assertTrue(any("job-level env" in error for error in errors))

    def test_component_kit_only_job_is_detected_without_environment(self) -> None:
        errors = self.errors(self.workflow("npm run verify:component-kit-api", env=False))
        message = "fixture.yml:browser: expected one block job-level env"
        self.assertTrue(any(message in error for error in errors))
        self.assertFalse(any("Missing browser jobs" in error for error in errors))

    def test_each_browser_entry_point_is_detected(self) -> None:
        for token in (
            "playwright test", "test:e2e", "capture.mjs", "capture-ptt.mjs",
            "verify:component-kit-api", "verify-package.mjs",
            "npx playwright install chromium && npx playwright test", "npx playwright   test",
            "|\n          npx playwright \\\n            test",
            "|\n          npx playwright\\\n            test",
        ):
            with self.subTest(token=token):
                errors = self.errors(self.workflow(token, env=False))
                self.assertTrue(any("job-level env" in error for error in errors))

    def test_commented_blocks_and_yaml_references(self) -> None:
        for run, message in (
            ("| # browser smoke\n          node frontend/fixtures/capture.mjs", "job-level env"),
            ("> # browser smoke\n          node frontend/fixtures/capture.mjs", "job-level env"),
            ("*capture", "anchors, aliases and merges"),
            ("&capture echo harmless", "anchors, aliases and merges"),
            ("echo harmless\n        env: {<<: {A: b}}", "anchors, aliases and merges"),
        ):
            self.assertTrue(any(message in e for e in self.errors(self.workflow(run, env=False))))

    def test_local_action_is_rejected(self) -> None:
        text = "jobs:\n  browser:\n    steps:\n      - uses: ./.github/actions/browser\n"
        self.assertTrue(any("local actions are not scanned" in e for e in self.errors(text)))

    def test_run_mentions_of_protected_names_are_rejected(self) -> None:
        for name in ("DBUS_SESSION_BUS_ADDRESS", "FC_FONTATIONS", "VK_ICD_FILENAMES"):
            for run in (
                f"echo '{name}=0' >> \"$GITHUB_ENV\"",
                f"{name}=0", f"export {name}=0", f"unset {name}",
                f"|\n          # {name}\n          echo harmless",
            ):
                text = self.workflow(run + "\n      - run: node frontend/fixtures/capture.mjs")
                self.assertTrue(any(f"run mentions {name}" in e for e in self.errors(text)))

    def test_install_only_step_is_not_a_browser_job(self) -> None:
        text = self.workflow("npx playwright install chromium", env=False)
        self.assertEqual(workflow_errors({"install.yml": text}, set()), [])

    def test_inline_and_block_overrides_of_each_variable_are_rejected(self) -> None:
        for name in BROWSER_ENV:
            for mapping in (
                f"{{ {name}: '0' }}",
                f"{{{name}: '0', B: y}}",
                f"\n          {{{name}: '0'}}",
                f"\n          {name}: '0'",
                f"\n            {name}: '0'",
                f"\n          '{name}': '0'",
            ):
                with self.subTest(name=name, mapping=mapping):
                    # Use a non-browser step in a separate, non-browser workflow.
                    text = self.workflow("echo harmless", env=False) + f"        env : {mapping}\n"
                    errors = workflow_errors({"non-browser.yaml": text}, set())
                    self.assertTrue(any(f"step overrides {name}" in error for error in errors))

    def test_multiline_flow_override_is_rejected(self) -> None:
        text = self.workflow() + "        env: {\n          FC_FONTATIONS: '0'\n        }\n"
        self.assertTrue(any("step overrides FC_FONTATIONS" in e for e in self.errors(text)))

    def test_expression_references_are_not_environment_keys(self) -> None:
        for name in BROWSER_ENV:
            for mapping in (
                f"{{ OTHER: '${{{{ env.{name} }}}}' }}",
                f"\n          OTHER: '${{{{ env.{name} }}}}'",
            ):
                with self.subTest(name=name, mapping=mapping):
                    self.assertEqual(self.errors(self.workflow() + f"        env: {mapping}\n"), [])

    def test_changed_missing_and_duplicate_job_values_are_rejected(self) -> None:
        for name, value in BROWSER_ENV.items():
            original = f"      {name}: {value}\n"
            for replacement in (f"      {name}: 'wrong'\n", "", original * 2):
                with self.subTest(name=name, replacement=replacement):
                    errors = self.errors(self.workflow().replace(original, replacement))
                    self.assertTrue(any(f"{name} must appear once" in error for error in errors))

    def test_flow_job_environment_is_rejected(self) -> None:
        text = self.workflow(env=False).replace("    steps:", "    env: {}\n    steps:")
        self.assertTrue(any("no flow form" in error for error in self.errors(text)))

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
