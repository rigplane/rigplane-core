# Audio Profile Policy Audit

Issue: #1475
Date: 2026-05-08

## Scope

This audit reviews radio profile audio policy candidates for:

- `rigs/ic7610.toml`
- `rigs/ic705.toml`
- `rigs/ic7300.toml`
- `rigs/ic9700.toml`
- `rigs/ftx1.toml`
- `rigs/examples/*.toml`

No runtime code or rig profile values were changed. Unknown per-radio audio
limits are intentionally left absent rather than inferred from generic defaults.

## Evidence Sources

| Source | Evidence |
| --- | --- |
| `src/rigplane/core/types.py:54` | `AudioCodec` conninfo values; local docstring says Opus is only available when the radio reports `connection_type == "WFVIEW"`. |
| `src/rigplane/core/types.py:179` | Current generic supported sample-rate set is `8000`, `16000`, `24000`, `48000`; this is not per-radio evidence. |
| `src/rigplane/core/types.py:191` | Current global codec preference is stereo PCM16, mono PCM16, stereo uLaw, mono uLaw, 8-bit PCM, then Opus. |
| `src/rigplane/runtime/_control_phase.py:198` | Existing stereo-to-mono retry on immediate conninfo rejection. |
| `src/rigplane/runtime/_control_phase.py:540` | Runtime forces Icom LAN `txcodec` to `PCM_1CH_16BIT`; comment cites stock firmware rejection of stereo TX and wfview's mono-only TX UI. |
| `src/rigplane/profiles/rig_loader.py:741` | Profile loader currently supports only `[audio].codec_preference`. |
| `references/wfview/include/audioconverter.h:118` | wfview maps codec bytes to audio formats and channel counts. |
| `references/wfview/src/settingswidget.cpp:106` | wfview RX UI exposes LPCM/uLaw/Opus/ADPCM choices; TX UI exposes only mono codecs. |
| `references/wfview/src/settingswidget.ui:732` | wfview sample-rate UI exposes `48000`, `24000`, `16000`, `8000`; this is generic UI evidence, not a per-radio support matrix. |
| `references/wfview/src/radio/icomudphandler.cpp:384` | wfview forces Opus back to LPCM16 when connection type is not `WFVIEW`. |
| `references/wfview/include/packettypes.h:322` | wfview conninfo send packet contains `rxcodec`, `txcodec`, `rxsample`, and `txsample`. |
| `references/icom-official/IC-7610_CI-V_Reference.pdf` via `pdftotext` | Documents LAN AF/IF output, LAN MOD level, and Network Audio Port, but did not expose a codec/sample-rate support matrix in this audit. |
| `references/IC-7300_CI-V_Reference.pdf` via `pdftotext` | Documents LAN AF/IF output and Audio Port entries, but this repo's `rigs/ic7300.toml` is `has_lan = false`; no direct LAN audio profile should be inferred for IC-7300. |
| `references/Yaesu-official/FTX-1_CAT_OM_ENG_2508-C.pdf` via `pdftotext` | Documents CAT control and fixed passband values; no direct LAN audio codec/sample-rate policy evidence was found. |

## Cross-Cutting Conclusions

Direct Icom LAN should be PCM-first. Opus is not an evidence-backed
radio-native default for direct Icom LAN because wfview explicitly downgrades
Opus when the connection type is not `WFVIEW`.

The only profile audio field currently implemented is `codec_preference`.
Adding `tx_codec`, `default_sample_rate_hz`, `supported_sample_rates_hz`,
`sample_rate_by_codec`, `browser_rx_transport`, or
`browser_rx_transcode_to_opus` is follow-up implementation work, not part of
this audit.

`supported_sample_rates_hz = [8000, 16000, 24000, 48000]` is a generic
rigplane/wfview capability set, not proof that every supported radio accepts
every rate over its direct radio audio path.

Browser Opus should be modeled as a server-to-client/web transport policy.
It must not cause direct Icom LAN conninfo to request Opus from the radio.

## Per-Radio Recommendations

| Profile | Evidence-backed current state | `codec_preference` | `tx_codec` | `default_sample_rate_hz` | `supported_sample_rates_hz` | `sample_rate_by_codec` | `browser_rx_transport` / `browser_rx_transcode_to_opus` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| IC-7610 | `rigs/ic7610.toml` has LAN, 2 receivers, hardware-testing provenance, and IC-7610-only LAN dual-RX audio routing. wfview has LAN+Ethernet and 2 receivers. Runtime already supports stereo RX with mono TX. | Recommend `["PCM_2CH_16BIT", "PCM_1CH_16BIT", "ULAW_2CH", "ULAW_1CH"]` if a profile-level field is added for IC-7610. Evidence: profile dual-RX LAN routing plus current global PCM-first order. Do not include Opus. | Recommend `PCM_1CH_16BIT`. Evidence: runtime forced mono TX and wfview mono-only TX UI. | Unknown for profile data. A 16000 Hz candidate belongs in #1467/#1470 hardware validation, not in the profile without captured measurement. | Unknown per-radio. Do not copy the generic `[8000, 16000, 24000, 48000]` set into the profile without official or hardware evidence. | Unknown; leave absent. | Recommend web policy outside radio-native profile: `auto` transport, Opus transcode allowed only after PCM capture/DSP. Do not request Opus from radio. |
| IC-705 | `rigs/ic705.toml` has LAN+WiFi, 1 receiver, and current mono-first `codec_preference`. wfview has LAN+WiFi and 1 receiver. No official IC-705 audio codec/rate matrix was present locally. | Keep current `["PCM_1CH_16BIT", "ULAW_1CH"]`. Evidence: existing profile pin plus single-RX caution. Do not add stereo or Opus by default. | Recommend `PCM_1CH_16BIT` for direct Icom LAN when `tx_codec` exists. Evidence: same Icom LAN TX mono constraint. | Unknown; leave absent. | Unknown per-radio; leave absent. | Unknown; leave absent. | Same web-only policy as above; do not encode as radio-native Opus. |
| IC-7300 | `rigs/ic7300.toml` has `has_lan = false` and current mono-first `codec_preference`. wfview IC-7300 also has `HasLAN=false`. | Keep current `["PCM_1CH_16BIT", "ULAW_1CH"]` for compatibility with existing audio abstractions. Do not add direct LAN-specific defaults to this non-LAN profile. | Unknown/not applicable for direct radio LAN in this profile. If reused through Icom LAN in future, use the generic mono TX rule only after profile identity is resolved. | Unknown/not applicable; leave absent. | Unknown/not applicable; leave absent. | Unknown/not applicable; leave absent. | Browser transport is not a radio profile fact here. |
| IC-9700 | `rigs/ic9700.toml` has LAN, 2 receivers, and current mono-first `codec_preference` because stereo negotiation has not been hardware validated. wfview has LAN+Ethernet and 2 receivers. | Keep current `["PCM_1CH_16BIT", "ULAW_1CH"]` until hardware or official docs prove stereo RX is accepted. Do not infer stereo from `receiver_count = 2`. | Recommend `PCM_1CH_16BIT` for direct Icom LAN when `tx_codec` exists. Evidence: same Icom LAN TX mono constraint. | Unknown; leave absent. | Unknown per-radio; leave absent. | Unknown; leave absent. | Same web-only policy as above; do not encode as radio-native Opus. |
| FTX-1 | `rigs/ftx1.toml` is Yaesu CAT, `has_lan = false`; wfview FTX-1 also has `HasLAN=false`. Official CAT manual does not document a direct LAN audio codec policy. | Unknown/not applicable; leave absent. | Unknown/not applicable; leave absent. | Unknown. The official CAT manual's `16000 Hz (Fixed)` hit is passband/mode context, not evidence for a rigplane audio stream sample rate. | Unknown; leave absent. | Unknown; leave absent. | Browser transport should be decided by backend/web policy, not a direct Icom LAN radio profile field. |
| `rigs/examples/ftx1.toml` | Example profile says Yaesu and no LAN/ethernet/wifi; protocol comment says CI-V-compatible via wfview, but this is an example, not a direct radio LAN profile. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent unless the example is rewritten as a web/server demonstration. |
| `rigs/examples/lab599_tx500.toml` | Example profile is Kenwood CAT, no LAN/ethernet/wifi. | Unknown/not applicable; leave absent. | Unknown/not applicable; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. |
| `rigs/examples/sdrplay_rspdx.toml` | Example profile is native SDRplay USB API with software demodulated audio and a sample-rate control for SDR/IQ, not Icom LAN audio. | Unknown/not applicable to Icom LAN; leave absent. | Unknown/not applicable; leave absent. | Unknown for radio audio policy; leave absent. | Existing sample-rate control is SDR control data, not this policy field. | Unknown; leave absent. | Browser transport belongs to web/SDR output policy if implemented. |
| `rigs/examples/xiegu_x6200.toml` | Example profile is CI-V-compatible Xiegu with WiFi but no LAN/ethernet. No direct Icom LAN audio evidence found. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. | Unknown; leave absent. |

## Values That Should Not Be Guessed

- Do not set per-radio `default_sample_rate_hz` from the generic rigplane or
  wfview sample-rate list.
- Do not set per-radio `supported_sample_rates_hz` without official docs,
  wfview model-specific evidence, or hardware capture.
- Do not set `sample_rate_by_codec` without measured or documented
  codec-specific behavior.
- Do not infer IC-9700 stereo RX support from `receiver_count = 2`.
- Do not infer IC-7300 direct LAN audio policy while the current profile and
  wfview both mark it as no LAN.
- Do not treat FTX-1 CAT passband widths as an audio stream sample-rate policy.
- Do not put Opus in direct Icom LAN `codec_preference` unless the connection is
  explicitly `WFVIEW` or a future server/client path is being described outside
  the radio-native profile.
- Do not encode browser playback workarounds as radio-native codec requests.

## Suggested Follow-Up Issues

1. Add profile audio policy fields and loader validation.
   Acceptance should cover `tx_codec`, `default_sample_rate_hz`,
   `supported_sample_rates_hz`, `sample_rate_by_codec`,
   `browser_rx_transport`, and `browser_rx_transcode_to_opus`, with unknown
   values omitted.

2. Capture IC-7610 LAN audio sample-rate evidence.
   Use hardware or packet/audio captures to decide whether `16000` Hz should be
   an IC-7610 profile default, a fallback candidate, or only an operator
   workaround.

3. Validate IC-9700 direct LAN stereo RX.
   Current profile is correctly mono-first until hardware or official evidence
   proves stereo RX conninfo is accepted.

4. Split browser RX transport policy from radio-native policy.
   Browser Opus can be allowed as server-to-client transcode after PCM-native
   radio capture, DSP, taps, and bridge consumers are handled.

5. Clarify IC-7300 vs IC-7300MK2 profile identity.
   Local official CI-V references include LAN AF/IF and Audio Port entries,
   while `rigs/ic7300.toml` and wfview IC-7300 both say no LAN. Any MK2 LAN
   behavior should be represented as a separate profile or documented variant,
   not silently folded into IC-7300.
