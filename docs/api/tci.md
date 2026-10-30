---
robots: noindex, follow
---

# TCI Server (Phase Control)

Client-facing TCI (Transceiver Control Interface) listener for rigplane,
phase "control": frequency, mode, split, RIT/XIT, drive and PTT over one
WebSocket per client. Audio streaming (RX/TX over the same socket) is a
later phase; binary frames are ignored until it ships.

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
`tx_frequency:`, `ready;`.

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
| `trx:0,<bool>` | PTT through the managed authority only (see below); `trx:0,true,<anything>` is refused — no source is keyed until the audio phase |
| `drive:0,<0..100>` | Power level through `resolve_power_level_target`; reply and notification always carry `0,<power>` |
| `rit_enable`, `xit_enable`, `rit_offset`, `xit_offset` | Capability-gated; RIT and XIT offsets are one value |
| `tx_enable:0` | Server only: true when the managed composition is present; a client set is answered with the current value |
| `tx_frequency` | Effective TX frequency: channel 1 while split is on, else channel 0, plus XIT when enabled |
| `dds`, `if` | Read-only; all three re-sent together |
| `start` / `stop` | Echoed, no radio action |
| `spot`, `spot_delete`, `spot_clear` | Accepted and dropped |
| `tune` | Not honoured: always `tune:0,false;`. `tune_drive` reads as `drive` and ignores sets |
| `vfo_limits`, `modulations_list`, `trx_count`, `channels_count`, `device`, `protocol`, `receive_only`, `cw_keyer_speed`, `cw_macros_speed` | Reads / init-only |
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

A key is refused, with no radio write, when the composite is absent,
the controller mode is `remote`, another owner holds PTT, the radio
transmits with no owner, an explicit source names any input, no
modulation was programmed through this server, or the observed TX tuple
(read back at key-down: mode, numeric DATA sub-mode, MOD input) differs
from the programmed one. The authority's TOT configuration bounds every
over; with TOT disabled an over holds until unkey.

## Known limitations

- The TX input chosen for an over is not restored afterwards.
- `tune` and `tune_drive` are not honoured (see table).
- No IQ, no spots on a panorama, no CW over TCI, no second receiver.
- Client behaviour above is reported, not captured: the C0 recording
  has not been done, so client expectations come from the TCI spec and
  second-hand client notes, and the server implements the spec text
  where they disagree.
