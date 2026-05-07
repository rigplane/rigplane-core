# Audio Route Resolver Design

**Date:** 2026-05-07
**Status:** design
**Scope:** rigplane-core route contract, with rigplane-pro integration notes

## Problem

`--bridge` is an implementation mechanism, not enough information to decide how
CAT packet modes should be mapped on the radio.

Two valid setups both use a bridge but need different radio policies:

- Direct Icom LAN radio: TX audio is streamed to the radio over the Icom LAN
  audio session. WSJT-X packet modes should use DATA2 with DATA2 MOD input LAN
  on multi-DATA radios such as the IC-7610.
- USB/serial radio with local or remote audio bridge: TX audio reaches the radio
  through the radio's USB audio device or another local input. WSJT-X packet
  modes should keep the legacy DATA1 behavior, usually with DATA1 MOD input USB.

The route decision must therefore come from the actual radio transport and audio
capabilities, not from whether a local loopback bridge is enabled.

## Design Principle

`bridge` means "local audio loopback mechanism".

`audio route` means "source of truth for radio audio and DATA-mode policy".

DATA2/LAN is only valid when TX audio really enters the radio over direct Icom
LAN audio. DATA1 remains user-owned unless a future explicit USB route policy
chooses to manage DATA1 MOD input.

## Core Route Model

Add a small route model in core:

```python
@dataclass(frozen=True)
class AudioRoute:
    radio_transport: Literal["lan", "serial", "remote"]
    tx_audio_source: Literal["lan", "usb", "acc", "unavailable"]
    rx_audio_source: Literal["lan", "usb", "acc", "unavailable"]
    data_mode_policy: Literal["data2_lan", "data1_usb", "legacy"]
    bridge_required: bool
```

Initial policy mapping:

| Condition | Route | DATA policy |
| --- | --- | --- |
| direct Icom LAN backend, native LAN audio, multi-DATA radio | `tx_audio_source="lan"` | `data2_lan` |
| direct Icom LAN backend, single-DATA radio | `tx_audio_source="lan"` | `legacy` |
| Icom serial/USB backend with local USB audio bridge | `tx_audio_source="usb"` | `data1_usb` or legacy DATA1 |
| remote/pro server | declared by server capabilities | route decided from server declaration |
| no usable audio route | `tx_audio_source="unavailable"` | no automatic DATA source changes |

## Core Responsibilities

Core should:

- expose backend identity and audio capabilities in a machine-readable form;
- resolve `AudioRoute` from the active radio/backend/profile;
- derive `RigctldConfig.wsjtx_data_mode` and `wsjtx_data_mod_input` from
  `AudioRoute.data_mode_policy`;
- keep `push_audio_tx_pcm()` aligned with the negotiated TX codec, not method
  names such as `push_audio_tx_opus`;
- avoid automatic DATA1 MOD writes in prewarm, profile apply, and state restore.

Core should not:

- treat `--bridge` as proof of LAN audio;
- infer USB vs LAN from a virtual audio device name;
- make proprietary product decisions about always-on bridge UX.

## Pro Responsibilities

Pro should start with `audio_route=auto` by default:

1. connect to the radio or remote server;
2. read backend/capability information;
3. resolve the audio route;
4. start the required LAN session, USB bridge, virtual device, or remote bridge;
5. configure embedded rigctld from the route policy;
6. show a user-facing route status such as:
   - `Audio: LAN direct`
   - `Audio: USB via local bridge`
   - `Audio: USB via remote server`
   - `Audio unavailable: <reason>`

Pro should preserve explicit override controls for support and debugging, but
the default commercial experience should not require the user to know whether to
pass `--bridge`.

## Testing Strategy

Core tests should cover:

- direct LAN route resolves to DATA2/LAN on multi-DATA profiles;
- direct LAN route falls back on single-DATA profiles;
- serial/USB route with bridge does not select DATA2/LAN;
- rigctld packet mode mapping follows route-derived config;
- profile apply and restore never rewrite DATA1 MOD automatically;
- codec contract sends PCM as raw PCM when negotiated TX codec is PCM.

Pro tests should cover:

- startup auto-routes direct LAN, serial/USB, and remote server scenarios;
- bridge lifecycle follows the resolved route, not user-visible flags;
- diagnostics and UI expose the selected route and failure reason;
- explicit overrides can force or disable bridge behavior for support cases.

## Migration Plan

1. Keep the current backend-based guard as a short-term safety patch.
2. Add `AudioRoute` and a resolver in core.
3. Replace direct `backend_id` checks in CLI/rigctld wiring with route-derived
   policy.
4. Expose route information through diagnostics/status APIs.
5. Update pro to default to auto route and consume the core resolver/status.
