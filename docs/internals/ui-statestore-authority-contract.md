# UI radio authority contract

Status: binding architecture contract for MOR-1405, introduced by MOR-1406.

Machine-readable inventory and guard baselines:
[`ui-radio-control-contract.toml`](ui-radio-control-contract.toml).

## Decision

Every operator-facing radio fact has one authority: the backend
`StateStore`. The two supported convergence directions are:

```text
physical radio change
  -> existing provider receive/transceive or acquisition/readback path
  -> confirmed Observation
  -> StateStore
  -> versioned public WebSocket state
  -> shared selector/view model
  -> UI

Web intent
  -> existing serialized command/ACK path
  -> existing confirmed observation/readback path
  -> the same StateStore
  -> the same versioned public WebSocket state
  -> the same shared selector/view model
  -> UI
```

An ACK proves command lifecycle, not radio state. An outgoing intent, HTTP
response, compatibility callback, cached overlay, local storage value, scope
frame, or audio frame cannot supply, hide, or override an observed radio fact.

This contract integrates the mechanisms already present in Core. It does not
replace the command queue, provider adapters, acquisition scheduler,
`Observation`, `StateStore`, public projection, or WebSocket delivery.

## Authority boundary

### StateStore-owned radio facts

The following are StateStore facts whenever they are displayed or used to
gate a radio operation:

- receiver, VFO and A/B identity; selected/unselected role and TX target;
- frequency, mode, data mode, filter and radio DSP state;
- every receiver and transmitter control value, toggle, level and status;
- meters, PTT, split, RIT/XIT, tuner, scan and memory-related live facts;
- scope mode, receiver, span, edge, reference, speed, hold and frequency
  geometry;
- radio connection, readiness, profile generation, capability availability
  and supported control ranges.

Unknown, stale, unavailable and unsupported are distinct states. None may be
replaced by a plausible default such as 14.074 MHz, USB, FIL1, VFO A, a power
level, or a compressor value.

### Allowed local lifecycle and preferences

Local UI state may describe:

- command queued/sent/acknowledged/observed/failed/cancelled/superseded;
- duplicate-click suppression and retry presentation;
- WebSocket, audio and scope transport session delivery state;
- TX-controller phase, release risk and teardown fault, provided observed PTT
  remains StateStore-only;
- layout, skin, language, panel order, brightness and tuning-step preference;
- user-stored memory records and mode-to-last-filter command preference,
  provided current radio values are read from StateStore.

Lifecycle state must be structurally separate from observed radio state. A
pending slider marker may show the requested value, but the control's confirmed
value remains the last StateStore value. A newer contradictory physical
observation wins immediately.

### Data-plane exception

Audio samples/FFT bins and spectrum/waterfall pixels remain on their existing
high-rate transports. This exception is payload-only. Labels, mode, receiver,
center/span/edges, tuning coordinates, control availability and routing facts
are StateStore-owned. A binary sample/frame may carry correlation metadata but
cannot write a radio fact in the browser.

## Machine-readable coverage contract

The TOML inventory is executable evidence, not a roadmap decomposition. Each
family row records:

- profile/capability family;
- every existing public control intent routed by `ControlHandler`;
- existing serialized command/ACK/readback route;
- existing provider observation/acquisition route;
- canonical `FieldPath` family;
- generated public fields and WebSocket ownership;
- shared selector/view-model responsibility;
- permitted pending/error semantics;
- semantic surfaces and required automated/hardware proof;
- the infrastructure child that owns each current gap.

The contract test derives the current command inventory from
`ControlHandler._COMMANDS`, public fields from the generated Pydantic state
schema, provider identity from `rigs/*.toml`, and semantic surfaces recursively
from the frontend. Every declared path is parsed by `FieldPath.parse`, must be
present in `DEFAULT_FIELD_REGISTRY`, and may be owned by exactly one family;
the union must equal the registry. Profile rows reject duplicate profiles and
unknown provider identities. A newly added command, registered field, public
field, profile, or nested semantic panel therefore fails until it is classified
in an infrastructure family. Public fields that do not yet have a registered
canonical path are recorded separately as `field_path_gaps` owned by MOR-1408,
never represented by invented or wildcard paths.

Rows include RF Power, compressor on/level, VFO identity and tuning,
mode/filter, receiver levels and DSP, TX-related controls, memories, meters,
scope metadata and capabilities. These are coverage rows for shared mechanisms;
they are not permission to build control-specific readers, timers, handlers or
state stores.

## Architecture guards

MOR-1406 lands before MOR-1407 through MOR-1409, so known violations are
explicitly baselined rather than silently blessed. Each exception includes an
owning ticket and an exact per-path occurrence count. The detected multiset
must equal the manifest multiset. Debt can shrink only when code and manifest
are updated together in the same reviewed change. Independently, the head
manifest is compared with the reachable PR-base manifest: paths must remain a
subset and every count must be less than or equal to its base value. The
introducing PR has a deterministic bootstrap only after Git proves that its
reachable base tree has no contract file. CI fails when:

- a violation appears in a new path;
- a known path's count changes without the matching manifest edit;
- a path or count increases even when code and manifest are expanded together;
- a new per-control polling/timer file or parallel radio/capability store
  appears;
- a command, public radio field, profile or semantic panel is unregistered.

The baselines cover:

1. direct optimistic/command/component radio-truth patches;
2. parallel HTTP/bootstrap and WebSocket writers;
3. presentation components importing transport semantics directly;
4. fabricated live-radio defaults;
5. persisted live-radio truth;
6. scope-frame metadata acting as a second radio authority;
7. every frontend `setInterval`/`setTimeout`, regardless of filename;
8. mutable radio/capability stores identified by state shape, not filename;
9. control-aware Python sleep loops outside the shared acquisition contract.

Frontend guards use the installed TypeScript Compiler API over a real
`Program`/`TypeChecker`. Svelte files are parsed by `svelte/compiler` and their
module/instance script blocks enter the same virtual module graph. Symbol
origins, aliases, wildcard/named/namespace re-exports, optional and bracket
calls, constant dynamic imports, literal aliases/ternaries, storage value flow,
frame carriers, global timer aliases, `$state` shapes and later property writes
are resolved from syntax and symbols. Radio-truth vocabulary is generated from
this manifest rather than maintained as a second hand-written control list.
Authority-sensitive unresolved imports or non-constant dynamic imports fail
loud.

The Python worker guard uses the Python AST, canonicalized import aliases and a
fixed-point local call graph. It detects loop + delay + radio-read composition
without relying on worker/function filenames. Adversarial tests apply all 23
independently reproduced bypasses as virtual mutations of the real repository
source set. A separate proof shows that adding both a new violation and its
matching head exception still fails historical monotonicity. Allowed
pending/error/theme/layout and audio/spectrum-payload fixtures remain clean.

Legitimate StateStore reducers, shared acquisition requests, ephemeral intent
lifecycle and audio/spectrum payload rendering remain allowed.

The baseline is temporary debt. MOR-1407, MOR-1408 and MOR-1409 must reduce it;
they must not edit a ceiling upward to make new violations pass. If a rule is
too broad, narrow its semantic definition and independently review the change
rather than increasing the exception count.

## Child ownership

### MOR-1407 — observation/acquisition integration

Owns missing shared provider observation promotion and existing acquisition
scheduler coverage. It may update provider-neutral tables/adapters and existing
freshness/priority policy. It may not add a second polling loop, per-control
timer, UI event path, or write pipeline. MOR-1404 exclusively owns the bounded
one-byte Icom command-07 A/B ingress conformance change.

### MOR-1408 — canonical projection and versioned WebSocket authority

Owns canonical StateStore paths, public schema/projection and ordering across
snapshot, delta, reset, expiry, reconnect and provider generation. Additive
compatibility is required. It may not introduce a second cache or delivery
channel.

### MOR-1409 — frontend projection and intent seam

Owns the single versioned WebSocket reducer, read-only StateStore replica,
shared selectors/view models and separate ephemeral intent lifecycle. It
removes optimistic truth, HTTP state races, fabricated defaults, direct
component transport/state semantics and data-plane metadata authority while
preserving exact command sends.

### MOR-1410 — cross-radio evidence

Owns exact-head IC-7300 and FTX-1 conformance. Representative controls from
every family must prove both convergence directions using the same canonical
paths. Unsupported controls remain explicitly unavailable. Physical changes
must emit zero radio writes; Web actions must emit exactly their established
serialized operation and display only confirmed StateStore results.

## Frozen behavior

MOR-1406 changes no production behavior. The following remain frozen for all
children unless a separately bounded issue explicitly changes them:

- command serialization/order, queue ownership, ACK matching and readback;
- rate limiting and user-write suppression;
- PTT OFF priority, no stale ON replay, TX ownership and teardown;
- audio transport, codec, bridge, driver and sample lifecycle;
- scope enable/disable, binary protocol, samples and spectrum data;
- USB, LAN, rigctld and provider write behavior.

No child may add a raw CI-V/CAT-to-UI path, model-name frontend branch,
command replay, auto-select workaround, or second live-radio state store.

## Verification

Every implementation child must show:

1. its relevant family rows are green without expanding a guard baseline;
2. physical observation reaches the registered FieldPath and StateStore with
   zero radio writes;
3. one Web intent keeps the existing command count/order/ACK behavior and the
   UI changes only after a confirmed observation/readback;
4. pending/error state cannot mask a newer StateStore revision;
5. cold start, reconnect, stale generation, expiry, NAK and timeout fail
   honestly;
6. command/PTT, audio and scope regression suites remain unchanged and green;
7. a fresh independent exact-head review issues binding domain verdicts.

Real hardware evidence must also record profile identity, exact head, initial
and final state, StateStore/WS revision lineage, operation counts, PTT OFF and
clean transport release. FTX-1 remains a mandatory release gate.

## Design lineage

This contract applies and does not supersede:

- [UI composition architecture v3](../plans/2026-07-25-ui-composition-architecture-v3.md)
  — runtime to adapters/view models to semantic presentation;
- [panel adapter migration](../plans/2026-04-29-panel-adapter-migration.md)
  — shared adapter seams instead of direct component transport/state access;
- [radio acquisition scheduler](radio-acquisition-scheduler.md) — freshness
  requests produce observations and never invent confirmed state;
- [legacy state writer inventory](legacy-state-writer-inventory.md) — no
  second writer and staged migration boundaries;
- [radio state pipeline contracts](radio-state-pipeline-contracts.md) —
  canonical `FieldPath`, `Observation`, `ChangeSet` and StateStore semantics.

The session-10 research inputs are
`research-mor1405-all-controls.md`, `design-mor1405-statestore-only.md`,
`audit-ui-statestore-only.md`, `diagnose-mor1404-physical-ab.md`, and
`spec-mor1403-1404-vfo-state.md` in the durable project archive. The checked-in
TOML and tests are the repository-owned continuation of those findings.
