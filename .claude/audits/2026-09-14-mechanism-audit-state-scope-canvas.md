# Mechanism audit: IC-7610 state, scope, and canvas stability

- Audited revision (exact HEAD): `de50644e9cd38a9041a68dbac2acafd8a1e767f3` (2026-09-14).
- Exact base: `601f27f71870e0f60e6872bb2c51495f97022cf3`.
- Ticket: MOR-2467.
- Final verdict: **PASS** — no deletion or consolidation is warranted.
- Read-only throughout; independently adjudicated at the exact HEAD named above.

## Scope and reconstructed path

The bounded tract is the changed state/projection/render mechanism, not every
unrelated declaration in its large composition files:

`StateStore snapshot → WebServer full/delta envelope → applyDeltaEnvelope →
setRadioState → runtime authority → projectRadioView / passband projection →
semantic hosts and canvases`.

**Tests were not run by the audit.** Mac mini tests, checks, build, live RX-only
sampling, and CPU measurements are separate implementation evidence.

## Definition, liveness, and dead-code census

The census counted definitions and mutable cells in each bounded tract, then
searched literal names in production and tests in both directions. It also
searched dynamic access and registries before clearing deletion candidates.

| Scoped module / tract | Enumerated definitions and state | Writer and consumer set | Result |
|---|---|---|---|
| `src/rigplane/web/_delta_encoder.py` | 1 class; 3 functions (`encode`, `reset`, `apply_delta`); 4 instance attributes (`_previous_state`, `_transport_seq`, `_delta_count`, `_full_state_interval`) | `encode` writes one full/delta envelope for WebServer; `reset` owns its lifecycle; pre-existing `apply_delta` is consumed by tests as a Python protocol utility | 0 dead candidates |
| `src/rigplane/web/server.py` state-broadcast tract | 6 scoped methods; 7 broadcast/cache/encoder slots | `_broadcast_state_update` is the writer; delayed broadcast, public-state builder, encoder, and WS fanout form its one consumer chain | 0 dead candidates |
| `frontend/src/lib/transport/ws-client.ts` state-update tract | 1 private function (`applyDeltaEnvelope`) and 2 accepted-state slots | the sole production `state_update` handler consumes it, then calls `setRadioState` | 0 dead candidates |
| `frontend/src/lib/stores/radio.svelte.ts` state-update tract | 1 class with `current`; 30 module functions; 4 revision cells; 1 subscriber set; 1 live-metadata key set | `setRadioState` is the sole production WS writer; store subscriptions consume accepted snapshots | 0 dead candidates |
| `SemanticRadioSurfaces.svelte` projection tract | 1 projection type; 2 functions (`radioViewStateSignature`, `projectRadioView`); 2 constants; 2 cache/version cells | canonical view, Station Meters, finite authority, Receiver, RX Audio, RF Front End, and Antenna consume the single projection | 0 dead candidates |
| `scope-passband-display.ts` | 9 top-level functions plus local `stalePathIsConfirmed` and `read`; 7 constants; no class attributes | `inspect` writes one candidate; `projectScopePassbandDisplay` consumes it into monotonic display/hold state | 0 dead candidates |
| `SpectrumPanel.svelte` resize tract | 6 resize/gesture functions; 5 capture/candidate cells | start captures authority, move derives a candidate, end revalidates and is the sole commit consumer | 0 dead candidates |
| `SpectrumCanvas.svelte` and `AudioSpectrumCanvas.svelte` | each has 3 lifecycle functions (`scheduleDraw`, `draw`, visibility observer) plus RAF/visibility/canvas/pixel cells | pushed pixels, options, size, and visibility invalidate one coalesced RAF per canvas | 0 dead candidates |
| `AudioSpectrumPanel.svelte` visibility tract | 6 visibility/subscription/lease cells; no named tract function | Intersection state is the writer and sole gate for FFT subscription/resource lease | 0 dead candidates |

Definition sites and the live path are frozen at
`src/rigplane/web/_delta_encoder.py:DeltaEncoder`,
`src/rigplane/web/server.py:WebServer._broadcast_state_update`,
`frontend/src/lib/transport/ws-client.ts:applyDeltaEnvelope`,
`frontend/src/lib/stores/radio.svelte.ts:setRadioState`,
`frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:projectRadioView`,
`frontend/src/lib/runtime/adapters/scope-passband-display.ts:projectScopePassbandDisplay`,
and `frontend/src/components/spectrum/SpectrumPanel.svelte:sameResizeAuthority`.

Literal-name and dynamic-dispatch searches found no second production writer,
alternate projection cache, or dynamic access path for the newly scoped private
symbols. Out-of-repository consumers are **UNKNOWN**, but none of these scoped
private symbols is public API. Python `apply_delta` is not a deletion candidate:
it predates this change and has test consumers; browser reconstruction is the
TypeScript implementation.

## Deletions

No D finding survived the consumer and deletion-guard checks.

## Prior rulings and in-flight search

- `docs/architecture/building-a-skin.md:33-34` rules that every semantic
  surface receives one `RadioViewModel`, produced by one `toRadioViewModel`.
- `docs/internals/radio-state-pipeline-validation.md:153-162` distinguishes an
  observed-stale fact from an unobserved compatibility default.
- `frontend/src/lib/runtime/adapters/scope-adapter.ts:toSpectrumAuthority`
  already retains observed stale readings as held display facts.
- Searches for the introduced cache, stale-confirmation, resize-authority, and
  RAF symbols found the live consumers below and no competing in-flight target.

## Steelman

The strongest case for doing nothing was that a plain derived semantic view and
continuous canvas RAF loops are locally simple, and that a strict freshness
barrier is conservative. That arrangement is not correct at IC-7610 meter/poll
cadence: it rebuilds the complete semantic model per host, draws unchanged
pixels continuously, and treats confirmed held RX readbacks as absent. Moving a
cache into each host would recreate divergence. The landed single cache around
the already-canonical adapter, locally owned invalidation-driven canvas loops,
and presentation-only held-readback policy are the simpler mechanisms.

## Consolidations

### F1 — canonical transport reconstruction is already shared

Verdict:          already-shared
Rank:             parallel
Elements:         `src/rigplane/web/_delta_encoder.py:DeltaEncoder.encode`; `src/rigplane/web/server.py:WebServer._encode_state_update`; `frontend/src/lib/transport/ws-client.ts:applyDeltaEnvelope`; `frontend/src/lib/stores/radio.svelte.ts:setRadioState`
Consumers:        server broadcast: one encoder path; browser: one WS handler; store: one production writer
Definition site:  `_delta_encoder.py:53-153`; `server.py:1461-1534,1734-1755`; `ws-client.ts:935-1007`; `radio.svelte.ts:242-307`
Divergence:       none found
Prior ruling:     canonical state/freshness revision pipeline; no later contradictory ruling found
In-flight:        none found
Required surface: exists
Depends on:       provider generation, capability topology, and revision/freshness/observation ordering
Confidence:       high
Falsifier:        a second production writer bypassing `setRadioState`, or a browser consumer bypassing `applyDeltaEnvelope`
Fix class:        none
Actionable:       no — one owner and one live consumer chain already exist

### F2 — semantic projection cache is the canonical composition point

Verdict:          C
Rank:             parallel
Elements:         `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:radioViewStateSignature`; `SemanticRadioSurfaces.svelte:projectRadioView`
Consumers:        canonical view, Station Meters, finite authority, Receiver, RX Audio, RF Front End, and Antenna
Definition site:  `SemanticRadioSurfaces.svelte:808-902,1277-1284,1860-1937`
Divergence:       none; scoped semantic hosts route through the same projection
Prior ruling:     `docs/architecture/building-a-skin.md:33-34` requires one `RadioViewModel` shape and producer
In-flight:        no parallel cache or direct per-host projection found
Required surface: exists
Depends on:       immutable radio snapshot identity plus capability, TX, audio, and scope snapshot identity
Confidence:       high
Falsifier:        a semantic host independently rebuilding `toRadioViewModel`, or a visible adapter input omitted from the signature
Fix class:        none
Actionable:       no — this is correctly local to the only composition site owning all inputs

### F3 — confirmed held-stale passband facts are display-only

Verdict:          C
Rank:             displaced
Elements:         `frontend/src/lib/runtime/adapters/scope-passband-display.ts:stalePathIsConfirmed`; `scope-passband-display.ts:projectScopePassbandDisplay`; `frontend/src/components/spectrum/SpectrumPanel.svelte:canResizePassband`
Consumers:        one passband projection consumed by one SpectrumPanel display/gesture surface
Definition site:  `scope-passband-display.ts:128-157,227-252,269-376`; `SpectrumPanel.svelte:303-359,611-702`
Divergence:       confirmed held-stale facts can preserve the passive overlay; unconfirmed stale remains conservative; a stale display cannot resize
Prior ruling:     `radio-state-pipeline-validation.md:153-162` and `scope-adapter.ts:toSpectrumAuthority` preserve observed stale facts as held readings
In-flight:        none found
Required surface: exists
Depends on:       provider-confirmed quality, monotonic markers, current hardware scope frame, session/epoch/domain checks
Confidence:       high for scoped UI behavior; medium for upstream truthfulness of `quality=confirmed`
Falsifier:        stale display enabling a resize commit, or any TX command importing this display projection as authority
Fix class:        none
Actionable:       no — presentation continuity does not grant TX or RF authority

### F4 — canvas scheduling is locally owned and bounded

Verdict:          C
Rank:             name-collision
Elements:         `frontend/src/components/spectrum/SpectrumCanvas.svelte:scheduleDraw`; `frontend/src/components-v2/panels/audio-scope/AudioSpectrumCanvas.svelte:scheduleDraw`; `AudioSpectrumPanel.svelte` visibility lease
Consumers:        SpectrumCanvas consumes scope pixels; AudioSpectrumCanvas consumes FFT pixels; AudioSpectrumPanel gates the FFT resource
Definition site:  `SpectrumCanvas.svelte:42-99`; `AudioSpectrumCanvas.svelte:53-127`; `AudioSpectrumPanel.svelte:16-50`
Divergence:       pixel inputs, renderers, and resource lifecycles differ; only the one-RAF-per-invalidation pattern is shared
Prior ruling:     none requiring a generic scheduler
In-flight:        no shared scheduler or duplicate second loop found
Required surface: exists locally in each independent renderer
Depends on:       browser RAF, canvas size, visibility, and latest pixels
Confidence:       high
Falsifier:        either canvas scheduling concurrent RAF callbacks for one invalidation or continuing to draw while hidden
Fix class:        none
Actionable:       no — extracting a generic scheduler would add an abstraction with dissimilar lifecycle consumers

### F5 — drag continuity uses the minimal authority tuple

Verdict:          C
Rank:             parallel
Elements:         `frontend/src/components/spectrum/SpectrumPanel.svelte:sameResizeAuthority`; resize capture/candidate/end revalidation
Consumers:        active resize presentation and the final filter-width commit
Definition site:  `SpectrumPanel.svelte:336-351,611-624,644-702`
Divergence:       intentionally excludes filter shape, raw PBT, and scope-toolbar fields because they do not alter the edge-to-width mapping
Prior ruling:     no separate ruling found; existing gesture contract captures only inputs used by the gesture
In-flight:        none found
Required surface: exists
Depends on:       provider generation, receiver, frequency, mode, DATA, filter, width, shift, rule, and sample geometry
Confidence:       high
Falsifier:        an omitted field changing edge-to-width mapping, or an included unrelated field causing a valid drag to abort
Fix class:        none
Actionable:       no — command-time identity and legal-width validation remain intact

## TX/RF safety adjudication

A falsely labelled upstream `confirmed` stale reading could make the passive
overlay misleading, but cannot grant TX authority. The passband adapter has no
PTT/TX-capability dependency; TX target and permit remain independently derived
in `frontend/src/lib/runtime/adapters/tx-capabilities.ts:toTxCapabilityState`.
A stale passband display is not resizable, and the command path separately
validates current context in
`frontend/src/lib/runtime/commands/panel-commands.ts:setFilterWidth`.

## Weakest link

Upstream assignment of `fieldStatus.quality` is the narrowest trust boundary.
If it marks an unconfirmed value as confirmed, the passive overlay may retain an
inaccurate reading. It does not cross into PTT authority in this tract. Check
provenance production first if this conclusion must be revisited.

## Cleared

- State producer, delta reconstruction, and radio-store writer each have one owner.
- Semantic projection is shared rather than rebuilt per surface.
- Spectrum and audio canvases have bounded, visibility-aware frame lifecycles.
- Station Meters remain mounted across freshness-only state updates.
- Confirmed held-stale scope facts remain presentation-only.
- Resize preserves command-time identity, geometry, and legal-width checks.
- TX, PTT, ATU, and RF authority remain independent and fail-closed.
- No new dead private declarations or competing in-flight mechanism were found.

## Verdict

**PASS.** The implementation repairs the mechanism at its ownership boundaries
rather than masking symptoms. No blocking deletion, displacement, or
consolidation finding remains at the audited revision.
