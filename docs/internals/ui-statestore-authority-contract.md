# UI radio authority contract

Status: binding architecture contract for MOR-1405, introduced by MOR-1406.

Machine-readable inventory and declared owners:
[ui-radio-control-contract.toml](ui-radio-control-contract.toml).

## One authority

Every operator-facing radio fact has one authority: backend `StateStore`.

```text
physical radio change
  -> existing provider receive/acquisition/readback
  -> confirmed Observation -> StateStore
  -> versioned public WebSocket state
  -> read-only selector/view model -> UI

Web intent
  -> typed intent facade
  -> existing serialized command/ACK/readback
  -> confirmed Observation -> the same StateStore
  -> the same WebSocket and selector/view model -> UI
```

ACK and pending state describe delivery, not observed radio truth. An outgoing
intent, HTTP response, local cache, persistent preference, audio frame, or
scope frame cannot supply or override an observed radio value. This contract
integrates the existing command queue, providers, acquisition scheduler,
`Observation`, `StateStore`, public projection, and WebSocket delivery; it
does not replace them.

StateStore owns displayed receiver/VFO identity, frequency, mode/filter, every
live control and meter, PTT/TX target, scope radio metadata, connection and
capability facts. Unknown, stale, unavailable, and unsupported remain distinct;
they are not replaced with plausible radio defaults.

## Capability boundary

Enforcement is structural first:

- the radio store exposes a read-only replica to presentation;
- the sanctioned WS reducer owns final observed-state writes;
- adapters/view models and props are the read-only UI seam;
- command bus/runtime command facades are the typed intent seam;
- every direct static import/export/re-export of a raw StateStore or transport
  origin is rejected at its first project hop unless that module is a declared
  owner, read-only selector seam, or typed intent seam;
- authority-sensitive presentation may use dynamic import/`require` only with
  a finite string literal; literal non-radio loaders remain available;
- persistent preference, sample-plane, and acquisition owners are declared;
- new owner/debt paths fail reachable-base monotonic comparison.

Exact legacy paths remain MOR-1407/MOR-1409 migration debt and may only shrink.
A local ESLint plugin enforces four finite rules with deterministic IDs:

1. `radio-authority/structural-boundary` — module capabilities;
2. `radio-authority/authority-sink` — canonical writer, module-level live
   store, observed persistence, and observed-value fallback sinks;
3. `radio-authority/scope-metadata` — ScopeFrame radio metadata ownership;
4. `radio-authority/recurring-control` — recurring callbacks crossing a
   radio read/write/transport seam.

The first-hop rule is intentionally not whole-program provenance: a forbidden
facade is rejected where it directly touches the sanctioned origin, while its
consumer is outside this rule's graph. The sink rule supports a deliberately
small algebra: imported authority symbols, direct aliases, properties/elements,
finite literals,
conditional/nullish composition, and calls receiving an authority argument.
An unsupported helper result is rejected only when it reaches a declared
authority sink. The guard does not analyze unrelated frontend code.

## Allowed local plane

The following remain local when structurally separate from observed truth:

- queued/sent/ACK/observed/failed/cancelled/superseded command lifecycle;
- retry, duplicate-click suppression, delivery error, and teardown state;
- theme, layout, language, skin, panel order, and user preferences;
- user memory records and command preferences;
- opaque domain objects with no authority source or sink;
- visual animation and debounce timers that do not cross a radio seam.

A pending slider marker may render next to the confirmed value. It may not
replace that value, and a newer physical observation wins immediately.

Local symbols named `setRadioState`, `eval`, `Function`, `Proxy`, or
`Reflect` are not authority capabilities. Direct unsafe executable syntax is
a concern only inside a sanctioned authority owner; MOR-1406 does not track
callable origins through arbitrary objects or reflection.

## Sample/data plane

Audio samples, FFT bins, spectrum pixels, and delivery counters stay on their
existing high-rate transports. Scope `pixels` are payload. Receiver, mode,
center/span/edges, tuning coordinates, availability, and routing are radio
metadata and may be consumed as authority only by the declared ingress/
projection owner. An opaque `Envelope<ScopeFrame>` is not opened by the guard.

## Executable inventory

The TOML matrix classifies every current `ControlHandler` intent, canonical
`FieldPath`, generated public field, radio profile/provider, and nested
semantic/panel surface. Tests derive those inventories from production sources:

- every intent appears exactly once;
- declared paths equal `DEFAULT_FIELD_REGISTRY`;
- every generated public field has one family owner;
- every profile/provider and nested surface is classified;
- declared owner/debt paths equal the executable ESLint plugin;
- the head owner set cannot exceed a reachable base.

Rows are coverage for shared mechanisms, not permission to build per-control
readers, timers, stores, or handlers.

## Finite proof and stop rule

The adversarial proof is grouped into ten binding classes: writer capability,
module graph, competing writer, parallel store, persistence, radio default,
scope/audio, recurring acquisition, presentation command semantics, and symbol
precision. Each forbidden family has a direct and one ordinary indirection;
helper-returned object/array plus nullish store values are explicit boundary
cases. Allowed fixtures cover pending/error, preferences, user memory, pixels,
opaque objects, animation timers, and local shadows.

A new bypass blocks only when all are true:

1. it is valid production-toolchain TS/Svelte/Python;
2. it crosses a declared structural boundary or finite source-to-sink rule;
3. it is a plausible ordinary production form;
4. it is a new equivalence class, not deeper nesting or another carrier
   spelling already represented.

MOR-1406 explicitly does **not** promise soundness for arbitrary TypeScript,
higher-order composition, unrestricted dynamic execution, or reflection.
After inventory/monotonic checks, ten-class forbidden/allowed fixtures,
deterministic output, and frozen runtime pipelines are green, deeper carrier
permutations are non-blocking hardening unless production adopts that form.

## Child ownership

- **MOR-1407:** shared provider observation/acquisition coverage; no second
  poller or per-control loop.
- **MOR-1408:** canonical paths, projection, versioned WS ordering, reconnect
  and generation semantics; no second cache/channel.
- **MOR-1409:** sole WS reducer, read-only replica/selectors, typed intent
  facade, and removal of exact legacy writer/transport/default debt.
- **MOR-1410:** exact-head IC-7300 and mandatory FTX-1 evidence in both
  convergence directions.

## Frozen behavior

MOR-1406 changes no production runtime. Command serialization/order, ACK and
readback, rate limiting, PTT OFF priority/no stale ON, TX ownership/teardown,
audio driver/codec/bridge/samples, scope binary protocol/samples, provider
writes, and USB/LAN/rigctld behavior remain frozen.

Implementation children must preserve those pipelines, prove physical changes
produce StateStore/WS revisions with zero radio writes, prove Web actions keep
the established command count/order and display only confirmed observations,
and obtain a fresh exact-head independent review. FTX-1 remains a release gate.
