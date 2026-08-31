# Mechanism-audit reports

Archived, point-in-time outputs of the `auditor` role running the
`.claude/skills/mechanism-audit/SKILL.md` method. Each report pins the exact
revision it audited in its own header; citations inside a report (including
`file:line` forms used where no symbol encloses the evidence) are frozen at
that revision and are not maintained. These files live under `.claude/` rather
than `docs/` deliberately: the doc-citation gate treats `docs/**` citations as
living references that must not use line numbers, while an archived audit is
evidence about one commit and must quote it verbatim. Like `agents/`,
`commands/` and `skills/`, this directory is tracked and **published in a
public repository** — never put session notes, baselines, or anything with
internal identifiers here; untracked working notes belong in the ignored
remainder of `.claude/`.

## 2026-08-30 — v3 pre-release audit (three tracts, tree `8c8a70d4`)

Commissioned to close the pre-release fix list before the v2.12.0 release
cycle. The audited commit `8c8a70d4` was squash-merged as main's `3ba1a14a`
(byte-identical tree), and the original commit survives as
`refs/pull/2801/head` — fetch that ref explicitly to resolve the pin; a
plain clone carries only the identical tree, via `3ba1a14a`. One report per
tract:

- [2026-08-30-mechanism-audit-command-path.md](2026-08-30-mechanism-audit-command-path.md)
  — UI/rigctld/CLI action → wire bytes. Headline findings: rigctld advertises
  IC-7610 capability data for every Icom and invents ATT/PREAMP domains
  (F1/F2, live-reachable bugs on both bench radios); per-receiver write
  routing duplicated with a `receiver=0` contract split (F3); queue-drain and
  deferred-TX lane written twice (F4); queue dedup key undoes the MOR-1499
  coalescing fix one layer down (F6).
- [2026-08-30-mechanism-audit-tx-truth.md](2026-08-30-mechanism-audit-tx-truth.md)
  — everything deriving or gating on "is the radio transmitting". Headline:
  eight live derivations of TX truth (MOR-1973 said three); the rigctld-client
  backend's web write path has no TX interlock and no key-down bound (F2); the
  tuner-engage write is gated by the loosest resolver on a queue-bypassing
  path (F1); interlock refusal envelope typed on Icom, untyped on FTX-1 (F3).
- [2026-08-30-mechanism-audit-state-feedback.md](2026-08-30-mechanism-audit-state-feedback.md)
  — radio truth → screen → control feedback. Headline: two S-unit algorithms
  disagreeing on FTX-1 at the profile's own anchors (F1); TX state-feedback
  decision duplicated byte-identically across both design languages while its
  nested visual descriptors are computed and discarded (F2); `StateStore`
  history retention deep-copies every observation for a `delta_since` API
  with zero production consumers (D7).

Owner rulings recorded 2026-08-30 (they supersede in-report recommendations
where they differ):

1. `receiver=0` means literal MAIN, always; encapsulate the
   switch-write-restore mechanism in the runtime write path and delete the
   web poller's inline copy (command-path F3).
2. Transmit-state interpretation goes through the profile's `tx_state_map` on
   every vendor, Icom included. This answers TX-authority ADR Q14; the ADR's
   own §9 table still records Q14 as open, and amending it belongs to the
   epic's decode ticket, not to this archive.
3. Consolidate the TX state-feedback decision into one shared resolver; the
   nested descriptor bodies are deletable (no consumer exists — verified same
   day, including the built-dist browser suite); `.text` stays per MOR-1482.
4. `delta_since` + the history retention are deleted **before** the release
   (dead public API must not be frozen by a release); the same rule applies to
   the other dead public-surface findings.
5. Meter-conversion divisors become profile-calibration data; the four inline
   STRENGTH copies collapse onto the existing helper first (text-neutral).

Ticket disposition for every actionable finding is tracked in Linear (the
2026-08-30 pre-release epic and the documents mirroring these reports); the
reports themselves are the evidence record, not the work queue.

## 2026-08-30 — VFO equalize/swap tract (tree `7479ebd5`)

Commissioned out of MOR-2007 (the vfo.py bound-command-map migration), which
surfaced but could not scope the question. The audited commit `7479ebd5` is a
main commit (#2838), so the pin resolves in a plain clone.

- [2026-08-30-mechanism-audit-vfo-swap-equalize.md](2026-08-30-mechanism-audit-vfo-swap-equalize.md)
  — every mechanism reaching "equalize or swap the VFOs" (CI-V `0x07` + one
  operation byte). Headline findings: `commands/vfo.py: vfo_a_equals_b` /
  `vfo_swap` are a vestigial fork — zero production call sites by AST census,
  byte-for-byte reducible to the live `set_vfo(code)`, hardcoding an equalize
  byte (`0xA0`) that no shipped dual-RX profile declares (D1); dead
  `RadioProfile.vfo_swap_code`/`.vfo_equal_code` legacy aliases (D2); stale
  test stubs for a pre-#1114 radio API (D3); the "does this rig support
  swap/equalize" decision inlined seven times across two incompatible
  discriminators, with an observable WS-vs-HTTP capability disagreement
  between `ControlHandler._capabilities` and
  `WebServer._projected_runtime_capabilities` (F1); the dual-RX mixin's
  `0x07` command byte still outside the MOR-2007 binder contract, in a file
  the plan's Steps 5..N explicitly name (F2). Cleared: the runtime mixin
  itself (the live side, correctly located), `set_vfo` post-MOR-1986, the
  Yaesu backend arms, `BoundCommands`, and the identically-named-but-unrelated
  WebSocket intent `"vfo_swap"`.
