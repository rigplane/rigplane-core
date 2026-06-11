"""Golden-gate normalization and regression gating for validation artifacts.

A raw ``ValidationArtifact`` is volatile: every run differs in
``core_version``/``core_commit``, ``generated_at``, per-check timestamps,
evidence payloads, and the transport endpoint. :func:`normalize_artifact`
projects an artifact dict down to its STABLE, status-only comparison key —
``(check_id, status, declaration, failure_domain)`` rows plus the radio
identity — so two equivalent runs serialize identically and a committed
"golden" can be diffed without spurious noise.

:func:`gate_artifacts` compares a current artifact against a golden one and
classifies every difference as a blocking regression (a check that
disappeared, entered fail/blocked, left pass, or drifted in declaration /
failure domain) or a non-blocking improvement/addition.

Like :mod:`rigplane.validation.comparison`, this module operates entirely on
plain ``dict`` objects (the output of ``ValidationArtifact.to_dict()`` or a
JSON-loaded equivalent) and imports only ``rigplane.validation.schema`` enums
and stdlib — no backends, no CLI.
"""

from __future__ import annotations

from dataclasses import dataclass

from rigplane.validation.schema import CheckStatus

_PASS = CheckStatus.PASS.value
_FAILING_STATUSES: frozenset[str] = frozenset(
    {CheckStatus.FAIL.value, CheckStatus.BLOCKED.value}
)

# The stable per-check key fields, in output order.
_KEY_FIELDS = ("status", "declaration", "failure_domain")


def _normalized_rows(artifact: dict[str, object]) -> list[dict[str, object]]:
    """Collect the stable per-check rows from a raw OR normalized artifact dict.

    Raw artifacts carry ``levels[*].checks[*]``; normalized ones carry a flat
    ``checks`` list. Malformed entries are skipped defensively (mirroring
    ``comparison._index``) so callers never raise on partial data.
    """
    raw_checks: list[object] = []
    levels_obj = artifact.get("levels")
    if isinstance(levels_obj, list):
        for level in levels_obj:
            if isinstance(level, dict):
                checks_obj = level.get("checks")
                if isinstance(checks_obj, list):
                    raw_checks.extend(checks_obj)
    else:
        checks_obj = artifact.get("checks")
        if isinstance(checks_obj, list):
            raw_checks.extend(checks_obj)

    rows: list[dict[str, object]] = []
    for check in raw_checks:
        if not isinstance(check, dict):
            continue
        check_id = check.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            continue
        row: dict[str, object] = {"check_id": check_id}
        for field_name in _KEY_FIELDS:
            value = check.get(field_name)
            if value is not None:
                row[field_name] = value
        rows.append(row)
    rows.sort(key=lambda row: str(row["check_id"]))
    return rows


def normalize_artifact(artifact: dict[str, object]) -> dict[str, object]:
    """Project *artifact* down to its stable, status-only comparison key.

    Keeps the radio identity, mode, and per-check
    ``(check_id, status, declaration, failure_domain)`` rows sorted by
    ``check_id``. Strips everything volatile: core version/commit, timestamps
    and durations, evidence, summaries, and the transport endpoint.

    Idempotent: normalizing an already-normalized dict is a no-op, so goldens
    stored normalized on disk can be fed back through this function safely.
    """
    radio: dict[str, object] = {}
    radio_obj = artifact.get("radio")
    if isinstance(radio_obj, dict):
        radio = {
            "model": radio_obj.get("model"),
            "profile_id": radio_obj.get("profile_id"),
        }
    return {
        "schema_version": artifact.get("schema_version"),
        "radio": radio,
        "mode": artifact.get("mode"),
        "checks": _normalized_rows(artifact),
    }


@dataclass(frozen=True, slots=True)
class GateReport:
    """Outcome of gating a current artifact against a golden one."""

    regressions: list[str]
    improvements: list[str]
    additions: list[str]
    matched: int

    @property
    def ok(self) -> bool:
        """True when no blocking regression was found."""
        return not self.regressions


def _row_key(row: dict[str, object]) -> tuple[object, ...]:
    return tuple(row.get(field_name) for field_name in _KEY_FIELDS)


def gate_artifacts(current: dict[str, object], golden: dict[str, object]) -> GateReport:
    """Diff *current* against *golden* on the normalized comparison key.

    Both inputs may be raw ``ValidationArtifact.to_dict()`` dicts or
    already-normalized dicts; each is normalized first.

    Blocking regressions:

    * a check present in the golden but missing from the current run;
    * a check (new or existing) whose current status is fail/blocked when the
      golden did not record it as such;
    * a check that left ``pass`` for any other status;
    * declaration or failure-domain drift on a check that did not improve to
      ``pass``.

    Non-blocking:

    * ``improvements`` — a previously non-passing check now passes (regen the
      golden to adopt);
    * ``additions`` — a new, non-failing check absent from the golden.
    """
    current_idx = {str(row["check_id"]): row for row in _normalized_rows(current)}
    golden_idx = {str(row["check_id"]): row for row in _normalized_rows(golden)}

    regressions: list[str] = []
    improvements: list[str] = []
    additions: list[str] = []
    matched = 0

    for check_id in sorted(set(current_idx) | set(golden_idx)):
        cur = current_idx.get(check_id)
        gold = golden_idx.get(check_id)

        if cur is None:
            golden_status = None if gold is None else gold.get("status")
            regressions.append(
                f"{check_id}: missing from current run "
                f"(golden status {golden_status!r})"
            )
            continue
        if gold is None:
            if cur.get("status") in _FAILING_STATUSES:
                regressions.append(
                    f"{check_id}: new check with status {cur.get('status')!r}"
                )
            else:
                additions.append(
                    f"{check_id}: new check with status {cur.get('status')!r}"
                )
            continue
        if _row_key(cur) == _row_key(gold):
            matched += 1
            continue

        changes = ", ".join(
            f"{field_name} {gold.get(field_name)!r} -> {cur.get(field_name)!r}"
            for field_name in _KEY_FIELDS
            if gold.get(field_name) != cur.get(field_name)
        )
        is_improvement = (
            cur.get("status") == _PASS
            and gold.get("status") != _PASS
            and cur.get("declaration") == gold.get("declaration")
        )
        if is_improvement:
            improvements.append(f"{check_id}: {changes}")
        else:
            regressions.append(f"{check_id}: {changes}")

    return GateReport(
        regressions=regressions,
        improvements=improvements,
        additions=additions,
        matched=matched,
    )


def format_gate_report(report: GateReport, *, golden_path: str) -> str:
    """Render a concise human-readable gate summary."""
    lines: list[str] = []
    verdict = "PASS" if report.ok else "FAIL"
    lines.append(
        f"Golden gate: {verdict} vs {golden_path} "
        f"({report.matched} check(s) match, "
        f"{len(report.regressions)} regression(s))"
    )
    if report.regressions:
        lines.append("Regressions:")
        lines.extend(f"  - {item}" for item in report.regressions)
    if report.improvements:
        lines.append("Improvements (regen the golden to adopt):")
        lines.extend(f"  - {item}" for item in report.improvements)
    if report.additions:
        lines.append("New checks (regen the golden to adopt):")
        lines.extend(f"  - {item}" for item in report.additions)
    return "\n".join(lines)


__all__ = [
    "GateReport",
    "format_gate_report",
    "gate_artifacts",
    "normalize_artifact",
]
