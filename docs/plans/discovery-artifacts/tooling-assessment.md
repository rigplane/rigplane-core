# Tooling Assessment — `tach` vs `import-linter`

**Phase 1, item 7 of `2026-04-29-internal-modularization-orchestrator.md`.**
Empirical dry-run of both candidate tools against the proposed layer rules.
No tooling has been wired into `pyproject.toml`, pre-commit, or CI. Configs in
this directory are drafts only — not the final production configuration.

---

## TL;DR

**Recommendation: `import-linter`.**

It expresses the proposed layer rules an order of magnitude more compactly than
`tach`, models the flat current layout cleanly via `forbidden_modules`, and its
output names *contracts* rather than file pairs — which is the right unit for a
layered architecture conversation. Both tools resolve the import graph
correctly; the difference is in maintainability of the rule set, not detection
power.

---

## 1. Side-by-side comparison

| Axis | `tach` | `import-linter` |
| --- | --- | --- |
| Version tested | 0.34.1 | 2.x (latest from PyPI as of 2026-04-30, Grimp-based graph) |
| Last release | active (multiple releases in 2026) | active (multiple releases in 2026) |
| Config location | `tach.toml` (or `pyproject.toml` table) | `.importlinter` / `setup.cfg` / `pyproject.toml` |
| Config format | TOML, one `[[modules]]` table per node | INI, one `[importlinter:contract:*]` per rule |
| Rule model | per-module `depends_on` allowlist | named contracts: `layers`, `forbidden`, `independence`, `forbidden + as_packages`, etc. |
| Layered architectures | not first-class — must be hand-encoded as N×N allowlists | first-class `layers` contract; one block covers all transitive layer rules |
| Flat package support | requires listing every top-level file as its own `[[modules]]` entry | works directly with module names; no need to enumerate top-level files unless they participate in a `forbidden` contract |
| Output | per-line `[FAIL] path:line: Cannot use 'X'. Module 'A' cannot depend on 'B'.` | grouped by contract, with collapsed import chains showing transitive paths |
| Transitive chain reporting | reports the offending direct import only | reports full chain (e.g. `web -> radio_protocol -> audio_bus -> audio`) — much more useful for refactoring |
| Performance on this repo | ~1s cold (uvx download dominates), <0.5s warm | analyzed 151 files / 434 dependencies in <1s after install |
| Install footprint | single Rust-backed binary, 16 transitive packages via uvx | pure Python, depends on `grimp` + `click`; 8 transitive packages via uvx |
| `uvx` ergonomics | `uvx tach check --dependencies` runs out of the box (parses the project from `source_roots`) | `uvx --from import-linter lint-imports` does **not** see the package by default — needs `uvx --with-editable . --from import-linter lint-imports`, or run inside `uv run` |
| CI integration | trivial: `uvx tach check --dependencies` returns non-zero on violations | trivial: `lint-imports` returns non-zero on broken contracts; need editable-install or pip-install of the package |
| pre-commit hook | official `pre-commit` hook published | official `pre-commit` hook published |
| Monorepo support | yes — `source_roots` accepts multiple roots; module paths are absolute | yes — `root_packages` accepts a list |
| `tach mod` interactive bootstrap | yes (`tach mod`) | n/a — config is hand-written |
| Auto-fix / `sync` | `tach sync` rewrites `depends_on` to the actual graph | none; user edits config |
| What it struggles with on this codebase | flat top level forces ~50 `[[modules]]` entries; one missing entry triggers spurious "module not found" warnings; `depends_on` lists balloon for `runtime` (>40 entries) | the `layers` contract reaches only the six real subpackages — top-level files require either explicit `forbidden` contracts or the (heavier) approach of moving them into subpackages first |

---

## 2. Bucketing heuristic (filename → proposed layer)

The package is currently flat: 56 `.py` files at `src/icom_lan/` top level plus
six real subpackages. To run the proposed brief rules, top-level files were
mapped to layers using filename prefix as a proxy. **This is a probe, not a
commitment.** Phase 2 must finalize the mapping by reading each file, not by
prefix matching.

| File / subpackage | Proposed layer | Reason for assignment |
| --- | --- | --- |
| `civ.py`, `protocol.py`, `transport.py`, `proxy.py`, `discovery.py`, `auth.py`, `sync.py`, `exceptions.py`, `types.py`, `env_config.py`, `capabilities.py`, `startup_checks.py`, `_optional_deps.py`, `_civ_rx.py`, `_connection_state.py`, `_control_phase.py` | `core` | low-level protocol / transport / shared dataclasses / connection FSM |
| `backends/` | `core` | per brief — backends are part of the core layer |
| `commands/`, `commander.py`, `command_map.py`, `command_spec.py` | `commands` | CI-V command builders + dispatch |
| `profiles.py`, `profiles_runtime.py`, `rig_loader.py`, `ic705.py`, `radios.py` | `profiles` | rig profile loaders + per-rig overrides |
| `radio.py`, `radio_protocol.py`, `radio_state.py`, `radio_state_snapshot.py`, `radio_initial_state.py`, `radio_reconnect.py`, `_bounded_queue.py`, `_bridge_metrics.py`, `_bridge_state.py`, `_poller_types.py`, `_queue_pressure.py`, `_runtime_protocols.py`, `_scope_runtime.py`, `_shared_state_runtime.py`, `_state_cache.py`, `_state_queries.py`, `_dual_rx_runtime.py`, `_audio_runtime_mixin.py` | `runtime` | high-level `IcomRadio`, commander queue, state cache, runtime mixins |
| `audio/`, `audio_bridge.py`, `audio_bus.py`, `audio_analyzer.py`, `audio_fft_scope.py`, `_audio_codecs.py`, `_audio_recovery.py`, `_audio_transcoder.py`, `usb_audio_resolve.py` | `audio` | audio bridge, codecs, transcoder, FFT scope |
| `scope.py`, `scope_render.py` | `scope` | scope rendering |
| `dsp/` | `dsp` | pure DSP — no internal deps allowed |
| `web/` | `web` | web server (Python side) |
| `rigctld/` | `rigctld` | rigctld TCP proxy |
| `cli.py`, `__main__.py` | `cli` | CLI entrypoints |
| `cw_auto_tuner.py`, `meter_cal.py` | **ambiguous** — assigned `runtime` (best-guess) | imports both `commands` and runtime types; could equally live as a stand-alone `tools/` layer. Flag for Phase 2. |

Ambiguous files flagged: `cw_auto_tuner.py`, `meter_cal.py`,
`_optional_deps.py` (used everywhere — currently treated as core but is
effectively a cross-cutting concern).

---

## 3. `tach` dry-run results

**Command:** `uvx tach check --dependencies --output text` from repo root with
the draft `tach.toml` (228 lines, ~50 `[[modules]]` entries) installed at
`./tach.toml` (later moved to `docs/plans/discovery-artifacts/`).

**Verdict:** ran cleanly, surfaced 358 individual `[FAIL]` lines representing
89 unique source-target module pairs.

**Top 10 violation pairs by count:**

| Count | Source → Target |
| --- | --- |
| 122 | `icom_lan.web` → `icom_lan._poller_types` |
| 49 | `icom_lan.backends` → `icom_lan._poller_types` |
| 27 | `icom_lan._civ_rx` → `icom_lan.commands` |
| 10 | `icom_lan.backends` → `icom_lan.audio` |
| 6 | `icom_lan.backends` → `icom_lan.radio` |
| 5 | `icom_lan.cli` → `icom_lan.audio_bridge` |
| 4 | `icom_lan.backends` → `icom_lan.commands` |
| 4 | `icom_lan._control_phase` → `icom_lan.transport` |
| 4 | `icom_lan._control_phase` → `icom_lan.auth` |
| 4 | `icom_lan._civ_rx` → `icom_lan.civ` |

**Top 5 source modules (where violations originate):**

| Count | Source |
| --- | --- |
| 140 | `icom_lan.web` |
| 82 | `icom_lan.backends` |
| 40 | `icom_lan._civ_rx` |
| 14 | `icom_lan._control_phase` |
| 10 | `icom_lan.sync` |

**Patterns observed:**

- **Half of all violations are bucketing artefacts, not real layer breaches.**
  The `web → _poller_types` (122) and `backends → _poller_types` (49) cases
  are because `_poller_types` was bucketed into `runtime` but is in fact a
  shared type module that everything-poller-shaped consumes. In Phase 2 it
  almost certainly belongs in `core` or in a new `commands.types` submodule.
- **Genuine architectural drift:** `backends → audio`, `backends → radio`,
  `backends → commands`, `backends → radio_state` — the `backends` package
  imports upward into runtime / audio / commands. These are real cross-layer
  imports that the brief's rules forbid. Roughly ~25 violations are of this
  kind.
- **The flat-top-level tax is brutal in tach.** Every cross-file import inside
  the current top level becomes a separate `[[modules]]` rule pair to encode.
  The `runtime` layer's `depends_on` list ended up at >40 entries — and that
  list has to be maintained by hand or by `tach sync` (which would rewrite
  it to the *current* state, defeating the point).
- **Strict-mode quirk:** when a top-level file is imported but not listed in
  `[[modules]]`, tach may emit "unknown module" warnings rather than treating
  it as out-of-scope. We had to enumerate every `_*.py` file even when its
  layer was obvious from the prefix.

**Could tach express the rules cleanly?** Yes, but tediously. The expression
is per-edge, not per-layer. Adding a new file requires deciding which layer
it belongs in *and* updating every `[[modules]]` entry that may now legally
import it.

---

## 4. `import-linter` dry-run results

**Command:** `uvx --with-editable . --from import-linter lint-imports` from
repo root with draft `.importlinter` (147 lines, 8 contracts).

**Verdict:** ran cleanly, surfaced **4 broken contracts of 8** (4 KEPT, 4
BROKEN). Across all broken contracts, 16 unique forbidden source-target
module pairs were reported.

**Contract results:**

| Contract | Status | Type |
| --- | --- | --- |
| Layered subpackages (audio/backends/commands/dsp/rigctld/web) | **BROKEN** | `layers` |
| dsp is pure (no internal `icom_lan` imports) | KEPT | `forbidden` |
| backends does not depend on runtime/web/rigctld/cli | **BROKEN** | `forbidden` |
| web does not depend on backends or cli | **BROKEN** | `forbidden` |
| rigctld does not depend on web/audio/scope/cli/backends | **BROKEN** | `forbidden` |
| commands does not depend on runtime/web/rigctld/cli/audio | KEPT | `forbidden` |
| audio subpackage does not depend on runtime/web/rigctld/cli/commands/backends | KEPT | `forbidden` |
| rigctld and web do not import each other | KEPT | `independence` |

**Layered-contract violations (only the 6 real subpackages were modelled in
the `layers` block):**

- `icom_lan.backends` → `icom_lan.commands`
- `icom_lan.backends` → `icom_lan.scope`
- `icom_lan.backends` → `icom_lan.audio`

**Forbidden-contract violations (notable cases beyond the layered set):**

- `icom_lan.backends` → `icom_lan.radio` (5 entry points: factory, ic705.core,
  ic7300.core, ic9700.core, icom7610.lan) — backends import the high-level
  `radio` module, which is the upward dep the brief rules forbid.
- `icom_lan.backends` → `icom_lan.radio_protocol`, `icom_lan._dual_rx_runtime`,
  `icom_lan._scope_runtime`, `icom_lan._audio_runtime_mixin`,
  `icom_lan._state_cache`, `icom_lan._state_queries`, `icom_lan.commander`,
  `icom_lan.audio_bus` — all reached via `icom_lan.radio`.
- `icom_lan.web` → `icom_lan.backends` — `web.web_startup` directly imports
  `backends.yaesu_cat.poller` and `backends.yaesu_cat.radio`; also
  `web.server` reaches `icom_lan.backends` via `from icom_lan import …`.
- `icom_lan.rigctld` → `icom_lan.audio`, `icom_lan.audio_bus`,
  `icom_lan.scope` — reached transitively via `rigctld.routing →
  radio_protocol → {audio_bus, scope}`.

**Patterns observed:**

- The `layers` contract is **dramatically smaller and more declarative** than
  the equivalent tach rules. One 8-line block specifies all layer ordering.
- import-linter's transitive-chain output (`A -> B -> C -> D (l.34)`) is far
  more actionable for refactoring than tach's per-edge `[FAIL]` lines: it
  shows *why* a forbidden import is reachable, not just that it is.
- `_poller_types` did not appear as a violator in import-linter output. That's
  because top-level files were not modelled as separate forbidden
  source/target sets (they're not subpackages). This is also import-linter's
  limitation: until the package is restructured, **a layered contract cannot
  enforce rules over flat top-level files** — only over real subpackages.
  Flat files require per-file `forbidden` contracts, which is comparable to
  tach's verbosity.
- The two tools agree on the architectural picture. They disagree on how
  *much* of it is currently expressible.

**Could import-linter express the rules cleanly?** Yes — for the six real
subpackages, far more cleanly than tach. For top-level files, no tool can do
much until Phase 4 actually moves them into subdirectories. Once it does,
import-linter's `layers` contract collapses to a single block.

---

## 5. Recommendation

**`import-linter`** for production enforcement, with the following rationale:

- The `layers` contract maps **1-to-1** onto the brief's "Allowed dependency
  directions" table. One contract block, ~10 lines, covers what tach needs
  ~50 module entries to express.
- Transitive-chain output (`A -> B -> C`) is the right diagnostic for a
  layered architecture. Tach's per-edge output is harder to use during
  refactoring because it doesn't show *why* the dep is reachable.
- The `forbidden` and `independence` contracts give a clean way to encode
  carve-outs that don't fit a strict total order (e.g. "rigctld and web
  must be independent siblings, not stacked").
- Pure-Python implementation keeps the toolchain consistent with the rest of
  the project. `tach`'s Rust binary is fine but adds a third native build
  artefact to debug if a CI runner has the wrong wheel.
- Phase 4 will physically move files into subpackages. Once that's done,
  the `layers` contract works without any per-file enumeration — exactly
  the maintenance characteristic we want.

Tach is not bad. It's actively developed, has `tach mod` interactive
bootstrap, has an `--exact` mode for catching dead allowlist entries, and its
Rust speed is real. If the team values `tach sync` (auto-update of `depends_on`)
or wants the interactive bootstrapping flow, that's a defensible reason to
prefer it. But for the *layered* model the brief actually proposes,
`import-linter` is the closer fit.

---

## 6. Adoption risk

Concrete obstacles to bringing `import-linter` into pre-commit + CI in Phase 2:

1. **Cannot enforce against the flat layout today.** If we add `lint-imports`
   to CI now, it surfaces 4 broken contracts immediately. We must either
   (a) defer enforcement until Phase 4 has moved files into subpackages, or
   (b) commit a *baseline* config that allows the current violations and
   tightens after each migration step. The brief's tooling-integration plan
   (Phase 2 deliverable §8) should pick one. Recommend (a): introduce
   import-linter at the same step that creates the new layer dirs.
2. **`uvx --with-editable .` is the right invocation.** Plain
   `uvx --from import-linter lint-imports` cannot find the package and
   silently reports 0 contracts. The CI command must be either
   `uv run lint-imports` (after declaring import-linter as an optional dep
   group, which the brief's "no new dependencies" rule may forbid) or
   `uvx --with-editable . --from import-linter lint-imports`. The latter
   works without touching `pyproject.toml`.
3. **Top-level files are not under enforcement until they move.** This is
   not a tool problem — neither tool can enforce a layer rule on a file
   whose physical location is ambiguous. Stakeholders should not expect
   import-linter to flag e.g. `_poller_types.py` until it lives in a
   layer-named directory.
4. **Pre-commit hook timing.** The official `import-linter` pre-commit hook
   does not install the package being linted; it relies on the system
   Python's import path. We would need a `language: system` hook that calls
   `uv run lint-imports` to make it work with this project's `uv` workflow.
   Verify this in Phase 2 before committing the hook config.
5. **Re-export shims may launder violations.** Phase 2's re-export shim
   policy (`from icom_lan.<new_layer>.<module> import *` in old locations)
   would route imports through a layer module that is allowed. import-linter
   resolves these by following the import chain — verify that re-exports
   don't accidentally hide layer violations during the migration.
6. **CI cost is negligible.** `lint-imports` ran in <1s on this codebase.
   No throughput risk.

**No blocker** to adoption was identified. The risks above are sequencing
choices for Phase 2, not show-stoppers.

---

## Reproducing the runs

```bash
# tach
cp docs/plans/discovery-artifacts/tach-config-draft.toml ./tach.toml
uvx tach check --dependencies --output text
rm ./tach.toml

# import-linter
cp docs/plans/discovery-artifacts/importlinter-config-draft.ini ./.importlinter
uvx --with-editable . --from import-linter lint-imports
rm ./.importlinter
```

Both configs assume the repository's current flat layout. They will need to
be rewritten — and should shrink dramatically — once Phase 4 moves files
into the proposed subpackages.
