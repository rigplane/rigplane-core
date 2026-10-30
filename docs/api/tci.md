---
robots: noindex, follow
---

# TCI Server (Control and Audio)

Client-facing TCI (Transceiver Control Interface) listener for rigplane:
frequency, mode, split, RIT/XIT, drive, PTT and RX/TX audio over one
WebSocket per client. Audio runs over the same socket as control
(`TciRxPump` serves RX, `TciTxSession` takes TX); a radio with no audio
route keeps the control-only behaviour, answering no audio command.

Enable it explicitly; it is off by default:

```bash
rigplane --model IC-7610 --host <RADIO_IP> web --tci
```

`station` accepts the same flags. `serve` has no TCI support. The flags
are `--tci` / `--no-tci` (default off), `--tci-port N` (default `50001`)
and `--ingress-host HOST`, the one bind-host setting shared by the
`rigctld` and TCI listeners. Without `--ingress-host` each listener
keeps its own default: TCI stays on loopback (`127.0.0.1`), while
`rigctld` keeps its existing rule (`127.0.0.1` managed, else `0.0.0.0`).
A non-loopback `--ingress-host` logs a WARNING on start. An
already-used TCI port aborts startup, and `check_ports_available`
covers the TCI port the same way it covers `rigctld`. AetherSDR already
listens on `50001` by default, so running both on one machine needs
`--tci-port` to relocate one of them.

The implementation lives in `rigplane.tci_adapter`: `TciAdapterServer`
builds a leaf `Tci` server, `TciAdapterHandler` answers one connection,
`TciNotifier` projects the shared `StateStore` into the burst and the
notifications, and `commands` holds the command and mode tables. The
announced strings are named constants in `commands`: protocol
`ExpertSDR3,2.1`, `trx_count:1`, `channels_count:2`, and `device:` is
the radio model. The strings stay open: a client capture may change
them, so they live in one place.

## Init burst

One deterministic sequence per connection, ending with `ready;`. The
order is identity, topology, limits, modes, state, then `ready;`
(`TciNotifier.init_burst`): `protocol:`, `device:`,
`receive_only:`, `trx_count:`, `channels_count:`, `vfo_limits:`
(when the profile publishes frequency ranges), `modulations_list:`
(the intersection of the TCI names below with the profile modes;
`DIGL`/`DIGU` additionally need the DATA-mode command), then
`dds:`, `if:` (twice), `vfo:0,0`, `vfo:0,1`, `modulation:`, `trx:`,
`tune:`, `drive:`, `tune_drive:`, `rit_enable:`, `xit_enable:`,
`split_enable:`, `rit_offset:`, `xit_offset:`, `tx_enable:`,
`tx_frequency:`, then the stream settings
(`audio_samplerate:`, `audio_stream_sample_type:`,
`audio_stream_channels:`, `audio_stream_samples:`) when the radio has an
audio route (`TciAdapterHandler.on_connect`), then `ready;`.

Only commands from the `eesdr-tci` dictionary are ever emitted.

## Honoured commands

A write is always answered: an accepted set echoes to every session, a
no-op or a refused write answers the actual state to the requester
only. A refused PTT is `trx:0,false;` to the requester only.

| TCI command | Behaviour |
|---|---|
| `vfo:0,0` / `vfo:0,1` | Channel 0 sets the frequency; channel 1 (VFO B) sets it through the relative-VFO surface and stays readable with split off |
| `modulation:0,<name>` | `AM LSB USB CW FM WFM DIGL DIGU` (`TCI_TO_CORE_MODE`); `DIGL` is LSB with data on, `DIGU` is USB with data on; anything outside `modulations_list` is ignored |
| `split_enable:0,<bool>` | `false` while a client programs channel 1 never erases it |
| `trx:0,<bool>` | PTT through the managed authority only (see below); `trx:0,true,tci` keys with TX audio when the radio has a PCM TX route (see below), any other explicit source is refused |
| `drive:0,<0..100>` | Power level through `resolve_power_level_target`; reply and notification always carry `0,<power>` |
| `rit_enable`, `xit_enable`, `rit_offset`, `xit_offset` | Capability-gated; RIT and XIT offsets are one value |
| `tx_enable:0` | Server only: true when the managed composition is present; a client set is answered with the current value |
| `tx_frequency` | Effective TX frequency: channel 1 while split is on, else channel 0, plus XIT when enabled |
| `dds`, `if` | Read-only; all three re-sent together |
| `start` / `stop` | Echoed, no radio action |
| `spot`, `spot_delete`, `spot_clear` | Accepted and dropped |
| `tune` | Not honoured: always `tune:0,false;`. `tune_drive` reads as `drive` and ignores sets |
| `vfo_limits`, `modulations_list`, `trx_count`, `channels_count`, `device`, `protocol`, `receive_only`, `cw_keyer_speed`, `cw_macros_speed` | Reads / init-only |
| `audio_samplerate` | `8000`, `12000`, `24000`, `48000`; anything else echoes the current rate |
| `audio_stream_sample_type` | `int16`, `int24`, `int32`, `float32` (reply is lower case); anything else echoes the current type |
| `audio_stream_channels` | Clamped to 1..2 and answered with the value in force, `1`: the server sends real mono in every mode |
| `audio_stream_samples` | Clamped to 100..2048 (defaults 2048/1024/512/256 at 48/24/12/8 kHz) |
| `tx_stream_audio_buffering` | Clamped to 50..500 ms (default 50): the TX playout target depth |
| `audio_start:0` / `audio_stop:0` | Start/stop this client's RX subscription; answered to the requester only |
| Everything else | Ignored without a reply, logged once per connection with the name only |

`modulation` also keeps channel B's mode tuple equal to channel A's
while split is on, through `write_relative_vfo_mode` with the numeric
DATA sub-mode.

## TX safety

Keying goes only through `ManagedTxAuthority` with the shared
`execute_managed_ptt` submission; there is no second keying path (no
`Radio.set_ptt` call exists in `rigplane.tci_adapter`). Each socket
mints one owner id (`tci-client-N`), released on disconnect through
the shared `release_managed_session_tx`: the release attempt is bounded
by the handback timeout, and a failed or twice-cancelled handback
leaves the documented `RELEASE_REQUIRED` outcome — the lease stays
held with its release obligation unsettled, surfaced by the
uncertain-shutdown diagnostics. Without the managed composition the
server announces `receive_only:true`, `tx_enable` is false, and every
key is refused.

A bare `trx:0,true` is refused, with no radio write, when the
composite is absent, the controller mode is `remote`, another owner
holds PTT, the radio transmits with no owner, no modulation was
programmed through this server, or the observed TX tuple (read back at
key-down: mode, numeric DATA sub-mode, MOD input) differs from the
programmed one. Any explicit source other than `tci` is refused.
`trx:0,true,tci` is accepted only with a PCM TX route
(`tx_route_ready`: `audio_tx_codec` is `PCM_1CH_16BIT`, the route is
accepted, the TX lease surface exists); the TX input is then
established and verified inside the authority's admitted key
(`before_on`: WSJT-X data policy applied for the current SSB/AM/FM
mode, DATA state and MOD input read back) and the key is refused
without a PTT write when the mode has no data route, the radio cannot
take DATA, or the readback differs. The authority's TOT configuration
bounds every over; with TOT disabled an over holds until unkey.

## Audio

RX audio served is the real demodulated signal in mono: each
`AudioSession` packet becomes a `PcmFrame` through the shared
`packet_to_pcm` helper, is resampled to the client's rate
(`PcmResampler`) and re-blocked to `audio_stream_samples`, and leaves
as a type-1 frame with `channels:1` (`TciRxPump`). A client behind on
its bounded queue drops the oldest frames and is counted; a client
that stays behind for 5 s is closed. TX audio is accepted as mono or
stereo float/int frames (type 2): stereo in `DIGU`/`DIGL` is I/Q and
contributes channel 0, otherwise the pair is averaged; frames are
resampled to the radio rate, clamped to [-1, 1] with NaN/Inf replaced
by 0, and scaled to int16 (`convert_tx_frame`). After `trx:0,true,tci`
the server sends `TX_CHRONO` markers (type 3) at the
`length / (channels * sample_rate)` cadence and the playout task pushes
one 20 ms radio frame per tick from a buffer with target depth
`tx_stream_audio_buffering` (underrun pushes silence and counts,
overrun drops the oldest and counts, unkey/disconnect flush)
(`TciTxSession`). Every push runs inside
`ManagedTxAuthority.owned_tx_submission(owner)`: frames with no keyed
owner, or from a session that is not the owner, are dropped and counted
and never key the radio.

## Known limitations

- The TX input chosen for a `source=tci` over is not restored
  afterwards.
- TX needs a `PCM_1CH_16BIT` route; anything else refuses
  `trx:0,true,tci` with no PTT write.
- `tune` and `tune_drive` are not honoured (see table).
- No IQ, no spots on a panorama, no CW over TCI, no second receiver.
- Client behaviour above is reported, not captured: the C0 recording
  has not been done, so client expectations come from the TCI spec and
  second-hand client notes, and the server implements the spec text
  where they disagree. Real-client TX behaviour (WSJT-X/JTDX audio and
  PTT over this socket) is not captured: this phase stays HOLD for
  merge until the C0-TX evidence lands.
