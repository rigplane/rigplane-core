# MOR-4772: plausible broadcast-station hint

Status: proposal for owner review; no product implementation in this note.
Scope: public Core frontend and EiBi helper; display only, one policy for all radios.
Evidence: source read at `5c794795365777377eca947928c834e73057cd30`.
Line references below describe that snapshot; symbols identify the relevant code.

## Decision

Show a **possible station**, not an assertion that its programme is playing, only
when all of these hold: hints enabled, schedule covers now, target matches the
explicit receiver area, mode/allocation permits the hint, and fresh RF evidence
supports a carrier at the candidate frequency near the tuned frequency.
Unknown evidence means no hint. This deliberately trades missed hints for fewer
false identifications. Neither a schedule nor a carrier proves station identity;
co-channel transmitters remain ambiguous. Replace the misleading LIVE wording
with a localized “Scheduled now” detail and label the identification as possible.
Propagation prediction, decoding programme identity, and automatic geolocation
are outside this proposal.

## What Core already has

- `frontend/src/components-v2/layout/StatusBar.svelte:271-305` (Now Playing effect)
  waits 800 ms, ignores frequency changes below 500 Hz, and takes `stations[0]`.
  Its rendering at lines 425-460 shows any selected entry; `on_air` gates only
  the LIVE chip. An expanded popup pauses identification.
- `frontend/src/lib/runtime/system-controller.ts:200` (`identifyFrequency`) calls
  `GET /api/v1/eibi/identify`; failures return null.
- Contrary to the ticket's backend assumption, `src/rigplane/web/eibi.py:765`
  (`EiBiProvider.identify`) already filters `is_on_air`, sorts by frequency
  distance, and truncates to five. `web_routing.py:601`
  (`_handle_route_eibi_identify`) defaults to ±5 kHz and may fall back to FCC.
- `src/rigplane/web/eibi.py:165` (`EiBiStation`) has frequency, UTC times, days,
  target, language, country, remarks and start/end dates, **no structured power**.
  `to_dict` at line 257 does not expose those dates. `is_on_air` at line 199
  ignores dates; `_matches_day` accepts unknown/special day codes. A schedule
  match is not a measurement of a transmitter being on.
- `eibi.py:332` (`_parse_fcc_pipe`) hardcodes `on_air=True` and puts a city/state
  string in `target`. FCC records cannot satisfy the proposed EiBi evidence
  contract; omit them from automatic hints, leaving the general lookup intact.

## Receiver area: cheapest filter first

No station latitude/longitude, Maidenhead locator, or receiver-location setting
was found by searching Core `src/rigplane` and `frontend/src` for location,
locator, grid-square, latitude and longitude fields. Core does know a **band-plan
selection**, not a verified receiver location:
`frontend/src/lib/stores/band-plan-region.svelte.ts:25` (`getBandPlanRegion`),
`src/rigplane/web/server.py:5314` (`_serve_band_plan_config`), and
`src/rigplane/web/band_plan.py:101` (`_REGION_HAM_FILE`). The choices are US and
IARU R1/R2/R3; `BandPlanRegistry.load` at line 154 can default to US when config
is missing. Do not infer reception geography from that fallback, UI language,
browser location, transmitter country, or the operator's computer.

Propose an explicit coarse “Receiver target area” choice in hint settings,
initially unset, referring to **where the radio receives**. No locator is needed
for v1. Workspace storage is browser-local (`presentation/workspace/repository.ts:32`,
`WORKSPACE_STORAGE_KEY`), not per radio. Save the choice there but require session
confirmation on connection and after a radio-context switch; a stored area alone
cannot qualify a different receiver. This avoids adding a receiver-location store.
An unset area hides hints and the settings explain how to enable them.

Add one shared, tested table mapping supported EiBi target codes (including
subregions and documented worldwide codes) to selectable receiver areas. Verify
the table against the dataset's target-code legend during implementation; this
offline inspection did not establish that legend. Match set intersection, not
substrings; unknown/empty target codes are ineligible. A match means intended
audience, not predicted coverage: real reception outside it will be missed.

Return all schedule/target-qualified entries within the narrow hint tolerance,
so a sixth candidate with a carrier is not lost. Propose an opt-in hint query
on the existing endpoint with receiver area and a tighter ±1 kHz tolerance;
preserve legacy lookup behavior when that option is absent. Return evaluated UTC
time and schedule expiry with hint candidates. Use one UTC instant per response.
The helper must honor recognized date/day/time constraints, including overnight
windows anchored to the starting day; unrecognized schedule constraints cannot
qualify as “scheduled now”. Pin the accepted formats with dataset fixtures.

## Best candidate, not first entry

After schedule/target qualification, rank by: smallest frequency error, specific
receiver-area match before broader/worldwide match at equal error, then an
explicit language preference if one exists. Do not infer a preferred broadcast
language from the UI locale. Break remaining ties by a stable tuple of station,
country, language, target and schedule, independent of CSV order; deduplicate
equivalent entries. Keep tied co-channel alternatives available in the details.
Power is excluded: Core's EiBi schema/parser has no power field, and remarks are
not a reliable numerical source. Do not add a power-data integration for v1.
The frontend selects the highest-ranked candidate with positive carrier evidence;
a carrier near a lower-ranked frequency must not validate the first candidate.

## Carrier evidence using the existing panorama

Available data paths:

- `frontend/src/lib/types/state.ts:182` (`ReceiverStatePublic.sMeter`) and
  `frontend/src/lib/runtime/props/panel-props.ts:1212` (`toMeterProps`) expose the
  active receiver's S-meter, including an unobserved state. This is passband
  energy, not a frequency-resolved carrier or a local noise-floor comparison.
  Do not substitute a universal S-unit threshold.
- `frontend/src/lib/runtime/frontend-runtime.ts:285` (`scope`),
  `scope-controller.svelte.ts:146` (`stream`), and `scope-stream.ts:285`
  (`ScopeStream.subscribe`) expose shared decoded frames. `ScopeFrame` in
  `scope-frame.ts:3` carries pixels, RF view/coverage bounds, receiver, source,
  epoch and sequence. No new radio polling or spectrum acquisition is needed.
- `frontend/src/lib/renderers/noise-floor.ts:12` (`estimateNoiseFloor`) estimates
  a floor in sample units; `followNoiseFloor` smooths it. Waterfall rendering
  already uses them (`waterfall-renderer.ts:389-409`, `pushRow`). These are not
  calibrated dBm. `SpectrumPanel.svelte:1231` uses amplitude maxima 80 for RF
  scope and 160 for audio FFT; never mix their sample scales.

Propose a pure detector fed through the existing runtime scope adapter/stream,
with qualified geometry, current receiver/session identity and monotonic receipt
time. Keep metadata derivation in `adapters/scope-adapter.ts` (its declared
ownership at lines 1-5). The status bar consumes only the resulting evidence.
Reuse already-demanded radio/IQ RF frames; exclude audio FFT, whose demodulated
audio can omit a carrier. Do not start a hidden scope subscription or audio stream
to obtain a hint. No RF stream, stale stream or mismatched receiver means unknown.

For each eligible candidate within ±1 kHz of tuning, inspect its RF bin and
adjacent bins within ±500 Hz. Require bin spacing ≤500 Hz and coverage of that
window plus a ±10 kHz neighborhood. Estimate floor from the covered neighboring
samples, excluding ±2 kHz around the candidate, using `estimateNoiseFloor`.
Normalize RF samples to the existing 0–80 display scale before comparing.
Initial proposed thresholds: peak minus floor ≥6 units continuously for 600 ms
to show; <3 units continuously for 1,500 ms to hide; intermediate values retain
state. These are tunable display-unit thresholds, not dB or measured accuracy.
Require at least three distinct frames to enter; evaluate dwell with a monotonic
clock, not frame count alone. Validate thresholds on recorded RF fixtures.

Reset evidence immediately on frequency/receiver/provider/source/epoch change,
TX, disconnect, missing pixels/coverage, or stale geometry. Reuse the existing
500 ms silence boundary (`scope-adapter.ts:40`, `SCOPE_FRAME_SILENCE_MS`) and
`ScopeStream.setFreshness` (`scope-stream.ts:337`); do not retain a hint through
loss of evidence. These conditions protect a listener from attributing another
signal or an old frame to the current frequency; the simpler S-meter threshold
cannot establish which RF frequency supplied that energy.

## Amateur allocations and settings

Use effective-region **ham segments**, independent of overlay visibility, from
`/api/v1/band-plan/segments`: `BandPlanRegistry._load_file`
(`src/rigplane/web/band_plan.py:231`) supplies start/end, layer, mode, license
and notes. They describe a plan, not actual occupancy or reception rights.
`band-plans/arrl-hf.toml:114-152` includes 7.248 MHz in its phone segment;
`iaru-r1.toml:80-107` and `iaru-r3.toml:157-184` end 40 m at 7.200 MHz.
The broadcast plan also starts 41 m at 7.200 MHz (`broadcast.toml:87-96`).
Do not equate a broadcast-band label with absence of amateur use, or use a
radio's TX limits as geography. Apply half-open segment intervals consistently.

Chosen rule: inside any ham segment, allow hints only in AM (or synchronous AM
if exposed as a supported mode), with data mode off. Hide in LSB/USB/CW/data;
do not merely dim a distracting claim. Outside ham segments, allow other receive
modes with data mode off, still requiring all evidence above. Unknown mode,
data-mode state or band-plan coverage hides the hint. Thus 7.248 MHz LSB is
hidden with the US plan; AM there can still identify a plausible broadcast.

Add “Show broadcast station hints”, default on, to Settings → Workspace beside
the receiver-area choice. Existing host: `RadioLayout.svelte:950`
(`WorkspaceSettingsPanel`); existing persistence: `presentation/workspace/`
`store.svelte.ts:73,149` (`defaultStorage`, `commit`) and `contract.ts:109`
(`WorkspaceV1`). Extend that validated contract with typed preference setters;
do not add ad hoc component storage. Disabled means no identify requests, no
detector listener, immediate hint/popup removal, and persistence across reload.
Host embeddings must be able to expose the same control through their settings.

Keep the 800 ms debounce, but clear stale presentation immediately when its
context changes and discard late responses by request/context generation.
Re-evaluate on mode/area/setting changes and at schedule expiry even without
tuning; an open popup must not freeze eligibility. No hardware writes.

## Delivery and tests (proposed, not executed)

1. This documentation PR: owner decides the policy and data gaps; no tests.
2. EiBi helper + focused tests: opt-in hint contract, target mapping, schedule
   boundaries/dates/overnight cases, complete candidates and deterministic ranking,
   no FCC false evidence. Preserve existing lookup consumers and fixtures.
3. Frontend + tests: detector, shared adapter wiring, typed settings, status bar
   eligibility and localized wording in one behavior PR. First write failing
   regressions: off-air reply hidden; wrong target hidden; flat/no-carrier RF
   hidden. Add positive evidence, sixth-candidate, ±1 kHz, coarse/out-of-range,
   audio-only, stale/wrong receiver, hysteresis, schedule-expiry, late-response,
   popup, mode/allocation and disable/reload cases. Retain the streaming-state
   debounce regression in `StatusBar.now-playing-debounce.component.test.ts`.

Run focused backend/Vitest targets on approved remote infrastructure during
implementation; required PR CI and independent review remain separate gates.

## Owner decisions needed

- Accept strict target filtering, manual receiver-area setup and its false
  negatives, including broadcasts heard outside their intended target?
- Accept no hint when RF panorama is absent/hidden/coarse, and the proposed
  thresholds subject to fixture validation, rather than a weaker schedule-only hint?
- Accept AM-only hints inside ham segments and “possible station” wording?
- Is per-browser, radio-context-confirmed area sufficient, or must a shared
  receiver-location setting be designed first? That would be additional scope.
