# Cross-surface artifact proof (MOR-1091)

Verification runbook. Proves that **one built Core web artifact** — the
`frontend/` Svelte/Vite SPA, built once from a pinned Core commit — is the
exact same bytes wherever it is consumed: a browser, RigPlane Pro's packaged
Tauri shell, and a RigPlane Station appliance. No surface gets its own
rebuild; each surface either serves the identical artifact or the gap is
recorded here by name. Verification-only; no production code added.

**Artifact boundary.** `docs/architecture/open-core-policy.md` §1/§7: "one
binary, two tiers" — Pro injects UI extensions into the open-core frontend
rather than shipping a second build; the frontend must render identically
across four WebView families. This runbook is the executable proof step for
that claim, scoped to the ticket's four named rows.

---

## 1. Core SHA + web-asset digest

```bash
git -C <core-checkout> rev-parse HEAD

cd frontend && npm ci && npm run build   # → frontend/dist/

# Canonical digest — cd INTO the artifact root first, so hashed paths are
# root-relative ("./assets/x.js"), not invocation-relative. Two builds of
# IDENTICAL content hashed with mismatched path prefixes ("frontend/dist/…"
# vs "../src/rigplane/web/static/…") produce DIFFERENT final digests — hit
# this during the pass; a naive `find dist | sha256sum` recipe is a trap.
digest_dir() {
  ( cd "$1" && find . -type f | LC_ALL=C sort | xargs shasum -a 256 | shasum -a 256 | awk '{print $1}' )
}
digest_dir frontend/dist
```

**No existing digest convention exists** — `quick.yml`/`full.yml` build the
SPA and `cp -r dist/* ../src/rigplane/web/static/` with no checksum step.
This recipe is new; nothing else in the repo depends on it.

**Determinism — verified, not assumed.** Built 3x independently this pass:
twice via `npm run build`, once via RigPlane Pro's own
`scripts/release/build-core-frontend.sh <core-checkout>` (read-only reuse of
Pro's packaging script, §3.2). All three produced the same digest, 77 files,
no filename-set drift:

```
17f416dd4a75693b5847dd518af9de69ac483ff17ff2b24d1f2a62b5180b2d9c   (x3)
```

Vite's content hashing leaves no timestamp/random leak into filenames or
bundle bytes. Verified on one machine only (macOS, Node v26.4.0, npm
11.17.0) — cross-OS determinism (notably Linux CI) is a design expectation
(content hashing is platform-independent), not independently re-measured.

---

## 2. The four required rows

Reused from the existing MOR-1070/1085/1086 browser fixture harness
(`frontend/fixtures/`) and component-test suite — no new coverage written,
per "existing packaging smoke hooks only."

| Row | Reused from | Note |
|---|---|---|
| **Topology** | `capture.mjs` MATRIX §B, "the four topology pairs": `topology-1-single`, `topology-1-ab`, `topology-2-ab-shared`, `topology-2-main-sub` (+ `--reference` twins) | Direct match — harness names this set itself. |
| **Workspace fallback** | `topology-2-main-sub--planned` fixture (`catalog.ts` `PLANNED_FIXTURES`) | **Interpretation, flagged**: fixture's own comment calls it "default workspace — no operator preference." No fixture is literally named `workspace-fallback`; a reviewer should correct if a more specific one is intended. |
| **Presentation switch** | `presentation-switch-tx/-resources/lazy-presentation.component.test.ts` (MOR-1086) | Direct match — real `App.svelte` + `TxController` stack, skin switch under a live TX key. |
| **TX indication** | `capture.mjs` MATRIX §F: `tx-phase-rx/-pending/-tx/-fault` (+ variants) | Direct match — RX/pending/TX/fault key-authority states. |

```bash
node frontend/fixtures/capture.mjs --only topology     # topology + workspace fallback
node frontend/fixtures/capture.mjs --only tx-phase      # TX indication
cd frontend && npx vitest run \
  src/__tests__/presentation-switch-tx.component.test.ts \
  src/__tests__/presentation-switch-resources.component.test.ts \
  src/__tests__/lazy-presentation.component.test.ts       # presentation switch
```

---

## 3. Per-surface status

### 3.1 Browser — PROVEN HERE

- Build determinism: 3/3 identical digest (§1).
- `--only topology`: **15/15 captures PASS**, all assertions green, 0 invalid.
- `--only tx-phase`: **11/11 captures PASS**, all assertions green, 0 invalid.
- Presentation-switch vitest: **3 files / 28 tests PASS**.
- `frontend/dist/` digest == `src/rigplane/web/static/` digest after the same
  `cp -r dist/*` step CI performs — the server would serve exactly what was
  built, not a stale copy.

Cross-WebView-engine pixel fidelity (WKWebView/WebView2/WebKitGTK) is a
separate, not-yet-executed claim — see §5.

### 3.2 Pro / Tauri — PARTIAL: packaging script proven, launch DEFERRED

**Proven:** Pro's `scripts/release/build-core-frontend.sh <CORE_DIR>` (a
positional-arg override, existing tooling) run read-only against this
worktree produced the identical digest (§1, build 3) — Pro's own unmodified
packaging script, pointed at this exact commit, builds the same bytes.
Nothing in `rigplane-pro` was read beyond that script or modified.

**Not executed, named why:**

- **Launching Pro's companion/Tauri shell against this artifact.**
  `rigplane-pro/pyproject.toml` pins Core via
  `[tool.uv.sources] rigplane = { path = "../rigplane-core", editable = true }`
  — a hardcoded sibling path, no env/CLI override. Here that resolves to
  `~/Projects/rigplane-core`, the **main checkout this task is barred from
  touching**. Closing this needs either building inside the barred checkout,
  editing Pro's `pyproject.toml` (barred, read-only), or a non-standard
  `PYTHONPATH` shadow-import — all ruled out as out of bounds.
- **The native Tauri WebView shell** (`cargo run -p rigplane-pro-tauri`, or
  signed `make release-macos`). The signed path uses real Apple Developer ID
  signing/notarization — disproportionate to a verification pass regardless
  of the sibling-path issue. The unsigned debug path needs a GUI-capable
  session, not attempted.

**Named requirement to close:** (a) owner approval to build inside
`~/Projects/rigplane-core` for a one-off smoke (needs a human decision), or
(b) a CI job checking out `rigplane-core`+`rigplane-pro` as true siblings at
matching SHAs — the pattern `rigplane-pro/scripts/local-act-ci.sh`'s
`RIGPLANE_PRO_RIGPLANE_DIR` env var already exists for — running
`cargo run -p rigplane-pro-tauri` headed/virtual-display, then re-running §2
against the live WebView.

### 3.3 Station — DEFERRED, structurally inapplicable at this commit

`rigplane-station` (present locally, not otherwise touched) pins Core by
**exact registry version**: `dependencies = ["rigplane==2.11.1"]`, no
path-dep, no sibling checkout.

**Finding:** this commit is **431 commits ahead of tag `v2.11.1`**
(`git rev-list v2.11.1..HEAD` = 431), and `pyproject.toml`'s version string
is still the unbumped `2.11.1`. Station only ever consumes a *published,
tagged* release by exact version — there is no source-level path for it to
consume an arbitrary untagged commit. Proving identity here needs either:

1. This commit (or later) shipping as a tagged release, then
   `rigplane-station`'s pin-bump PR (`dispatch-downstream.yml`, triggers on
   `release: published`, already wired) landing, or
2. An owner-approved one-off pin override for a pre-release smoke — out of
   scope; this task was given no access instructions for a third repo.

Real Pi hardware, the `iwd` Wi-Fi backend, and the GPIO reset button are
further limitations, moot given the version-pin blocker above.

---

## 4. Evidence table

| Field | Value |
|---|---|
| Core SHA | `ccbc17973910fde38dc3f3f63cd05e9449bfbd1b` |
| Branch | `codex/mor-1091-cross-surface-proof` (worktree, off `origin/main`) |
| `pyproject.toml` version | `2.11.1` (unbumped; 431 commits ahead of tag `v2.11.1`@`997cca0e`) |
| `frontend/package.json` version | `2.0.0` |
| Web-asset digest | `17f416dd4a75693b5847dd518af9de69ac483ff17ff2b24d1f2a62b5180b2d9c` |
| Determinism | 3/3 independent builds identical |
| Build tooling | Node v26.4.0, npm 11.17.0, macOS arm64 |

| Row | Browser | Pro/Tauri | Station |
|---|---|---|---|
| Topology | PASS 15/15 | digest-only (§3.2) | DEFERRED (§3.3) |
| Workspace fallback | PASS 33/33 assertions | same | same |
| Presentation switch | PASS 3 files/28 tests | same | same |
| TX indication | PASS 11/11 | same | same |

CI/run links (placeholders — this pass ran locally):

- Browser leg CI run: `<quick.yml/full.yml run matching the Core SHA above>`
- Pro packaging-script run: `<cross-repo CI job once §3.2's requirement lands>`
- Station pin-bump PR: `<rigplane-station PR from dispatch-downstream.yml once this ships tagged>`

---

## 5. Explicitly out of scope

- Cross-WebView-engine pixel fidelity (open-core-policy §7 names the real
  risk: `backdrop-filter`, `color-mix()`, container queries) — this runbook
  proves DOM/behavioral identity, not pixel identity across all four engines.
- Real hardware on any surface.
- Full signed Tauri installer build — never attempted, real Apple signing
  credentials, disproportionate to a verification pass.

## 6. Follow-up (not required by this ticket's size guard, named for record)

- CI step uploading the §1 digest as a build artifact on every frontend-
  changed `quick.yml`/`full.yml` run, for cross-commit/cross-OS comparison.
- Extend `rigplane-pro/scripts/local-act-ci.sh`'s existing
  `RIGPLANE_PRO_RIGPLANE_DIR` sibling-override pattern into a real cross-repo
  smoke CI job that closes the §3.2 gap — wiring, not a new abstraction.
  Out of scope here (CI change, not verification/doc).
