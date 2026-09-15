# LCD display primitive to radio-field map

This is the canonical truth map for the first fixed-native **A / Peer Split**
LCD glass slice. The visual/spec source is
`design_handoff_lcd_sdr_radio/direction-a-peer.jsx` plus
`lcd-primitives.jsx` and `lcd-icons.jsx`; the production truth source remains
the existing `RadioViewModel` produced by `SemanticRadioSurfaces.svelte`.
`radio-display-model.ts` is a read-only projection, not another radio state
reader or command authority.

The selected reference is `lcd-handoff-render/A-clean-idle.png`, 1280×540,
SHA-256 `0dd37ab1cc9e211ea546cd1fe72de8a9061608f917f5f54dc6caafdb9ffe17cf`.
Its measured useful region is about 95.6% × 89.6%; the top rail is about 6%,
the VFO deck 68.5%, and the footer 13.5%. Within a VFO column the tag/chips,
hero frequency, offsets, meter, and AF trace consume approximately 7.6%,
21.4%, 9.7%, 9.7%, and 22.7% respectively. The two VFO columns are always
50/50 inside the fixed 1280×540 stage; only `ScaledStage` may scale it.

## Primitive field map

| Handoff JSX primitive | Fixed zone | `RadioDisplayModel` / source field | Known active | Known inactive (ghosted) | Unknown | Structural unsupported | v3 mechanism or gap |
|---|---|---|---|---|---|---|---|
| `LcdScreen` | Entire glass | structural; `PeerSplitDisplayModel.kind`, `rfState` | Amber ground and normal ink; TX may tint only from real RF authority | Same geometry, RX palette | RX/TX accent stays neutral | N/A: the selected skin owns the chassis | None; production uses self-hosted DSEG7 and Share Tech Mono |
| `FlagZone(OP)` / `StatusFlag(VOX)` | Top-left rail, stable slot | `top.vox` ← `RadioViewModel.txAux.vox` | Strong VOX flag | Ghosted VOX flag | Ghosted VOX flag | Invisible spacer | Existing semantic fact |
| `StatusFlag(PROC)` | Top-left rail, stable slot | `top.compressor` ← `txAux.compressor` | Strong PROC flag | Ghosted PROC flag | Ghosted PROC flag | Invisible spacer | Level suffix requires a separately mapped, known `compressorLevel`; not in this slice |
| `StatusFlag(SPLIT)` | Top-left rail, stable slot | `top.split` ← `RadioViewModel.split` | Strong SPLIT flag | Ghosted SPLIT flag | Ghosted flag | Stable slot; the radio contract always carries the fact shape | Existing semantic fact |
| `StatusFlag(LOCK)` | Top-left rail, stable slot | **not projected** | — | — | Ghosted only after a semantic field exists | Invisible spacer | Add a radio-wide semantic `dialLock` field from the wire fact; never read the store from the skin |
| `StatusFlag(RIT)` | Top-left rail, stable slot | `top.rit` ← `radioWideIndicators.ritActive` | Strong RIT flag | Ghosted RIT flag | Ghosted RIT flag | Invisible spacer | Existing semantic fact |
| `FlagZone(TX)` / `StatusFlag(TX)` | Top-right rail, stable slot | `top.tx` ← singleton `radioWideIndicators.rfState`, falling back only to the same `meters.rfState` authority | TX accent only for observed transmitting | Ghosted in observed receive | Ghosted | Stable slot | Existing App-owned RF authority; never infer from assignment or raw `ptt` |
| `StatusFlag(TUNE)` and `StatusFlag(ATU)` | Top-right rail, two stable visual slots | `top.tune`, `top.atu` ← one real `AtuStatus` field | TUNE is strong only for `tuning`; ATU is strong for `on` or `tuning` | Both ghost independently from that one tri-state | Ghosted | Invisible spacers | Do not invent independent tuner booleans; a future richer tuner model may split them truthfully |
| `Column` / VFO tag | Each 50% receiver column | `receivers[].receiver`, `.label`, `.activity` ← topology, VFO scheme, active receiver | Strong column and outer accent | Entire column remains present at 30% ink | Same full geometry, ghosted | Second column still exists for this two-receiver layout, with unknown cells | B/D topologies remain separate future component seams |
| `LcdPill(mode)` | Column header | `receivers[].mode` ← selected receiver VFO | Real mode | Same real mode, ghosted with column | `?` | Invisible structural spacer only if topology has no cell | Existing semantic fact |
| `LcdPill(filter)` | Column header | `receivers[].filter` ← selected receiver VFO | Real filter id/label | Same real filter, ghosted | `?` | Invisible spacer | Profile-resolved display labels can be added semantically |
| `LcdPill(band)` | Column header | active column `.band` ← `RadioViewModel.band.currentBand` | Real current band | Not duplicated into the peer column | `?` only when the active band fact exists but is unreadable | Invisible spacer | Receiver-scoped band facts are needed before both columns may show band labels; named band edges must come from a semantic mechanism |
| `FreqDigits` | Hero frequency row | `receivers[].frequency` ← selected receiver VFO `frequencyHz` | Exact observed Hz in stable glyph cells; trailing three digits are exactly 62% | Same cells, ghosted by column | Stable `—.———.———` cells | Structural spacer only for a topology without that VFO | Existing semantic fact; no pending command target is shown as radio truth |
| `OffsetLine(RIT)` | Three stable offset cells per column | `offsets.rit` ← radio-wide RIT active + offset facts | Signed observed kHz | Zero or observed offset remains ghosted, never removed | `?` | Invisible spacer | Existing one-register/two-gate semantic facts |
| `OffsetLine(XIT)` | Three stable offset cells per column | `offsets.xit` ← radio-wide XIT active + offset facts | Signed observed kHz | Ghosted | `?` | Invisible spacer | Existing semantic fact |
| `OffsetLine(SPLIT)` | Three stable offset cells per column | `offsets.split` ← known TX target frequency minus known active frequency | Computed signed delta | Stable ghosted `—`; split-off does not prove a zero delta | `?` if either required fact is unknown | Stable slot | No synthetic delta; computation is allowed only when split is on and both frequencies are known |
| `SMeter` | Meter row in each column | `receivers[].sMeter` ← receiver-scoped `receiverIndicators[].sMeter` | Real reading mapped through the existing calibration helpers | Real peer reading, ghosted with its column | Ghosted scale with no numeric-zero claim | Ghosted scale with no numeric-zero claim | Receiver-scoped meters already exist; calibration remains profile-owned |
| `SpectrumScope` FFT trace | AF scope background | Optional `PeerSplitDisplay.normalizedFftBins[receiver]`; not part of `RadioDisplayModel`; availability comes only from `RadioViewModel.scope.audioFftScope` | Real normalized bins in normal ink only for a live active receiver; heading is `AF SCOPE · BANDPASS` | `LcdAfFft` internally uses its private zero buffer and draws a flat ghosted trace | Same private flat ghosted trace for missing/stale/unknown input | Only the AF-FFT SVG/grid is absent; heading truthfully contracts to `BANDPASS`, and independent filter/PBT content keeps its slot | Current AF FFT frames are primary-only, not receiver-scoped. Hardware RF scope is a separate future renderer, never an AF-FFT availability signal. Parent/model must never create zero arrays or fake frames |
| `SpectrumScope` filter/PBT envelope | AF scope overlay | `receivers[].bandwidthHz` plus active-column `.ifShiftHz`, `.pbtInnerHz`, `.pbtOuterHz` ← existing `RadioViewModel.filterPassband` facts | Real width and center; distinct known PBT offsets render the handoff dashed/dotted twin envelopes | Real peer width remains ghosted; radio-wide passband shaping is not duplicated into the inactive peer | No envelope when a structurally present shaping fact is unknown; AF-FFT keeps its own neutral state | Plain centered envelope when shaping is structurally absent; remains independently visible when AF-FFT is unsupported | Existing passband helpers own Hz conversion; future receiver-scoped passband facts are needed before both columns may show independent PBT |
| DSP `NB`, `NR` flags | Lower fact rail | active receiver `.dsp.nb`, `.dsp.nr` | Strong flag | Ghosted flag | Ghosted flag | Invisible spacer | Existing receiver-scoped facts |
| DSP `NOTCH` / `ANF` | Lower fact rail, two stable slots with distinct handoff glyphs | active receiver `.dsp.notch` | Mutually exclusive: `manual` makes NOTCH strong; `auto` makes ANF strong | `off` ghosts both slots | Unknown ghosts both slots | Invisible spacers only when the one enum is structurally unsupported | Existing single notch-mode enum, rendered as two mutually exclusive labels; never infer two independent booleans or light both |
| DSP `AGC` | Lower fact rail | active receiver `.dsp.agc` | Real profile label or ordinal | Same slot, ghosted if inactive in a future richer model | `?` ghosted | Invisible spacer | Existing receiver-scoped fact |
| FRONT `PRE` | Lower fact rail | active receiver `.front.preamp` | Generic `PRE Pn`/ordinal presentation | Generic PRE zero state, ghosted | `?` ghosted | Invisible spacer | Project profile-resolved `preLabels` semantically before showing vendor labels such as IPO/AMP; never infer IPO from ordinal zero |
| FRONT `ATT`, `DIGI`, `IP+` | Lower fact rail | active receiver `.front.attenuator`, `.digiSel`, `.ipPlus` | Real value/flag | Ghosted | Ghosted `?` | Invisible spacer | Existing receiver-scoped facts |
| `MemoryChip` | Lower-left footer | **not projected** | — | — | — | Four stable invisible spacers in the first slice | Requires a real v3 memory model with channel identity, label, and frequency |
| `TelemetryStrip(VD/ID/PWR/SWR/ALC/COMP)` | Lower-right footer | `telemetry.*` ← existing meter facts plus their `relevant` bit | Known raw/semantic value only | Known but currently irrelevant value is ghosted | `?` | Invisible spacer | Units/calibration remain source-owned; temperature needs a real telemetry field |
| `TimeStamp` | Handoff top-center | **intentionally absent** | — | — | — | No radio-information slot | Wall time is not current radio information and there is no radio clock fact in this contract |
| `lcd-icons.jsx` glyphs | Inside status chips | Presentation mapping only; no fields | May decorate an already truthful active label | Same glyph ghosted | Same glyph ghosted | Invisible spacer | Icons never create state; text remains the semantic fallback |

## Forbidden demonstration values

The handoff is a geometry and visual-language source, not a data fixture.
Production must not present any of these as observed radio truth:

- Synthetic FFT peaks. They are authored arrays in `direction-a-peer.jsx`,
  not scope frames. The first slice draws only a private flat safe-empty trace
  until optional real bins arrive.
- `QSY-1`, `QSY-2`, `M-01`, `M-02` and their frequencies. No memory semantic
  model currently backs them.
- `TEMP 31°C` / `42°C`. The current display model has no temperature field.
- Named band-edge labels or markers. A frequency range or handoff label is not
  an observed receiver-scoped edge.
- UTC/local clock strings. They are wall-clock data, not radio state.
- `IPO` inferred from `preamp === 0`. Preamp is an ordinal; profile
  `preLabels` must be resolved and projected before vendor-specific wording is
  truthful.
- Simultaneously active `NOTCH` and `ANF` flags from one notch mode. The one
  enum maps mutually exclusively: `manual` to NOTCH, `auto` to ANF, and `off`
  to neither.
- Independent `ATU` and `TUNE` booleans. The current source is one real
  `off | on | tuning` status.
- SPLIT `−54.500` or any other delta unless both the active frequency and TX
  target frequency are known and the delta is computed from them.
- Handoff S-meter, voltage, current, power, SWR, ALC, compression, filter, PBT,
  and IF-shift numbers unless the corresponding semantic field is known.

## Boundary

`SemanticRadioSurfaces.svelte` still constructs the sole canonical
`RadioViewModel` and retains all command-bus callbacks in its existing control
branch. Its `readonlyDisplay` snippet passes facts one way into the glass.
`PeerSplitDisplay.svelte`, `LcdAfFft.svelte`, and the projection contain no
buttons, links, form fields, focus targets, callbacks, command availability,
transport handles, runtime readers, or resource-demand logic. The existing
control deck remains a sibling outside this glass.
