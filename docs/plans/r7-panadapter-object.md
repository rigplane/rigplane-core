# R7: the panadapter as a server-side object

Design document, draft 1 — 2026-11-09. Author: the rig-deck coordination session (Fable) for the
owner; reviewer: sumo (Core/Pro coordinator). Epic: MOR-4267 (panorama/scope pipeline redesign).
Thread: MOR-4311. Code references are to `origin/main` = `f45f581` unless marked otherwise.
Nothing here is scheduled; implementation waits for Pro beta.16 and Core 3.2.0.

## 0. Intent

Owner's words (2026-11-09): any width of panorama — a whole band in the screen, or the CW / phone /
digital part of a band, like on Flex — the radio picks the matching slice and Core crops to the
requested width; one universal mechanism for any radio, including an external wideband panadapter
(rig-iq). Core must be what the radio itself is on Flex: a panadapter object on the server, so the
browser orders one panorama, the app another, the deck a third.

## 1. What exists (R4 + R5), and what is missing

| Already there | Where |
|---|---|
| Per-client request `Subscription(source_id, receiver, view, width, max_fps)` and grant `GrantedSubscription(view, coverage, width, max_fps)`; every wire frame carries `view` and `coverage` separately | `scope/models.py:187-230`, `web/protocol.py:131-162` |
| `View(start_hz, end_hz)` and `FollowView(follow="vfo", span_hz)`; Icom fixed-edge mapping for absolute views | `scope/models.py:57-86`, `runtime/radio_scope_source.py:95-128` |
| `SourceCapabilities(receivers, view_modes, span_choices_hz, native_width, max_fps, coverage_rule, receiver_native_widths)` filled from the profile | `scope/models.py:102-151`, `radio_scope_source.py:46-62` |
| Per-client resampling by max-hold onto the client's width; width is clamped to the native width (no upsampling) | `scope/resample.py:8`, `scope/hub.py:186,290-` |
| Latest-wins pacing per client (`max_fps`, `0` pauses) and demand accounting (`ScopeDemand`) | `scope/hub.py:101,237` |
| One CI-V writer for the radio scope with demand per owner (`"web"`, `"sdk"`), TX gating, hidden grace | `runtime/scope_session.py:42-330` |
| Source plugins: radio, audio FFT, IQ FFT (rig-iq lands here, no new transport) | `server.py:1786-1823`, `sdr/scope_source.py:51` |
| Band plans as Core data: `band-plans/*.toml`, regions `US`, `IARU-R1/R2/R3`, segments with `mode` (cw, digital, phone, beacon, …) | `web/band_plan.py:101-332` |
| Ownership tokens for TX: a plain string per web control session or ticket, `owner_disconnect`, `tx_holder_nowait`, `held_by_other` | `runtime/managed_tx_authority.py:388,855-935` |

| Missing | Consequence today |
|---|---|
| A subscription has no identity, owner or sharing rule; `ScopeHandler` keys streams by `(source_id, receiver)` per socket | Two clients on one Icom scope: each `subscribe` calls `plugin.set_view` → `set_center`/`set_fixed` on the radio; the second client re-centres the radio under the first (`hub.py:186`, `radio_scope_source.py:108-127`). The first keeps its grant geometry and sees honest partial coverage, but has no say. Last writer wins. |
| No `maxPanadapters` or retune cost in capabilities | The hub cannot know that the radio can serve one view and the IQ source many |
| `FollowView` is `vfo` only; no band/segment presets | "Band in the screen" needs the client to compute edges itself from the band table and the band plan |
| Upsampling absent | A client wider than the native width gets a narrower frame than it asked for (`width=min(req, native)`), which is fine for the deck but surprising for a 2000 px browser |

## 2. The object: `HubSubscription` becomes the panadapter

No parallel class. `HubSubscription` gains identity and ownership and is what clients, the hub and
the scope writer reason about:

```
HubSubscription
  id: str                       # server-assigned, returned in "granted"
  owner: str                    # the client's identity token (see §5)
  shareable: bool = False       # another client may attach read-only (same frames, no set_view)
  request: Subscription         # as today
  granted: GrantedSubscription  # as today, replaced per frame
  priority: int                 # derived, §4
```

Creation: `subscribe` as today. When the source's `max_panadapters` are all taken by other owners
and the new view cannot be served from an existing one (§4), the hub answers
`{"type":"error","code":"no_panadapter","message":…}` instead of silently re-aiming the radio.
Reconfiguration: only the owner may re-subscribe (replace) its panadapter; a read-only attacher
receives the same frames and its own pacing but cannot change the view.

Wire additions (backwards compatible, all optional): `granted` gains `id`, `shared: bool`,
`hzPerPx`; `subscribe` accepts `attach: "<id>"` to share; `unsubscribe` by `id` as an alternative
to the target key.

## 3. Capabilities, not a new passport

`SourceCapabilities` grows; all values come from the profile or the plugin, never from `if model`:

```
max_panadapters: int            # radio scope 1; iq_fft N (CPU bound, default 4); audio_fft N
retune_cost_ms: int             # radio: CI-V span/centre change (hundreds of ms); others 0
max_edge_span_hz: float | None  # fixed-edge mode width limit (Icom ~1 MHz); None if no fixed mode
min_span_hz: float              # smallest useful view (radio: smallest preset; iq: a few bins)
```

`span_choices_hz`, `view_modes`, `native_width` / `receiver_native_widths`, `max_fps` stay as they
are. For the IC-705 the profile already carries the presets and the fixed-edge ranges; it gains
`max_panadapters = 1` and the retune cost.

## 4. Covering a view

Order of preference inside the hub, per source:

A. **Pick and crop** (today, kept). The grant defines the client's frequency scale; the client
   stretches only inside `coverage`. Downsampling stays server-side max-hold per client pixel
   (`resample.max_hold`). **Upsampling** is added only when `width > native_width`: linear
   interpolation inside coverage, zeros outside; `hzPerPx` in the grant tells the client the real
   resolution. Averaging is never a server path; a client may smooth for display.
B. **Band and segment views** (R5 mechanics). New `ViewRequest` variants
   `BandView(band: str)` and `SegmentView(band: str, mode: "cw"|"phone"|"digital"|…)` resolved by
   the hub into an absolute `View` through the band plan (`BandPlanRegistry.get_segments`) for the
   effective region (market variant → default, user override) and the radio's band table. On Icom
   this maps to fixed edges when the view fits `max_edge_span_hz`, else to the largest preset
   centred on the segment with honest partial coverage.
C. **Survey mode** (explicit, never default): a view wider than the source can serve is covered
   by stepping the radio scope across segments into a persistence buffer with age; one owner,
   visibly marked, stops on any other panadapter request.
D. **Multi-source**: the IQ FFT source (rig-iq) serves the wide view, the radio scope overlays the
   VFO neighbourhood at higher resolution. Both already exist as plugins; the client asks for two
   panadapters and composites, or (later) the hub offers a composite source id.
E. **Spectrum memory**: per-frequency last-seen buffer with age, so a wide view remains useful
   with a single Icom. Shares the buffer with C.

## 5. Arbitration for `max_panadapters = 1`

The radio scope has one writer (`ScopeSession`). Arbitration lives in front of it, in the hub's
per-source lock, not in a new layer:

1. Collect the active panadapters of the source (positive `max_fps`).
2. If the **union** of their views fits one capability (a preset span in centre mode, or one
   fixed-edge window ≤ `max_edge_span_hz`), command that and crop per client. Every client gets
   full coverage.
3. Otherwise the panadapter whose owner is the **controller** wins: the owner that holds TX
   authority (`ManagedTxAuthority.tx_holder_nowait()`) or, with no TX holder, the most recently
   tuned controller session; the others keep their grants and receive honest partial coverage with
   `coverage` in every frame. No last-writer-wins.
4. A retune is issued only when the chosen union/winner view changes beyond the preset grid, and
   not more often than `retune_cost_ms` allows; a client re-subscribing with the same resolved
   view causes no CI-V traffic.

Owner identity: the web control session's `tx_owner`/session id token as used by managed TX; the
scope socket has to learn it (query parameter or a first message `{"type":"identify","owner":…}`
within the existing exact-key message set). Native clients (the deck) have no session today
(MOR-4311 §2) — they would send the same identify message with their device id. Until that exists,
the deck is a plain non-controller owner.

## 6. Clients

- Deck: SPAN key cycles `span_hz` (re-subscribe, already in hand); later a view menu VFO± / band /
  segment using the new variants; shows `hzPerPx`.
- Browser (Pro and Core FE): the same presets; the band-plan overlay it already draws aligns with
  the segment views for free.
- Any client: handle `no_panadapter` and `shared` honestly in the UI.

## 7. First failing test

`tests/scope/test_hub_arbitration.py`: two clients, one `RadioScopeSource` with a fake writer,
`max_panadapters = 1`. Client A subscribes 100 kHz follow-VFO, client B subscribes a 500 kHz fixed
view. Expected after R7: the writer receives one `set_center` with the union span (500 kHz) and both
grants report full coverage; then B asks for a view that does not fit (1 MHz absolute): the writer
is not retuned, B's grant shows partial coverage, A's frames are unchanged. Today the second
subscribe retunes the radio under A — the test must fail on `origin/main` first.

## 8. Order of work (after beta.16 / 3.2.0)

1. Capabilities fields + profile data (IC-705; iq_fft defaults). Small, no behaviour change.
2. `HubSubscription` identity/owner/shareable, `no_panadapter`, wire additions, identify message.
3. Arbitration (union, controller wins) in the hub in front of `ScopeSession`; the failing test.
4. Band/segment view variants on `BandPlanRegistry`; move loader from `web` to `core` data if the
   hub needs it.
5. Upsampling + `hzPerPx`.
6. Survey mode and spectrum memory (C, E) as one package; multi-source (D) when rig-iq ships IQ.

## 9. Open questions for review

- Owner identity on the scope socket: query parameter vs. first message; what the deck sends.
- Should `shareable` default to true for the radio source (one physical view anyway)?
- Where the controller rule should fall back when nobody holds TX: most recent tune, or explicit
  "take scope" action in the UI?
- Does the FE want `hzPerPx` or derive it from `coverage`/`width`?
- Persistence buffer ownership for C/E: per source or per panadapter?
