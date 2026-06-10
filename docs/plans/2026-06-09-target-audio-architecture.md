# Target Audio Architecture — Universal Audio Path ADR

**Date:** 2026-06-09
**Status:** Implemented (2026-06-10) — see **As-built status** below for the
step → issue → PR mapping and the deferred items. Design history: rev 2.
Operator approved the overall direction
(AudioSession + desired-state machine + PCM spine + edge-only lossy) and added
three requirements folded in below: adaptive/opt-in lossy egress with a strict
lossless digital-path invariant (T1a, §3.6), a tract-wide debug/analytics tap
surface (T5, §3.7), and WebRTC as the first-class primary remote carrier
(T6, §1.9).
**Builds on:** MOR-532 epic (`AudioTransport` neutral surface), MOR-531/534 (USB duplex),
MOR-556/559 (stream-order regressions), MOR-241/506 (single-slot callback bugs),
MOR-307/#104 (WebRTC DataChannel transport)
**Format model:** `docs/plans/2026-04-12-target-frontend-architecture.md`
**Base commit:** `467f40bd` (main)

## As-built status (2026-06-10, main @ `314c0df9`)

The MOR-562 epic is **implemented**: 17 of the 20 §4 migration steps plus one
added step (9b) are fully merged to `main`, and step 15 is half-merged (PR-1
of 2, MOR-591); the remaining exceptions are steps 11 and 12, step 15 PR-2,
plus the live hardware re-validations, all listed below. Where the realized
code deviates from a step's "files (indicative)" column, the merged PR is
authoritative (notable deviations noted inline).

### Merged (§4 step → issue → PR)

| §4 step | Issue | PR | As-built notes |
|---|---|---|---|
| 1 — bridge teardown on failed start | MOR-560 | #1755 | |
| 2 — typed lifecycle errors | MOR-563 | #1757 | `AudioAlreadyStartedError`/`AudioNotStartedError` live in `audio/usb_driver.py` (subclassing `AudioDriverLifecycleError`), not `core/exceptions.py` |
| 3 — AudioBus RX heartbeat | MOR-564 | #1756 | `AudioBus.last_rx_frame_monotonic` in the runtime payload |
| 4 — tap-surface skeleton | MOR-565 | #1758 | `STAGE_RX_PCM`/`STAGE_RX_POST_DSP` in `audio/bus.py`; `rx.pcm` hosted on the bus, `rx.post_dsp` on the broadcaster |
| 5 — stateful test fakes | MOR-566 | #1759 | `FakeAudioBackend(strict_device_exclusive=True)` + shared order-sensitive radio stubs |
| 6 — lifecycle conformance suite | MOR-567 | #1760 | `tests/contracts/test_audio_lifecycle_conformance.py` |
| 7 — `audio_setup_order` descriptor | MOR-575 | #1761 | Additive on the backends, derived from `audio_duplex_mode`; conformance-pinned to stay OUT of the frozen 10-member `AudioTransport` — consumers read it via `getattr(radio, "audio_setup_order", "rx_first")` |
| 8 — `AudioSession` skeleton | MOR-576 | #1763 | `audio/session.py`, single-lock `_apply()` reconciliation |
| 9 — bridge on session | MOR-577 | #1765 | `rx_first` branch + `_subscribe_bus` band-aid deleted |
| 9b — radio-owned session singleton *(added step)* | MOR-579 | #1766 | Lazy `radio.audio_session` (`runtime/radio.py`) — resolves open question 1 |
| 10 — bus surfaces RX-start failures | MOR-582 | #1769 | Subscriber rollback in the bus + broadcaster error envelope to WS clients |
| 13 — web TX through session lease | MOR-580 | #1767 | `session.acquire_tx("web")` |
| 14 — health watchdog + events | MOR-581 | #1768 | ~1 s watchdog on the bus heartbeat; ~3 s silence ⇒ RECOVERING + local `AudioSessionEvent` |
| 15 (PR-1 of 2) — `PcmFrame` carrier + LAN decode-at-ingress | MOR-591 | #1778 | Additive dual-publish: legacy `AudioPacket` path byte-identical; decoded `PcmFrame` fans out to registered PCM taps; decode skipped with no taps. PR-2 (decoder removal) is MOR-592, deferred |
| 16 — `AudioDeviceConfig` carrier | MOR-578 | #1764 | Frozen dataclass in `audio/backend.py` |
| 17 — per-connection egress codecs | MOR-584 | #1771 | Per-client encoder pool + `audio_format` ack |
| 18 — client link-quality uplink | MOR-585 | #1773 | `audio_stats` every ~1.5 s per client |
| 19 — adaptive egress controller | MOR-588 | #1775 | Flag-gated `WebConfig.audio_adaptive_egress` (default off); T1a pinned by `tests/contracts/test_adaptive_egress_conformance.py` |
| 20 — recovery unification | MOR-586 | #1772 | `AudioSession.reestablish()` from live demand; legacy snapshot replay retired |

Found and fixed along the way (not §4 steps): MOR-574 (#1762) — bridge
`stop()` with TX armed leaked the running LAN RX stream, surfaced by the
step-6 conformance suite; MOR-583 (#1770) — end-to-end audio-path smoke tests
over fakes.

### Deferred / not yet merged

- **Step 11 — `UsbAudioDriver.ensure()` exclusive-duplex wiring (MOR-546).**
  Hardware-gated (FTX-1 / X6200). `start_duplex` still has no production
  caller; the session's `tx_first`/`atomic` sequencing is the prepared seam.
- **Step 12 — poller PTT through the session + the `_should_restart_rx` flip
  (MOR-554).** Hardware-gated (IC-7610). As-built the poller PTT path still
  arms `radio.start_tx` directly on the neutral surface (MOR-543, #1747) and
  re-arms RX for every duplex mode — the one remaining audio-arming path not
  routed through `AudioSession`. The session's `_REARM_RX_AFTER_TX_DROP` seam
  is pre-built for the flip.
- **Step 15 PR-2 — per-consumer decoder removal + the #762 fix (MOR-592,
  deferred).** PR-1 (MOR-591, #1778, `314c0df9`) merged the additive
  `PcmFrame` carrier and LAN decode-at-ingress dual-publish; the
  per-consumer decoders are still in place (bridge opuslib decoder,
  broadcaster uLaw branch, Opus DSP/tap gates — #762) until consumers read
  `PcmFrame`. Dormant in practice: every shipping default delivers PCM
  natively. PR-2 is the [BC] half (#762 pass-through replaced by re-encode;
  FFT scope lights up on Opus-native radios).
- **All live hardware re-validations** (radio offline 2026-06-10): IC-7610
  LAN full-duplex + reconnect `reestablish()`, FTX-1 exclusive duplex +
  WSJT-X FT8 through the bridge, browser TX over the session lease, adaptive
  egress over a real WAN.

### CI/infra fixes found along the way

- **MOR-587** (#1774): quick.yml swallowed the pytest exit code (`tee`
  without `pipefail`) — CI failures were masked.
- **MOR-589** (#1776): `discover --serial` raised `SystemExit(2)` on
  Python 3.11/3.13.0 (argparse prefix abbreviation) — `allow_abbrev=False`.
- **MOR-590** (open): quick.yml's "Set up Python 3.11" does not pin the
  pytest interpreter (runs 3.13.0), and real 3.11 hides 136 latent
  AsyncMock / runtime-`Protocol` test failures that must be fixed before
  pinning.

## Purpose

Single authoritative reference for the universal audio path: from any radio that
exposes a sound source (USB codec or LAN audio protocol), through the server-side
PCM spine, out to every consumer (browser web UI over WS or WebRTC, audio FFT
scope, virtual-device bridges, future native clients). All audio implementation
issues should be judged against this document.

This ADR is evidence-driven: every problem claim cites `file:line` on the base
commit. The target design is constrained to be reachable through atomic,
independently shippable steps (≤3 files / ≤200 LOC each, per repo guardrails).

---

## Hard constraints (non-negotiable)

These bound every design decision below:

1. **Open-core policy** (`docs/architecture/open-core-policy.md`): no telemetry
   (this covers the §3.7 tap/analytics surface — taps are local-process only,
   never phoned home), headless operation is sacred, the Pro boundary sits at
   the Radio protocol and `local-extensions/`. The Pro-stable contract is **additive-only** — the
   `rigplane.audio` export surface is pinned as a superset
   (`tests/contracts/test_audio_transport_conformance.py:159`).
2. **`AudioCapable` is FROZEN** — 14 members, conformance-pinned
   (`src/rigplane/core/radio_protocol.py:787-873`,
   `tests/contracts/test_audio_transport_conformance.py:178-197`). It is never
   modified; all evolution happens in `AudioTransport`
   (`core/radio_protocol.py:876-966`, pinned at 10 members,
   `tests/contracts/test_audio_transport_conformance.py:60-73`).
3. **import-linter layer matrix** (`.importlinter`): `audio/` is a sibling of
   `profiles/`, above `commands/scope/dsp`, below `runtime/`. New components must
   fit this matrix — anything that needs a radio reference takes it as a duck-typed
   `AudioTransport`-shaped object, exactly as `AudioBus` does today
   (`src/rigplane/audio/bus.py:261`).
4. **Keep-alive timings must never weaken**: control ping 500 ms, idle/audio
   100 ms (`src/rigplane/core/transport.py:38-39` — `PING_PERIOD = 0.5`,
   `IDLE_PERIOD = 0.1`).
5. **Incremental migration only.** Legacy `*_opus`/`*_pcm` paths keep working via
   shims for the whole transition (they are permanent for `AudioCapable`).

---

## 1. Current-state map

### 1.1 Transport family: Icom LAN (UDP audio port)

Used by: `IcomRadio` (IC-7610 LAN, IC-705 LAN, IC-9700 LAN).

```
IC-7610 ◄────────────────── UDP audio port ──────────────────► rigplane
   │  conninfo negotiates rx codec (PCM_2CH_16BIT default; ULAW_*;
   │  OPUS_1CH/2CH possible — core/types.py:54-71) and tx codec
   │  (forced PCM_1CH_16BIT for direct Icom LAN)
   ▼
IcomTransport (audio port)                       core/transport.py
   ping loop 0.5s · idle loop 0.1s · retransmit loop 0.1s
   │  _ensure_audio_transport()   runtime/_audio_runtime_mixin.py:560-602
   │  + EPIPE-storm watchdog      runtime/radio.py:873-890
   ▼
AudioStream._rx_loop              audio/lan_stream.py:556-587
   parse_audio_packet → JitterBuffer(depth 5 ≈ 100ms)
   → _rx_callback  (SINGLE SLOT, lan_stream.py:358)
   → _rx_taps      (list, lan_stream.py:359)
   ▼
AudioBus._on_opus_packet          audio/bus.py:292-295
   fan-out → AudioSubscription bounded queues (64, drop-oldest)
   ├─► web AudioBroadcaster._relay_loop        (see 1.3)
   ├─► AudioBridge._rx_loop                    (see 1.5)
   └─► any Pro / library subscriber

TX (full-duplex, RX keeps flowing):
browser/bridge PCM → radio.push_tx → AudioStream.push_tx
   → chunked at 1364 B (lan_stream.py:608-637) → UDP send_tracked
```

Wire payload on the bus: `AudioPacket(ident, send_seq, data)` where `data` is
**whatever the radio negotiated** — PCM16, uLaw, or Opus. The codec does not
travel with the packet; every consumer re-reads `radio.audio_codec`.

### 1.2 Transport family: USB PortAudio (serial Icoms, X6200, Yaesu FTX-1)

Used by: `_IcomSerialRadioBase` subclasses (IC-705/7300/9700/7610 serial; the
Xiegu X6200 rides the IC-705 serial class, `backends/factory.py:87-95`) and
`YaesuCatRadio` (FTX-1 et al.).

```
Radio USB CODEC ◄──► OS audio (CoreAudio / WASAPI / ALSA) ◄──► PortAudio
   │
   ▼
_PortAudioRxStream (audio-thread callback)       audio/backend.py:427+
   → _RxFramer: stereo→mono downmix per rx_audio_channel
     ("mix"/"left"/"right", MOR-504/508; backend.py:293-343, 346-400)
     + re-chunk to fixed 20 ms frames
   ▼
UsbAudioDriver.start_rx callback                 audio/usb_driver.py:792-863
   device selection: override → serial-port topology → name heuristics
   contract resolution: sample-rate fallback chain + channel clamps
   ▼
backend start_rx wrapper — fabricates a SYNTHETIC AudioPacket:
   ident=SYNTHETIC_RX_IDENT(0x9781), local uint16 seq
   - Icom serial:  _icom_serial_base.py:380-434 (+ optional pcm→opus
     transcode when the configured codec is an Opus variant, :410-419)
   - Yaesu CAT:    backends/yaesu_cat/radio.py:375-403
   ▼
AudioBus → (identical consumer fan-out as LAN)

TX: push_tx → driver._push_tx_pcm → _PortAudioTxStream.write
   (~1 s bounded queue, drop-oldest) → CODEC playback
Duplex today: TWO separate PortAudio streams on the same C-Media device.
   The single-sd.Stream duplex path exists (driver.start_duplex,
   usb_driver.py:911-1002) but has ZERO production callers — see P3.
```

### 1.3 Consumer: web WS `/api/v1/audio` (browser RX/TX)

```
AudioBus ──► AudioBroadcaster (singleton per server)   web/handlers/audio.py:83
   subscribe(name="web-audio") on first WS client      :456-470
   _relay_loop per packet:                             :472-576
     1. ulaw → PCM16 decode if radio codec is uLaw     :493-499
     2. DSP pipeline (PCM16 only; SKIPPED for Opus-native radios, #762) :506-516
     3. TapRegistry fan-out (FFT scope, analyzer; SKIPPED for Opus)     :517-524
     4. frame_ms derived from payload size (#765)      :527-535
     5. browser egress encode: optional PCM→Opus transcode, shared
        for ALL clients (aggregate decision)           :536-548, :352-419
     6. fan-out to per-client bounded queues (HIGH_WATERMARK=10)        :550-566
   ▼
AudioHandler (per WS client)                           :592+
   reader loop: audio_start/audio_stop/audio_config + binary TX frames
   sender loop: queue → ws.send_binary (5 s dead-conn timeout)
   TX: browser Opus → opus_to_pcm transcode (when radio TX codec is
   PCM) → radio.push_tx                                :1019-1103

Frame format: 8-byte header [type, codec, seq16, sr/100, ch, frame_ms]
   (web/protocol.py:38-39, 82-124). PCM16 mono 48 kHz ≈ 768 kbps;
   IC-7610's default PCM_2CH_16BIT ≈ 1.54 Mbps per client.

Browser side (frontend/src/lib/audio/):
   audio-manager.ts — owns the WS, reconnect/backoff, RX/TX intent;
     preferred_rx_codec: 'opus' when WebCodecs AudioDecoder exists,
     'pcm16' under Tauri/WebViews (audio-manager.ts:29-38)
   rx-player.ts — WebCodecs AudioDecoder for Opus, direct PCM16 path,
     adaptive jitter buffer; tx-mic.ts — mic capture + encode
```

Carrier note: the WS framing above is one of **two** carriers — the same
`AudioHandler` also runs unchanged over the WebRTC `audio` DataChannel (§1.9).

### 1.4 Consumer: audio FFT scope WS `/api/v1/audio-scope`

```
AudioBroadcaster._tap_registry ──"fft-scope" PCM tap──► AudioFftScope
   (server wiring: web/server.py:745-772)
   AudioFftScope.on_frame() is a SINGLE-SLOT setter (audio/fft_scope.py:223);
   the server registers exactly one dispatcher that fans out to both
   /api/v1/audio-scope and (for non-hw-scope radios) /api/v1/scope —
   registering twice would clobber (MOR-241 comment, server.py:753-757)
   Tap lifecycle: lazy — first audio-scope client sets the PCM tap and
   calls broadcaster.ensure_relay(); last client removes the tap
   (server.py:1033-1065). The relay (and therefore radio RX) keeps
   running as long as either WS clients OR taps exist
   (handlers/audio.py:170-188, 267-291).
Also tapped: AudioAnalyzer (SNR estimator), registered permanently at
   server construction (server.py:766-772).
Note: taps receive NOTHING when the radio-native codec is Opus
   (handlers/audio.py:517-524) — FFT scope is silently dark on
   Opus-native radios (#762).
```

### 1.5 Consumer: AudioBridge → virtual devices (WSJT-X et al.)

```
AudioBus ──subscribe("audio-bridge")──► AudioBridge    audio/bridge.py:228
   RX: _rx_loop — Opus decode (own opuslib decoder if radio codec is
       Opus, :496-500, 917-919) → stereo→mono downmix (:140-153) →
       PortAudio output stream → BlackHole / PipeWire null-sink /
       VB-Cable → WSJT-X/fldigi
   TX: loopback capture stream → _tx_queue(64) → _tx_loop →
       radio.push_tx (neutral) or push_audio_tx_pcm (legacy)  :958-1032
   Reconnect: own state machine IDLE/CONNECTING/RUNNING/RECONNECTING/
       FAILED + exponential backoff (:403-428, 699-766)
   Started via CLI --bridge / web POST /api/v1/bridge
       (web/server.py:1870-1929); --bridge-rx-only forces tx_enabled=False
       (cli/__init__.py:1245-1246, 3384-3392)
```

### 1.6 Consumer: rigctld

rigctld does **not** consume the audio stream. Its only audio-adjacent surface is
the APF (audio peak filter) radio setting (`rigctld/handler.py:178,191`). WSJT-X
audio reaches the radio via the AudioBridge + a loopback device, not via rigctld.

### 1.7 Virtual-device drivers (per platform)

| Platform | Driver | Where it lives |
|---|---|---|
| macOS | BlackHole (or Rogue Amoeba Loopback) | Third-party; bridge detects by name (`audio/bridge.py:93-103`) |
| Linux | PipeWire loopback / PulseAudio null-sink / snd-aloop | Third-party / OS |
| Windows | VB-Cable today; project's own virtual driver in R&D | **Outside this repo** — rigplane-pro (`crates/tauri-app/src/audio_bridge.rs`, research under `rigplane-pro/docs/research/2026-05-24-stream-A*` covering custom WDM driver, VB-Cable bundling, VAD sponsor-fork) |

**Boundary note:** rigplane-core's contract with all of these is identical — a
named PortAudio device. The Windows virtual driver effort changes *which device
name exists*, not the core audio path. This ADR therefore treats "virtual device"
as one consumer class with a per-platform discovery list; the core must not grow
Windows-driver-specific logic.

### 1.8 Lifecycle / ordering of stream setup (the regression surface)

Who starts what, today:

| Actor | RX | TX |
|---|---|---|
| `AudioBus` | First subscriber triggers `radio.start_rx` (`bus.py:307-308, 327-356`); last unsubscribe stops it | — |
| Web broadcaster | Subscribes on first WS client / first tap (`handlers/audio.py:152-164`) | — |
| Web `AudioHandler` | — | `audio_start direction=tx` → `radio.start_tx` (`handlers/audio.py:716-733`) |
| RadioPoller | Re-arms RX after PTT-off via `bus.restart_rx()` for ALL duplex modes (`radio_poller.py:203-214, 1233-1245`) | Arms TX on PttOn, stops on PttOff (`radio_poller.py:1184-1231`) |
| AudioBridge | Subscribes to bus | Arms `radio.start_tx` itself, **order branched on `audio_duplex_mode`** (`bridge.py:504-519, 605-607`) |

Three independent actors arm TX; two arm/re-arm RX. No component owns the
combined RX×TX state of a radio's audio transport. The ordering constraints are
real and transport-specific:

- **LAN**: `AudioStream.start_rx` raises if state != IDLE — including
  TRANSMITTING (`lan_stream.py:522-523`). Arming TX first makes the later
  RX-subscribe fail ⇒ MOR-556 (#1750, `0acfea3f`).
- **Same-device macOS USB CODEC**: adding a second stream to a device that
  already has one triggers CoreAudio AUHAL `-50` and silently kills the capture
  ⇒ MOR-559 needed the OPPOSITE order (#1753, `467f40bd`), now expressed as the
  `rx_first = audio_duplex_mode != "exclusive"` branch in the bridge —
  a patch on a patch.

Git history of `audio/bridge.py` tells the saga directly: `467f40bd` (MOR-559
order branch) ← `0acfea3f` (MOR-556 subscribe-before-TX) ← `a52263d9` (MOR-545
neutral TX) ← `d0a15d27` (MOR-531 duplex) — three live ordering regressions in
two days.

### 1.9 Carrier: WebRTC DataChannel transport (gated; #104 / MOR-307)

Core already ships a second client carrier next to the WS endpoints:

```
browser ──POST /api/v1/transport/webrtc/offer──► WebRtcSessionManager
   web/transport/webrtc_session.py — lazily built (server.py:4137-4176;
   field at server.py:742-744); gated on WebConfig.webrtc_enabled AND the
   [webrtc] aiortc extra; capability CAP_WEBRTC (core/capabilities.py:172-173)
   │  one RTCPeerConnection per peer; the browser (offerer) opens three
   │  DataChannels: control — ordered/reliable; scope + audio — unordered,
   │  lossy (webrtc_session.py:9-23, labels :69-71)
   ▼
WebRtcDataChannelConnection (web/transport/connection.py, webrtc.py)
   each inbound channel is wrapped and dispatched BY LABEL into the
   UNCHANGED ControlHandler / ScopeHandler / AudioHandler
   (webrtc_session.py:225-262) — the same AudioHandler as §1.3
```

Three consequences this ADR must honor:

- **One egress design, two carriers.** Because the WebRTC path reuses the WS
  handlers verbatim, the `AudioSession` + egress design (§3.6) and the tap
  surface (§3.7) must serve both carriers unchanged — WS binary frame and
  WebRTC DataChannel are interchangeable transports under the same edge (T6).
- **Repo boundary.** Signaling/brokering is Tower's job — "no Tower signaling
  — the broker path is A3 / pro" (`webrtc_session.py:3-5`). The RigStation
  box and fleet design live in `rigplane-station`
  (`docs/DESIGN.md`, `tower-contract-v1.md`,
  `connectivity-fleet-decomposition.md`); rigplane-core owns only the
  transport seam and the shared handlers and must not grow RigStation- or
  Tower-specific logic.
- **North star.** The operator's target is the RigStation box proxying ALL
  traffic — audio + control + scope — through WebRTC as the **primary remote
  path**. The unordered/lossy `audio` channel is the right choice for human
  monitoring but is **wrong for remote digital decode** — see the digital-intent
  constraint in §3.6 and tenet T1a.

---

## 2. Problem inventory

Severity: **S1** = produced live regressions / silent data loss; **S2** =
architectural debt that keeps producing S1s; **S3** = maintainability smell.

### P1 (S1) — Stream setup is order-sensitive and the order is caller-owned

Evidence:
- `bridge.py:517` — `rx_first = getattr(self._radio, "audio_duplex_mode", "full") != "exclusive"`,
  with the 14-line comment at `bridge.py:504-516` explaining two mutually
  exclusive orderings. The bridge — a *consumer* — encodes per-transport
  device-arming knowledge.
- `lan_stream.py:522-523` — RX start hard-fails from TRANSMITTING; the LAN
  state machine supports RX→TX only.
- MOR-556 (#1750) and MOR-559 (#1753) were both live-only discoveries.
- The poller and web handler each contain their own TX arming sequence
  (`radio_poller.py:1184-1213`, `handlers/audio.py:716-733`) with no shared
  sequencing point — only the bus's first-subscriber rule for RX.

Root cause: consumers issue *imperative call sequences* (`start_rx`, `start_tx`)
against a transport whose legal transition graph they cannot see. Any new
consumer or new transport multiplies the orderings to get right.

### P2 (S1) — Silent failure class; no runtime health model

Evidence:
- `bus.py:347-356` — `AudioBus._start_rx` swallows every start exception
  (`logger.exception("audio-bus: failed to start RX")`), leaving `rx_active`
  False while the subscriber believes it is attached. Same for `restart_rx`
  (`bus.py:358-374`).
- `handlers/audio.py:463-470` — `_start_relay` swallows subscription failures;
  the WS client still gets a queue and "subscribed to RX broadcast"
  (`handlers/audio.py:1000-1009`) with zero frames ever arriving.
- CoreAudio kills captures with `err='-50'`/`'-10863'` written to **stderr only**
  (no Python exception reaches the asyncio layer); the bridge then logged
  "started (RX+TX)" with dead RX — fixed only for the bridge by the band-aid at
  `bridge.py:630-645` (`_subscribe_bus` raises when `bus.rx_active` is False
  after subscribe, MOR-559).
- Health primitives exist but nothing consumes them as a watchdog:
  `TxStreamHealth` (`backend.py:70-114`), bridge `_tx_loop`'s per-iteration
  `capture.running` check (`bridge.py:965-969`), the LAN EPIPE watchdog
  (`runtime/radio.py:873-890`) — each local, none unified, RX capture has no
  liveness check at all outside the bridge.
- `_audio_recovery.py:93-136` replays only the **legacy** start methods on
  reconnect; a bus-managed neutral subscription is invisible to it.

### P3 (S2) — Radio-side duplex path exists but is unwired

Evidence:
- `usb_driver.py:911-1002` — `UsbAudioDriver.start_duplex` (MOR-531) has zero
  production callers (repo grep: only `tests/test_audio_duplex.py`). The radio
  side always opens two separate streams on the same C-Media device
  (`_icom_serial_base.py:429-434` + `441-448`; `yaesu_cat/radio.py:403, 420-424`),
  which is exactly the AUHAL `-50` topology on macOS. Only the *bridge's own
  loopback device* uses single-stream duplex (`bridge.py:575-594`).
- `resolve_usb_duplex_mode` (`usb_driver.py:449-484`) computes the policy but,
  per its own docstring, "nothing consumes it yet" beyond the bridge's order
  branch. MOR-546 is the pending wiring ticket.
- Consequence: the web path (poller PttOn → `radio.start_tx` while bus RX runs)
  on a same-device exclusive radio (FTX-1) reproduces the killed-capture failure
  the duplex stream was built to avoid — currently mitigated operationally by
  `--bridge-rx-only` and by FTX-1 being used rx-only.

### P4 (S2) — Codec leaks inward: names, synthetic idents, per-consumer decode

Evidence:
- Legacy surface is Opus-named forever: `start_audio_rx_opus` etc. are permanent
  delegate shims (`_audio_runtime_mixin.py:144-154, 224-229, 247-254, 326-331,
  365-370`; frozen `AudioCapable`, `radio_protocol.py:831-873`).
- Non-Opus radios *impersonate* the LAN wire: synthetic `AudioPacket` with
  `SYNTHETIC_RX_IDENT = 0x9781` (`lan_stream.py:45-54`), fabricated in
  `_icom_serial_base.py:421-426` and `yaesu_cat/radio.py:394-401`.
- Every consumer carries its own codec handling: the bridge owns an opuslib
  decoder (`bridge.py:496-500, 917-919`); the broadcaster owns uLaw decode
  (`handlers/audio.py:493-499`), an Opus pass-through, and a PCM→Opus egress
  transcoder (`handlers/audio.py:386-419`); DSP and taps are *gated off*
  entirely for Opus-native radios (`handlers/audio.py:506-524`, issue #762) —
  the FFT scope goes dark rather than decode.
- The Icom serial base even *encodes* locally captured PCM to Opus when the
  configured codec is an Opus variant (`_icom_serial_base.py:400-419`) — PCM →
  Opus → (bridge) → PCM round-trip inside one process.
- The neutral `AudioTransport` (descriptors + `start_rx`/`push_tx`,
  `radio_protocol.py:876-966`) is conformed-to structurally by all shipping
  backends (`test_audio_transport_conformance.py:92-110`), and the spine
  (bus/poller/broadcaster/bridge) prefers it via `getattr` probing — but the
  *payload* is still codec-opaque bytes, so neutrality stops at method names.

### P5 (S3) — Per-device audio config is parameter plumbing across 6 layers

Evidence (`rx_audio_channel` trace): `rigs/ftx1.toml [audio]` →
`profiles/rig_loader.py:133, 1252, 1335-1345, 1408` → `yaesu_cat/radio.py:216`
→ `usb_driver.py:507, 520` (ctor) → `usb_driver.py:855, 992` (open calls) →
`backend.py:227-236` (`open_rx` keyword) → `backend.py:346-371` (`_RxFramer`)
→ `backend.py:293-343` (downmix). Seven hops for one scalar; `sample_rate`,
`channels`, `frame_ms`, `rx_device`, `tx_device` each travel the same road as
separate keywords. Adding any per-device knob (e.g. input gain) means touching
every layer again.

### P6 (S2) — Test doubles are order-insensitive; CI was blind to P1

Evidence:
- `FakeAudioBackend` and its streams (`backend.py:1277-1514`) enforce only
  per-stream double-start, never cross-stream/device exclusivity, and
  `FakeDuplexStream` coexists freely with separate fake streams on the same
  device id.
- The MOR-556/559 regression tests had to hand-build bespoke stateful radio
  stubs (`tests/test_audio_bridge.py:303-358` — "FakeAudioBackend-style
  order-insensitive stubs cannot catch this" — and `:399-477`), proving the
  shared fakes encode no transition graph.

### P7 (S3) — Error taxonomy by string matching

Evidence: `handlers/audio.py:44-55` — `_is_benign_tx_restart` does
case-insensitive substring matching on `"already transmitting"` /
`"already started"` because the LAN stream raises bare `RuntimeError`
(`lan_stream.py:601-603`) and the USB driver raises
`AudioDriverLifecycleError` (`usb_driver.py:58-59`) with different texts.
Bridge degrade paths likewise catch broad `(RuntimeError, NotImplementedError)`
(`bridge.py:559, 1011`).

### P8 (S2) — Single-slot callback registries persist

History: MOR-241 (web relay tap overwrite), MOR-506 (`_noop_rx` PttOff clobber).
Still single-slot today:
- `AudioStream._rx_callback` (`lan_stream.py:358, 528`) — the bus's callback
  IS this slot; any direct `start_rx` caller clobbers all bus subscribers
  (mitigated by convention only; `restart_rx` exists to repair it,
  `bus.py:358-374`).
- `IcomRadio._opus_rx_user_callback` mirrors the same single slot
  (`_audio_runtime_mixin.py:79`).
- `AudioFftScope.on_frame()` (`fft_scope.py:223`) — guarded by a comment at the
  sole call site (`web/server.py:753-757`).
- `AudioBroadcaster.set_pcm_tap` is a compat wrapper that now delegates to the
  multi-consumer `TapRegistry` (`handlers/audio.py:200-215`) — the good pattern.

### P9 (S1) — Bridge leaks its bus subscription on failed start (MOR-560)

Evidence: `_setup_streams` subscribes early on the rx-first path
(`bridge.py:517-519`); if anything later raises (device open, TX arm,
`open_duplex`), `start()` catches, sets state IDLE, and re-raises
(`bridge.py:786-791`) **without teardown**; `stop()` then early-returns on IDLE
(`bridge.py:806-809`). The orphaned subscription keeps the bus subscriber count
> 0, which keeps radio RX running with no consumer draining the queue.

### P10 (S2) — PTT/RX re-arm policy is scattered and over-broad

Evidence: `radio_poller.py:203-214` — `_should_restart_rx` returns True for ALL
duplex modes ("the re-arm currently fires for every audio_duplex_mode (including
'full')…", `radio_poller.py:1236-1242`); the flip to duplex-aware behavior is
hardware-gated (MOR-554). Meanwhile the bridge, web handler, and poller each own
a slice of the PTT-adjacent TX lifecycle (see table in §1.8), so "what happens
to audio when PTT toggles" has three partial answers.

### P11 (S3) — Egress codec decision is aggregate, not per-connection

Evidence: `handlers/audio.py:358-362` — "Keep this as an aggregate transport
decision because the relay currently builds one shared frame for all browser
clients"; one PCM16-preferring client (any Tauri WebView,
`audio-manager.ts:29-38`) forces PCM16 for everyone. This blocks the WAN/Opus
egress story (§3.6) and couples codec choice to client population.

---

## 3. Target architecture

### 3.1 Design tenets

**T1 — PCM spine.** The internal audio spine — bus, scope taps, DSP, bridge,
broadcaster — is strictly PCM s16le with explicit format metadata. Any
compressed transport ingress (Icom LAN-negotiated Opus or uLaw) is decoded **at
ingress, in the transport adapter**, never inside a consumer. Lossy compression
may exist only at the client-facing edge, only adaptively on degraded links
(§3.6), and never on a digital-decode path (T1a).

  *Validated against code:* no shipping radio is configured to deliver Opus by
  default — the LAN default is `PCM_2CH_16BIT` (`core/types.py:186-218`, bridge
  comment `bridge.py:294-297`), the serial/Yaesu paths are natively PCM
  (`_icom_serial_base.py:183-192`, `yaesu_cat/radio.py:318-331`). The Icom LAN
  protocol *can* negotiate radio-encoded Opus (`AudioCodec.OPUS_1CH/2CH`,
  `core/types.py:58-71`; resolvable in `audio/route.py:38-39`); today that
  capability leaks all the way to consumers (P4). Under T1 it becomes a
  transport-internal detail: decode once at ingress; do not send Opus toward a
  radio unless the transport itself negotiated it, in which case the transport
  encodes at egress-to-radio (the existing per-backend transcoders already
  prove feasibility: `_audio_runtime_mixin.py:412-429`,
  `_icom_serial_base.py:460-467`).

**T1a — The digital-decode path is strictly lossless, always (hard
invariant).** Any path whose consumer is a digital decoder — radio → bus (PCM)
→ `AudioBridge` → virtual device (BlackHole / PipeWire / VB-Cable) →
WSJT-X/fldigi — is PCM end-to-end, no exceptions. A lossy codec must NEVER sit
anywhere on it: not at radio ingress (an Opus-negotiated LAN session would
violate the invariant upstream of the bus, so lossless-class consumption
forbids selecting Opus ingress), not on the spine (T1), and not at egress —
the bridge consumer (`audio/bridge.py:228`) and the `--bridge-rx-only` output
(`cli/__init__.py:1245-1246`) are categorically exempt from all §3.6
egress-codec logic. Rationale: FT8/FT4/RTTY and friends tolerate no lossy
transform; an Opus re-encode hop wrecks decode margins. The invariant extends
unchanged to REMOTE digital consumers — lossless even over slow links, or not
at all (§3.6, "digital intent").

**T2 — Desired-state, not call sequences.** Consumers declare what they need
(RX, TX, both); one component per radio owns the transition plan. Setup order
becomes a *transport-internal* property, making the system order-insensitive by
construction.

**T3 — No silent audio death.** Every stream leg has a liveness signal; failures
transition an explicit state machine and surface to consumers and the runtime
API. "Started" is only reported after liveness is confirmed.

**T4 — One fan-out pattern.** Multi-consumer registries (the `TapRegistry`
pattern) everywhere; no single-slot callbacks on shared paths.

**T5 — Observable by construction.** Every stage of the tract, RX and TX
symmetrically, exposes a named tap point built on the existing `TapRegistry`
(`dsp/tap_registry.py:38-93`): zero-cost when no subscriber (the `active`
check), attachable/detachable at runtime, exception-isolated per tap.
Analytics (levels, SNR, loss, latency, codec state), the FFT scope/analyzer,
and the §3.4 liveness heartbeats are all just taps (§3.7). Taps are
local-process only — never telemetry (hard constraint 1).

**T6 — One egress design, two carriers.** The client edge (session
subscription → egress codec → framed delivery) behaves identically over the WS
binary carrier and the WebRTC `audio` DataChannel; the WebRTC transport
already dispatches into the unchanged `AudioHandler`
(`webrtc_session.py:225-262`), and WebRTC is the primary remote carrier — the
RigStation box proxies all traffic through it (§1.9). Nothing in the session,
egress, or tap design may assume a WebSocket.

### 3.2 Components and ownership

```
┌──────────────────────────────────────────────────────────────────────┐
│ CONSUMERS         web broadcaster (WS + WebRTC carriers, §1.9, T6)   │
│                   audio-scope taps · bridge (lossless class, T1a)    │
│                   recorder/Pro subscribers                           │
│   API: session.subscribe_rx(name) → PCM frames                       │
│        session.acquire_tx(owner) → TxLease (push / release)          │
├──────────────────────────────────────────────────────────────────────┤
│ AudioSession (NEW, audio/session.py) — one per radio                 │
│   · owns the RX×TX desired-state machine (§3.3)                      │
│   · absorbs AudioBus fan-out (bus stays as a thin facade)            │
│   · owns health monitor + recovery (§3.4)                            │
│   · owns the tract-wide tap surface (§3.7, T5)                       │
│   · single caller of the transport surface below                     │
├──────────────────────────────────────────────────────────────────────┤
│ AudioTransport (EXISTING protocol, core/radio_protocol.py:876)       │
│   + additive descriptor: audio_setup_order /                         │
│     atomic ensure()-style entry (§3.3) — backend-owned ordering      │
│   implementations:                                                   │
│   · LAN adapter (IcomRadio mixin) — decodes Opus/uLaw at ingress (T1)│
│   · USB adapter (serial base / Yaesu) — delegates topology to        │
│     UsbAudioDriver.ensure() (two-stream vs single duplex, MOR-546)   │
├──────────────────────────────────────────────────────────────────────┤
│ Drivers: AudioStream (LAN UDP framing) · UsbAudioDriver (PortAudio)  │
│ Devices: radio UDP port · USB CODEC · loopback (BlackHole/PipeWire/  │
│          VB-Cable/own Windows driver)                                │
└──────────────────────────────────────────────────────────────────────┘
```

Ownership rules:

- **AudioSession is the only component that calls
  `start_rx/stop_rx/start_tx/stop_tx` on a radio.** Bus subscribers, the web
  poller's PTT hooks, the web TX handler, and the bridge all go through the
  session. (The bus's first-subscriber/last-unsubscriber rule survives — it
  becomes the session's RX refcount.)
- **The bridge becomes a pure consumer + device pump**: bus-RX → loopback out,
  loopback in → TX lease. Its reconnect machinery shrinks to device-side
  concerns; radio-side recovery belongs to the session.
- **Carrier-blind edge (T6).** The session and the egress controller serve the
  WS `AudioHandler` and the WebRTC-dispatched `AudioHandler`
  (`webrtc_session.py:225-262`) through one interface. Signaling, brokering,
  and box logic stay in Tower / rigplane-station (§1.9 repo boundary); core
  grows neither.
- **Taps are first-class (T5).** Each pipeline stage owns its named tap point
  (§3.7); the FFT scope and analyzer — today special-cased into the
  broadcaster's single registry (`web/server.py:745-772`) — become two
  ordinary taps among many.
- **Layering:** `AudioSession` lives in `audio/` (it references the radio only
  through the `AudioTransport` duck type, exactly like `AudioBus` today), so the
  import-linter matrix is untouched (`TapRegistry` is in `dsp/`, below
  `audio/`). `runtime/` constructs it; `web/` and the bridge consume it.

### 3.3 Stream lifecycle as an explicit state machine

Per-radio session state (RX axis × TX axis collapsed into one machine):

```
                    ┌────────────┐
        rx_demand>0 │            │ rx_demand==0 && tx_demand==0
       ┌───────────►│   IDLE     │◄───────────────────────┐
       │            └─────┬──────┘                        │
       │                  │ ensure()                      │
       │            ┌─────▼──────┐   tx_demand>0    ┌─────┴──────┐
       │            │  RX_ONLY   ├─────────────────►│   RX_TX    │
       │            └─────┬──────┘   (plan-ordered) └─────┬──────┘
       │                  │ failure / stall               │ tx_demand==0
       │            ┌─────▼──────┐                        │ (→ RX_ONLY,
       └────────────┤ RECOVERING │◄───────────────────────┘  re-arm RX if
            give-up └─────┬──────┘                           transport says so)
                          ▼
                       FAILED  (surfaced; manual or demand-change retry)
```

Key mechanics:

1. **Demand counters, not calls.** `subscribe_rx` / `acquire_tx` adjust demand;
   the session computes the desired state and runs `_apply(desired)` under one
   lock. Repeated/concurrent consumer actions cannot interleave arming calls —
   this deletes the double-start class (P7's *cause*, not just its detection).
2. **Transport-owned ordering.** `_apply` does not hardcode RX-first or
   TX-first. The transport declares its plan — minimally an additive descriptor
   next to `audio_duplex_mode`:

   ```python
   @property
   def audio_setup_order(self) -> Literal["rx_first", "tx_first", "atomic"]: ...
   ```

   - LAN → `"rx_first"` (state machine constraint, `lan_stream.py:522`)
   - USB separate devices → `"rx_first"` (don't care, pick one)
   - USB same-device exclusive → `"atomic"`: the adapter's TX arm internally
     tears down and re-opens as a single duplex stream via
     `UsbAudioDriver.ensure(rx=…, tx=…)` (this is where
     `start_duplex` finally gets its production caller — MOR-546).

   The bridge's `rx_first` branch (`bridge.py:517`) is then deleted: the bridge
   just declares demand. New transports ship their ordering with the backend,
   and no consumer is ever taught about it again.
3. **PTT integration.** Poller PttOn/PttOff become `session.acquire_tx("ptt")` /
   `release_tx("ptt")`. The post-TX RX re-arm (`_should_restart_rx`,
   `radio_poller.py:203-214`) collapses into the RX_TX → RX_ONLY transition:
   the session re-arms RX iff the transport's plan requires it
   (`tx_first`/`atomic` transports yes; `full` duplex no) — which lands MOR-554
   as a property of the machine instead of a poller heuristic.
4. **TX ownership.** `acquire_tx(owner)` returns a lease; concurrent owners are
   refcounted (web handler + poller both arming around PTT is today's reality,
   `handlers/audio.py:729-732` tolerates it by string match). Typed
   `AudioAlreadyStartedError` remains for legacy callers during migration (P7).

### 3.4 Health and failure model

Signals (all already half-exist; the session unifies them):

| Leg | Liveness signal | Today | Target |
|---|---|---|---|
| LAN RX | packets/s through `AudioStream` (`_rx_packets_received`) | stats only (`lan_stream.py:469-486`) | session heartbeat: `last_rx_frame_monotonic` |
| USB RX capture | PortAudio callback cadence (fires even on silence) | none — CoreAudio `-50` dies silently (P2) | callback timestamps a heartbeat; stall > N frames ⇒ RECOVERING |
| TX playback | `TxStreamHealth` (`backend.py:70-114`) | exposed, unconsumed | watchdog reads `write_failures`/underruns |
| Bus → consumer | subscription drop counters (`bus.py:106-113`) | logged | exported per-subscriber in runtime payload |
| Bridge device | `_on_stream_error` → reconnect (`bridge.py:699-766`) | bridge-local | device legs stay bridge-local; radio legs move to session |

Rules:

1. **Start is verified.** A state transition completes only when the first
   heartbeat arrives (or a transport-specific readiness probe passes). The
   MOR-559 band-aid (`bridge.py:630-645`) generalizes into the machine: no
   consumer ever observes "running" with a dead leg.
2. **Failures are events.** `AudioSessionEvent(state, reason, leg)` published to
   a listener registry (T4); the web server forwards them on the existing
   runtime/event surface (`server.py:2355+` bridge payload pattern) — local
   only, no telemetry (open-core §2).
3. **No exception swallowing on the demand path.** `AudioBus._start_rx`'s
   blanket `except Exception` (`bus.py:355-356`) is replaced by: raise to the
   demanding subscriber, mark machine RECOVERING for established demand.
4. **One recovery loop.** The session owns retry/backoff for radio-side legs
   (absorbing `_audio_recovery.py`'s snapshot replay — which today replays only
   legacy methods — and the bridge's radio-side reconnect). Device-side
   (loopback) recovery stays in the bridge.
5. **Watchdog cadence** ≥ 1 s, fully decoupled from keep-alive loops (constraint
   4 untouched).
6. **Liveness is a tap (T5).** The heartbeat signals in the table publish
   through the tap surface (§3.7) as stage-level liveness taps; the session
   watchdog is merely their first subscriber, the runtime-payload exporter the
   second. Debug tooling attaches to the same points with no new plumbing.

### 3.5 Codec policy (PCM spine, T1)

```
            INGRESS (decode once)                 SPINE (PCM only)                EGRESS (encode per edge)
radio LAN ─ PCM16/uLaw/Opus ─► LAN adapter ──► PcmFrame(sr, ch, s16le, seq) ──► web: per-conn PCM16 default,
radio USB ─ PCM (native)    ─► USB adapter ──►   bus / DSP / taps / bridge  │      adaptive Opus (§3.6)
                                                                            ──► bridge: PCM → loopback
                                                                            │      (T1a — never lossy)
                                                                            ──► radio TX: transport encodes
                                                                                 to ITS negotiated codec
```

- `PcmFrame` is an additive carrier (`audio/`): sample-rate, channels, s16le
  payload, monotonic seq. The bus dual-publishes `AudioPacket` (legacy,
  Pro-pinned export) and `PcmFrame` during migration; `SYNTHETIC_RX_IDENT`
  fabrication (`_icom_serial_base.py:421-426`, `yaesu_cat/radio.py:394-401`)
  survives only inside the legacy adapter shim.
- Ingress decode kills the per-consumer decoders: the bridge's opuslib decoder
  (`bridge.py:496-500`), the broadcaster's uLaw branch
  (`handlers/audio.py:493-499`), and the Opus gates that silence DSP/FFT
  (`handlers/audio.py:506-524`, #762) all collapse. FFT scope works on
  Opus-native radios for the first time.
- **Cost acknowledged:** an Opus-native LAN radio whose browser client also
  wants Opus today gets bit-exact pass-through
  (`handlers/audio.py:394-395`); under T1 it gets decode→re-encode. This is
  accepted: it is the only configuration that loses (no shipping default uses
  it), comms-grade Opus re-encode at 48 kHz is perceptually transparent, and it
  buys a uniform spine. A pass-through fast-path may be re-added later strictly
  inside the web egress (edge optimization, never a spine codec).
- **Toward the radio:** never send Opus by default. The transport adapter owns
  encode-to-radio when (and only when) its own negotiation selected a
  compressed TX codec — no shipping backend does (`bridge.py:529-545` degrade
  documents this; `_icom_serial_base.py` TX codec is hard PCM, `:183-192`).
- **Digital exemption (T1a):** the bridge / virtual-device egress never
  participates in §3.6 codec logic — `--bridge-rx-only` and the bridge output
  ship spine PCM verbatim (downmix only). A conformance test pins that no
  egress encoder is ever constructible on a lossless-class consumer path
  (migration step 19).

### 3.6 Client edge: adaptive egress codec controller (design only — no implementation in this ADR)

Today the WS relay ships PCM16 at ≈768 kbps mono / ≈1.54 Mbps stereo (§1.3) —
fine on LAN, hostile over WAN. Rev 1 framed the fix as a static per-connection
Opus negotiation; the operator review hardened it into firm rules: **lossy
egress is adaptive and opt-in — it engages only on detected link
degradation**.

**Policy (firm, not options):**

- **Default = PCM16, everywhere.** Opus is never the initial codec and never
  always-on; on a healthy LAN the controller never leaves PCM16.
- **Engagement is automatic and per-connection**, driven by congestion
  detection (table below) with hysteresis. Clients without Opus decode
  (no WebCodecs `AudioDecoder` — `audio-manager.ts:29-38`) are pinned PCM16
  and exempt from adaptation entirely.
- **Lossless-class consumers (T1a) are categorically exempt** — the bridge and
  any consumer flagged as feeding digital decode never pass through this
  controller.

**Controller placement:** per-client, in the broadcaster, downstream of the
PCM spine (after DSP and taps) — replacing the aggregate `_web_codec` shared
frame (`handlers/audio.py:358-362, 536-548`, P11) with per-`client_id` codec
state + encoder. Carrier-blind (T6): one controller serves WS and WebRTC
clients; only the *signal source* differs per carrier.

**Detection signals per carrier:**

| Carrier | Server-side signal | Client-side signal |
|---|---|---|
| WS | per-client send-queue depth and drop-oldest rate (bounded queue, `HIGH_WATERMARK=10`, drop-on-full at `handlers/audio.py:556-566`) | client-reported playback underruns + buffer depth via a new periodic `audio_stats` uplink — today the player counts only autoplay-suspend drops (`rx-player.ts:54-57`); underrun accounting is new |
| WebRTC | RTCP receiver-report fraction-lost / jitter / RTT from the peer-connection stats (`aiortc` `getStats()` on the §1.9 session) | same `audio_stats` envelope, carried on the `control` channel |

**Switching thresholds + hysteresis (initial values, tunable):**

- degrade PCM16 → Opus when, over a rolling 3 s window: ≥ 5 queue-drop
  events, or client-reported underruns ≥ 3, or RTCP fraction-lost > 2 %, or
  RTT > 150 ms with jitter > 30 ms;
- upgrade Opus → PCM16 only after a fully clean 30 s window;
- minimum dwell 10 s per state, at most one switch per 10 s — no flapping;
- a mid-stream switch is seamless: every frame already carries the codec byte
  (`web/protocol.py:82-124`), and the browser player branches per frame.

**Negotiation:** per-connection, extending the existing `audio_start`
handshake (`preferred_rx_codec`, `handlers/audio.py:58-64`;
`audio-manager.ts:106-110`) — reinterpreted as a *capability declaration*
("codecs I can decode"), not a static choice. Server replies with an
`audio_format` acknowledgment `{codec, sample_rate, channels, frame_ms}` at
start AND on every adaptive switch; absence of the ack = legacy server, client
assumes current behavior. An explicit `force_codec` pin opts a client out of
adaptation. Fallback ladder: client pin → profile policy
(`browser_rx_transport`, `rig_loader.py:127-128`) → adaptive with PCM16
initial state.

**Per-connection encoding:** per-client encoder state keyed by `client_id`
(one `PcmOpusTranscoder` per currently-degraded client; PCM16 clients reuse
the raw frame). The encoder pool lives in the broadcaster, downstream of
DSP/taps, so the spine stays PCM. CPU: libopus mono 48 kHz encode is ~1% core
per stream — acceptable for the ≤ handful of concurrent clients the server
targets, and zero when no client is degraded (the common case).

**Framing:** unchanged 8-byte binary header (`web/protocol.py:82-124`) —
codec byte already distinguishes `AUDIO_CODEC_OPUS`; seq/frame_ms semantics
preserved (#765).

**Bitrate/latency budget:** Opus VBR 32–64 kbps mono (configurable),
20 ms frames, complexity 5. Added latency ≈ 5 ms encoder lookahead +
decode ≪ 1 frame — well inside the browser's existing adaptive jitter window
(`rx-player.ts` `setJitterBounds`). End-to-end target ≤ 250 ms on WAN.

**Browser decode path:** unchanged — WebCodecs `AudioDecoder` already decodes
Opus (`rx-player.ts:268-305`); the PCM16 path remains the fallback for
WebViews without WebCodecs (`audio-manager.ts:29-38`). TX uplink may later
adopt the mirrored option (browser Opus is already transcoded server-side,
`handlers/audio.py:1053-1080`).

**Digital intent over WAN (design point + open question #6):** when a REMOTE
consumer feeds digital decode (a remote WSJT-X behind a RigStation box, §1.9),
the transport to it must be lossless even over a slow link — a
reliable/ordered channel carrying PCM, or a lossless codec (e.g. FLAC, ~50 %
of PCM16) — **never adaptive Opus** (T1a). The current WebRTC `audio`
DataChannel is unordered/lossy (`webrtc_session.py:9-23`), which is wrong for
remote digital twice over: Opus must not engage, and even raw PCM frames are
dropped on loss. Remote digital therefore needs one of: a second
ordered/reliable DataChannel for lossless audio, application-level FEC over
the lossy channel, or a separate lossless audio path. Choosing among these —
and how "digital intent" is *signaled* (radio mode = data/FT8? explicit client
flag in `audio_start`? consumer class, bridge-vs-monitor?) — is open question
#6 and must be settled before any remote-digital feature ships in
rigplane-station.

### 3.7 Tract-wide tap surface (T5)

The multi-consumer `TapRegistry` already exists
(`dsp/tap_registry.py:38-93`: `register`/`unregister`, exception-isolated
`feed` at `:74-87`, zero-cost `active` check at `:89-93`) and is already the
sanctioned fix for the single-slot class (P8) — but it is wired at exactly one
point: the broadcaster's post-DSP PCM, feeding the FFT scope and the analyzer
(`web/server.py:745-772`, `handlers/audio.py:129`, single-slot
`fft_scope.py:223` downstream). The target generalizes it into a uniform,
stage-named, runtime-attachable surface across the whole tract, owned by the
`AudioSession`:

| Stage tap | Payload | Today |
|---|---|---|
| `rx.ingress` | raw transport bytes pre-decode (LAN UDP payload / PortAudio callback chunk) | none |
| `rx.pcm` | post-decode PCM at spine entry | bus subscribers only |
| `rx.post_dsp` | post-DSP PCM | the one existing `_tap_registry` |
| `rx.egress.<client>` | per-client framed output + codec state (PCM16/Opus, §3.6) | none |
| `tx.ingress` | browser/bridge TX input as received | none |
| `tx.pcm` | post-transcode PCM headed to the radio | none |
| `tx.radio_egress` | pre-radio-encode bytes at the transport adapter | none |

Properties (each already proven by the existing registry, now made uniform):

- **zero-cost when unobserved** — every stage guards on `registry.active`;
  an idle tract pays one boolean check per stage per frame;
- **runtime attach/detach** — live debugging on a running radio without
  restart or rebuild; the `/tmp` audio-level probes hand-rolled during the
  MOR-507/512 live debugging become one-line tap attachments;
- **per-tap exception isolation** (`tap_registry.py:74-87`) — a broken
  analyzer never stalls the tract;
- **consumers**: analytics (levels, SNR, loss, latency, codec state), the FFT
  scope and analyzer (two ordinary taps among many), §3.4 liveness heartbeats
  (rule 6), ad-hoc debug dumps;
- **layering**: `TapRegistry` lives in `dsp/`, below `audio/` — the session
  hosts stage registries with zero import-matrix change;
- **open-core**: taps are local-process only; nothing leaves the box
  (constraint 1). Headless library users get the identical surface through
  the session — it is not a web feature.

### 3.8 How universality is achieved

A new transport (e.g. Kenwood network audio, SDR source) ships exactly:

1. an `AudioTransport` implementation (descriptors + neutral methods) that
   **decodes to PCM at ingress** and **declares its setup order**;
2. nothing else. The session, bus, broadcaster, scope, bridge, tap surface,
   and egress codecs are transport-blind.

This mirrors the frontend ADR's guarantee table: *new transport = one adapter;
audio bug = fixed once in the session; behavioral parity enforced by one state
machine and one conformance suite.*

### 3.9 Test architecture (P6)

1. **Stateful fakes by default.** `FakeAudioBackend` gains a
   `strict_device_exclusive=True` mode: opening a second stream on a device id
   that already has one raises `OSError(-50)`-shaped errors, and
   `FakeRxStream` exposes heartbeat injection. The bespoke MOR-556/559 stubs
   (`tests/test_audio_bridge.py:303-477`) graduate into shared fixtures.
2. **Lifecycle conformance suite** (extends
   `tests/contracts/test_audio_transport_conformance.py`): for every shipping
   backend (with its fake driver/link), run the same scenario matrix —
   `rx→tx→ptt-cycle→stop`, `tx-while-rx`, `rx-while-tx`, `double-start`,
   `start-failure-cleanup` — asserting (a) final state, (b) **no leaked
   subscriptions/streams**, (c) typed exceptions. Ordering bugs of the MOR-556
   class then fail in CI for *all* transports, not just the one that broke live.
3. **Order-insensitivity property:** for each transport fake, all permutations
   of consumer demand arrival (`bridge`, `web`, `ptt`) must converge to the
   same session state.

---

## 4. Migration plan

Ordered, atomic (≤3 files / ≤200 LOC), each independently shippable and
test-gated. **[BP]** = behavior-preserving, **[BC]** = behavior-changing.

| # | Step | Files (indicative) | Mode |
|---|---|---|---|
| 1 | **Fix MOR-560 leak**: `AudioBridge.start()` failure path runs `_teardown_streams()` (closes the early bus subscription) before re-raising; regression test | `audio/bridge.py`, `tests/test_audio_bridge.py` | [BP] |
| 2 | **Typed audio lifecycle errors**: add `AudioAlreadyStartedError` / `AudioNotStartedError` (subclassing `RuntimeError` for compat) in `core/exceptions.py`; raise from `lan_stream.py:522,601` and `usb_driver` lifecycle guards; `_is_benign_tx_restart` matches type first, keeps string fallback | `core/exceptions.py`, `audio/lan_stream.py` + `audio/usb_driver.py`, `web/handlers/audio.py` | [BP] |
| 3 | **Heartbeats**: `last_rx_frame_monotonic` on `AudioBus` (stamped in `_on_opus_packet`) + per-subscriber stats already present; expose in the web runtime payload next to the bridge block | `audio/bus.py`, `web/server.py`, test | [BP] |
| 4 | **Tract-wide tap surface skeleton** (T5, §3.7): stage-named registries — `rx.pcm` hosted on `AudioBus`, `rx.post_dsp` = the broadcaster's existing `_tap_registry` renamed into the scheme; FFT scope + analyzer re-register as named stage taps; `AudioSession` absorbs ownership at step 8. Behavior identical; the tract becomes observable before anything else changes | `audio/bus.py`, `web/handlers/audio.py`, tests | [BP] |
| 5 | **Stateful test fakes**: `FakeAudioBackend(strict_device_exclusive=True)` + promote the MOR-556/559 radio stubs to shared fixtures | `audio/backend.py`, `tests/conftest`-adjacent | [BP] (tests only) |
| 6 | **Lifecycle conformance suite** per §3.9.2 over all shipping backends | `tests/contracts/` | [BP] (tests only) |
| 7 | **`audio_setup_order` descriptor** (additive) on LAN mixin, serial base, Yaesu — derived from today's `audio_duplex_mode` values so semantics are identical; conformance-pin it | `runtime/_audio_runtime_mixin.py`, `backends/_icom_serial_base.py`, `backends/yaesu_cat/radio.py` | [BP] |
| 8 | **`AudioSession` skeleton** in `audio/session.py`: demand counters + state machine wrapping the existing `AudioBus` (bus API unchanged, session delegates); absorbs the step-4 stage registries; consumed by nothing yet; unit tests against fakes | `audio/session.py`, tests | [BP] |
| 9 | **Bridge on session**: replace the `rx_first` branch (`bridge.py:517`) + `_subscribe_bus` band-aid with session demand calls driven by `audio_setup_order`; conformance suite green for both orders | `audio/bridge.py`, `audio/session.py`, tests | [BP] (same external behavior, ordering now declarative) |
| 10 | **Bus stops swallowing RX-start failures**: raise to the first/demanding subscriber; broadcaster `_start_relay` surfaces the error to the WS client as an `error` envelope instead of silent dead air | `audio/bus.py`, `web/handlers/audio.py`, tests | [BC] (failures now visible; flagged in release notes) |
| 11 | **`UsbAudioDriver.ensure(rx, tx)`**: internal topology choice (two-stream vs `start_duplex`) — gives MOR-531 its production caller; serial base + Yaesu `start_tx` route through it (lands MOR-546) | `audio/usb_driver.py`, `backends/_icom_serial_base.py`, `backends/yaesu_cat/radio.py` | [BC] (same-device radios switch to single duplex stream; hardware-gated validation on FTX-1/X6200) |
| 12 | **Poller PTT through session**: PttOn/PttOff become `acquire_tx`/`release_tx`; `_should_restart_rx` collapses into the RX_TX→RX_ONLY transition keyed on the transport plan (lands MOR-554) | `web/radio_poller.py`, `audio/session.py`, tests | [BC] (full-duplex LAN stops the redundant post-TX RX restart; hardware-gated on IC-7610) |
| 13 | **Web TX handler through session** (`acquire_tx("web")`); string-match fallback removed once both arming paths are leases | `web/handlers/audio.py`, tests | [BP] |
| 14 | **Session health watchdog + events**: liveness per §3.4 (published via the step-4 taps, rule 6), runtime payload + WS event; bridge radio-side reconnect folds in | `audio/session.py`, `web/server.py`, `audio/bridge.py` | [BC] (new recovery behavior; replaces silent death) |
| 15 | **Decode-at-ingress (LAN)**: uLaw + Opus decoded in the LAN adapter; bus dual-publishes `PcmFrame` + legacy `AudioPacket`; broadcaster's uLaw branch and Opus gates removed; bridge decoder removed | 2–3 staged PRs: `runtime/_audio_runtime_mixin.py` + `audio/` carrier; `web/handlers/audio.py`; `audio/bridge.py` | [BC] (#762 pass-through replaced by re-encode; FFT scope lights up on Opus-native radios) |
| 16 | **`AudioDeviceConfig` carrier**: one frozen dataclass (rx/tx device, sample-rate, channels, frame_ms, rx_audio_channel) built by `rig_loader`, threaded as ONE object loader→backend→driver→framer (P5) | `profiles/rig_loader.py`, `audio/usb_driver.py`, `audio/backend.py` | [BP] |
| 17 | **Per-connection egress codecs** per §3.6 (capability negotiation + `audio_format` ack → per-client encoder pool) — replaces the aggregate `_web_codec`; static per-client choice only, adaptation arrives in step 19 | `web/handlers/audio.py`, `frontend/src/lib/audio/*` | [BC] (per-client encode; PCM16 default unchanged) |
| 18 | **Client link-quality uplink**: periodic `audio_stats` message (playback underruns, buffer depth) from the browser player; server records per client; WS queue drop-oldest counters exported per client (also feeds `rx.egress` taps) | `web/handlers/audio.py`, `frontend/src/lib/audio/*`, tests | [BP] (stats only, no behavior change) |
| 19 | **Adaptive egress codec controller** per §3.6: per-client PCM16↔Opus switching on step-18 + carrier signals with dwell/hysteresis; lossless-class consumers exempt by construction — conformance test pins T1a (no encoder constructible on a bridge/digital path); WebRTC RTCP signal source wired where the §1.9 carrier is active | `web/handlers/audio.py`, tests (+ frontend ack handling) | [BC] (adaptive switching; PCM16 default unchanged; flag-gated first release) |
| 20 | **Recovery unification**: `_audio_recovery.py` snapshot replay replaced by session-demand re-establishment on reconnect | `runtime/_audio_recovery.py`, `audio/session.py` | [BP] (same outcome, one mechanism) |

Steps 1–6 are pure de-risking and can ship this week — the tap surface
(step 4) lands early deliberately: it is behavior-preserving and makes every
later step observable. Steps 7–9 build the session without changing
behavior. Steps 10–12, 14, 15, 17, and 19 are the behavior changes, each
individually flagged and hardware-gated where noted. The legacy
`AudioCapable` shims and `rigplane.audio` export pins are untouched throughout
(constraint 1/2).

---

## 5. Risks and open questions

### Risks

1. **Hardware-gated steps (11, 12, 14).** Same-device duplex on the radio side
   and the RX-restart flip were both burned before by live-only failure modes
   (MOR-531/556/559). Mitigation: conformance suite first (steps 5–6), live
   validation on FTX-1 + X6200 (exclusive) and IC-7610 LAN (full) before each
   flip; ordering stays declarative so a revert is a descriptor change.
2. **Session as a new choke point.** A bug in `AudioSession` affects every
   consumer at once. Mitigation: it wraps (not replaces) `AudioBus` until step
   14; demand API is tiny; the conformance suite runs every backend through it.
3. **Decode-at-ingress CPU/quality** on Opus-native LAN radios (re-encode for
   Opus-preferring browsers). Accepted per §3.5; an edge pass-through can be
   restored later without touching the spine.
4. **Pro/local subscribers** consuming `AudioPacket` semantics (including
   `SYNTHETIC_RX_IDENT`) must keep working — dual-publish in step 15 and the
   pinned export tests are the guard; removal of the legacy carrier is
   explicitly out of scope.
5. **Windows virtual driver timing** (rigplane-pro): if the own-driver effort
   changes the device *model* (e.g. exposes control IPC), the bridge boundary
   may need an extension point. Current design assumes "named PortAudio
   device" stays the contract.
6. **Adaptive switching mis-detection.** A transient signal (queue spike,
   one-off underrun burst) could flap codecs or degrade a healthy LAN client
   to Opus. Mitigation: PCM16-initial always, conservative thresholds with
   10 s dwell and a 30 s-clean upgrade rule (§3.6), flag-gated first release
   (step 19), and the per-client `force_codec` pin as an escape hatch.
7. **Remote digital over the lossy channel.** Until open question #6 is
   settled, nothing may route a digital-decode consumer over the WebRTC
   `audio` DataChannel or through the adaptive egress — the T1a conformance
   test (step 19) is the in-repo guard; rigplane-station remote-digital
   features are blocked on the decision, not the other way around.

### Open questions

1. **Where does the session get constructed?** Proposal: lazily by the radio
   (mirroring `audio_bus`, `runtime/radio.py:1016-1021`) so library users get
   it for free; alternative: web server constructs it, leaving headless library
   users on the raw bus. Leaning radio-owned for headless parity (open-core §3).
2. **`audio_setup_order` vs richer plan object?** A literal is enough for the
   three known transports; if a future transport needs multi-step plans
   (e.g. mode switch before TX), upgrade to a declarative plan — decide when it
   exists, not before.
3. **Should `restart_rx` survive?** Under the session machine the post-TX
   re-arm is an internal transition; the public `AudioBus.restart_rx`
   (`bus.py:358-374`) likely stays as a no-op-compatible shim. Confirm no Pro
   consumer calls it directly.
4. **Stereo on the spine.** IC-7610 dual-RX ships interleaved stereo and the
   frontend splits L=MAIN/R=SUB (#792); `PcmFrame.channels=2` carries this, but
   the bridge and scope downmix policies (`mix/left/right`) should become
   per-consumer choices on the PCM spine — fold into step 16 or keep per-driver?
5. **Per-client DSP.** Once egress is per-connection (step 17), is DSP still
   global-per-radio (today: one pipeline in the broadcaster,
   `handlers/audio.py:124`) or per-client? Global is cheaper and matches the
   "radio audio" mental model — default to global unless a concrete need shows.
6. **Digital-intent signaling and the remote-lossless transport** (§3.6,
   T1a). How does a remote consumer declare "feeds a digital decoder" —
   inferred from radio mode (DATA/FT8 — fragile), an explicit flag in
   `audio_start`, or a consumer class on `AudioSession.subscribe_rx`
   (bridge-type vs monitor-type)? And which lossless WAN mechanism: an
   ordered/reliable DataChannel (head-of-line latency is acceptable for
   decode, not for monitoring), application-level FEC over the lossy channel,
   or a separate lossless path (e.g. FLAC)? The decision is owned by the
   rigplane-station design (`tower-contract-v1.md`); core only needs the
   consumer-class hook on the session API so the answer plugs in without a
   core rework.

---

## Summary

The audio path's recurring failures share one root: **no single owner of a
radio's audio stream lifecycle**, so ordering knowledge, codec knowledge, and
failure handling are smeared across consumers. The target gives each radio one
`AudioSession` (desired-state machine + health + tract-wide tap surface),
pushes ordering and codecs down into transport adapters (PCM at ingress,
transport-declared setup plans), and encodes the lifecycle in conformance
tests so the next MOR-556 fails in CI instead of on the air.

At the client edge, lossy compression is adaptive and exceptional: PCM16 by
default, Opus engaging per connection only on detected link degradation with
hysteresis — and **never on a digital-decode path**: the radio → bridge →
WSJT-X tract is lossless end-to-end, always (T1a), including its future
remote form. The edge serves two carriers with one design — WS today, WebRTC
DataChannels as the primary remote path the RigStation box will proxy all
traffic through — with rigplane-core owning only the transport seam and the
shared handlers, never the box or the broker.
