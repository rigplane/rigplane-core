# Mechanism audit — Icom STOP CW NAK handling

Audited revision: `4cbd5ba4c016753c45876e74e7a077513c5bdeea`
Method: `.claude/skills/mechanism-audit/SKILL.md`
Scope: the bounded STOP-CW response path, not a whole-`CoreRadio` sweep.

## Boundary census

Eight symbols were read: `commands.cw:stop_cw`, `commands._frame:parse_ack_nak`,
`CoreRadio:stop_cw_text`, `_send_civ_raw`, `_send_civ_expect`, and
`CivRuntime:send_civ_raw`, `_execute_civ_raw`, `_civ_expects_response`.
`IcomCommander:send` is the priority executor reached when present. This tract
does not make deletion claims or census unrelated `CoreRadio` attributes.

## Steelman

The commander serializes completed exchanges, STOP-CW uses the immediate lane,
and `CivRequestTracker` registers an ACK/NAK waiter before send. The tracker
and generic parser already provide attribution and outcome normalization; the
missing behavior is only the local caller's discarded value.

### F1 — STOP-CW discards a delivered NAK

Verdict: C — correctly located canonical-adapter correction
Rank: none; this is not a duplicate or displacement
Elements: `runtime.radio:CoreRadio.stop_cw_text`; `commands._frame:parse_ack_nak`
Definition site: the former sends the profile-built STOP-CW frame; the latter
normalizes bare ACK/NAK frames to `True`/`False`/`None`.
Consumers: `stop_cw_text` is exposed by the radio protocol and reached through
the synchronous wrapper and control handler; `_send_civ_raw` reaches
`IcomCommander:send` when a commander exists. `send_cw_text` separately
consumes `parse_ack_nak` and raises `core.exceptions:CommandError` on NAK.
Divergence: `stop_cw_text` awaits its immediate response but ignores the frame;
`send_cw_text` interprets NAK.
Prior ruling: `docs/plans/2026-08-29-profile-driven-command-bytes.md` assigns
the STOP-CW bytes to the command map. `docs/plans/2026-09-01-runtime-transmit-authority.md`
makes stop-CW subordinate diagnostic work during ForceOff; neither establishes
manufacturer reply semantics.
In-flight: none found in this bounded source reading.
Required surface: exists — the current response wait and `parse_ack_nak`.
Depends on: none
Confidence: high for source flow; no manufacturer response claim
Falsifier: an inbound NAK does not resolve the ACK waiter for this exchange.
Fix class: none beyond consuming the existing result at this call site
Actionable: yes; a NAK can raise the existing `CommandError` without changing
priority, command bytes, timeout handling, or authority ownership.

## Weakest link

Whether a particular radio sends no reply is not established by this tract.
The audited code waits for a response; this finding concerns only a delivered
NAK and does not prove CW interruption, RF state, or ForceOff completion.

## Cleared

The profile-backed `stop_cw` builder, immediate commander routing, ACK/NAK
registration, and the shared parser are each correctly located for this
bounded path. No new queue, protocol API, profile, or authority mechanism is
indicated.
