"""MOR-3985: rigplane.dev PostHog snippet is cookieless and off the rigplane.com project."""

import re
from pathlib import Path

SNIPPET = (
    Path(__file__).resolve().parents[1]
    / "docs/overrides/partials/integrations/analytics/custom.html"
)
RIGPLANE_COM_KEY = "phc_yTcX74uByJQwDsKDop2KGHACRE6GrRuYmjeSrEt9ff3R"


def _init_options() -> str:
    text = SNIPPET.read_text(encoding="utf-8")
    match = re.search(
        r'posthog\.init\("(phc_[A-Za-z0-9]+)", \{(.*?)\n    \}\);', text, re.S
    )
    assert match, "posthog.init call not found"
    assert match.group(1) != RIGPLANE_COM_KEY, "still the rigplane.com project key"
    return match.group(2)


def test_snippet_is_cookieless_without_person_profiles() -> None:
    options = _init_options()
    assert 'cookieless_mode: "always"' in options
    assert 'person_profiles: "never"' in options
    assert "persistence:" not in options


def test_snippet_keeps_replay_autocapture_and_surveys_off() -> None:
    options = _init_options()
    for line in (
        "autocapture: false",
        "disable_session_recording: true",
        "disable_surveys: true",
        'capture_pageview: "history_change"',
    ):
        assert line in options
