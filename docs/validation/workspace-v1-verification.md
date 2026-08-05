# Workspace v1 — forward compatibility, rollback and legacy-read window

**Ticket:** MOR-1083 · **Verified at:** `origin/main` `b8e22558` ·
**Kind:** verification runbook (no production behaviour is defined here)

This runbook covers the persisted **workspace v1** object — the single
`rigplane:workspace` key that owns the operator's layout, design-language,
theme, density, per-zone surface visibility/order and pinned commands.

It answers two acceptance questions, and only those two:

1. **Rollback.** Can a build from *before* the workspace chain still read the
   legacy state that this build retained?
2. **Never-overwrite.** Can this build destroy recoverable input before it has
   successfully committed a v1 object?

Everything else in the matrix exists to make those two answers trustworthy
across the states a real browser can actually be in.

---

## The system under verification

| Module | Owns |
|---|---|
| `frontend/src/presentation/workspace/contract.ts` | v1 schema, validator, defaults, forbidden-class scan, N=2 forward-read window |
| `frontend/src/presentation/workspace/legacy-readers.ts` | the 29-key MOR-1076 legacy inventory + its routing table |
| `frontend/src/presentation/workspace/repository.ts` | the bytes: one storage key, one sentinel, atomic single `setItem` |
| `frontend/src/presentation/workspace/store.svelte.ts` | reactivity, semantic setters, notices, the write latches |

Two storage keys are written, ever:

- `rigplane:workspace` — the object. Unversioned key; the schema version lives
  *inside* the object so an older build can forward-read a newer one.
- `rigplane:workspace-migrated:v1` — the migration sentinel. Written **only
  after** a successful workspace write.

Everything else in the 29-key inventory is **retained, never written, never
deleted**. That is the rollback window.

---

## How to re-run

```bash
cd frontend
npm ci                       # or symlink an existing node_modules
npx vitest run src/presentation/workspace/__tests__/workspace-compatibility-verification.test.ts
```

Full gate (what CI runs):

```bash
cd frontend
npx vitest run          # whole frontend suite
npm run lint
npm run lint:boundaries
npm run check
```

The suite is **fast-pool** (MOR-1272): storage is injected as a fake, so there
is no `vi.mock`, no `vi.stubGlobal` and no `vi.resetModules()`. Do not add any
of those to this file — it would have to move to the isolated pool.

---

## The rollback probe

The rollback build is a build at or before **`727b9efe`**
(`feat(MOR-1079): single workspace store and persistence boundary`) — the last
commit before MOR-1081 turned the two v2 readers into workspace façades.

Its read semantics are encoded in the suite as fixture functions, copied from
those two files as they read at that commit:

| Rollback reader | Source at `727b9efe` | Semantics |
|---|---|---|
| `rollbackReadLayout` | `lib/stores/layout.svelte.ts::loadMode` | `rigplane-layout` **??** `rigplane-skin`, then `normalizeLayoutMode` (aliases `lcd`/`amber-lcd`→`lcd-cockpit`, `spectrum`/`desktop-v2`→`standard`; unknown → `auto`) |
| `rollbackReadTheme` | `components-v2/theme/theme-switcher.ts::getTheme` | `rigplane:theme` **\|\|** `'default'` — raw string, *no* allow-list on read |
| `rollbackHasExplicitTheme` | `…::hasExplicitTheme` | `rigplane:theme-user-choice !== null` — **key presence**, any value |
| `rollbackReadVfoTheme` | `…::getVfoTheme` | `rigplane:vfo-theme`, raw |

They are **copies on purpose.** Importing the live modules would import today's
façades, which read the *workspace* rather than the legacy keys — the probe
would then assert nothing. Divergence between these fixtures and the live v3
modules is expected and correct; a v3-side change to `normalizeLayoutMode` must
not silently change what a rollback build does with retained bytes.

**Probe shape.** For each seeded legacy snapshot: capture the byte ledger and
the rollback view → `initWorkspaceStore` → exercise all seven semantic setters
→ reboot → exercise again. Then assert (a) the rollback view is *identical* to
the pre-boot view, (b) every retained key is byte-for-byte unchanged, (c) no
deletes, and (d) no key outside the two owned keys was ever a write target.

---

## Scenario matrix

| # | Evidence class | Scenarios | Verdict |
|---|---|---|---|
| 1 | Fresh install | empty storage; `theme` omission so a skin default stays reachable; reboot agreement; pristine rollback view | **PASS** |
| 2 | Every known legacy snapshot | all 29 inventory keys seeded individually (table-driven); full modern; legacy-alias; partial; manufacturer-prefixed; pre-#889 `rigplane-skin`; theme-user-choice precedence; forbidden auth-token key | **PASS** |
| 3 | Corrupt / partial state | unparsable, truncated, empty, scalar, array, `null`, whitespace; per-field repair with rejection reporting; cross-zone duplicate; throwing `getItem` | **PASS** |
| 4 | Future version | v2/v3 lossless read-update-writeback un-downgraded; v2/v3 **lossy** latched read-only; escape only via reset/import; v0/v4/v99/v1.5/`'two'`/`null`/`true` discarded without overwrite; full downgrade round trip | **PASS** |
| 5 | v1 export / import | byte-stable same-build round trip; forward-read export round trip; forbidden-content import refused whole; malformed import refused without throwing | **PASS** |
| 6 | Downgrade / rollback window | 10 seeded snapshots × (boot + migrate + 7 setters + reboot + 7 setters) → rollback view and retained bytes unchanged | **PASS** |
| 7 | Fallback after failed write | failed first write → nothing committed, sentinel unset, re-migrates next boot; sentinel never written on failure; failed update leaves prior object byte-identical; single atomic `setItem` per write; serialization failure writes nothing | **PASS** |
| 8 | No forbidden fields persisted | all six frozen classes stripped before writeback; unique planted sentinels absent from stored bytes; benign unknown fields still preserved; stored key set is exactly the eight frozen fields | **PASS** |

**96 assertions, 0 failures.**

### Acceptance

- *The rollback build can read retained legacy state* — **MET.** Across all 10
  rollback snapshots the retained bytes are unchanged and the `727b9efe` read
  semantics resolve identically before and after two full v3 generations.
- *The v3 build never overwrites recoverable input before a successful v1
  commit* — **MET.** The migration sentinel is written only after a successful
  workspace write; a failed write leaves both the legacy bytes and any
  previously committed object byte-identical; every write is one atomic
  `setItem`; and a lossy forward read is latched read-only for the session.

---

## Findings

### F1 — a corrupt workspace object short-circuits the legacy migration (informational)

**Severity:** low. **Acceptance impact:** none — both acceptance criteria still
hold.

`repository.ts::loadWorkspace` branches on the **presence** of
`rigplane:workspace` before it considers whether the bytes are readable. So a
present-but-unreadable object takes the `stored` branch, resets to defaults, and
the legacy migration never runs — even when the sentinel records that it never
ran and the legacy keys are still sitting there fully readable.

Nothing is lost that cannot be recovered: the legacy bytes are retained
untouched (asserted), the operator gets a visible `reset` notice, the rollback
build is entirely unaffected, and clearing the unreadable object lets the
migration run after all. What is lost is only the v3-side convenience of
re-folding the legacy values automatically.

Pinned as a characterization test (`F1: a corrupt workspace object
short-circuits the migration, but retains legacy bytes`) so a future change to
that ordering is deliberate.

**Proposed atomic ticket (if the owner wants it changed):** *"Fall back to the
legacy migration when the stored workspace object is unrecoverable and the
migration sentinel is absent."* Scope: one condition in
`repository.ts::loadWorkspace` (take the migration branch when the stored read
returns `reset`/`version-discarded` **and** the sentinel is absent), plus its
notice semantics and tests. One file, well under the LOC guardrail. Out of
MOR-1083's verification-only scope.

No other finding. No production defect was observed in any of the eight
evidence classes.

---

## Known deliberate behaviours (not findings)

These surprised nobody on this pass but are worth stating, because each looks
like a bug until you know why:

- **`theme` is omitted from a fresh write.** Explicitness-via-presence
  (MOR-1081): writing `theme: 'default'` unchosen would be indistinguishable
  from an explicit Default Dark, and a skin's own default (amber-lcd →
  `lcd-warm`) would become permanently unreachable.
- **`rigplane-skin` is not migrated.** MOR-1078 routed it `retire` (dead write
  path), so the pre-#889 fallback is deliberately dropped by v3 — while the
  rollback build's `??` fallback still finds it, which is exactly why the key
  must never be deleted.
- **A lossy forward read is latched for the whole session.** Once the
  unrepresentable value is repaired away a patched result validates cleanly, so
  re-deriving the verdict per update would re-enable the write and destroy the
  newer data. Only an explicit whole-object reset or import may unlatch it.
- **19 of the 29 inventory keys are `retain-outside`.** They have no field in
  the frozen v1 schema and keep their current owners. Their presence in the
  inventory is a routing decision, not a migration backlog.
