# Frontend tests that read source text

A test that reads a `.svelte`, `.ts` or `.css` file and asserts on its text
pins how the code is written, not what it does. The existing set is frozen in
a ledger: a new source-reading test fails the meta-test until a ledger row is
added for it.

- Ledger: `frontend/scripts/source-text-pin-baseline.mjs`
  (`SOURCE_TEXT_PIN_BASELINE`, one `path::describe > test` row per test).
- Inventory: `frontend/scripts/source-text-pin-inventory.mjs`
  (`scanSourceTextPins`). A test file is scanned when its text matches
  `READ_PATTERN` and it is not listed in `DATA_READERS`; inside it, a test is
  pinned when its body calls a file read, uses a `?raw` import, or references
  a binding derived from either.
- Enforcement: `frontend/src/__tests__/source-text-pins.meta.test.ts` fails
  on a pinned test missing from the ledger, on a ledger row that no longer
  matches a pinned test, and on any `createHash(...).update(x)` where `x`
  derives from a file read (`auditDigests`; the only exemption is
  `DIGEST_READ_EXEMPT_PREFIXES`).

When a ledgered test stops reading source, delete its row. To regenerate the
ledger, run `node scripts/source-text-pin-inventory.mjs` from `frontend/` and
write its output to the ledger file; a diff that adds rows is a new
source-text test and needs a reason in review.

## Projection goldens

A projection golden pins the output of a pure projection on a fixed input:
the test builds the projection from its fixture and compares it with
`toEqual` against a committed JSON file, so a change shows as a readable
diff instead of a re-signed digest.

- Golden: `frontend/src/lib/runtime/adapters/__tests__/fixtures/strict-projection.golden.json`,
  pinned by the `preserves legacy strict model members` test in
  `frontend/src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts`.
- A golden is loaded with an ES JSON import (as `fixtures/ic7300-profile.ts`
  loads `ic7300-state.json`), so it is not a source-text pin and needs no
  ledger row: none of `READ_PATTERN`'s forms matches it.
- A golden is regenerated only by running that test file with
  `UPDATE_GOLDENS=1` on the test host; the env-guarded `writeFileSync` inside
  the test is the golden's only writer.
- A golden diff that changes a value is a behaviour change: the PR body must
  name the ticket that asked for it.
