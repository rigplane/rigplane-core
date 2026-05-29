# Universal validation matrix — validate your radio, contribute a profile

The **universal validation matrix** lets anyone point RigPlane at a radio,
exercise its CAT control surface against what a profile *declares*, and share
the evidence. It is profile-driven: the check list is generated from the radio's
`rigs/<model>.toml` capabilities — you never hand-author a template. This page
is the contributor-facing guide; for the day-to-day debugging reference (recipes
for the X6200 and IC-7610, the full flag table, status meanings) see
[Running validation](running-validation.md).

It is **open-core** by design (ADR §11.1): headless, no telemetry, MIT-licensed.
Everything here works without RigPlane Pro and never couples to a Pro feature.
Your contributions — an artifact JSON and a reviewed `rigs/<model>.toml` — are
public and independently reproducible.

> **Safety is non-negotiable.** Hardware execution is opt-in and
> read-modify-verify-restore (RMVR): every write reads the original, writes a
> different value, verifies the readback, and **always restores** the original.
> TX (PTT) and the antenna tuner are **never** auto-actuated by any of the tools
> below — not by a flag, not by an override file. See [Safety](#safety-can-never-be-relaxed).

---

## 1. Run it

The first-class, profile-driven entry point is `radio-validate`:

```bash
rigplane radio-validate <MODEL> \
  [--provider native|hamlib|both] \
  [--read-only] \
  [--hardware --allow-hardware] \
  [--tx-allowed] [--tuner-allowed] \
  [--compare prior.json] \
  [--no-overrides] \
  [--json] [--output PATH] \
  [--write-template PATH]
```

`<MODEL>` is positional (e.g. `X6200`, `IC-7610`); if omitted, the global
`--model` is used. Connection flags (`--backend`, `--serial-port`,
`--serial-baud`, `--host`, `--pass-file`, `--radio-addr`, `--timeout`) go
**before** the subcommand — see the [recipes in Running validation](running-validation.md#recipes).

### Dry-run is the default

With no hardware flags, `radio-validate` generates and plans the matrix from the
profile and exits — nothing touches a radio. This is the safe default for
inspecting what *would* run.

### The double hardware gate

To touch a real radio, **both** of these must be present:

- `--hardware --allow-hardware` on the command line, **and**
- `RIGPLANE_VALIDATION_ALLOW_HARDWARE=1` in the environment.

Any one missing keeps the run a dry-run plan. The two-part gate (an explicit flag
*and* an explicit env var) is deliberate — it makes "touch my radio" impossible
to trigger by accident or by a copied-and-pasted command alone.

### Read/write-with-restore vs `--read-only`

By default a hardware run is **read/write with automatic restore** (RMVR): write
checks set a different value, verify it, and restore the original. Pass
`--read-only` to run reads only — every write check reports `skip` and the radio
is never mutated. Read-only is the right first pass on an unfamiliar radio.

Regardless of mode, **TX and tuner are never auto-actuated.** Even with
`--tx-allowed` / `--tuner-allowed`, those checks are operator-verified
(`manual_required`), never keyed by the tool.

### Inspect the matrix without a radio

`--write-template PATH` builds the generated in-memory matrix and dumps it as
JSON, then exits (no hardware). Use it to see exactly which checks the profile
produces, or to seed an [override file](#4-overrides).

### Legacy `validate --template` still works

The older `validate` subcommand is unchanged. `validate --template <path>` runs
a hand-authored template (useful for CI fixtures); `radio-validate` is the
profile-driven form and the two share the same run path. New contributors should
prefer `radio-validate`.

---

## 2. Interpret the artifact + the three comparison dimensions

Add `--json --output report.json` for a machine-readable artifact. Each check
carries a status and an `evidence` block; the status meanings (`pass`, `fail`,
`unsupported`, `manual_required`, `blocked`, `skip`) are tabulated in
[Running validation](running-validation.md#reading-the-results).

`--provider both` runs **native then hamlib** sequentially (the serial port is
released between them) and attaches `metadata.comparison.dimensions` — three
distinct "declared vs reality" lenses:

| Dimension | Question it answers | Who acts on a `differ` |
|---|---|---|
| `profile_vs_reality` | Does the radio do what the **rigplane TOML profile** declares? | RigPlane — fix the profile or the backend. |
| `hamlib_vs_reality` | Does the **Hamlib model DB** match the radio? | Upstreamable to Hamlib. |
| `cross_impl` | Where do **rigplane-native and Hamlib disagree** on the same radio? | Whichever implementation is wrong. |

`profile_vs_reality` reports `agree`, `differ`, and a `differing` list of
check IDs. `hamlib_vs_reality` and `cross_impl` report `agree`, `differ`, and
`na`.

**`na` is never a failure** (ADR D6). For `cross_impl`, `na` means the two
implementations cannot be meaningfully compared on that check — most commonly
Hamlib has **no token map** for it, or the check did not resolve to a
`pass`/`fail` on both sides (e.g. a write collapsed to `skip` under
`--read-only`).

### Worked example — Xiegu X6200, read-only, `--provider both`

Real output from live hardware (2026-05-29), Hamlib model 3091:

```text
summary: pass 3, fail 0, skip 13 (read-only), unsupported 13,
         manual_required 1, blocked 2

profile_vs_reality:  16 agree /  0 differ
hamlib_vs_reality:   21 agree /  0 differ / 11 na
cross_impl:           0 differ / 32 na   (read-only collapses writes to na)
```

Reading it:

- **No `fail`, no `differ` anywhere** → the X6200 profile and the radio agree,
  and so does Hamlib's model. A clean result.
- `cross_impl` is **all `na`** because `--read-only` turned every write into a
  `skip`; with no `pass`/`fail` pair to compare, every cross-implementation slot
  is `na`. This is expected, not a problem.
- The 11 `na` in `hamlib_vs_reality` are checks where Hamlib has no token for
  that capability — again expected, not a defect.

With **writes enabled** (drop `--read-only`), the one known X6200 disagreement
surfaces: `mode.set` passes natively but times out through Hamlib. That shows up
as a `cross_impl` **differ** — exactly the kind of signal this dimension exists
to catch.

---

## 3. Bootstrap & contribute a profile (the converter)

If your radio has no `rigs/<model>.toml` yet, bootstrap a draft from Hamlib's
`dump_caps` with the top-level `convert` verb:

```bash
rigplane convert <HAMLIB-MODEL-ID|NAME> \
  [--draft-out PATH] \
  [--compare-profile MODEL] \
  [--json]
```

`<HAMLIB-MODEL-ID|NAME>` is a Hamlib numeric model id (e.g. `3091`) or a known
rigplane model name (e.g. `X6200`). It writes `<slug>.draft.toml` to the current
directory by default (**not** `rigs/`).

A draft is **safe-by-construction**:

- it carries a `# REVIEW:` banner and `TODO(human):` markers on every
  non-auto-filled field;
- the `.draft.toml` suffix means `discover_rigs` **never auto-loads it** — it
  only becomes a real profile when a human reviews it and renames it to
  `rigs/<model>.toml`;
- drafts are **never auto-committed**. Human review is mandatory.

### The cross-check buckets

Pass `--compare-profile MODEL` to compare the Hamlib-derived capabilities
against an existing profile (or just to see what `dump_caps` covers). Real
verified output for the X6200 (Hamlib model 3091):

```text
agreed:        af_level, attenuator, nb, nr, preamp, rf_gain, squelch
rigplane_only: agc, audio, rit, xit, tx, tuner, notch, …
hamlib_only:   (none)
```

| Bucket | Meaning |
|---|---|
| `agreed` | The profile declares it **and** Hamlib has a token — both sides agree. |
| `rigplane_only` | The profile declares it but **Hamlib lacks a token** (e.g. `agc`, `rit`, `xit`, `tx`, `tuner`, `notch`, `audio`). Not a defect — Hamlib's model simply has no representation for it. |
| `hamlib_only` | Hamlib has a token the profile omits — a candidate for **widening profile coverage**. |

Use these buckets to decide what to finalize when you turn a `*.draft.toml` into
a real `rigs/<model>.toml`: confirm the `TODO(human):` fields, fill in the
RigPlane-specific bits Hamlib can't know (CI-V address, command byte maps,
LAN/audio policy), and reconcile `hamlib_only` tokens.

---

## 4. Overrides

A radio with **no** override file still gets the full generated matrix — overrides
are purely additive tuning. A per-rig override lives at
`docs/validation/templates/<profile_id>.json` and is auto-merged onto the
generated matrix when present.

It uses the v1 template shape with a top-level `"override": true` flag, and is
interpreted as a **sparse patch keyed by `check_id`**:

```json
{
  "schema_version": 1,
  "radio": { "model": "IC-7300", "profile_id": "ic7300" },
  "override": true,
  "entries": [
    { "check_id": "scope.capture", "capability": "scope", "level": 4,
      "declaration": "supported",
      "summary": "Automated scope capture is safe on IC-7300.",
      "tx_adjacent": false }
  ]
}
```

Merge semantics (keyed by `check_id`):

- **replace** — a matching `check_id` updates that entry's mutable fields
  (`level`, `declaration`, `summary`, `tx_adjacent`);
- **append** — a `check_id` the generator does not emit is added;
- **`"excluded"`** — the reserved declaration value `"excluded"` drops the entry
  (for a control that is declared but known broken on a specific unit).

When `"override"` is absent or `false`, the file is treated as a **full** template
(full backward compatibility with the pre-existing shipped templates).

Every applied / appended / excluded / rejected change is recorded in
`metadata.overrides` so the merge is auditable, never silent. Pass
`--no-overrides` to skip override application entirely (e.g. for deterministic
CI runs).

### Safety can never be relaxed

**An override can never relax a safety gate.** TX/tuner authorization, the RMVR
write discipline, and each check's `CheckKind` safety class come from the
**registry**, not from the template. The runner re-applies the authorization
pre-gate for `tx`/`tuner` capabilities regardless of what the template's
`tx_adjacent` flag says. If an override attempts an unsafe relaxation, it is
**refused** and recorded in `metadata.overrides.rejected` — you can see exactly
what was rejected and why.

---

## 5. Share evidence / contribute

Two artifacts are the public contribution:

1. **The artifact JSON** (`--json --output report.json`) — independently
   reproducible evidence of what your radio did. A `--provider both` artifact
   with its `comparison.dimensions` is the most useful form.
2. **A reviewed `rigs/<model>.toml`** — bootstrapped via `convert`, finalized by
   hand. This is what lets the next person validate the same radio.

Where the dimensions point:

- A `profile_vs_reality` **differ** → fix the rigplane profile or backend, then
  re-run and contribute the corrected profile.
- A `hamlib_vs_reality` **differ** → an upstreamable fix to the **Hamlib** model
  database. Capture the artifact as evidence.
- A `cross_impl` **differ** → one of the two implementations is wrong on that
  check; the evidence block tells you which.

Everything stays open-core (ADR §11.1): headless CLI, no telemetry, MIT/open. Do
not couple validation contributions to Pro features.

---

## See also

- [Running validation](running-validation.md) — recipes, full flag table, status
  meanings, the human-readable summary.
- `docs/contracts/validation-matrix-v1.md` — the versioned artifact/template schema.
- `docs/plans/2026-05-28-universal-validation-matrix.md` — the design ADR
  (registry, generators, comparison dimensions, converter, override layer,
  safety model).
