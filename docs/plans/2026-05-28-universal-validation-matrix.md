# Universal Profile-Driven Radio Validation Matrix

**Date:** 2026-05-28
**Status:** Accepted design (SPEC only — no implementation in this document)
**Linear:** `MOR-196` (parent); children `MOR-197`..`MOR-204`
**GitHub epic:** `#1645` (validation matrix vertical)
**Builds on:** `#1653`–`#1657` (merged schema + dry-run + hardware RMVR + CLI foundation),
`MOR-180` (write-only set+observe), `MOR-182` (Hamlib bridge)
**Supersedes nothing; extends `docs/contracts/validation-matrix-v1.md` (v1, non-breaking)**

---

## 1. Summary

The current vertical (`docs/plans/2026-05-28-real-radio-validation-matrix.md`)
validates a radio against a **hand-authored** JSON template
(`docs/validation/templates/*.json`). Each new radio needs a human to write and
maintain a parallel template that duplicates what the radio's TOML profile
already declares. That does not scale to "validate RigPlane against ANY radio".

This ADR designs a **universal, profile-driven validation matrix**:

1. A single **capability→check-spec registry** (`validation/registry.py`) keyed
   on the closed `KNOWN_CAPABILITIES` vocabulary
   (`src/rigplane/core/capabilities.py:171`). Each entry declares how to derive,
   run, and compare a check — safely.
2. **Generator A (native)** turns a `RadioProfile`
   (`src/rigplane/profiles/__init__.py:135`) into the existing
   `MatrixTemplate` (`src/rigplane/validation/schema.py:196`) by walking
   `profile.capabilities` + the parsed `[commands]` dict through the registry.
   `execute_hardware_checks` (`src/rigplane/validation/hardware.py:120`) then
   runs it **unchanged**.
3. **Generator B (Hamlib)** turns the same registry into a check list filtered
   by a `rigplane-capability ↔ hamlib-token` map, run through the rigctld client
   (`src/rigplane/backends/rigctld_client/radio.py:37`) over the bridge
   (`src/rigplane/hamlib_bridge.py:73`).
4. A **comparison/report model** that records three "declared vs reality"
   dimensions plus per-capability native-vs-Hamlib agreement, folded into the
   existing `ValidationArtifact.metadata` and the existing
   `_compare_artifacts` machinery (`src/rigplane/cli/_validate.py:538`).
5. A **profile converter**: Hamlib `dump_caps` → draft `rigs/<model>.draft.toml`
   + a TOML-vs-Hamlib cross-check report. Human-review-required, never
   auto-committed.

The hand-authored templates become **optional overrides**, not the source of
truth.

### Decisions at a glance

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | One shared registry produces inputs for **both** providers | Avoids two divergent check catalogs; the registry is the single source of truth for "what a capability means to validate". |
| D2 | Generators emit the **existing** `MatrixTemplate` / `CapabilityDeclarationEntry`; no new artifact schema | `execute_hardware_checks` and the v1 contract run unchanged. No `schema_version = 2`. |
| D3 | Overrides **merge into** generated templates, keyed by `check_id` | Hand-authored templates evolve into a thin override layer; the generator owns the exhaustive list. |
| D4 | Safety class is encoded **in the registry**, not in the generator | Generation cannot emit an unsafe check; the safety posture is data, audited in one place. |
| D5 | The registry + native generator live in a **new sibling module** (`validation/registry.py`), but Generator A's profile read lives in `cli/` | The `validation` layer may only import `core.capabilities` + stdlib (`validation/LAYER.md`); reading `RadioProfile` is a `profiles`/CLI concern. See §2.4. |
| D6 | Hamlib N/A is `UNSUPPORTED` with evidence, **never** `FAIL` | A missing token map is "we can't drive this through Hamlib", not "the radio is broken". |
| D7 | `radio-validate` is a **thin wrapper** over the existing `validate` run path, not a fork | One execution + artifact + comparison code path; the wrapper only adds template generation. |

---

## 2. Capability → check-spec registry

### 2.1 Where it lives

`src/rigplane/validation/registry.py` (new module in the existing `validation`
layer). It imports **only** `rigplane.core.capabilities`,
`rigplane.validation.schema`, and the standard library — honouring the layer
charter (`src/rigplane/validation/LAYER.md`: "Imports only
`rigplane.core.capabilities` … and the standard library"). It does **not**
import `RadioProfile`, backends, or the radio Protocols at runtime; the get/set
operation references are stored as **strings** (method names) and the capability
Protocol is named by string and resolved against the same
`hardware._CAP_PROTOCOL` table at run time. This keeps the registry a pure data
artifact and keeps the layer boundary intact (see §2.4 for why Generator A's
profile read is split out).

### 2.2 Data shape

```python
# validation/registry.py
from enum import StrEnum

class CheckKind(StrEnum):
    READ_ONLY            = "read_only"             # pure read, always safe
    RMVR_SAFE_WRITE      = "rmvr_safe_write"       # read-modify-verify-restore
    WRITE_ONLY_OBSERVE   = "write_only_observe"    # set + observe, no readback (MOR-180)
    TX_ADJACENT_BLOCKED  = "tx_adjacent_blocked"   # never auto-actuated
    MANUAL               = "manual"                # operator-verified out of band

@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str                       # stable id, also the override merge key
    capability: str                     # "" or a member of KNOWN_CAPABILITIES
    kind: CheckKind
    level: ValidationLevel              # schema.ValidationLevel (default placement)
    failure_domain: FailureDomain       # schema.FailureDomain on FAIL/BLOCKED
    summary: str                        # default human summary (overridable)
    # Operation references — names resolved at runtime, NOT imports:
    protocol: str | None = None         # key into hardware._CAP_PROTOCOL, e.g. "rf_gain"
    get_op: str | None = None           # method name, e.g. "get_rf_gain"
    set_op: str | None = None           # method name, e.g. "set_rf_gain"
    # Value generation + comparison (mirror hardware.py make_changed/equal):
    value_rule: str = "toggle_bool"     # see §2.3
    tolerance: int = 0                  # reuse _tolerant_equal(tol) from #1654
    # Provider applicability:
    hamlib_token: str | None = None     # rigctl letter/func token; None ⇒ N/A on hamlib
    tx_adjacent: bool = False           # mirrors CapabilityDeclarationEntry.tx_adjacent
```

`CheckKind`, `value_rule`, and `tolerance` together carry exactly the
information `hardware.py` already hard-codes per handler — they let a single
generic handler reproduce the existing behaviour (see §2.5). `level` and
`failure_domain` are the **defaults**; overrides may change `level` (see §4).

### 2.3 `value_rule` vocabulary

These name the existing `make_changed` closures in `hardware.py` so generation
reproduces today's behaviour exactly:

| `value_rule` | Behaviour | Mirrors |
|---|---|---|
| `toggle_bool` | `lambda b: not b` | `attenuator.set`, `notch.set`, `nb.set`, `nr.set`, `xit.set` (`hardware.py:803,824,852,880,946`) |
| `step_level_255` | `lambda v: 200 if v < 128 else 50` | `rf_gain.set`, `af_level.set` (`hardware.py:739,760`) |
| `bump_hz` | `lambda f: f + 1000` (freq) / `+100` (rit) | `freq.write` (`:586`), `rit.set` (`:924`) |
| `nudge_filter` | `lambda w: w + 200 if w <= 2600 else w - 200` | `filter_width.set` (`:716`) |
| `preamp_cycle` | `lambda v: 1 if v == 0 else 0` | `preamp.set` (`:782`) |
| `agc_flip` | SLOW↔FAST via `AgcMode` | `agc.set` (`:901`) |
| `mode_cycle` | first of USB/LSB/CW/AM ≠ current | `mode.set` (`:618`) |

`tolerance` reuses the `_tolerant_equal(tol)` approach from #1654
(`hardware.py:344`): `filter_width` → 50, `rf_gain`/`af_level` → 3, `rit` → 10,
booleans/modes → 0.

### 2.4 Why the registry's profile read is split out (D5)

Generator A reads `RadioProfile.capabilities` — a real `frozenset[str]`
(`profiles/__init__.py:143`). Those live in the `profiles` layer, which is
**above** `validation` in the DAG. To avoid an illegal upward import, the split is:

- `validation/registry.py` — pure data + a `build_template_from_capabilities(...)`
  function that takes an already-resolved **`frozenset[str]` of capabilities**
  and an **optional `Callable[[str], bool]` command-probe**, never a `RadioProfile`.
- `cli/_validate.py` (or a small `cli/_validate_generate.py`) — resolves the
  `RadioProfile` via `get_radio_profile` (already imported there at
  `_validate.py:375`), then calls `build_template_from_capabilities(
  profile.capabilities, probe=None)`.

This mirrors how `_run_hardware_hamlib` already reaches into `profiles` from the
CLI while `hardware.py` stays profile-free.

> **API note (corrects an earlier draft):** the runtime `RadioProfile` returned by
> `get_radio_profile` exposes `capabilities: frozenset[str]` and the numeric
> `supports_cmd29(...)`, but **no string command table and no `supports_command(name)`
> method** — the string-keyed `commands: dict[str, CommandSpec]` lives only on the
> internal `RigConfig` (`rig_loader.py:77`), which the CLI does not hold. Therefore
> Generator A keys generation on `capabilities` alone, and the four **structural**
> checks (`discovery.identify`, `freq.write`, `freq.reverse_sync`, `mode.set`) are
> emitted **unconditionally** — matching today's shipped templates. The
> `Callable[[str], bool]` command-probe parameter is reserved for **optional** finer
> gating; supplying it requires first exposing a `supports_command` probe on
> `RadioProfile` (a small, non-breaking prerequisite tracked under MOR-197/198). v1
> generation does **not** depend on it.

### 2.5 Generic handler (no new per-capability handlers)

`hardware.py` dispatches on `check_id` via `_SUPPORTED_HANDLERS`
(`hardware.py:952`). Rather than grow that table per radio, the registry's
`CheckSpec` is consumed by **one** generic RMVR handler that the existing
`_read_modify_verify_restore` (`hardware.py:358`) already parameterises
(`read`, `write`, `make_changed`, `equal`). The existing named handlers stay as
the fallback for the four untagged structural checks
(`discovery.identify`, `freq.write`, `freq.reverse_sync`, `mode.set`). New
capability checks resolve `get_op`/`set_op` by `getattr` on the radio after the
`_CAP_PROTOCOL` `isinstance` gate — exactly the pattern `_check_nb_set`
(`hardware.py:829`) already uses for `get_nb`/`set_nb`.

> Implementation note (out of scope here, flagged for `MOR-199`): a small
> `_check_from_spec(radio, entry, spec, ...)` added to `hardware.py`'s dispatch,
> looked up when `check_id` is not in `_SUPPORTED_HANDLERS`. This is the only
> `hardware.py` change the whole design needs, and it is additive.

### 2.6 Concrete registry entries (current `KNOWN_CAPABILITIES`)

`level` column uses the v1 `ValidationLevel` IntEnum
(`schema.py:44`): 1=discovery, 2=basic_control, 3=capability_matrix,
4=compatibility_surfaces, 5=stress_recovery.

| check_id | capability | kind | level | get_op / set_op | value_rule / tol | hamlib_token | failure_domain |
|---|---|---|---|---|---|---|---|
| `discovery.identify` | `""` | READ_ONLY | 1 | `get_freq` | — | (native `\get_info`) | discovery |
| `freq.write` | `""` | RMVR_SAFE_WRITE | 2 | `get_freq`/`set_freq` | `bump_hz`/0 | `F`/`f` | readback |
| `freq.reverse_sync` | `""` | READ_ONLY | 2 | `get_freq` + `radio_state.main.freq` | — | n/a (state model differs) | state_publishing |
| `mode.set` | `""` | RMVR_SAFE_WRITE | 2 | `get_mode`/`set_mode` | `mode_cycle`/0 | `M`/`m` | readback |
| `filter_width.set` | `filter_width` | RMVR_SAFE_WRITE | 3 | `get_filter_width`/`set_filter_width` | `nudge_filter`/50 | `M` passband | readback |
| `rf_gain.set` | `rf_gain` | RMVR_SAFE_WRITE | 3 | `get_rf_gain`/`set_rf_gain` | `step_level_255`/3 | `RF` | readback |
| `af_level.set` | `af_level` | RMVR_SAFE_WRITE | 3 | `get_af_level`/`set_af_level` | `step_level_255`/3 | `AF` | readback |
| `preamp.set` | `preamp` | RMVR_SAFE_WRITE | 3 | `get_preamp`/`set_preamp` | `preamp_cycle`/0 | `PREAMP` | readback |
| `attenuator.set` | `attenuator` | RMVR_SAFE_WRITE | 3 | `get_attenuator`/`set_attenuator` | `toggle_bool`/0 | `ATT` | readback |
| `notch.set` | `notch` | RMVR_SAFE_WRITE | 3 | `get_manual_notch`/`set_manual_notch` | `toggle_bool`/0 | — | readback |
| `nb.set` | `nb` | RMVR_SAFE_WRITE | 3 | `get_nb`/`set_nb` | `toggle_bool`/0 | `NB` | readback |
| `nr.set` | `nr` | RMVR_SAFE_WRITE | 3 | `get_nr`/`set_nr` | `toggle_bool`/0 | `NR` | readback |
| `agc.set` | `agc` | RMVR_SAFE_WRITE | 3 | `get_agc`/`set_agc` | `agc_flip`/0 | — | readback |
| `rit.set` | `rit` | RMVR_SAFE_WRITE | 3 | `get_rit_frequency`/`set_rit_frequency` | `bump_hz`/10 | — | readback |
| `xit.set` | `xit` | RMVR_SAFE_WRITE | 3 | `get_rit_tx_status`/`set_rit_tx_status` | `toggle_bool`/0 | — | readback |
| `squelch.set` | `squelch` | RMVR_SAFE_WRITE | 3 | `get_squelch`/`set_squelch` | `step_level_255`/3 | `SQL` | readback |
| `audio.rx` | `audio` | MANUAL | 4 | (presence-only) | — | — (USB-audio) | audio |
| `scope.capture` | `scope` | MANUAL¹ | 4 | (presence-only) | — | — | scope_waterfall |
| `meters.read` | `meters` | READ_ONLY | 4 | `get_s_meter` | — | `STRENGTH` (get_level) | readback |
| `tuner.tune` | `tuner` | TX_ADJACENT_BLOCKED | 5 | (never actuated) | — | — | command_execution |
| `tx.ptt` | `tx` | TX_ADJACENT_BLOCKED | 5 | (never actuated) | — | `T`/`t` (never set) | command_execution |

¹ `scope.capture` is `MANUAL` by default to match the shipped IC-7300 template's
operator-verified posture for compatibility surfaces; an override may promote it
to a real capture check on rigs where automated capture is proven safe.

**Capabilities with no registered functional check yet** (e.g. `dual_rx`,
`split`, `vox`, `compressor`, `monitor`, `drive_gain`, `cw`, `break_in`,
`repeater_tone`, `tsql`, `dtcs`, `data_mode`, `power_control`, `dial_lock`,
`scan`, `band_edge`, `digisel`, `ip_plus`, `apf`, `twin_peak`, `pbt`,
`contour`, `if_shift`, `filter_shape`, `antenna`, `rx_antenna`,
`webrtc`, `lan_dual_rx_audio_routing`, `system_settings`, …): the generator
emits a `STATIC_PROFILE` (level 0) **presence** entry with declaration
`unsupported_pending_evidence` so the matrix is exhaustive and the gap is
visible, never silently dropped. New functional checks are added by registering
a `CheckSpec` — never by editing a template. (`MOR-200` widens the registry;
this ADR fixes the structure, not the final coverage.)

---

## 3. Generator A (native): profile → template

### 3.1 Algorithm

`build_template_from_capabilities(capabilities, *, probe=None, model, profile_id)`
(`probe: Callable[[str], bool] | None`):

1. Start with the **structural** entries that every CI-V radio gets, in order:
   `discovery.identify`, `freq.write`, `freq.reverse_sync`, `mode.set`
   (capability `""`). These are emitted **unconditionally** (every CI-V rig has
   freq/mode), matching today's shipped templates. If an optional `probe` is
   supplied (future, see §2.4 API note), it MAY downgrade a structural entry to
   `unsupported_pending_evidence` when the profile lacks the command; v1 passes
   `probe=None` and emits all four.
2. For every `CheckSpec` in the registry whose `capability` is non-empty:
   - If `capability in capabilities` → emit `CapabilityDeclarationEntry` with
     `declaration = SUPPORTED` (or `MANUAL_REQUIRED` when `kind == MANUAL`,
     mirroring the shipped `audio.rx`/`tuner.tune` templates).
   - Else → emit with `declaration = UNSUPPORTED_PENDING_EVIDENCE`.
3. For every `KNOWN_CAPABILITIES` tag declared on the radio but with **no**
   registered functional `CheckSpec`, emit the level-0 presence entry (§2.6).
4. `tx_adjacent` is copied from the `CheckSpec`; `TX_ADJACENT_BLOCKED` and
   `MANUAL`+tuner specs set `tx_adjacent = True`, so the existing
   `_is_authorized` gate (`runner.py:58`, `hardware.py:212`) fires unchanged.
5. Sort entries by `(level, registry order)` and build a `MatrixTemplate`
   (`schema.py:196`). Because every emitted object is a validated
   `CapabilityDeclarationEntry` with a `capability` in `KNOWN_CAPABILITIES` or
   `""`, the result passes `validate_template_dict` (`schema.py:278`) as-is.

### 3.2 Unknown / undeclared handling

- A capability tag the registry does not know is **impossible** — `RadioProfile`
  rejects unknown tags at load (`rig_loader.py:544`), and the registry keys on
  `KNOWN_CAPABILITIES`. No defensive branch needed.
- A registered capability the radio does **not** declare → emitted as
  `UNSUPPORTED_PENDING_EVIDENCE`, which `execute_hardware_checks` already turns
  into a `UNSUPPORTED` `CheckResult` with `capability_present` evidence
  (`hardware.py:225`). The matrix stays exhaustive; absence is recorded, not
  hidden.

### 3.3 Ordering

Within a level, registry declaration order is preserved (deterministic, stable
diffs). `execute_hardware_checks` already groups by ascending level
(`hardware.py:163`); Generator A pre-sorts so the emitted template reads
top-to-bottom in execution order.

---

## 4. Override layer

### 4.1 Format and location

Overrides live where the current templates live: `docs/validation/templates/<profile_id>.json`.
The file keeps the **same v1 template shape** but is now interpreted as a
**sparse patch** keyed by `check_id`, not a full matrix. A new optional
top-level key distinguishes intent:

```json
{
  "schema_version": 1,
  "radio": { "model": "IC-7300", "profile_id": "ic7300" },
  "override": true,
  "entries": [
    { "check_id": "scope.capture", "capability": "scope", "level": 4,
      "declaration": "supported", "summary": "Automated scope capture is safe on IC-7300.",
      "tx_adjacent": false }
  ]
}
```

When `override` is absent or `false`, the file is treated as a **full** template
exactly as today (full backward compatibility; the four shipped templates keep
working untouched).

### 4.2 Merge semantics and precedence

`merge_overrides(generated: MatrixTemplate, override: MatrixTemplate) -> MatrixTemplate`:

1. Index `generated.entries` by `check_id`.
2. For each override entry: if `check_id` exists, **replace** that entry's
   mutable fields (`level`, `declaration`, `summary`, `tx_adjacent`); if it does
   not exist, **append** it (lets an override add a check the generator does not
   yet emit).
3. A reserved declaration value `"excluded"` (override-only, never produced by
   the generator) **drops** the entry — for a control that is declared but known
   broken/unsafe on a specific unit. Excluded entries are recorded in
   `metadata.overrides.excluded` so the exclusion is auditable, not silent.
4. Re-validate the merged result with `validate_template_dict`.

**Precedence:** generated baseline < override patch < CLI safety gates. An
override can never relax a safety gate — it cannot turn a `TX_ADJACENT_BLOCKED`
spec into an auto-actuated write, because `tx_adjacent` and the `CheckKind`
safety class come from the registry, and `execute_hardware_checks` re-applies
the authorization pre-gate regardless of the template's `tx_adjacent` flag for
`tuner`/`tx` capabilities (`_is_authorized`, `runner.py:58`).

**Overridable:** safe-freq window / value rule (via a future per-check
`value_rule` override — flagged open question §11), manual-prompt vs automated,
write-only flag, expected-unsupported, exclusion, `level` placement, `summary`.
**Not overridable:** the safety class, the `_CAP_PROTOCOL` gate, the
authorization requirement for `tx`/`tuner`.

---

## 5. Hamlib ingestion + capability mapping

### 5.1 Obtaining and parsing `dump_caps`

Reuse the existing Hamlib plumbing rather than inventing a parser:

- The **model catalog** (`rigctld -l` / `rigctl -l`) is already parsed by
  `parse_hamlib_model_list` / `load_hamlib_model_catalog`
  (`src/rigplane/backends/hamlib_models.py:44,73`), with graceful degradation to
  an empty catalog on missing tools.
- For per-model capability ingestion the converter (§7) and Generator B read
  **`rigctl -m <id> --dump-caps`** (or the `\dump_caps` rigctld command) through
  a small, additive parser in `backends/hamlib_models.py` (e.g.
  `parse_hamlib_dump_caps(text) -> HamlibCaps`). This stays in `backends/`
  beside the catalog loader, off the `validation` import graph, and reuses the
  same "shell out, parse stdout, degrade on failure" pattern already proven in
  `_load_from_tool` (`hamlib_models.py:102`).

`HamlibCaps` (new frozen dataclass in `hamlib_models.py`) normalizes the
relevant subset: `get_funcs`/`set_funcs` (NB, NR, …), `get_levels`/`set_levels`
(RF, AF, ATT, PREAMP, …), `modes`, `vfo_ops`, `ptt_type`, `has_set_freq`. The
converter and Generator B consume only this normalized view, never raw
`dump_caps` text — keeping the Hamlib-leakage rule from the provider contract
(`docs/plans/2026-05-23-hamlib-provider-contract.md`).

### 5.2 The rigplane-capability ↔ hamlib-token table

This table lives in `validation/registry.py` as the `hamlib_token` field on each
`CheckSpec` (§2.2), so there is exactly one place that knows the mapping:

| rigplane capability | hamlib token | rigctl access |
|---|---|---|
| `rf_gain` | `RF` | `l RF` / `L RF` (level, 0.0–1.0) |
| `af_level` | `AF` | `l AF` / `L AF` |
| `squelch` | `SQL` | `l SQL` / `L SQL` |
| `preamp` | `PREAMP` | `l PREAMP` / `L PREAMP` (dB) |
| `attenuator` | `ATT` | `l ATT` / `L ATT` (dB) |
| `nb` | `NB` | `u NB` / `U NB` (func) |
| `nr` | `NR` | `u NR` / `U NR` (func) |
| `meters` (S-meter) | `STRENGTH` | `l STRENGTH` (read-only level) |
| freq / mode / ptt / vfo | `F`/`f`, `M`/`m`, `T`/`t`, `V`/`v` | core rigctl verbs |
| `notch`, `agc`, `rit`, `xit`, `filter_width`, `scope`, `audio`, `tuner` | **None** | not mapped (N/A) |

The mapping is grounded in what the rigctld client **actually drives today**:
`RigctldClientRadio` (`src/rigplane/backends/rigctld_client/radio.py`) implements
exactly freq/mode/ptt/vfo + `rf_gain`/`af_level`/`preamp`/`attenuator`/`nb`/`nr`
(its `_SUPPORTED_COMMANDS`, `radio.py:11`, and `capabilities`, `radio.py:93`).
A `hamlib_token` is present **only** for capabilities the client can express;
everything else is `None` ⇒ N/A on the Hamlib provider (D6).

> Code-grounded correction to the task brief: the client's `get_nb`/`get_nr`
> take **no** `receiver` argument (`radio.py:241,249`), which is why
> `hardware.py`'s `_check_nb_set`/`_check_nr_set` reach them via `getattr`
> (`hardware.py:839,867`). Generator B must not assume a uniform
> `(receiver=0)` signature for func reads. `squelch`/`meters` tokens (`SQL`,
> `STRENGTH`) are listed as the natural next client additions but are **not yet
> implemented** in `RigctldClientRadio`; until they are, Generator B treats them
> as N/A too (the table is the contract, the client is the gate).

---

## 6. Generator B (Hamlib): registry → Hamlib check list

`build_hamlib_template_from_capabilities(capabilities, hamlib_caps, *, model, profile_id)`:

1. Walk the same registry as Generator A.
2. For each functional `CheckSpec`:
   - `hamlib_token is None` → emit `declaration = UNSUPPORTED_PENDING_EVIDENCE`
     with evidence `{"hamlib": "no_token_map"}`. This becomes a `UNSUPPORTED`
     `CheckResult` (N/A), **never** `FAIL` (D6).
   - `hamlib_token` set but absent from `hamlib_caps.get_funcs/levels/...` →
     emit `UNSUPPORTED_PENDING_EVIDENCE` with evidence
     `{"hamlib": "token_not_in_dump_caps", "token": "<RF>"}`.
   - Token present **and** the rigplane radio declares the capability →
     `SUPPORTED`.
3. TX/tuner specs stay `TX_ADJACENT_BLOCKED` regardless of Hamlib support —
   Generator B never emits an auto-actuating PTT/tune check.

Execution reuses the existing `_run_hardware_hamlib` path verbatim
(`src/rigplane/cli/_validate.py:352`): a `HamlibBridge` owns the real radio
(`hamlib_bridge.py:73`), stock `rigctld` is spawned with the profile's
`hamlib_model_id` (`profiles/__init__.py:180`), and `RigctldClientRadio` runs
`execute_hardware_checks` against it. The only required `RigctldClientRadio`
change to widen coverage (squelch, S-meter) is additive and tracked in
`MOR-201`; this ADR does not require it for the first cut.

---

## 7. Comparison / report model

### 7.1 Three "declared vs reality" dimensions

The matrix is run with `--provider both`, producing two `ValidationArtifact`s
(native + hamlib) over the **same** generated template. The report records:

| Dimension | Question | Source |
|---|---|---|
| (a) **rigplane profile vs reality** | Does the radio actually do what the TOML profile declares? | native artifact: per-check `SUPPORTED` declaration vs observed `pass`/`fail`/`unsupported`. |
| (b) **Hamlib model vs reality** | Does the Hamlib model DB match the radio? (upstreamable to Hamlib) | hamlib artifact: `dump_caps`-implied support vs observed result. |
| (c) **rigplane vs Hamlib cross-impl** | Where do the two implementations disagree on the same radio? | per-check native status vs hamlib status (the existing `_compare_artifacts` row). |

### 7.2 Artifact shape (reuse v1, no v2)

No schema bump. The per-check comparison reuses `_compare_artifacts`
(`_validate.py:538`) and `_attach_comparison` (`_validate.py:559`) exactly —
they already emit `{ "other_provider", "rows": [{check_id, this, other, agree}] }`
into `metadata.comparison`. The universal matrix **extends** that metadata with
the three named dimensions:

```jsonc
"metadata": {
  "summary": { /* existing status counts (runner.summarize_results) */ },
  "provider": "native",
  "generated_from": "profile",            // new: marks generator-derived templates
  "overrides": { "applied": ["scope.capture"], "excluded": [] },   // new
  "comparison": {                          // existing key, extended
    "other_provider": "hamlib",
    "rows": [ /* per-check agree/differ, unchanged shape */ ],
    "dimensions": {                        // new, optional
      "profile_vs_reality": { "agree": 14, "differ": 1, "differing": ["preamp.set"] },
      "hamlib_vs_reality":  { "agree": 9,  "differ": 0, "na": 6 },
      "cross_impl":         { "agree": 13, "differ": 2, "na": 6 }
    }
  }
}
```

Every new field is **optional `metadata`** — fully v1-compatible per the
versioning rule in `validation-matrix-v1.md` ("adding optional fields keeps the
version"). `validate_artifact_dict` (`schema.py:487`) accepts arbitrary
`metadata`, so no validator change is required.

Per-capability native-vs-hamlib agreement is the existing `rows` table; the
`dimensions` block is a roll-up computed from the two artifacts' per-check
statuses (native declaration+status, hamlib declaration+status). N/A (Hamlib
`UNSUPPORTED` from no token map) is counted separately and never as a
disagreement.

---

## 8. Profile converter (Hamlib → draft TOML + cross-check)

`rigplane radio-validate convert <hamlib-model-id|name>` (subcommand of the new
verb, §9). Two outputs, both **human-review-required, never auto-committed**:

### 8.1 `dump_caps` → `rigs/<model>.draft.toml`

From `HamlibCaps` (§5.1), auto-fill the fields the loader requires
(`_REQUIRED_SECTIONS`, `_REQUIRED_RADIO_FIELDS`, `rig_loader.py:47`):

| Auto-fillable | Source |
|---|---|
| `[radio].model`, `id` | catalog name / slug |
| `[radio].hamlib_model_id` | the model id |
| `[capabilities].features` | tokens → rigplane tags via the §5.2 table (RF→`rf_gain`, NB→`nb`, …) |
| `[modes].list` | `dump_caps` mode list, normalized to rigplane mode strings |
| `[filters].list` / width bounds | `dump_caps` filter passbands |
| `[vfo].scheme` + selects | `vfo_ops` (best-effort `ab` vs `main_sub`) |
| `[protocol].type` | `civ` for Icom model ids, else `kenwood_cat`/`yaesu_cat` |

| Needs human | Why |
|---|---|
| `civ_addr` | not in `dump_caps`; per-unit |
| `[commands]` CI-V byte maps | `dump_caps` does not expose RigPlane's CI-V wire bytes |
| `has_lan`/`has_wifi`, audio/codec policy, scope refs, meter calibration | RigPlane-specific, not in Hamlib |
| capability tags with no token (`agc`, `rit`, `notch`, `scope`, …) | unmappable from `dump_caps` alone |

The draft is written with a `# REVIEW:` banner and `# TODO(human):` markers on
every non-auto field, and a `.draft.toml` suffix so `discover_rigs`
(`rig_loader.py:940`, which loads `*.toml`) **does** pick it up only if a human
renames it — i.e. drafts are excluded by convention, flagged as an open question
(§11) on whether `discover_rigs` should explicitly skip `*.draft.toml`.

### 8.2 Cross-check existing TOML vs Hamlib → mismatch report

For a radio that **already** has a profile, compare `profile.capabilities`
against `HamlibCaps` tokens (mapped through §5.2) and emit a mismatch report:

- `rigplane declares X, Hamlib lacks token` → candidate RigPlane over-claim **or**
  Hamlib model gap (feeds dimension (b)).
- `Hamlib has token, rigplane omits tag` → candidate RigPlane under-coverage.
- modes/filters set differences.

Output is a plain report (JSON + human table), never a profile edit.

---

## 9. CLI / UX

`rigplane radio-validate <model> [--provider native|hamlib|both] [...]` — a new
subcommand registered alongside the existing `validate`
(`cli/_validate.py:add_subparser`). It is a **thin wrapper** (D7):

1. Resolve the `RadioProfile` (`get_radio_profile`, auto-detect via
   `resolve_radio_profile` when `--model`/address omitted, reusing
   `profiles.resolve_radio_profile`, `profiles/__init__.py:327`).
2. Generate the template in-memory (Generator A; Generator B for hamlib).
3. Merge any `docs/validation/templates/<profile_id>.json` override (§4).
4. Hand the resulting `MatrixTemplate` to the **existing** `_run_hardware` /
   `_run_hardware_hamlib` path — no second execution engine.

Flags (superset of `validate`, reusing its parser options): `--provider`,
`--read-only`, `--tx-allowed`, `--tuner-allowed`, `--allow-hardware`
(+`RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`), `--compare`, `--json`, `--output`,
`--operator-id`. New: `--write-template <path>` to dump the generated matrix
(so a maintainer can seed/inspect an override), and the `convert` subcommand
(§8). `--provider both` runs native then hamlib and attaches the §7 comparison.

The legacy `validate --template <path>` keeps working unchanged for hand-authored
and CI-fixture flows. `radio-validate` is the profile-driven entry point; the two
share the run path.

---

## 10. Safety model

Carried verbatim from the runner/hardware engine; the registry **encodes**
safety so generation cannot emit an unsafe check:

- **Read-only default for unknowns:** undeclared capabilities →
  `UNSUPPORTED_PENDING_EVIDENCE` (no write). `--read-only` makes every write
  check `SKIP` (`hardware._write_gate`, `hardware.py:456`).
- **RMVR mandatory restore:** `RMVR_SAFE_WRITE` specs run through
  `_read_modify_verify_restore` whose `finally` restore never raises
  (`hardware.py:408`).
- **TX/PTT + tuner never auto-actuated:** `TX_ADJACENT_BLOCKED` specs set
  `tx_adjacent = True`; `_is_authorized` (`runner.py:58`) fail-closes, and even
  when authorized they only report `MANUAL_REQUIRED` — `_manual_required_result`
  performs no actuation (`hardware.py:250`).
- **Write-only set+observe (MOR-180):** `WRITE_ONLY_OBSERVE` is the registry
  kind for controls with no readback; the engine sets and observes side effects
  without asserting an exact readback. (Hooks into the MOR-180 mechanism;
  the registry just classifies — no new write path here.)
- **Per-op timeouts:** every read/write is wrapped by `_guard` with
  `per_check_timeout` (`hardware.py:295`), default `DEFAULT_PER_CHECK_TIMEOUT = 5.0`.

Because `CheckKind` is a registry field and `tx_adjacent`/authorization are
re-derived from capability at execution time, an override **cannot** downgrade a
check's safety class (§4.2). This is the structural guarantee behind D4.

---

## 11. Open-core, phasing, dependencies, risks

### 11.1 Open-core

- **Headless, no telemetry:** generators, registry, converter, and reports are
  pure local computation. No network calls beyond the user's own radio and the
  user's own `rigctld`. Conforms to `open-core-policy.md` §2.
- **Public evidence, Core-only:** the matrix, artifacts, and the
  `rigplane-validation-matrix` contract are MIT/open. Pro-tier extensions
  (extended check libraries, signed attestation, fleet aggregation) stay behind
  the `local-extensions/` boundary per the v1 contract's "Core boundary" section
  and the Hamlib provider contract's Open-Core boundary.
- **Community workflow:** a user runs `radio-validate <model> --provider both`,
  reviews the three-dimension report, and contributes (a) a corrected/ new
  `rigs/<model>.toml` (seeded by `convert`, hand-reviewed) and (b) the artifact
  JSON as public evidence. Dimension (b) mismatches are upstreamable to Hamlib.

### 11.2 Phasing → children

> **Numbering note (reconciled 2026-05-29):** an earlier draft of this table
> drifted from the actual Linear tickets. The table below now matches the real
> ticket numbers. In particular `MOR-199` is the generic engine dispatch (§2.5,
> shipped in PR #1661), the override merge layer (§4) is tracked as `MOR-206`,
> and the Hamlib/comparison/converter slices are `MOR-200`–`MOR-204` as listed.
> There is no dedicated "widen registry coverage" ticket: new `CheckSpec`
> entries are added incrementally (alongside Generator B / as small registry
> additions), never by editing a template.

| Issue | Slice | Depends on |
|---|---|---|
| `MOR-197` | `registry.py`: `CheckSpec`, `CheckKind`, the §2.6 entries, value-rule/tolerance map | foundation #1653–#1657 |
| `MOR-198` | Generator A + `build_template_from_capabilities`; CLI profile-read split (§2.4) | MOR-197 |
| `MOR-199` | `hardware.py` generic `_check_from_spec` dispatch (the single additive engine change, §2.5) | MOR-197, MOR-198 |
| `MOR-200` | `HamlibCaps` parser: `dump_caps` ingestion + capability↔Hamlib token map (§5) | MOR-182 (bridge), MOR-197 |
| `MOR-201` | Generator B (Hamlib): registry → Hamlib check list via token map; optional `RigctldClientRadio` squelch/S-meter (§6) | MOR-200, MOR-197 |
| `MOR-202` | Comparison/reporting: native-vs-hamlib + profile-vs-reality `dimensions` roll-up (§7) | MOR-198, MOR-201 |
| `MOR-203` | Profile converter `convert` subcommand + cross-check report (§8) | MOR-200 |
| `MOR-204` | CLI `radio-validate --provider both` + OSS docs (§9) | MOR-198, MOR-202 |
| `MOR-206` | Override merge layer (§4) | MOR-198 |

Prereqs: **MOR-180** (write-only set+observe) for the `WRITE_ONLY_OBSERVE`
kind; **MOR-182** (Hamlib bridge) for Generator B execution — both already
landed per the brief.

### 11.3 Risks / open questions (for the owner)

1. **`value_rule` overridability** — should the override layer be allowed to
   change a check's value-generation (e.g. a narrower safe-freq window)? §4.2
   lists it as overridable but the `CheckSpec.value_rule` is currently a
   closed enum; exposing it to overrides widens the safety surface. **Recommend:
   keep value rules registry-only for v1; revisit if a real radio needs it.**
2. **`*.draft.toml` discovery** — `discover_rigs` loads all `*.toml`
   (`rig_loader.py:953`). Drafts must not be auto-loaded. **Recommend: add an
   explicit `*.draft.toml` skip to `discover_rigs`** (one-line, but it is a
   `profiles`-layer change outside this ADR's `validation` scope — needs its own
   tiny issue).
3. **Hamlib `dump_caps` stability** — format varies across Hamlib versions; the
   parser must degrade gracefully (mirror `_load_from_tool`). N/A-on-parse-failure
   keeps Generator B safe.
4. **`mode_cycle` on rigs without USB/LSB/CW/AM** — the existing `mode.set`
   handler picks the first of a fixed list (`hardware.py:618`); a data-only or
   FM-only rig could have none of them. **Flag:** the generic handler should
   derive candidates from `profile.modes` rather than a hard-coded tuple — a
   small improvement to fold into MOR-199.
5. **`freq.reverse_sync` on Hamlib** — depends on RigPlane's `radio_state`,
   which the rigctld client populates differently (`radio.py:117`); marked N/A
   for Hamlib in §2.6 rather than risking a false `state_publishing` fail.

---

## 12. References

- Schema: `src/rigplane/validation/schema.py`
- Hardware engine: `src/rigplane/validation/hardware.py`
- Dry-run runner: `src/rigplane/validation/runner.py`
- CLI: `src/rigplane/cli/_validate.py`
- Capability vocabulary: `src/rigplane/core/capabilities.py`
- Profiles: `src/rigplane/profiles/__init__.py`, `src/rigplane/profiles/rig_loader.py`
- Capability Protocols: `src/rigplane/core/radio_protocol.py`
- Hamlib provider: `src/rigplane/backends/rigctld_client/radio.py`,
  `src/rigplane/backends/hamlib_models.py`, `src/rigplane/backends/hamlib_probe.py`,
  `src/rigplane/hamlib_bridge.py`
- Existing templates: `docs/validation/templates/*.json`
- v1 contract: `docs/contracts/validation-matrix-v1.md`
- Open-core policy: `docs/architecture/open-core-policy.md`
- Prior plan: `docs/plans/2026-05-28-real-radio-validation-matrix.md`
- Hamlib provider contract: `docs/plans/2026-05-23-hamlib-provider-contract.md`
