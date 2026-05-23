---
robots: noindex, follow
---

# Hamlib Provider Rollout Handoff

This note documents the completed public Core rollout for the first Hamlib
provider path. It is for future agents and contributors working in
`rigplane-core`; do not copy private Pro or Strategy material into this file.

The design contract remains
[`docs/plans/2026-05-23-hamlib-provider-contract.md`](../plans/2026-05-23-hamlib-provider-contract.md).
This page is the post-rollout map: where the implementation lives, what Core
owns, and where the public/private boundary sits.

## Accepted integration boundary

The accepted initial Hamlib boundary is:

```text
RigPlane Core -> TCP rigctld text protocol -> external rigctld -> Hamlib -> radio
```

Core does not embed or link `libhamlib` for this rollout. Direct `libhamlib`
remains deferred unless a future spike explicitly proves the need and includes
acceptance criteria for:

- licensing and redistribution impact;
- process isolation or other crash-containment strategy;
- public API compatibility and migration from the external `rigctld` path;
- tests proving parity with the external boundary.

Future work should extend the external `rigctld` provider first unless an issue
with those criteria supersedes this decision.

## Core ownership

Core owns generic behavior that is useful in the public open-core repository:

| Area | Public Core surface |
| --- | --- |
| Provider/protocol behavior | `src/rigplane/backends/rigctld_client/` implements the external `rigctld` client backend under the existing `Radio` protocol. |
| Discovery candidate schema | `DiscoveryCandidate` and `DiscoveryEvidence` are exported through `src/rigplane/backends/discovery.py` and implemented in `src/rigplane/backends/hamlib_probe.py`. |
| Serial inventory and model metadata | `src/rigplane/backends/discovery.py` inventories serial candidates; `src/rigplane/backends/hamlib_models.py` loads/parses Hamlib model metadata from installed `rigctld`/`rigctl` tools. |
| Safe read-only probing/ranking | `src/rigplane/backends/hamlib_probe.py` limits validation to read-only `\get_info`, `f`, and `m` operations, with redacted audit records. |
| Public CLI/docs | `src/rigplane/cli/_discover_hamlib.py` builds the public assisted-discovery payload and human output; operator-facing docs remain in the public docs site. |
| Tests | `tests/fake_rigctld.py`, `tests/test_fake_rigctld.py`, `tests/test_hamlib_external_rigctld_contract.py`, `tests/test_hamlib_models.py`, `tests/test_hamlib_probe.py`, and `tests/test_rigctld_client_backend.py`. |

Provider behavior must stay capability-driven. `web/`, `rigctld/`, CLI command
execution, and Pro-facing consumers should branch on RigPlane capabilities, not
Hamlib model IDs, Hamlib command names, or rigctld command letters.

## Pro and Strategy boundaries

`rigplane-pro` may build managed product workflows on top of these Core outputs,
but it should consume them rather than reimplementing Hamlib probing or ranking.
In particular, Pro should use Core candidates, evidence, confidence, and
read-only validation status as inputs to managed setup UX.

Pro owns:

- managed setup and onboarding UX;
- diagnostics and support evidence presentation;
- packaging, bundled binary, update, and legal decisions;
- redaction-specific support workflows.

Strategy owns private validation matrices and private decision records. Public
Core docs may reference that such validation exists, but must not copy private
matrix entries, customer context, private device notes, or proprietary decision
records into this repository.

## Rollout references

The rollout was split into public Core issues and PRs:

| Issue | PR | Delivered |
| --- | --- | --- |
| [#1576](https://github.com/rigplane/rigplane-core/issues/1576) | [#1583](https://github.com/rigplane/rigplane-core/pull/1583) | Package/docs alignment with the Hamlib provider strategy. |
| [#1577](https://github.com/rigplane/rigplane-core/issues/1577) | [#1584](https://github.com/rigplane/rigplane-core/pull/1584) | Provider contract and discovery candidate schema. |
| [#1578](https://github.com/rigplane/rigplane-core/issues/1578) | [#1585](https://github.com/rigplane/rigplane-core/pull/1585) | Fake rigctld simulator and contract tests. |
| [#1579](https://github.com/rigplane/rigplane-core/issues/1579) | [#1586](https://github.com/rigplane/rigplane-core/pull/1586) | External rigctld client backend with minimal capabilities. |
| [#1580](https://github.com/rigplane/rigplane-core/issues/1580) | [#1587](https://github.com/rigplane/rigplane-core/pull/1587) | Serial inventory and Hamlib model metadata cache. |
| [#1581](https://github.com/rigplane/rigplane-core/issues/1581) | [#1588](https://github.com/rigplane/rigplane-core/pull/1588) | Safe read-only Hamlib probe ranking internals. |
| [#1582](https://github.com/rigplane/rigplane-core/issues/1582) | [#1589](https://github.com/rigplane/rigplane-core/pull/1589) | Public discover CLI validation for Hamlib candidates. |

Issue [#1590](https://github.com/rigplane/rigplane-core/issues/1590) and
Linear `MOR-32` track this handoff documentation.

## Contributor checklist

When changing the Hamlib provider path in Core:

- keep the external `rigctld` boundary unless a later accepted spike supersedes
  it;
- keep discovery read-only and privacy-safe;
- keep candidate evidence structured enough for CLI, docs, and Pro consumers;
- add or update fake rigctld tests for backend/probe behavior;
- document any public API, CLI, config, or rigctld wire behavior change in the
  issue and PR;
- leave managed UX, packaging/legal, and private support workflows out of this
  repository.
