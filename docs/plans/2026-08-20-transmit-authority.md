# Transmit Authority — Command-Ingress Filter ADR

**Date:** 2026-08-20
**Status:** **Superseded 2026-09-01.** Historical record only; do not implement
this document's target architecture or reopen its pending questions. Replaced
by [Runtime Transmit Authority ADR](2026-09-01-runtime-transmit-authority.md).
**Base commit:** `769bfc71` (main; re-anchored 2026-08-20 from the authoring
base `fb7a86da` — three merges landed in between, see §1.6)
**Format model:** `docs/plans/2026-06-09-target-audio-architecture.md`
**Supersedes:** `docs/plans/2026-08-19-tx-authority-architecture.md` (untracked;
returned for rework by its independent review and then overtaken by measurement —
see §0). That document must not be committed; its useful parts are absorbed here
and credited where used.
**Evidence base:** `rigplane-archives/tx-authority-owner-decisions.md`
(the §9 rulings settled on 2026-08-20 and that morning's bench: B1, B2, B3, B6
— it predates Q13/Q14/Q15, which were raised on 2026-08-21, and has not been
updated with them; see §9),
`rigplane-archives/2026-08-19-write-during-tx-measurement.md`
(bench, both radios), `rigplane-archives/2026-08-19-permitted-during-transmit.md`
(manuals, Hamlib, operating literature), `rigplane-archives/2026-08-19-ptt-push-measurement.md`
(radio-pushed TX state: unusable in the shipped configuration),
`rigplane-archives/tx-authority-dossier-2026-08-19.md` (code map). Every
`file:line` citation below was re-verified against `769bfc71` (originally
authored and verified against `fb7a86da`), then the whole
document was re-verified by an independent five-agent pass (two citation
sweeps, an evidence-consistency sweep, and two adversarial mechanism reviews);
the enforcement design was reworked once on that pass's findings. Input-document
claims that did **not** survive verification are in Appendix A.
**Builds on:** MOR-1888 (one decision point), MOR-1881/1882 (rigctld drop +
truthful terminal results), MOR-1884 (seat at `_execute` with one exemption),
MOR-1904 (interim rigctld key bound, merged `769bfc71`), MOR-1905 (Yaesu TX2
inversion — fixed separately, merged `c87c59c3`), frozen ruling
`docs/plans/2026-08-14-state-backed-command-lifecycle.md:40` ("Safety controls …
dedicated TX controller with its own authoritative phases … safety remains
fail-closed").

---

## §0 The argument

Three bench sessions and a documentation sweep changed the question
this subsystem answers. The previous design — and everything shipped today —
gates writes on a boolean: *is the radio transmitting?* The evidence says that
is the wrong quantity:

- **Measured, both bench radios, controlled, with read-back:** the IC-7300 and
  the FTX-1 both **accept and apply a frequency write while keyed**. The FTX-1
  demonstrably has a transmit interlock — it refused mode and split with an
  explicit `?;` in the same battery — and deliberately withheld it from
  frequency. A vendor that has the mechanism and withholds it from one command
  has made a decision about that command.
- **Documented, both vendors:** not a single CAT command is documented as
  unavailable while keyed; neither vendor locks the front panel during
  transmit; both publish numbered procedures that instruct the operator to
  **key up first and then adjust** power, mic gain, compression and monitor
  level. Hamlib blocks mode, split-mode and split-vfo only — it lets frequency
  through, and for a band change it unkeys the rig, waits 200 ms, then writes.
- **The hazard the industry warns about with one voice is not "writing while
  keyed".** It is **a discrete RF-carrying contact changing state while RF is
  present** — antenna relays, tuner bypass relays, band-filter relays.
  Adjusting a continuously variable element under full power is textbook
  practice. Throwing a relay under the same power welds it.
- **Radio-pushed transmit state is unusable** in the shipped single-cable
  configuration (linking to the [REMOTE] bus caps CI-V at 19200 and kills the
  waterfall). Foreign transitions are observable only by polling — at cadence,
  on Icom without source attribution, on Yaesu with it (`TX` P1=2).
- **Operating fact (deliberately unmeasured; see §3.5's accepted residual):** under full break-in,
  "transmitting" is a duty cycle, not a boolean. Any design that samples a
  boolean is wrong roughly half the time under QSK.

So the question a transmit authority must answer is not "are we transmitting"
but **"would this command throw a contact under RF"** — and the answer is
per-radio, because the radios differ in what they even have.

Three consequences, all corrections of the shipped table
(`core/tx_interlock_contract.py:14-105`):

1. `FREQUENCY` is DEFER today and should not be gated at all. With it ungated,
   the MOR-1892 defect class (writes swallowed for up to a poll cadence after an
   unkey) has no member — the causal truth model, boundary ledger and pending
   budgets of the previous ADR were machinery built downstream of one
   misclassified command family. They are not built.
2. `TUNER_OFF` is `ALWAYS_PASS` today (`runtime/tx_interlock.py:366-367`) — our
   most permissive class — and it is **a bypass-relay throw under power**. A
   safety inversion in the dangerous direction.
3. `MODE` and split are refused by the radio itself on Yaesu and accepted on
   Icom. A gate that duplicates a refusal the radio already makes adds latency,
   staleness bugs and a second opinion; the honest design attempts the write and
   reports the radio's own answer.

What survives unchanged in importance: the **key-down bound** (unattended
transmission is a regulatory matter — and the inventory in §1.5 shows the hole
is *worse* than previously recorded), **ownership** of the transmitter, and
**knowing whether we are transmitting** for the UI, the lease, the bound and
audio arming — where recency of tens of milliseconds is sufficient and no
causal machinery is warranted.

The owner's acceptance criteria, verbatim intent: *simple and reliable;
debuggable ("why did it decide that" answerable from data the system already
has); typed, closed input/output contracts; everything superfluous cut, by
name.* And one structural demand: **a filter in front of command ingress, for
all consumers** — web, rigctld, CLI, SDK, the commercial layer; one gate they
cannot go around. §3.2 places that filter where this tree actually lets it be
airtight — inside the backend write layer, below every consumer — and
Appendix A reports honestly which part of the brief's structural premise did
not survive contact with the code.

## §1 Current state

Terse; the dossier holds full detail. Everything below re-verified at
`769bfc71` (§1.6 lists what changed since the authoring base `fb7a86da`).

### 1.1 Policy cores

```
core/tx_interlock_contract.py:14-105   4 dispositions × 17 families, no rigplane imports
runtime/tx_interlock.py (437 LOC)      classify (:354, by concrete dataclass),
                                       evaluate (:400), SetTunerStatus(0)
                                       always-pass (:366-367) / (1|2) BLOCK
                                       (:369-370), tighten-only override raise
                                       (:393), DeferredTxCommandLane (:144-249;
                                       TTL 3.0 s / quiet 1.0 s :153-154)
profiles/rig_loader.py:1682-1734       [tx_interlock] override hook: TX_SAFE→DEFER
                                       only (:1719,:1726); zero shipped TOMLs use it
```

### 1.2 Enforcement seats — and the paths that bypass all of them

| Seat | Site | Coverage note |
|---|---|---|
| Web `_execute` head | `web/radio_poller.py:748-765`, called at `:2257`; immediate-block set `:218-227` (**`PTT_ON` present since #2745**, merged `b3ab76b1` — with `TxInterlockRefusal` at `runtime/tx_interlock.py:80` and a typed `blockedBy`/`reason` failure envelope at `web/server.py:2161-2187`); one `connection_epoch_bootstrap` exemption (`:2256-2257`, `:4024`) | web queue only |
| Web staging lane | `radio_poller.py:824-876` + lane instance `:723` | shadowed by the `_execute` seat (MOR-1884) |
| rigctld DEFER gate | `handler.py:770-832`; 8 call sites (`:1463,1632,1664,2185,2644,2732,2879,2910`); **fail-open with no canonical store** (`:816-820`, flag `:701`); known-TX drop answers `RPRT 0` (`:826-833`) | drop only — no lane (MOR-1881 ruling) |
| rigctld BLOCK pre-gate | `handler.py:462-469`, inside the executor | same fail-open flag |
| Yaesu poller drain / execute | `backends/yaesu_cat/poller.py:678-682` (defers **only at `RfState.TX`**), `:903-907` (raises only for BLOCK or base-TX_SAFE) ⇒ base-DEFER at UNKNOWN executes on FTX-1 | third gate copy; own lane `:138` |
| Web tuner seat | `control.py:1685-1690` inside `_ro_set_tuner_status` (`:1674-1716`) — a **fourth, independently wired** gate call, on the loosest resolver | bypasses the command queue |
| Web CW auto-tune | `control.py:1937-1942` | |
| CLI | **nothing** — `evaluate_tx_interlock` appears nowhere in `cli/`; 25 direct `await radio.set_*` sites (freq `:2727`, mode `:2746`, power `:2772`, antenna `:3179-3194`, tuner `:3271`, …) | ungated |
| SDK | `runtime/sync.py:49-90` `_SyncCommandExecutor` — no gate; also **bypasses the factory** (`sync.py:37,136-145` constructs `IcomRadio` directly) | ungated |
| Web HTTP executor | `web/server.py:398-422` — `set_powerstat` (`:420`) and `raw_civ_transaction` (`:411`) direct on the radio | ungated |
| CW text | web `control.py:1573-1587` and CLI `cli/__init__.py:3090-3093` → `runtime/radio.py:4942-4955` — **keys the transmitter; structurally invisible to the entire interlock framework** (not a typed command family) | ungated |
| rigctld raw `w` | `handler.py:547-561` → `_send_civ_raw` — can key PTT with no interlock, no ownership, no bound | ungated |
| **Backend-internal queue drains** | `web/web_startup.py:126-128` hands the web `CommandQueue` to `radio.create_observation_poller(...)`; `backends/yaesu_cat/radio.py:2691-2704` and `backends/rigctld_client/radio.py:655-666` return pollers **bound to the raw backend** that drain that queue and execute writes on `self.radio` (`yaesu_cat/poller.py:658-659, 892-897` — *"Commands come from the web UI CommandQueue"*; `rigctld_client/radio.py:199-235`) | on FTX-1 and hamlib-provider radios, **web writes enter the backend through a queue handoff, not through `radio.set_*`**; `backends/rigctld_client/` contains zero `tx_interlock` references |

The last row is load-bearing for placement (§3.2): any gate that wraps the
radio *object* from outside never sees the web write path of two shipping
backends, one of them a bench radio.

### 1.3 RF-truth resolvers — six spellings of one question

`web/radio_poller.py:729-746` (`_current_rf_state`) ·
`rigctld/handler.py:735-768` (strictest) · `control.py:1718-1731` (FRESH ∧ bool
only — gates the tuner) · `backends/yaesu_cat/poller.py:168-198` (private;
never reads the shared store) · `radio_poller.py:3702-3711` (meter gating) ·
`rigctld/server.py:573-594` (`_derive_tx_active` → scheduler `tx_only` hint,
`:605`). No Python resolver filters on observation provenance — which is why
the MOR-1900 fabrication was invisible to the strictest one.

### 1.4 Truth stores and producers

Canonical: `global.tx_state.ptt` in the `StateStore`
(`core/state_store.py:38-44` freshness has three members, none causal — and
none needed, per §0; decay solely via `mark_stale_due`, `:806`). Producers: the
CI-V pump (`runtime/_civ_rx.py:2383-2390`; provenance narrowed to
`poll_response` for a directed exact `1C 00` reply, `:2636-2650`; **max-age
hardcoded** at `:101` — 1.0 s), Yaesu observations
(`backends/yaesu_cat/observations.py:328-333`, `source="yaesu_poll_response"`),
rigctld-client (`observations.py:212-218`, `"hamlib_response"`). Laundering
at `769bfc71`: the rigctld `t`-poll mirror **write is gone** — #2759
(`6bdb5846`) deleted it, answering the client from the mirror without
publishing it as canonical and recording a `rigctld_ptt_mirror_fallback`
diagnostic instead (`handler.py:1747-1766`); its `split` twin **survives
unchanged** (`handler.py:2699-2704`, `source="state_poller"`, MOR-1901), as
do the SDK's self-write mint (`runtime/sync.py:92-101`) and the web
legacy-mirror TX rows. Dead truth:
`StateCache.ptt/ptt_ts` (`core/_state_cache.py:78-79`, written at
`_civ_rx.py:1656`, **zero readers**). Yaesu's `set_ptt` **no longer**
self-mutates `_state.ptt` from its own write: MOR-1941 (`a1cf9f48`) deleted
that assignment, and `backends/yaesu_cat/radio.py::set_ptt` now says so in its
own docstring. The two surviving writers of that mirror are the poll parse and
the read-back inside `get_ptt` — correction, 2026-08-21; the paragraph
previously described the pre-`a1cf9f48` tree.

**A read primitive now exists on all three backends** (correction,
2026-08-21 — this paragraph previously said "on two backends only", which was
true at the `769bfc71` re-anchor and stopped being true when row 5 merged).
`read_transmit_state` is declared on the capability protocol
`core/radio_protocol.py::TransmitStateReadable` and implemented on
`runtime/radio.py` (the Icom family), `backends/yaesu_cat/radio.py` and
`backends/rigctld_client/radio.py`, landed by MOR-1914 (`24eac81d`). **Row 5
is discharged.** What remains true of the older description: the `Radio`
protocol itself still has **no PTT read member**
(`core/radio_protocol.py::Radio` declares `set_ptt` and no read — which is
why §3.9 hosts the primitive on a capability instead), the older
`read_ptt`/`get_ptt` pair still exists only on Yaesu and rigctld-client, and
`runtime/radio.py::_request_authoritative_ptt_read` is still private and bound
to the managed-TX machinery neither bench radio arms — the reason row 5 could
not simply reuse it. §3.5 depends on the shipped primitive.

**The Yaesu transmit-truth inversion (MOR-1905) is fixed** (correction,
2026-08-21 — this paragraph previously described the inversion as live).
`c87c59c3` replaced the `bool(result["state"] == "1")` predicate; the raw `TX`
token is now read by `backends/yaesu_cat/radio.py::read_ptt_token` and
interpreted by `_interpret_ptt_token`, which routes it through the profile's
`[tx_policy].tx_state_map` and fails closed (transmitting) with a diagnostic on
an unmapped token, so `TX2` — *the radio is transmitting, keyed by
mic/key/footswitch/VOX* — no longer reads as **receiving**. §3.7 is what makes
this defect class structurally impossible rather than merely corrected, and it
is now the shipped shape rather than a proposal.

### 1.5 Key-down bounds — the corrected inventory

The brief said "five ingresses bound a key five different ways, and two do not
bound it at all." The tree at the authoring base `fb7a86da` was **worse**:
exactly **three** bounding mechanisms, and on the bench radios (both
unmanaged) **seven of nine keying paths with no bound at all**. Since then
MOR-1904 (merged `769bfc71`) added a fourth mechanism and bounded the rigctld
row — six of nine remain unbounded at the re-anchor:

| Keying path | Managed radio | Unmanaged (bench reality) |
|---|---|---|
| Web PttOn | supervisor 180 s (`core/tx_safety.py:25`, ticked by `managed_radio_runtime.py:103-136`) | 180 s, poller's own timer (`radio_poller.py:255-263,1016-1052`, armed at `:2488`) — covers **only keys this poller issued**; the fired OFF is deliberately **enqueued**, so the `PttOff` arm runs audio teardown and identity clearing (`:1016-1033`, MOR-1181/MOR-1013/MOR-1878) |
| rigctld `T 1` | supervisor 180 s | **180 s since MOR-1904** (`769bfc71`): a handler-owned 0.25 s ticker task armed below a successful unmanaged key (`_arm_key_down_backstop` `handler.py:1771-1788`, loop `:1856-1927`, constant imported at `:252`), with a causal RX veto (`_ptt_observed_after` `:1790`); the bound deliberately **outlives the socket** (`release_session_tx` still writes nothing, `:1363-1372`) and server stop cancels it with an honest warning rather than firing it (`server.py:778-786`, `handler.py:1841-1854`) |
| CLI `ptt on` | supervisor 180 s | process lifetime + optional `--for`, no ceiling (`cli/__init__.py:517-528, 2855-2879`) |
| SDK `_execute_ptt` | supervisor 180 s | **none** (`sync.py:375-397`); note the sync facade's event loop runs only inside calls (`run_until_complete`), so no in-process timer can fire between calls |
| CW text (web / CLI) | **none — bypasses the managed gate entirely** | **none** |
| Raw CI-V (web / rigctld) | **none — bypasses ownership entirely** | **none** |
| Raw CI-V (hamlib bridge) | **none** | **none** — `hamlib_bridge.py:304` drives `send_civ_raw_fire_and_forget` (`runtime/radio.py:1959`), reachable from `cli/_validate.py:1059`; the tree's own vocabulary names the door (`TxSource.HAMLIB_BRIDGE`, `core/tx_safety.py:32`) |

Neither bench radio arms the supervisor: `_IcomSerialRadioBase.connect()`
(`backends/_icom_serial_base.py:335-372`) never arms it, and `YaesuCatRadio` /
`RigctldClientRadio` (`yaesu_cat/radio.py:148`, `rigctld_client/radio.py:557`)
have no `CoreRadio` base at all (MOR-1219/MOR-1190).

### 1.6 Merged since authoring (the re-anchor delta)

Authored against `fb7a86da`; three PRs merged between it and `769bfc71`, and
§1 above describes the merged tree:

- **#2745** (`b3ab76b1`, MOR-1879) — web `ptt_on` joined the immediate-block
  set; `TxInterlockRefusal` landed in `runtime/tx_interlock.py:80` (relocated
  out of `web/` for the strict-mypy boundary); the web `failed` envelope
  gained a whitelisted typed shape `{session_id, blockedBy, reason}` with two
  reason codes (`web/server.py:2161-2187`). Recorded honestly: the PR body's
  own **"HOLD for bench" merge gate has no on-record discharge** — every
  review comment reiterates the hold, no comment records the measurement, and
  the owner merged it three days after the last review.
- **#2759** (`6bdb5846`, MOR-1900) — the `t`-poll mirror launder deleted
  (§1.4).
- **#2760** (`769bfc71`, MOR-1904) — the rigctld key-down backstop shipped
  (§1.5). Note for row 12b: the shipped driver is a **handler-owned
  self-scheduled task**, not the server drain loop the ticket had mooted.

MOR-1905 — **shipped** (`c87c59c3`, #2761), correction 2026-08-21: this line
previously read "still Backlog, unstarted: the Yaesu predicate is unchanged",
which was true at the re-anchor and false from `c87c59c3` onward. The predicate
is gone; the Yaesu parse is `read_ptt_token` + `_interpret_ptt_token` over the
profile's `tx_state_map` (§1.4). Row 0c is discharged.

**Merged since the re-anchor** (added 2026-08-21, because §1–§2 above were
written against `769bfc71` and the design has since started shipping).
**Selection rule, stated so the list can be checked and so it is not mistaken
for a curated one: every commit in `769bfc71..bbd2ac3d`, none omitted** (a
fixed end ref, not `..main`, which would silently stop meaning this list the
next time anything merges) — the
sibling list above is exhaustive over its own range, and a list that looks
exhaustive beside one that is had better be. That is eighteen commits at the
time of writing, plus the change carrying this list; grouped below by what
each means for this document, not by merge order:

- **This document landing:** `5239f561` (#2762).
- **Defect fixes in the same subsystem, ahead of the rows:** MOR-1879
  follow-up `377b2e5d` (#2764 — `main` was red after `b3ab76b1`; the premise
  the gated `ptt_on` needs, now stated in the tests), MOR-1905 `c87c59c3`
  (#2761, §1.4/§1.6 above), MOR-1906 `10781d65` (#2763 — unconfirmed-RF
  honesty and a swallowed unkey), MOR-1903 `af352781` (#2765 — a
  client-gated CI-V PTT re-read for standalone rigctld, which has no cadence
  poller). **Unchecked by this change:** MOR-1903 is characterised in §3.3 and
  §7 as an outside concern about five profiles that never poll PTT; whether
  that characterisation still fits what `af352781` shipped was flagged as a
  possible drift by the 2026-08-21 audit, was not resolved there, and is not
  resolved here.
- **Reclassification:** MOR-1940 `9fc90943` (#2766 — FREQUENCY/RIT_XIT to
  TX_SAFE; §2 P1, §3.8, §4 preamble, rows 9/10, R4).
- **Rows 1–6, in row order:** MOR-1911 `9a05ecb0` (#2767, row 3b), MOR-1910
  `94168f4e` (#2769, row 2), MOR-1909 `67fbcbea` (#2770, row 1), MOR-1912
  `ad89d10c` (#2771, row 3a), MOR-1913 `07d40580` (#2772, row 4), MOR-1941
  `a1cf9f48` (#2773, row 6), MOR-1914 `24eac81d` (#2774, row 5).
- **Follow-ups to those rows:** MOR-1947 `33947560` (#2775 — `[tx_policy]`
  data for the six unmeasured rigs, extending row 3a), MOR-1953 `cf3c4cf0`
  (#2776 — **two honesty gaps left behind by row 5**, raised by that row's
  reviewer and deferred to their own row; row 5 is marked DONE below and this
  is what came after it), MOR-1954 `dbeb4511` (#2778 — the T5
  argument-predicate short-circuit, §3.3/T5/INV-6).
- **Corrections to this document:** MOR-1945 `8ffacc6a` (#2777) and the change
  carrying this list.
- **Repo process, not this subsystem:** `bbd2ac3d` (#2780 — the standing
  prose-claim check).

Citations elsewhere in this document are still anchored at `769bfc71` unless
the sentence says otherwise.

## §2 Problems

**S1** = produced live or shipped defects · **S2** = debt that keeps producing
S1s · **S3** = smell.

- **P1 (S1) — The gate protects the wrong families and misses the right one.**
  Correction, 2026-08-21: the frequency half of this problem **has since been
  fixed**. MOR-1940 (`9fc90943`) moved `FREQUENCY` and `RIT_XIT` to `TX_SAFE`
  in `core/tx_interlock_contract.py` and out of
  `runtime/tx_interlock.py::_DEFER_TYPES`, so neither is gated or dropped on
  any surface today. What remains of P1 is the other half, unchanged and still
  live: `tuner-off` — a bypass-relay throw under power — is `ALWAYS_PASS`
  (`core/tx_interlock_contract.py`, `TUNER_OFF`), i.e. our most permissive
  class, while mode, band, vfo-select, vfo-topology and memory are still
  DEFER. The shipped classification still inverts the real hazard axis; it now
  does so in one direction rather than two.
- **P2 (S1) — Whole write surfaces bypass every gate.** CLI (25 sites), SDK,
  web HTTP executor, CW text (which *keys the transmitter*), raw CI-V — and,
  structurally worst, the backend-internal queue drains (§1.2 last row): on
  FTX-1 the web write path itself enters the backend below any seat an upper
  layer could add.
- **P3 (S1) — Six of nine keying paths have no key-down bound** on the bench
  radios (§1.5; seven of nine at the authoring base — MOR-1904 has since
  bounded the rigctld row). The regulatory requirement is held together by
  one web timer, one rigctld backstop task and the CLI's process lifetime —
  three private mechanisms where the design ships one.
- **P4 (S1) — Transmit truth can be fabricated or inverted and no gate can
  tell.** The mirror launders (MOR-1900 — fixed by #2759 since authoring;
  its `split` twin MOR-1901 still live), the SDK self-write mint, the Yaesu
  `TX2`→receiving inversion (MOR-1905 — **fixed since**, `c87c59c3`; correction
  2026-08-21, this bullet previously read "still open") — no Python consumer
  filters on provenance, and RX could be produced by omission (`== "1"`) on the
  path MOR-1905 closed. The provenance gap and the SDK mint are what remain.
- **P5 (S2) — Six RF resolvers, four gate copies, two lanes, five truth
  stores** for one question. The loosest resolver gates the only genuinely
  hazardous actuator the web exposes (the tuner).
- **P6 (S2) — Fail directions fell out of construction order.** rigctld is
  fail-open without a canonical store (`handler.py:816-820`); Yaesu executes
  base-DEFER at UNKNOWN; web fails closed; CLI has no direction at all.
- **P7 (S2) — The gate depends on cached truth it does not control.** Every
  seat samples a store whose freshness is a poll-cadence artifact (hardcoded
  1.0 s for CI-V at `_civ_rx.py:101`; 8.0 s profile default on FTX-1), then
  argues about staleness. For rare hazard commands the honest move — ask the
  radio now — was never on the table, and on the Icom family it *could not*
  be: at the time this problem was written there was no read primitive
  (§1.4 — row 5 has since supplied one on all three backends, `24eac81d`).
  No shipped seat consults it: nothing in `src/` constructs a
  `TransmitAuthority` yet, which is rows 7/8.
- **P8 (S3) — Refusal vocabulary is five unrelated dialects** (English prose,
  a hamlib errno, a whitelisted `heldBy/reason` dict at `web/server.py:2126-2141`,
  9 TS ineligibility codes, 7 `KeyBlockedReason` values), none carrying the
  evidence a debugging operator needs.

## §3 Target architecture

### 3.1 Tenets

- **T1 — Gate the hazard, not the transmission.** Only the four
  owner-ruled hazard families (band, tuner, antenna, VFO select — §3.3) and
  the keying commands are gated on transmit truth. Everything the
  manufacturers permit passes without consulting truth at all. *Prevents
  P1.*
- **T2 — One authority, below every consumer.** The gate lives inside the
  backend write layer — the last typed hop before the transport — so every
  ingress, including the backend-internal queue drains, passes it. Ingresses
  may only add strictness on top, never subtract it. *Prevents P2.*
- **T3 — For a hazard, ask the radio now.** A relay throw is rare and slow;
  one solicited PTT read (~one command round-trip) before it is cheap,
  eliminates every staleness argument, and works on radios that never poll
  PTT. Cached truth is for display and UX, never for a hazard decision.
  *Prevents P7 and deletes the freshness gymnastics.*
- **T4 — Only the radio is evidence of *receiving*; our own commands are
  evidence of *transmitting*.** Truth consumers accept only radio-readback
  provenance; a transmit-state wire value maps to RX only through an
  explicit per-radio table entry — anything unmapped is *not receiving*; the
  solicited read must satisfy the directed-exact-reply discipline. And when
  rigplane itself started the transmission (a key, a CW message, a tune),
  the authority holds "transmitting" regardless of the radio's report —
  forced by B6, where the radio reported receiving mid-CW. The asymmetry is
  the point: own-write inference may only ever tighten. *Prevents P4 by
  construction.*
- **T5 — A de-key is never made harder.** The UNKEY dispatch has no refusal
  branch; PTT-off / power-off / `stop_cw_text` short-circuit ahead of all
  classification and profile data; teardown stays biased toward OFF. The same
  short-circuit owns the *other* direction of that pair (added by MOR-1954):
  an admission of `set_ptt` / `set_powerstat` whose argument the engine cannot
  read is resolved there to the strict twin — PTT_ON (KEYING, a branch with no
  refusal path) or POWER_ON (PASS) — instead of being left to the table, so no
  table can make an unreadable one refuse either, and no key-down can hide in
  the unkey branch because of how its argument was spelled. A *readable*
  key-down is not short-circuited: it goes to the table like any other write.
  *(Doctrine preserved from `managed_tx_ingress.py:1-21` and
  `tx_interlock.py`.)*
- **T6 — One deadline, every ingress; the OFF rides the right rails.** Every
  key admitted through the authority arms the same named deadline; the expiry
  is executed by the delivery that can do it properly (the web enqueues its
  `PttOff` so audio teardown and identity clearing run — MOR-1181/1013/1878),
  with a direct backend unkey only as last resort. *Closes the rigctld, SDK
  (while its loop runs) and web-CW rows of P3; the CLI's process-lifetime
  bound and the raw paths remain named residuals.*
- **T7 — Decisions carry their evidence.** Every refusal states what was
  observed, how old it was, where it came from, and whether the condition is
  bounded — so "why did it decide that" is answered by the decision itself,
  and each client renders the same decision its own way. *Prevents P8.*

### 3.2 Ownership and placement — the filter

**One component:** `TransmitAuthority` — one instance per radio, constructed by
the backend at connect, owning classification, the hazard gate, keying
attribution, the key-down deadline, and a decision log. It performs no I/O of
its own; the backend injects two callables (the solicited PTT read and the
last-resort unkey) and drives `poll(now)` from its existing loop — the exact
pattern `TxSafetySupervisor` + `managed_radio_runtime._tick_loop` already ship.

**One enforcement point per write:** the **admission on every gated backend
write method** — the last typed hop before the transport — carried in either
of the two forms INV-2 accepts (reformulated by owner ruling 2026-08-21): the
bare `@tx_admit` decorator on the method, or the
`async with self._tx_authority.admit(...)` block as the first awaited
statement of the body, wrapping the write. `TransmitAuthority.admit` is an
async context manager and the lock spans the write
(`core/tx_authority.py: TransmitAuthority.admit`), so the in-body form is a `with` block, not a
bare `await`. The decorator is the expected form across the large Icom write
surface (row 7, ~100 methods); the in-body form is for the methods whose body
needs the yielded `TxAdmission` (`core/tx_authority.py: TxAdmission`) in scope.

```
┌──────────────────────────────────────────────────────────────────────┐
│ CONSUMERS   web · rigctld · CLI · SDK · validation · Pro             │
│             call radio.set_* / queue commands; render TxRefusal      │
├──────────────────────────────────────────────────────────────────────┤
│ BACKEND WRITE LAYER (runtime/radio.py · yaesu_cat · rigctld_client)  │
│   each method in the pinned per-backend GATED map carries the        │
│   admission, in one of the two forms INV-2 accepts:                  │
│       @tx_admit               ← bare, outermost decorator            │
│       async def set_antenna_1(self, ...) -> None: ...                │
│   or, when the body needs the TxAdmission in scope:                  │
│       async with self._tx_authority.admit(name, args, kwargs):       │
│           ...the write...                                            │
│   — including the methods the backend-internal queue drains call —   │
│   so web, rigctld, CLI, SDK, CW, HTTP and routing paths all pass it  │
├──────────────────────────────────────────────────────────────────────┤
│ TransmitAuthority (engine in core/tx_authority.py)                   │
│     PASS   → return immediately (no truth consulted — invariant)     │
│     HAZARD → own-transmit hold check, then solicited RX read;        │
│              TX or unknown → TxRefusal, no sub-cases (owner rule)    │
│              (set_freq only: band relation first, from profile data) │
│     KEYING → attribution + own-transmit hold + arm the one deadline  │
│     UNKEY  → allow unconditionally, clear the deadline               │
│   every non-PASS admission appends a TxDecisionRecord                │
├──────────────────────────────────────────────────────────────────────┤
│ TRUTH SUPPLY  read_transmit_state() — NEW TransmitStateReadable (row 5):│
│   Yaesu/rigctld-client adapt their existing reads; Icom implements   │
│   it over the directed-exact-reply discipline (_civ_rx.py:2636-2650) │
└──────────────────────────────────────────────────────────────────────┘
```

**Why in-backend, not a wrapping facade.** The first draft of this ADR placed
the gate in a `GuardedRadio` facade returned by the factory; its adversarial
review demonstrated the facade cannot work in this tree, and the finding is
worth preserving because it constrains every future design here:

1. **Runtime-checkable `isinstance` resolves via `getattr_static` on Python
   3.12/3.13** (CPython gh-102433; the repo documents it at
   `managed_tx_ingress.py:54`), so a `__getattr__`-delegating wrapper answers
   *False* to ~60 capability probes across `web_startup`, `runtime_helpers`,
   rigctld store resolution (`handler.py:699,714` — turning the current gates
   silently fail-open), and routing — while `quick.yml` runs 3.11 only, so
   every PR would stay green and the breakage would surface in the Mon/Wed/Fri
   full matrix.
2. **The web write path on FTX-1 and hamlib-provider radios never touches the
   radio object from outside**: `web_startup.py:126-128` hands the
   `CommandQueue` into `create_observation_poller`, and the backend builds the
   poller bound to raw `self` (§1.2 last row). No outer wrapper sees those
   writes.
3. Concrete introspection breaks under delegation in at least four more ways
   found in the tree: the web TX-audio resolver walks `type(owner).__mro__`
   and compares `__self__` (`web/handlers/audio.py:135-152`); `async with
   radio:` needs type-level dunders (`cli/__init__.py:1925`); two sites probe
   `radio.__dict__` directly (`web/radio_poller.py:684`,
   `rigctld/server.py:328`); and `rigplane.IcomRadio` is a public export
   (`src/rigplane/__init__.py:112,352`) that hands out unwrapped instances.

Placing the admission **on** the backend write methods (either INV-2 form —
the axis here is in-backend versus wrapping facade, not decorator versus
in-body) dissolves all of this: no delegation, no identity change, the queue-draining pollers call the
now-gated methods on `self.radio` (verified: both drain dispatchers use only
public backend methods — `yaesu_cat/poller.py:965-1132`,
`rigctld_client/radio.py:319-362`), and even a directly constructed
`rigplane.IcomRadio` is gated. The cost, stated honestly: completeness is held
by a **table plus two small source-level pins** (INV-1/INV-2 — the same house
pattern as the MOR-1884 bootstrap-exemption pin), not by construction alone.
The brief hoped structure would make any completeness test unnecessary; that
premise does not survive this tree (Appendix A item **2** — corrected from
"item 5", 2026-08-21: item 2 is the completeness-premise item; item 5 is the
PTT-read one), and a two-pin totality test is the honest minimum under any
placement.

**The name-space of completeness is the backend, not the protocol.** The
gated families live partly on capability protocols (`set_powerstat` on
`PowerControlCapable`, `set_split` on `SplitCapable`, the VFO trio on
`VfoSlotCapable`, antennas on `AntennaControlCapable`, CW on
`CwControlCapable`, tuner/band on `SystemControlCapable`, `memory_to_vfo` on
`MemoryCapable` — only freq/mode/ptt are on `Radio` itself), and the backends
add write methods of their own: Yaesu ships **29** backend-only writes
(corrected from 25, 2026-08-21; the count now travels with the rule that
produces it, because a bare number is what went wrong the first time). **The
rule:** public `async def` members of `YaesuCatRadio` that appear on no
protocol in `core/radio_protocol.py`, minus `get_*`/`read_*`. That yields 29
at both `769bfc71` and `dbeb4511`, five of them carrying no `set_`/`send_`
prefix at all — `band_down`, `band_up`, `reset_clarifier`, `vfo_a_to_b`,
`vfo_b_to_a` — and those five are inside the count, not excluded from it,
including two the web queue actually uses for gated families — `set_tuner`
(`yaesu_cat/radio.py:2252`; the protocol's `set_tuner_status` at `:2411-2413`
is a pure alias onto it, and the poller dispatch calls `set_tuner` directly,
`poller.py:1099-1100`) and `set_vfo_select` (`:1697`; the `SelectVfo` arm at
`poller.py:975` calls it at `:991`) — plus writes a `set_*`/`send_*` prefix
rule would miss entirely: `reset_clarifier` (`:2293`), the scope writes
(`enable_scope`/`disable_scope`, `runtime/_scope_runtime.py:124,203`), the
audio-TX members (`start_tx`/`push_tx`, `runtime/_audio_runtime_mixin.py:99`,
`backends/_icom_serial_base.py:717`), and the entire §3.3 vfo-select family
(`swap_vfo_ab`/`equalize_vfo_ab`/`equalize_main_sub`/`memory_to_vfo`).
Therefore the totality base (INV-1) is **deny-by-default**: every public
`async def` member of `Radio`, of every capability protocol, and of each
backend class, **minus** a pinned non-write allow-list (reads, lifecycle,
audio-RX subscriptions) and a pinned raw exclusion list (§3.3) — never a
name-prefix match. And every **alias chain carries exactly one admission, at
the innermost named body** (`set_tuner_status → set_tuner` admits once, in
`set_tuner`; on Icom the vfo admissions sit in the outer methods themselves —
`equalize_main_sub` (`runtime/_dual_rx_runtime.py:328`), `swap_vfo_ab`
(`:347`), `equalize_vfo_ab` (`:378`), `swap_main_sub` (`:309`),
`select_receiver` (`:447`), and `_set_vfo_slot_impl` (`:545`, the
innermost named body of the `set_vfo_slot`/`_set_vfo_slot_confirmed` alias
pair, `:525`/`:540`)) — otherwise a HAZARD write pays two solicited reads and
logs two decisions. (Correction, 2026-08-21: `swap_main_sub` and
`select_receiver` were missing from this list. They are vfo-select writes —
`swap_main_sub` builds and sends its own `_CMD_VFO` frame through
`_send_civ_raw` without touching `_set_vfo_wire`, and `select_receiver` issues
MAIN/SUB select through `_set_vfo_wire` — and an owner ruling of 2026-08-21
put them in the hazard family, closing Q13 (that ruling postdates the §9
record and is not in it; provenance at Q13). See §3.3.)

**A shared command template is not an alias chain** (added 2026-08-21, after a
revision of this document asserted one that does not exist). The alias-chain
rule above says "one admission at the innermost named body", and the canonical
example is real: `set_tuner_status` (`backends/yaesu_cat/radio.py`) has the
body `await self.set_tuner(value)` — **method** delegation, one wire write, one
correct seat. But the Yaesu backend also writes through a template registry:
`self._write(name, **kw)` takes a **CAT command-spec name**, looks up
`spec.write` and formats it. That name is drawn from the same vocabulary as the
method names and frequently equals one, so two bodies can emit the identical
wire command with **no call between them**. To a skimming eye the two
mechanisms are indistinguishable; to an admission they are opposite.

The instance that matters, because the family is HAZARD and row 8 seats there:
**three separate Yaesu bodies each emit `VS` via `self._write("set_vfo_select",
…)` and none calls another** — the method `set_vfo_select` itself, plus
`select_receiver` and `set_vfo_slot`. Seating one admission at `set_vfo_select`
covers exactly one of the three; `select_receiver` alone is reached from
`web/radio_poller.py: RadioPoller._execute`,
`rigctld/handler.py: RigctldHandler._execute_set_vfo`,
`runtime/profiles_runtime.py: _apply_vfo` (twice) and `set_cross_band_split`.
Each body needs
its own map entry. The same shape holds for `set_tx_source` (template
`set_tx_func`) and `set_key_speed` (template `set_keyer_speed`), whose spec
names are likewise other public methods. Counted over `YaesuCatRadio`, 37
method bodies call `_write`/`_query` with a spec name that is also a method of
the same class and is not the enclosing method: 4 writes (the three above plus
`select_receiver`) and 33 reads — 29 of the form `read_* → get_*`, plus
`get_active_receiver`, `get_manual_notch`, `get_tx_source` and `get_vfo_slot`,
whose own names already start `get_` — the reads mattering only for keeping
INV-1's non-write allow-list honest.

**Rule for rows 7/8:** an alias chain is one method body `await`ing another
method. A body that calls `self._write("X", …)` is **not** chained to the
method named `X`, however identical the wire result — check the call, not the
name. Getting this backwards leaves a ruled HAZARD member silently ungated,
which is the same defect class as this document's own "Icom has no `set_band`".

**A public alias is a category, not a footnote.** `runtime/radio.py` ends in a
backward-compatibility alias block that binds old names to the same function
objects: `set_frequency = set_freq`, `set_power = set_rf_power`,
`set_band_stack = set_bsr`, `set_band = set_bsr`, `start_scan = scan_start`,
`stop_scan = scan_stop`, plus the read aliases. These have **no `async def` of
their own**, so they are invisible to any pin that looks for a definition, yet
`inspect.getattr_static(CoreRadio, "set_band")` resolves to the coroutine
`set_bsr` and a consumer calling `radio.set_band(...)` reaches a real write
body — with the alias target's signature, not the protocol's (§3.3). Row 7's
map must therefore enumerate public aliases as a class of its
own: for each, either the alias name carries a map entry resolving to the same
family as its target, or the pin must state that an alias is covered by the
admission on its target body. Which of the two INV-1 enforces decides whether
its enumeration may be written against source definitions at all — an
AST-level walk cannot see a name that has no `def`. This
is structurally the same trap as `_set_vfo_slot_impl` (below), pointing the
other way: there the admission must go *under* the public name, here there is
no `def` under the public name at all.

**`_set_vfo_wire` is not that chokepoint** (correction, 2026-08-21: an earlier
revision of this section named it as one). It is neither necessary nor
sufficient. *Under-covering:* `_set_vfo_slot_impl` reaches it only under
`if self.receiver_count > 1` and emits directly on the `else` branch
(`_dual_rx_runtime.py:575-582`); `swap_vfo_ab` and `equalize_vfo_ab` guard it
with `if self._profile.receiver_count > 1` (`:370-372`, `:395-397`);
`equalize_main_sub` never calls it on **any** profile (`:328-345`); and
`rigs/ic7300.toml:16` declares `receiver_count = 1`, so on the bench radio all
of those bypass it entirely. Even on a dual-receiver rig
`_run_with_receiver_vfo_fallback` calls it only when `current != target`
(`:114`), so an already-active receiver produces no call at all.
*Over-covering:* it is the shared per-receiver dispatch —
`_run_with_receiver_vfo_fallback` has 13 call sites in `runtime/radio.py`, of
which **five are reads** (`get_filter_width:2332`, `get_repeater_tone:4410`,
`get_repeater_tsql:4488`, `get_tone_freq:4561`, `get_tsql_freq:4629`) and the
remaining eight are PASS-class writes (`set_freq:2138`, `set_filter_width:2268`,
`set_mode:2412`, `set_data_mode:2461`, and the four repeater/tone setters). An
admission there would gate reads that INV-1 pins to the non-write allow-list
and would make PASS writes consult transmit truth, which INV-3 forbids
outright. The method's own docstring (`runtime/radio.py:3948-3954`) enumerates
only `select_receiver`, `_run_with_receiver_vfo_fallback`, `swap_vfo_ab` and
`equalize_vfo_ab`.
One residual follows from placing the admissions outside and is named rather
than hidden: the cross-module profile-restore path calls `_set_vfo_wire`
directly (`runtime/radio_state_snapshot.py:105`) and no outer-method admission
covers it.

**Layering.** `core/tx_authority.py` — vocabulary, typed contracts, the
classification table, and the engine — has no rigplane imports (the
`tx_interlock_contract` precedent) and classifies by **method name plus
argument predicate**, so the previous ADR's 1119-LOC `_poller_types`
relocation is still not needed. The backends (`runtime/radio.py`,
`backends/yaesu_cat/`, `backends/rigctld_client/`) import `core` — already
legal; no `.importlinter` change, no new exemption. `core/LAYER.md:51-52`'s
no-stateful-singletons rule is satisfied the way `TxSafetySupervisor`
satisfies it: a per-radio object constructed by its backend, no module state.
Profile policy (`[tx_policy]`) reaches the authority at construction — the
backend already owns its profile; rigctld imports nothing new.

### 3.3 The classification

One closed vocabulary of **write classes**, replacing the four dispositions:

```python
class TxWriteClass(StrEnum):
    PASS   = "pass"    # manufacturer-permitted or RF-unrelated; truth never consulted
    HAZARD = "hazard"  # the owner's four-family rule: refused while transmitting
                       # or while transmit state is unknown
    KEYING = "keying"  # induces transmission (ptt-on, cw-text)
    UNKEY  = "unkey"   # ends transmission; never refused
```

**The hazard classification is one owner rule, four families, no sub-cases**
(ruling 2026-08-20, `rigplane-archives/tx-authority-owner-decisions.md`):
**band change, the tuner family in its entirety, antenna switching, and VFO
select/swap are refused while transmitting or while transmit state is
unknown.** This replaces the earlier per-sub-family treatment — no
tune-start-versus-bypass-throw split, no band-relation computation on the VFO
path, and no `tuner-off` in a permissive class. The evidence behind the rule:
B1 measured the IC-7300 accepting a band change while keyed and **audibly
throwing its band-filter relays at operating power** — the radio provides no
protection of its own; neither reference implementation protects these
families (wfview has no write-during-transmit interlock at all; Hamlib guards
only mode and the two split calls and has no PTT guard on `set_ant` or the
tuner); the industry closed the hazard in hardware (PTT-sense lines on
external switches), never in rig-control software; and the cost is nil,
because changing band, engaging a tuner or switching antennas in the middle
of one's own transmission is not an operating practice. We are deliberately
stricter than both reference implementations — stricter than wfview
everywhere, stricter than Hamlib on band change — and that is the intended
position.

Classification is a total function `(method_name, arguments) → (family,
class)`. It must be argument-aware — the same method carries two classes in
three places (`set_ptt(True)`=KEYING vs `(False)`=UNKEY; `set_freq`
in-band=PASS vs resolved-cross-band=HAZARD;
`set_powerstat(False)`=PASS-with-short-circuit) — so the classification is
two pinned literals: a neutral **family → class** table in
`core/tx_authority.py`, and a **per-backend method-name → family map**
(one explicit literal per backend class, covering its real write surface
including its backend-only names and marking alias chains), each entry
optionally carrying a named, pure argument predicate. Both literals are
pinned, never computed. Totality (INV-1) is asserted per backend: every
write-capable member of `Radio`, of every capability protocol, and of the
backend class itself appears in that backend's map — PASS is an explicit
entry, never a fall-through default.

The default table, with its evidence:

| Family | Methods (representative) | Class (default) | Evidence |
|---|---|---|---|
| frequency | `set_freq` (in-band), `set_rit_frequency`, `set_rit_tx_status` (RIT/XIT — **B3, closed: measured** accepted during transmit, confirmed by read-back) | **PASS** | measured applied on both radios; WSJT-X Doppler/"Fake It"; FTX-1 has an interlock and withheld it from frequency; RIT is receive-only by definition (IC-7300 manual p.4-3); XIT is a bounded in-band TX offset |
| mode | `set_mode`, `set_data_mode` | **PASS** (+ per-radio `refused_during_tx`) | IC-7300 accepts+applies; FTX-1 refuses itself with `?;` — do not duplicate the radio's refusal, report it (§3.8) |
| vfo-topology (split) | `set_split`, `set_dual_watch`, quick-split family | **PASS** (+ per-radio `refused_during_tx`) | same asymmetry, measured |
| levels / TX chain | power, mic gain, compressor, monitor, TX bandwidth | **PASS** | the best-documented category in the study: both vendors publish key-up-then-adjust procedures |
| RX path / DSP / meters / memory-write / scan-stop / scan-start / power-on / `stop_cw_text` | everything else, each an explicit table entry (INV-1) | **PASS** | receive-side or no-op on the transmitted signal; scan-start declassified from BLOCK — no documented hazard (owner sign-off Q5); `stop_cw_text` joins the T5 short-circuit set |
| band change | `set_freq` **only when both current and target resolve to declared bands and differ** (gap/unknown → PASS — see the band-relation rules below); `set_band` (declared on `core/radio_protocol.py::SystemControlCapable`; **bound** on both families, **conformingly implemented on one** — correction, 2026-08-21, this cell previously read "implemented only on Yaesu", which is false about the binding; "implemented on both" would be false about the implementation. Yaesu defines its own `async def set_band(band, receiver=0)`. The Icom family only *binds* the name: `set_band = set_bsr` in `runtime/radio.py`'s backward-compat alias block, so `inspect.getattr_static(CoreRadio, "set_band")` resolves to the coroutine `set_bsr` and a caller reaches a real band-select write — but not through the protocol's signature. The protocol declares `set_band(band_code: int)`; `set_bsr` takes a `BandStackRegister`, so a protocol-shaped `set_band(5)` reaches the body and dies on `bsr.band` with `AttributeError` (verified by call). **This is a live defect as well as a gating question, and it is filed separately** — for this document the point is only that the name is on the Icom class, reaches a write body, and therefore needs a map entry; do not read this cell as saying Icom conforms. Separately, the web UI's `SetBand` *command* is composed in the poller from a band-stack-register read plus `set_freq`/`set_mode`, `radio_poller.py:2839` — it does not call `radio.set_band`, so that path's gating lands on the composed `set_freq` admission, best-effort. Both need covering: the composed command path **and** the `set_band` method itself); `memory_to_vfo` and `set_memory_mode` (recall = band+mode+freq, target unknowable → hazard by design) | **HAZARD** | **B1, closed: measured** — accepted, applied, band-filter relays audibly thrown under RF; the radio does not protect itself. Hamlib's unkey-then-write convenience is deliberately not adopted (Q2: refuse, never auto-unkey) |
| vfo-select | `set_vfo_slot`, `swap_vfo_ab`, `equalize_vfo_ab`, `equalize_main_sub`, **`swap_main_sub`** (`runtime/_dual_rx_runtime.py:309-326`) and **`select_receiver`** (`:447-471`) — six members, not four (correction, 2026-08-21: the last two were listed here as unclassified and omitted from row 7 entirely; an owner ruling of 2026-08-21 put them in this family, closing Q13 — a ruling that postdates the §9 record and is not in it; provenance at Q13). The first three are on `VfoSlotCapable`, `swap_main_sub`/`equalize_main_sub` on `DualReceiverCapable`, `select_receiver` on `ReceiverBankCapable`; plus Yaesu's backend-only `set_vfo_select` (`yaesu_cat/radio.py`, the name its own poller dispatch calls). Wire-equivalence is the reason the ruling is not arbitrary: `swap_main_sub` sends the same `_CMD_VFO` frame as its already-classified twin `equalize_main_sub` and reaches it through `_send_civ_raw`, not `_set_vfo_wire`; `select_receiver` issues MAIN/SUB select through `_set_vfo_wire`, indistinguishable on the wire from `set_vfo_slot`. **Still open, and deliberately not classified here:** `set_bsr` (`core/radio_protocol.py::MemoryCapable`, implemented on both backends — and on Icom it is the *same function object* as `set_band`, so one admission covers both names there, while Yaesu defines the two separately), `set_tx_source` and `set_cross_band_split` (`TransceiverBankCapable`, Yaesu-only bodies — and **a chain, which is why they must be classified together**: `set_cross_band_split` emits `FR00;` via `set_rx_func(0)`, then calls `select_receiver(rx_xcvr)` — itself a member of this now-ruled HAZARD family, and on Yaesu **a sibling of `set_vfo_select`, not an alias chain onto it** (correction, 2026-08-21: an earlier revision of this clause said it delegates to `set_vfo_select`; it does not — see §3.2, "A shared command template is not an alias chain") — and only then `set_tx_source(tx_xcvr)`. So an admission seated on `set_tx_source` alone fires with **two frames already on the wire**, the second of them taken under its own nested HAZARD admission. Same shape as the `_set_vfo_slot_impl` trap row 7 spells out; whoever classifies these must say where the admission sits, and whether the outer call is one decision or three), and Yaesu's backend-only `vfo_a_to_b` / `vfo_b_to_a`. Their class is an owner decision; INV-1's totality test fails on them until it is taken | **HAZARD** | joined the set by ruling (Q11, closing it without B8): a same-band swap is harmless, but telling it from a cross-band one would cost an extra read and an extra branch for the *other* VFO, and nobody swaps VFOs mid-transmission; neither reference implementation issues a VFO exchange during TX from its own code (`newcat.c:1948`, `icom.c:922` — avoidance comments in their backends, not an interlock for clients, so this contradicts nothing in the stricter-than-both position) |
| antenna | `set_antenna_1/2`, `set_rx_antenna_ant1/2` | **HAZARD** | relay welding is ~9-10 % of failures (W8JI); every switch vendor forbids hot switching |
| tuner (entire family) | `set_tuner_status(0|1|2)`; on Yaesu the admission sits on the backend-only method `set_tuner` (`radio.py:2252` — the alias-chain rule, §3.2; which method, not which INV-2 form). An **admitted** tune start (`2`, at confirmed RX) additionally records an own-transmit hold and arms the deadline (§3.6/§3.7 — the tune is a transmission we asked for) | **HAZARD** | `0`/`1` are bypass-relay throws under power (today's `ALWAYS_PASS`/BLOCK split inverted the hazard); `2` while keyed is refused permanently by the rule (Q4 — B2 is now informational: the radio accepted a stacked tune start, and both radios drop to minimum power before a tune cycle, operator knowledge found in no manufacturer source) |
| ptt-on | `set_ptt(True)` | **KEYING** | attribution + the deadline (§3.6) |
| cw text | `send_cw_text` | **KEYING** | the one write Icom explicitly documents as acted on during TX; at RX it keys the rig — it records an own-transmit hold for its computed duration and arms the deadline (§3.6; the message is atomic by contract — Q8, dissolved) |
| ptt-off, power-off | `set_ptt(False)`, `set_powerstat(False)` | **UNKEY** / **PASS** | structurally ahead of all tables (T5). The same short-circuit also answers the pair when the argument cannot be read, and answers it the strict way — PTT_ON / POWER_ON, never the off twin (MOR-1954); a readable key-down goes to the table as usual |
| raw | a **pinned by-name exclusion list** `RAW_EXCLUDED = frozenset({"send_civ", "send_civ_transaction", "send_civ_raw_fire_and_forget"})` (protocol `send_civ` at `radio_protocol.py:1270` — called from `web/radio_poller.py:2066,2276`, `web/handlers/audio.py:1057,1690`, `rigctld/server.py:476,485,488`; the fire-and-forget at `runtime/radio.py:1959` — driven by `hamlib_bridge.py:304`), plus the rigctld raw `w` (`getattr`-fetched `_send_civ_raw`, `handler.py:547-561`, below the typed layer) | **excluded, by name** — INV-1's totality test covers map ∪ exclusion list, so an unmapped method can never hide as "raw" | bytes cannot be classified, so the authority does not classify them — but the **shipped raw-during-TX refusal is retained as ingress-side strictness** (Q12): today `RAW_CIV` is BLOCK (`tx_interlock_contract.py:73`) and enforced at the web immediate-block seat (`radio_poller.py:219`, covering `send_civ` at `:2271-2281`) and the rigctld executor pre-gate (`handler.py:462-469`); rows 9/10 rewire both checks to `TransmitTruth` instead of silently deleting them. The authorization/read-only gap stays its own ticket |

**Where the data lives.** A new profile section `[tx_policy]`, carrying only
measured per-radio facts — no speculative hooks (the unused `[tx_interlock]`
override machinery is deleted, not re-shipped; a tighten-only override key can
be added the day a rig needs one):

```toml
[tx_policy]
# families the radio refuses itself while keyed (measured, per radio)
refused_during_tx = ["mode", "vfo-topology"]        # ftx1; empty for ic7300
# positive transmit-state map for the PTT read-back (see §3.7)
tx_state_map = { "0" = "rx", "1" = "tx_cat", "2" = "tx_other" }  # ftx1
```

Hazard membership itself (HAZARD/KEYING) is a code-level constant — the
four-family rule is an owner ruling on universal evidence (relay physics,
both vendors, B1), and providing no loosening mechanism is the strongest
possible tighten-only rule.

**Band-edge data source, named.** The band relation comes from the profile's
existing band tables — `[[freq_ranges.ranges.bands]]`
(`rigs/ic7300.toml:808+`, `rigs/ftx1.toml:547+`), parsed into `BandInfo`
(`profiles/__init__.py:164-172`) / `FreqRangeInfo` (`:176-182`), carried on
`RigProfile.freq_ranges` (`:258`) — and reaches the authority as **plain
tuples in its constructor data**, built by the backend from its profile:
`core/tx_authority.py` imports nothing from `profiles` (the layer matrix puts
`profiles` above `core`; INV-12 holds because the data travels in, the import
does not).

**The band relation is added strictness, never a new gate on frequency.**
The measurement's whole point is that a plain frequency write is
manufacturer-permitted; a rule that re-refuses it whenever the relation is
murky would re-enter fail-closed through the back door — and the relation is
murky often (`ic7300.toml` declares one 30 kHz–60 MHz range holding eleven
bands, so any frequency in a gap is in no declared band; four shipped
profiles poll nothing headless, so store frequency can be absent or stale).
Rules, therefore:

- `set_freq` classifies **HAZARD only when both the current and the target
  frequency resolve to declared bands and the bands differ**. A gap value,
  a missing/stale current frequency, or a profile with no `freq_ranges` →
  **PASS** — the manufacturer-permitted floor. Cross-band detection on
  `set_freq` is best-effort extra protection, honestly stated as such
  (a stale current frequency can mis-resolve a real crossing to same-band;
  the cost is falling back to exactly what the vendors permit).
- Commands that **name a band or a channel** — `set_band`, `memory_to_vfo`,
  `set_memory_mode` (`runtime/radio.py:5022-5028` — it retunes to the
  channel's frequency; same hazard, previously unclassified) — carry the
  HAZARD class unconditionally: for a memory recall the target is unknowable
  without reading the channel, so the hazard assumption is the design, not a
  fallback. The vfo-select family needs no relation at all — it is in the
  four-family rule outright, which is precisely what removes the
  band-relation computation from the VFO path.

**Fail-direction rule, per class:**

| Class | Truth needed | On confirmed RX | On TX (radio-reported or own-transmit hold) | On unknown / read failure |
|---|---|---|---|---|
| PASS | none — structurally never consulted | send | send (radio may refuse; reported) | send |
| HAZARD | own-transmit hold check (no wire), then solicited read (for `set_freq`: band relation first — resolved crossing gates, else PASS) | send — and an admitted tune start (`set_tuner_status(2)`), once sent, records an own-transmit hold and arms the deadline (§3.5 step 3, §3.6, INV-7) | **refuse** `refused-while-transmitting` — whoever keyed it, however it was keyed; no unkey-first, no sub-cases (Q2/Q3/Q4/Q11, one rule) | **refuse** `tx-truth-unavailable` (fail-closed) |
| KEYING | attribution state | admit + record own-transmit hold + arm deadline | ptt-on: pass-through (idempotent re-key, current semantics); `send_cw_text`: pass-through — the one write Icom explicitly documents as acted on during TX; the own-transmit hold extends | admit (a key is explicit operator intent — preserved; extending the web's confirmed-RX key rule to other ingresses stays an ingress-side choice) |
| UNKEY | none | send | send | send |

Note what this deletes: **`DEFER` is empty and gone.** No family holds a write
waiting for a state change; both `DeferredTxCommandLane` lanes go with it —
which is **three** construction sites, not two, because the Yaesu poller
rebuilds its lane on cancel (§5).
The fail-closed cells above are honest because they are *rare* (hazard
commands) and *cheap to satisfy* (one read). The MOR-1903 problem — five
profiles that never poll PTT — stops interacting with the gate entirely: the
solicited read works on any radio implementing the row-5 primitive.

### 3.4 Contracts

Input: a method invocation on the `Radio` protocol — the contract consumers
already hold — classified by the total `(method, arguments)` table above.
Output, fully typed and closed:

```python
class TxRefusalCode(StrEnum):
    REFUSED_WHILE_TRANSMITTING = "refused-while-transmitting"
        # the four-family rule, in the owner's own words. Condition-bounded,
        # not time-bounded: it clears when the transmission ends — and under
        # continuous break-in or a held mic that is whenever the operator
        # stops, which no timer of ours bounds. It is honest to call this
        # bounded only in that sense; nothing on any wire consumes the label
    TX_TRUTH_UNAVAILABLE = "tx-truth-unavailable"
        # unbounded: the read failed, timed out, is unverifiable
        # (rigctld-client), or the backend lacks the capability

@dataclass(frozen=True, slots=True)
class TxEvidence:                 # REQUIRED on every refusal, no exceptions (INV-14)
    value: bool | None            # what the radio answered (None: no answer)
    attributed: str | None        # "rx" | "tx_cat" | "tx_other" | None (Icom)
    age_seconds: float | None     # solicited: the measured read latency —
                                  # the number that quantifies the QSK window
    source: str | None            # provenance tag (RADIO_READBACK_SOURCES member)
    solicited: bool               # True: read performed for this decision
    verified_readback: bool       # False on rigctld-client (§3.7): the answer
                                  # may be an upstream cache, not the radio
    failure: str | None           # why no answer: "timeout" | "transport" |
                                  # "unverifiable-provenance" | "no-capability"
    own_transmit_hold: str | None # "key" | "cw" | "tune" | "lease" — the §3.5
                                  # step-1 refusal, made with no wire read at
                                  # all: the hold itself IS the evidence, and
                                  # INV-14's no-exceptions rule is satisfied
                                  # by naming it, never by exempting it

@dataclass(frozen=True, slots=True)
class TxDecisionRecord:           # one per non-PASS admission; ring of 256 per radio
    monotonic: float
    provider_generation: int
    method: str
    family: str
    write_class: TxWriteClass
    action: Literal["sent", "refused"]
    code: TxRefusalCode | None
    evidence: TxEvidence | None   # None only for KEYING/UNKEY records

class TxRefusal(Exception):
    code: TxRefusalCode
    evidence: TxEvidence
```

That is the entire published decision surface. `TxRefusal` is the one new
exception consumers handle. The bounded/unbounded note on each code is
**documentation for the operator and the owner** — it says whether a refusal
will clear by itself — and is read by no wire: the one success-without-write
rendering (the rigctld `RPRT 0`, §3.8/INV-10) is licensed by its own
three-part condition, not by a code attribute. There is deliberately **no**
`bounded` field or derived property, because no consumer exists for one (a
published field with no consumer is what "cut everything superfluous"
forbids). `TxEvidence`
is the brief's "age and provenance of the evidence it used", carried on
**every** refusal including `TX_TRUTH_UNAVAILABLE` — the one refusal where
the operator most needs the cause, so it carries the `failure` tag instead of
being exempted. Consumer/session identity is deliberately absent from the
record: the admission sits below the seats and does not know it; the seat's
own logs carry it. Because the rigctld wire (`RPRT -9`) has no evidence
channel, **every refused admission also emits one structured log line** —
that is the operator's path to the evidence on a headless box, and it is the
one logging duty the authority carries (INV-11 allows it explicitly). The
truth view (read side, display/UX grade):

```python
@dataclass(frozen=True, slots=True)
class TransmitTruth:              # built from the StateStore
    value: bool | None            # None: nothing qualifying known
    attributed: str | None
    age_seconds: float | None
    source: str | None
    generation_current: bool
```

`TransmitTruth` deliberately does **not** pre-collapse into fresh/stale: the
consumer applies its own tolerance — a panel indicator accepts half a second,
the lease UX a second, and the hazard gate accepts none of it (T3: it reads
the radio). `RADIO_READBACK_SOURCES = frozenset({"poll_response",
"civ_unsolicited", "hamlib_response", "yaesu_poll_response"})` — an explicit
literal, consuming the producer-side narrowing already shipped
(`_civ_rx.py:2636-2650`) server-side for the first time; `command_response`,
`state_poller`, `local_reconcile`, `test` have no intake. (This pin and its
mutation are kept verbatim from the previous ADR — its review's second-praised
idea.)

**The debuggability contract.** `authority.view()` returns the last N
`TxDecisionRecord`s plus current `TransmitTruth`, keying attribution and the
deadline; `web/tx_safety_view.py` and Pro consume it read-only under its four
existing honesty rules (`tx_safety_view.py:1-39` — an ACK is not RF; an
unarmed watchdog is not coverage; unmanaged is stated; the uncertain shutdown
is named). No events, no sockets, no telemetry — consumers pull.

### 3.5 The hazard gate mechanics

For a HAZARD write, the admission performs, on the caller's own task, under a
**per-radio admission lock** held from the first check through the write
handoff — two concurrent hazard admissions must not each read RX and both
write, and the authority's own `set_ptt(True)` (KEYING, never truth-gated)
must not slip between a read and the write it authorised. Any KEYING
admission or TX observation that lands while the lock is held invalidates the
read (INV-4). The steps:

1. **Own-transmit hold — no wire.** Refuse `refused-while-transmitting`
   immediately if the authority itself holds "transmitting": an admitted key
   without a subsequent unkey, a CW message inside its computed duration, or
   a tune we started (§3.7's own-commands rule; on managed radios, an active
   supervisor lease, consulted read-only). **The radio's own report cannot
   clear this hold** — B6 measured the IC-7300 answering `1C 00` =
   *receiving* while audibly still sending a CAT-issued CW message, so
   during our own transmissions the solicited read is exactly the instrument
   that lies. The refusal's evidence names the hold
   (`own_transmit_hold="key"|"cw"|"tune"|"lease"`) — a step-1 refusal
   involves no wire read, and the hold itself is the evidence. Inferring
   *transmit* from our own commands only ever tightens the gate; the
   standing prohibition forbids inferring *receive*.
2. **One solicited transmit-state read** through the injected row-5
   primitive, with the admission's **own deadline:
   `TX_READ_DEADLINE_SECONDS = 0.3`** — deliberately not the backend's
   generic 2.0 s GET bound (`runtime/radio.py:910`,
   `self._civ_get_timeout = min(timeout, 2.0)`). Refuse
   `tx-truth-unavailable` on expiry. On Icom the primitive validates the
   reply with the directed-exact-reply discipline already shipped for the
   pump (`_civ_rx.py:2636-2650`) — an ACK, setter echo or unrelated frame
   can never satisfy it (INV-13). Honesty about where this read runs: it
   enters the backend's **own serialization**, not above it — the Commander
   FIFO with 50 ms serial / 35 ms LAN pacing on Icom, the transport's single
   lock behind the poller on Yaesu — and on the rigctld socket the
   connection loop is strictly serial (`rigctld/server.py:1010-1018` →
   `:1074-1078`), the same mechanism behind MOR-1881's measured **+2.003 s**
   for the rejected in-band hold. The comparison, stated side by side rather
   than asserted away: the rejected hold waited up to a 3 s TTL for a *state
   change*; this is one read with a 300 ms ceiling — an order of magnitude
   under the unkey barrier, non-recurring, and only on hazard commands
   (which the rigctld wire does carry: `U TUNER 0|1` routes straight to
   `set_tuner_status`, `rigctld/routing.py:417-419`). So the worst case a
   same-connection `T 0` can queue behind a hazard admission is ≈0.3 s.
3. **Verdict** (still under the lock): answer RX → send — and if the sent
   write was a tune start (`set_tuner_status(2)`), record an own-transmit
   hold (`"tune"`) and arm the deadline: the tune is a transmission we
   asked for (§3.7). Answer TX — any attribution, whoever keyed it — no
   answer, transport error, or unverifiable readback (§3.7) → `TxRefusal`
   (fail-closed), evidence attached. There is no unkey-first path: the owner ruled a band change is
   **refused**, never made safe by auto-unkeying (Q2, folding Q3 into the
   same rule — Hamlib's unkey-then-write convenience is deliberately not
   adopted).
4. Append the `TxDecisionRecord` and emit the one structured log line
   (§3.4).

**The QSK residual, stated plainly.** The gate's during-TX evidence is one
instantaneous read. Under a *foreign* full-break-in transmission the read can
land between elements, answer RX, and admit a relay throw into the next
element — a real window, bounded only by the read latency the evidence
records. An earlier draft closed it with a cached-TX holdoff; that mechanism
could not be built from `TransmitTruth` (which carries only the newest
observation, no last-TX high-water mark) and the owner deleted it rather than
repairing it: these are families nobody exercises during a transmission, the
blanket refusal already covers every case where transmit state is *known or
ours*, and the residual is accepted by ruling. It remains strictly better
than every current seat (cached truth up to 1.0–8.0 s old) and equal to the
front panel, which the radios do not interlock either. B4 (duty-cycle
sampling) was dropped as low-value — the B6 finding already answers its
question qualitatively.

**The connect-time bootstrap exemption is retained — and it does not reach the
seat this design creates. OPEN (Q15, raised 2026-08-21, not decided.)** The one
`connection_epoch_bootstrap` exemption (`radio_poller.py:4024`) exists
because RF/VFO truth is structurally unobservable until that first
`SelectVfo` lands. A previous draft claimed the solicited read dissolves it;
it does not — vfo-select is HAZARD, whose read-failure direction is
fail-closed, and a timed-out connect-time read would refuse the very write
that makes identity observable, permanently.

Both halves, stated neutrally, because an earlier revision of this paragraph
asserted only the first and the second contradicts it:

- **The shipped exemption survives its own row.** It is one named constant,
  one call site, the same source-level pin (MOR-1884), and row 9 keeps it.
- **It cannot travel to the backend admission.**
  `connection_epoch_bootstrap` is a keyword parameter of
  `web/radio_poller.py::_execute`, guarding the call to
  `_enforce_tx_interlock` in that method's own body. The backend admission
  takes `(method, args, kwargs, target=…)`
  (`core/tx_authority.py::TransmitAuthority.admit`); nothing in that signature
  can carry the flag, and the exempted `SelectVfo(vfo="A")` dispatches down to
  `set_vfo_slot` / `_set_vfo_slot_confirmed`, i.e. into
  `runtime/_dual_rx_runtime.py::_set_vfo_slot_impl` — precisely the body §3.2
  designates for the Icom vfo-select admission. So the retained exemption
  protects a seat rows 9–11 delete and gives no cover at the seat rows 7–8
  add.

What this does **not** say: that the bootstrap write will fail. Under T3 the
hazard read is solicited rather than cached, so a connect-time read will
usually answer promptly and RX, and the write will usually be admitted. The
exposure is the read that times out, on a family whose declared fail direction
is closed. Resolving it is an owner decision between at least these: an
explicit connect-epoch suppression inside the authority, a bootstrap path that
does not enter the GATED map, or accepting the residual and saying so. **This
document takes none of them**, and the row that deletes the web seat must not
be read as having taken one either.

### 3.6 The keying axis — ownership and the one deadline

Unchanged in structure, unified in mechanism:

- **Managed radios** keep `TxSafetySupervisor` exactly as shipped (pure,
  per-radio, `core/tx_safety.py:222`); ingresses keep binding `ManagedTxApi`
  and calling `managed.set_ptt` — that path does not traverse
  `radio.set_ptt` and the authority does not touch it (it consults the
  supervisor's lease read-only, for attribution). Arming the supervisor on
  the bench backends stays MOR-1219/MOR-1190, hardware-gated, unblocked but
  not required by this design.
- **Unmanaged keys** — every `radio.set_ptt(True)` from any ingress — pass the
  authority's KEYING admission: it records attribution (an admitted key is
  open, since when — the own-transmit hold of §3.7) and **arms the one
  deadline**:
  `BACKEND_MAX_KEY_DOWN_SECONDS` (imported, not re-spelled — the
  `tx_safety.py:22-24` doctrine). The deadline is *state in the authority*;
  each driver either arms its own timer from that state (web: `call_later`)
  or polls it for due OFF effects (`authority.poll(now)` — rigctld drain,
  backend loops), and executes the OFF on its own rails — the
  supervisor-tick house pattern, with the timer kept where a timer already
  works:
  - **web, Icom branch**: the driver arms a `loop.call_later` from the
    authority's deadline — **keeping today's firing mechanism**
    (`radio_poller.py:1023-1027`), which fires even if the drain wedges,
    the exact MOR-1181 scenario the enqueue design exists for — and the
    fired callback executes the effect by enqueuing `PttOff`, preserving
    MOR-1220's deliberate design (`radio_poller.py:1035-1052`: enqueued,
    never written in place, so the `PttOff` arm runs TX-audio teardown
    (MOR-1013), clears keyer identity (MOR-1878) and survives shutdown via
    the MOR-1181 drain). What moves into the authority is the deadline
    *state* and the arming *decision*; the timer and the routing stay where
    they work.
  - **web, `ObservationPollable` branch** (FTX-1, rigctld-client — these
    backends get no `RadioPoller` at all; `web_startup.py:105-193` is a
    three-way exclusive branch): the backend's observation poller drives
    `poll()` from its drain and executes the effect by enqueuing `PttOff`
    into the same web `CommandQueue` it drains, whose `PttOff` arm is a
    plain `set_ptt(False)` (`yaesu_cat/poller.py:1018`) — honestly noted:
    that branch has no TX-audio-teardown machinery **today either**; this
    design neither adds nor removes it.
  - **standalone rigctld**: the server's 50 ms drain loop
    (`rigctld/server.py:521`) drives `poll()` and routes the OFF through its
    unmanaged unkey path — absorbing the **shipped** MOR-1904 backstop
    (merged `769bfc71`): today that is a handler-owned, self-scheduled
    0.25 s ticker task (`handler.py:1771-1788,1856-1927`), not a
    drain-driven one, so row 12b replaces a private task with the
    authority's deadline rather than merely relocating a constant. Scope
    honesty: that loop exists only for
    profiles declaring `[state_acquisition]` (`server.py:394-396,442-447`) —
    both bench radios qualify; the four undeclared profiles fall to the
    backend loop below where one exists (all four are CI-V serial radios).
  - **backend last resort** (bare async-API use with no delivery): the Icom
    family has real periodic tasks to drive `poll()` — the LAN transport's
    100 ms idle loop (`core/transport.py:391-399,732`) and the serial
    watchdog at 0.2 s (`_icom_serial_base.py:113,943-951`) — and executes
    the OFF as a direct backend unkey, last resort. **`YaesuCatRadio` and
    `RigctldClientRadio` run no task of their own** (`yaesu_cat/radio.py:272-280`,
    `rigctld_client/radio.py:602-604` — their only loops are the pollers a
    delivery creates), so a directly constructed Yaesu/hamlib-provider radio
    with no delivery attached has **no deadline driver — a named residual**.
    It is a narrow one: the SDK sync facade constructs `IcomRadio` only
    (`sync.py:37,136-145`), so every shipped path to those two backends goes
    through a delivery that carries a driver. The sync facade's own loop
    runs only inside calls (`run_until_complete` per call), and a killed
    script on a serial radio remains unbounded — identical to today, minus
    the paths the rows above now cover.
  Armed only for keys the authority itself admitted (a front-panel key is not
  ours to time out — MOR-1220/MOR-1904 doctrine preserved); cleared by any
  admitted unkey; re-armed by a newer key.
- **Unkey** (`set_ptt(False)`) is `UNKEY` class: never refused, never gated,
  clears the deadline — the one-sidedness doctrine
  (`managed_tx_ingress.py:1-21`) becomes a property of the dispatch, pinned
  by mutation.
- **Ingress-side key policy stays ingress-side, and may only add
  strictness.** The shipped #2745 rule — web `ptt_on` is a BLOCK family at
  the server interlock, refused whenever RF truth does not read fresh RX —
  and the single-keyer teardown identity (MOR-1878,
  `radio_poller.py:718-722,2488,2492`) remain seat policy above the
  authority — the authority is the floor, not the ceiling. Teardown bias
  rules (`radio_poller.py:995-1014`, `control.py:1315-1389`) are retained
  verbatim.
- **CW text is atomic by contract, and the deadline's OFF is inert against
  it.** B6 measured it: a CAT unkey landed mid-message and the fifteen-tone
  message played to completion. The owner's ruling: a CW message is an
  atomic action by design — the operator presses enter and commits to the
  whole message; it is not cancellable, and that was the intent when the
  feature was built. A documented contract, not a defect. Consequences: the
  deadline still fires its OFF regardless (Q9 — worst case it is inert, and
  for CW it *is* inert); the effective bound of a CW transmission is its own
  length (the web caps the text at 512 characters, `control.py:184`; the CLI
  has no cap — a stated residual); and the honest limitation on record is
  that a CAT-issued CW message is a transmission rigplane **starts itself,
  cannot stop with a CAT unkey (B6), and — before the §3.7 own-commands
  rule — could not even see**. The rule closes the third. Precision the
  tree demands: the shipped `stop_cw_text` (`core/radio_protocol.py:1673`
  → `runtime/radio.py:4958`, CI-V `0x17` data `0xFF`; Yaesu
  `radio.py:2431`) was **not** tested by B6 — whether it stops an
  in-progress message is open (B9, informational). The owner's atomicity
  contract stands either way — the product commits the whole message on
  enter — but if B9 answers yes, the deadline's CW effect can upgrade from
  an inert `set_ptt(False)` to `stop_cw_text`. The
  send records an own-transmit hold for the message's computed duration
  (character count × keyer speed, CAT-readable: protocol `get_key_speed`,
  `core/radio_protocol.py:1716`; `get_key_speed = [0x14, 0x0C]`,
  `rigs/ic7300.toml:1018`; Yaesu backend `get_keyer_speed` over CAT `KS`). The CLI's one-shot CW
  (`cli/__init__.py:3090-3093` then `async with radio:` closes at `:1925`)
  exits before any deadline could fire — the CLI's bound remains its process
  lifetime and signal handling, stated, not hidden.

### 3.7 Truth — one view, positive RX, provenance-pinned

- One reader: `TransmitTruth` built from the canonical `StateStore` snapshot
  with the `RADIO_READBACK_SOURCES` filter. It replaces all six resolver
  spellings (§1.3) for every display/UX/scheduler consumer; the hazard gate
  does not use it (T3).
- **RX is a positive, enumerated mapping.** Each backend parses its
  transmit-state answer through the profile's `tx_state_map`; an unmapped
  value is **not receiving** — it maps to transmitting for safety purposes,
  with a diagnostic. This is the structural form of the MOR-1905 fix: the
  `bool(state == "1")` class of inversion becomes unwritable, because the only
  way to produce RX is an explicit table entry. On Yaesu that shape has
  shipped — `c87c59c3` removed the inline predicate and `a1cf9f48` routed the
  parse through `tx_state_map` in
  `backends/yaesu_cat/radio.py::_interpret_ptt_token` (correction, 2026-08-21:
  this bullet used to cite the predicate as live at `:1003`).
  The Icom decode keeps its `0x00/0x01` allowlist (`_civ_rx.py:1566`) with the
  same rule stated: unlisted byte → not RX.
- **Open (Q14, undecided — raised 2026-08-21): does the Icom path parse
  through `tx_state_map` at all?** This bullet and §3.9 item 1 read
  differently and neither is amended here, because the answer is a design
  decision belonging to this document's owner. *Reading A (this section):*
  each backend parses its own answer and the Icom path keeps its existing CI-V
  `0x00/0x01` decode allowlist; `tx_state_map` is the mechanism for backends
  whose wire token is vendor-encoded. *Reading B (§3.9 item 1, "the parse goes
  through the profile `tx_state_map` … and — on CI-V — the directed-exact-reply
  discipline"):* every backend, CI-V included, parses through the profile map,
  with the shape check added on top. **What the merged code implements is
  reading A:** row 5's shipped `IcomRadio.read_transmit_state`
  (`runtime/radio.py:3674-3731`) never consults `profile.tx_policy` — it
  re-validates the reply through `_observations_from_frame` and the
  `0x00/0x01` allowlist (`_civ_rx.py:1563-1566`) — while the Yaesu predicate
  does consult it (`backends/yaesu_cat/radio.py:1083-1090`). The visible
  consequence is that `rigs/ic7300.toml:1613` ships
  `tx_state_map = { "0" = "rx" }` that nothing on the Icom path reads. Left
  open.
- **Our own commands are a source of "transmitting" — never of "receiving"
  (owner ruling, 2026-08-20).** When rigplane itself issues a command that
  makes the radio transmit — a CW message, a tune start, a key — the
  authority holds "the radio is transmitting" for the duration it can
  compute or observe, **regardless of what the radio reports**. This does
  not breach the standing prohibition: what the programme forbids is
  inferring *receive* from our own write — the direction that puts a write
  into a keyed amplifier; inferring *transmit* only ever makes the gate
  stricter, and the design already states a cache may tighten but never
  admit. The rule was **forced by measurement (B6)**: during a CAT-issued CW
  message the IC-7300's `1C 00` reported *receiving* while the rig was
  audibly still sending — a fail-open blind spot of the same class as the
  Yaesu `TX2` inversion. Without the rule the hazard gate is blind for the
  whole message and would admit a band change, a tuner engage or an antenna
  switch into an active transmission. Scope, precisely: **CW messages we
  sent** (duration = character count × keyer speed, CAT-readable — protocol
  `get_key_speed`, `core/radio_protocol.py:1716`; `[0x14, 0x0C]`,
  `ic7300.toml:1018`; Yaesu `KS`); **tune starts we asked for** and **keys we
  issued**, held until the radio says otherwise. **Not hand-keyed CW**: the
  software has no key support, only messages, so a hand-sent transmission is
  by definition front-panel — the already-tracked foreign-transition case.
- **Attribution is a per-vendor capability, now carried, not discarded.**
  Yaesu's three-valued answer (`0` RX / `1` TX-by-CAT / `2` TX-by-other)
  flows through `tx_state_map` into `TxEvidence.attributed` and
  `TransmitTruth.attributed`; Icom reports no attribution and the field is
  honestly `None`. The FTX-1's unpolled `RI;` composite (P4: RX / TX /
  **TX INHIBIT**; P6: tuner-is-tuning) is free additional truth on the same
  wire — noted for a future bench visit as the natural corroboration source
  for tuner state on Yaesu.
- **The rigctld-client backend's "solicited read" is not radio truth, and
  the model says so.** `backends/rigctld_client/radio.py:737-743` sends `t`
  upstream; upstream Hamlib answers from its own cache, and upstream
  rigplane answers from the retained `_FallbackRigState`. A value like that
  stamped `solicited=True, age_seconds≈0` would be a fabricated freshness
  claim of the exact MOR-1900 class this design forbids. So that backend's
  read carries `verified_readback=False`, and the hazard gate **refuses**
  HAZARD admission on unverifiable readback
  (`tx-truth-unavailable`, `failure="unverifiable-provenance"`) — hazard
  families through the rigctld-client backend are fail-closed, stated, with
  the upstream server's own protections as the operative gate. The
  conformance matrix pins the marking (INV-13 covers all three backends).
- **The `civ_unsolicited` producer tag is tightened before the view trusts
  it**: today it requires no `from_addr` check (`_civ_rx.py:2635-2637`)
  while the `poll_response` branch two lines below does — row 13c adds
  `from_addr == radio_addr` to the tag's condition, so a frame from an
  unknown bus address can never satisfy the pinned source class.
- Producers keep their tags; the laundering paths are deleted (§5). The
  hardcoded CI-V ptt max-age (`_civ_rx.py:101`) becomes profile-sourced with
  the hardcode as fallback, and `ftx1` declares an explicit 1.0 s ptt policy
  (today it inherits the 8.0 s profile default — display-grade now, still
  worth declaring).

### 3.8 Renderings per client — one decision, several renderings

| Client | `TxRefusal` (both codes) | radio-refused-during-TX (`refused_during_tx` family + the radio's own typed refusal + truth says TX) | notes |
|---|---|---|---|
| web | typed error envelope with code + evidence — **a narrow typed channel exists since #2745 and row 9b extends it**: the `failed` details whitelist now admits `{session_id, blockedBy, reason}` for exactly two interlock reason codes (`web/server.py:2161-2187`); everything else still reaches the browser as English prose (`:2089-2098`). Row 9b widens the same whitelist additively with `{code, evidence}` over the full `TxRefusalCode` set, with i18n keys in a **new command-refusal namespace** (`core.commandRefusal.*` — deliberately not `core.rxTx.blocked.*`, which is the `KeyBlockedReason` key-*eligibility* vocabulary, `semantic/rx-tx-surface.ts:85-93`, a different axis) | real error with the radio's refusal named | reason banners |
| rigctld | `RPRT -9` (ERJCTED) — recommendation Q6: an antenna/tuner client that gets a silent success and no relay movement is worse off than one that gets an error; WSJT-X never sends these families | **`RPRT 0`** — byte-for-byte what Hamlib does for mode/split during PTT (`rig.c` returns OK without writing); a non-zero mid-sequence tears down WSJT-X, which is not negotiable. Trigger: family ∈ `refused_during_tx` **∧** the radio's typed refusal **∧** `TransmitTruth` reads TX within its max-age. The third conjunct exists because a Yaesu `?;` is *any* rejection — unsupported command or out-of-range value included (`transport.py:71`, read that way by the tree itself at `observations.py:1097-1099`) — and rendering an out-of-range `set_mode` as success would be an **unbounded** lie. Outside confirmed TX the `?;` renders as the honest error it is. Cached truth here chooses the wire rendering of an already-failed write — no RF is at stake, so T3 is intact | the `RPRT 0` license is INV-10's three-part condition (declared family ∧ radio refusal ∧ truth reads TX) — no code attribute is consulted |
| CLI | non-zero exit + printed code and evidence | radio's error printed | |
| SDK / Pro | `TxRefusal` propagates typed | typed backend error | additive tier-1 symbols, issue-first per `open-core-policy.md:174-186` |

**The dialect ledger — P8 goes down, not up.** P8 counted five refusal
dialects. This design deletes two and adds one: the per-seat English-prose
`CommandError` reasons die with their seats (rows 9–11), and the
`heldBy/reason` held-envelope dialect dies with the lanes — its whole
surface is dead after rows 9/11 and is deleted by name (emitters
`web/radio_poller.py:818` and `backends/yaesu_cat/poller.py:856`, the
whitelist branch `web/server.py:2126-2141`, the frontend parser
`ws-client.ts:599-602`, and `kind: 'held'` in
`commands.svelte.ts:34,272-277` — row 9b). `TxRefusalCode` is the addition.
The hamlib errno remains the rigctld wire (a renderer, not a vocabulary),
and the browser's `TxIneligibility`/`KeyBlockedReason` pair remains the
key-eligibility axis — browser-owned, distinct, retained with reason. Net:
5 → 4, with the one machine-readable vocabulary where prose used to be.

**Lifecycle honesty at the executor layer.** With `_defer_write_gate` deleted,
a refused hazard write raises *inside* the executor, after `CommandService`
has recorded its optimistic overlay and emitted `accepted`/`queued`/`sent` —
the exact sequence the deleted gate's docstring warned about
(`handler.py:780-787`). This is acceptable, for the reason the tree already
accepts it: today's rigctld BLOCK refusals raise in the executor
(`handler.py:462-469`) and produce the same record-then-expire-then-`failed`
sequence (`command_service.py:172-198`). The gate's pre-`CommandService`
placement existed for writes that **claimed success without being attempted**;
with the DEFER drop deleted, the only success-claiming rendering left
(`RPRT 0` above) follows a write that genuinely went to the radio — the
overlay was true, the `failed` event is true, only the wire code differs. A
`TxRefusal` maps to the wire in the existing typed error ladder
(`handler.py:1328-1345`; one added `except` clause), which rigctld may
legally import from `core`.

Two renderings deserve their own line. First, the **known-limitations
rewrite**: the `## rigctld write handling during transmit` section of
`docs/release-notes/2026-beta-known-limitations.md` documents the **mode/VFO/
split** drop and the post-unkey swallow window, and both paragraphs describe
behavior this design deletes; the section must be rewritten in the same PR as
the rigctld cutover (row 10). Correction, 2026-08-21: this sentence used to say
the section documents the *frequency/mode/VFO/split/RIT* drop. MOR-1940
(`9fc90943`) already removed the frequency and RIT/XIT half and replaced it
with a third bullet stating both families are exempt — so row 10 rewrites what
is left, and must take that third bullet with it rather than leaving it
orphaned.
Second, the internal lifecycle stays truthful even where the wire lies: a
radio-refused write flows through `CommandService` as a normal failure
(overlay cleared, `failed` lifecycle event); only the rigctld *renderer* maps
it to `RPRT 0`. That is strictly more honest than today's
pre-`CommandService` silent drop, and the drop-precedes-overlay machinery
(`handler.py:770-832`) is no longer needed — there is nothing left to drop
silently before execution.

### 3.9 What a client ships, and what a backend ships — and nothing else

**A client (any consumer that writes):**
1. Holds a `Radio` and calls it, exactly as today.
2. Renders `TxRefusal` through its pinned wire map.
3. Keys managed radios through `ManagedTxApi` as today.
4. If it hosts a delivery loop (web poller, rigctld server drain), drives
   the authority's deadline — a timer armed from it or `authority.poll(now)`
   — and executes OFF effects on its own unkey rails (§3.6).
5. Nothing else: no RF reads, no freshness arithmetic, no classification, no
   lane, no keyer bookkeeping, no private timer.

**A backend (supplies truth and primitives; consumes no decisions):**
1. Implements the row-5 read primitive `read_transmit_state()` — hosted on a
   **new capability protocol `TransmitStateReadable`**, not on `Radio`:
   `Radio` is `@runtime_checkable` (`core/radio_protocol.py:133`), so adding
   a required member would silently fail `isinstance` for any implementer
   lacking it — the identical capability-loss mechanism §3.2 uses to kill
   the facade — and `open-core-policy.md:178-180` classes a new required
   method as breaking. A capability keeps the change genuinely additive
   (issue-first), with the fail direction declared: **a backend not
   implementing `TransmitStateReadable` refuses every HAZARD admission**
   — the four families unconditionally, `set_freq` when its relation
   resolves to crossing (`tx-truth-unavailable`,
   `failure="no-capability"`) — fail-closed, per the §3.3 table. The parse
   goes through the profile `tx_state_map` (positive-RX rule) and — on
   CI-V — the directed-exact-reply discipline. **Open (Q14):** this sentence
   and §3.7's positive-mapping bullet disagree about whether the CI-V path
   goes through `tx_state_map`; the merged row-5 Icom implementation does
   not (`runtime/radio.py:3674-3731`). Neither passage is amended — the
   choice is the owner's.
2. Constructs its `TransmitAuthority` at connect, injecting the read and
   last-resort-unkey callables and its `[tx_policy]` data; drives `poll()`
   from its liveness loop.
3. Carries the admission on every write method in its GATED map — as the
   bare, outermost `@tx_admit` decorator or as the first awaited statement of
   the body, an `async with self._tx_authority.admit(...)` block, the two
   forms INV-2 accepts — once per alias chain, at the innermost named body
   (INV-2).
4. Publishes PTT observations with a readback-class `ObservationSource`,
   stamped with the current provider generation, never from its own writes
   (the Yaesu `set_ptt` self-mutation **was** deleted, by MOR-1941/`a1cf9f48`;
   this clause read as future work until 2026-08-21).
5. Surfaces the radio's refusal as a typed error (`CatCommandRejected` at
   `yaesu_cat/transport.py:71` already exists; CI-V NG replies map likewise).
6. Passes the conformance matrix (§3.10). It is never recognised by the
   authority — only conformed to.

### 3.10 Test architecture

Designed before any component ships (rows 1–6 precede every cutover):

1. **Fakes.** The existing `FakeRadio`/fake-wire harnesses, extended with a
   scripted transmit-state answer (RX / TX / TX2 / silence / `?;`) so the
   gate's solicited read exercises the real code path, including the
   directed-exact-reply validation on the CI-V fake. No MagicMock near the
   authority (CLAUDE.md hard rule); the supervisor double is the real
   `TxSafetySupervisor` on a fake clock, as its own tests already do.
2. **Gate unit tests:** per class × per truth answer, including read-timeout
   → fail-closed, band-relation arithmetic (same-band / cross-band / gap /
   no-`freq_ranges` profile), the own-transmit hold (the **B6 golden**: a
   scripted RX answer during an in-progress own CW message must not admit a
   hazard write), and decision log contents.
3. **Conformance matrix** (historical authority rows; the surviving observation
   subset is `tests/contracts/test_tx_observation_conformance.py`) over **every
   shipping backend class** with its fake link — the audio
   precedent (`test_audio_lifecycle_conformance.py`): a HAZARD write at
   scripted TX is refused and **no wire write occurs**; at scripted RX the
   read precedes the write on the wire; **the same rows driven through
   `create_observation_poller`'s queue path** (the D1 lesson — the matrix
   must exercise the ingress the facade design missed); an unmapped
   TX-state value is not RX; a key through any path arms the deadline and
   each shipped driver fires the OFF on a fake clock; `set_ptt(False)` is
   never refused; a `set_ptt` write alone produces no observation.
4. **Totality and call-site pins** (INV-1/INV-2): each backend's map covers
   the union of `Radio`, every capability protocol, and the backend's own
   write surface; every GATED-map method carries exactly one statically
   discoverable admission per alias chain, in either accepted form — the bare
   `@tx_admit` decorator at `decorator_list[0]`, or the in-body
   `async with … admit(...)` block — and a method carrying neither form, or
   carrying a parameterised `@tx_admit`, fails the pin (AST-level, the
   MOR-1884 pin pattern).
5. **Wire-map totality** per client column: every `TxRefusalCode` × every
   client has a pinned rendering; sets are explicit literals, never computed
   from the enum (`test_audio_transport_conformance.py:65-81` rule).
6. **Characterisation pins landed before any cutover** (row 2), one list,
   the same list §5 cites: the `RPRT 0` answered for a **policy-dropped**
   write during known TX (`_defer_write_gate`, `handler.py:770-833` — the
   drop is taken before `CommandService` is entered, so the radio never sees
   the write and refuses nothing; row 10 keeps the wire rendering and
   replaces the trigger with a genuine radio refusal), the raw-during-TX
   refusal (Q12 — the one behaviour ruled retained), teardown-toward-OFF,
   the one-sided unkey **of the interlock** (the ownership layer above it can
   still decline one — `_route_ptt`, `handler.py:1991-2003`), read-only
   EACCESS, `t` answered from the canonical projection where it has one
   (`handler.py:1740-1744`) and from `RadioState.ptt` **beneath** it
   (`:924-926`, consumed `:1746-1768`) — the mirror is the fallback, not the
   primary, ungated `force_unkey`, the watchdog-honesty
   view rules — each with its named mutation.
7. **Mutation duty:** every §6 invariant names the mutation that must go red;
   batteries run with `PYTHONDONTWRITEBYTECODE=1`.

## §4 Migration

One row per PR; ≤3 files / ≤400 LOC unless a deviation is declared in-row
(test-file blast radius is counted — the previous draft under-counted it and
the review measured it); every row leaves `main` shippable; **[BP]**
behavior-preserving / **[BC]** behavior-changing with the bench observable
that would reveal a regression. Between rows 7–8 (the authority goes live
per backend) and row 11 the old seats still stand *above* it; the two compose
monotonically — the strictest answer wins, previously-ungated paths gain
protection immediately, and each remaining liberalization arrives only when its
seat row deletes the old refusal. Correction, 2026-08-21: this sentence used
frequency-during-TX as its example of a liberalization still waiting on a seat
row. That one has already arrived, and not by a seat row — MOR-1940
(`9fc90943`) delivered it by reclassification, removing the frequency and
RIT/XIT commands from `runtime/tx_interlock.py::_DEFER_TYPES`, and the
known-limitations file already records it as shipped. The liberalizations still
held by their seats are mode and split.

**Rows 1–6 have merged.** Each is marked in place below with its commit. Rows
0a/0b/0d were already marked; rows 0c and 1–6 were not, and were still written
in the future tense with present-tense factual claims attached — two of which
had gone false under them (rows 3b and 6). Corrected 2026-08-21.

| # | Step | Files (indicative) | Mode / bench observable | Pin → mutation that must go red |
|---|---|---|---|---|
| 0a | ~~Land PR #2759~~ — **DONE, merged `6bdb5846`** (the `t`-poll launder deleted, §1.4) | merged | [BC], shipped | its own refusal-replaces-fabrication tests |
| 0b | ~~Land PR #2760~~ — **DONE, merged `769bfc71`** (MOR-1904 rigctld key bound, §1.5); absorbed later by row 12b | merged | [BC], shipped | its own tests |
| 0c | ~~**Fix MOR-1905**~~ — **DONE, merged `c87c59c3`** (#2761; Yaesu `TX2` → transmitting, fail-closed predicate) | merged | [BC] on FTX-1 truth, shipped | restoring `== "1"` fails the TX2 test |
| 0d | ~~PR #2745~~ — **DONE, merged `b3ab76b1`** by the owner; the PR's own bench hold has no on-record discharge (§1.6), superseded mechanism deleted at row 9 either way | merged | — | — |
| 1 | ~~**Vocabulary + engine**~~ — **DONE, merged `67fbcbea`** (#2770, MOR-1909): `core/tx_authority.py` — `TxWriteClass`, the neutral family→class table with argument predicates (the per-backend method-name maps land with rows 7/8, beside the methods they pin), `TxRefusalCode`, `TxEvidence`, `TxDecisionRecord`, `TxRefusal`, `TransmitTruth` builder, `RADIO_READBACK_SOURCES`, `RAW_EXCLUDED`, the pure `TransmitAuthority` engine (injected read/unkey callables, own-transmit holds + deadline state, decision log) | 1-2 src + tests (~500 LOC src — declared; the engine and the vocabulary may split 1a/1b if review prefers) | [BP] — consumed by nothing | frozenset pins; the INV-1 totality harness (armed per backend as rows 7/8 land); add `"state_poller"` to the sources pin → red |
| 2 | ~~**Characterisation pins**~~ — **DONE, merged `94168f4e`** (#2769, MOR-1910): the retained behaviors (§3.10 item 6) | tests only | [BP] | each pin names its mutation inline |
| 3a | ~~**Profile `[tx_policy]`**~~ — **DONE, merged `ad89d10c`** (#2771, MOR-1912; extended to the six unmeasured rigs by `33947560`, MOR-1947): loader + `ftx1.toml` + `ic7300.toml` data from the measurements | `profiles/rig_loader.py`, 2 TOMLs, tests | [BP] — parsed, consumed by nothing | golden dry-run gates |
| 3b | ~~**CI glob**~~ — **DONE, merged `9a05ecb0`** (#2767, MOR-1911): `rigs/**` and `contracts/**` are both entries of `quick.yml`'s `core:` filter now. Correction, 2026-08-21: the row's justification — "today a profile-only PR runs zero CI" — was true when written and is false from `9a05ecb0` onward | `.github/workflows/quick.yml` | [BP] | a `rigs/`-only test PR triggers the core job |
| 4 | ~~**Conformance matrix skeleton + fake extensions**~~ — **DONE, merged `07d40580`** (#2772, MOR-1913): (§3.10 items 1, 3 — including the queue-path rows). Lands **before** the primitive and the Yaesu honesty rows so their pins exist when they cite them — the house pattern (`2026-06-09-target-audio-architecture.md:959-960`: fakes and conformance before every component) | `tests/contracts/` + fakes | [BP] | capability rows xfail until cutovers |
| 5 | ~~**The read primitive**~~ — **DONE, merged `24eac81d`** (#2774, MOR-1914), on all three backends; issue-first: `read_transmit_state()` on a **new capability protocol `TransmitStateReadable`** — not on the runtime-checkable `Radio` (§3.9: a required member there would silently break `isinstance` for lacking implementers; `open-core-policy.md:178-180` classes it breaking); fail direction for a backend without it: every HAZARD admission refuses `tx-truth-unavailable` with `failure="no-capability"` (the four families unconditionally; `set_freq` on a resolved crossing). Icom implementation applies the directed-exact-reply shape check (`_civ_rx.py:2636-2650`) **itself** — three named implementation traps: `CivRequestTracker` matches `(command, sub, receiver)` with no address check (`core/civ.py:76-82,380-393`), so the shape check cannot be inherited; do not build on `execute_civ_transaction` (single-slot, raises on concurrent use, `_civ_rx.py:1027-1028`); do not reuse the poller's `Commander.send(dedupe=True)` key (`commander.py:151-156` would hand back a pre-decision in-flight read, gutting INV-4); `request_authoritative_ptt_read` is not reusable (observer-bound: def `_civ_rx.py:679`, binding check `:718-732`). Yaesu/rigctld-client adapt their existing `read_ptt` through `tx_state_map` (rigctld-client marked `verified_readback=False`, §3.7). Split 5a (protocol + Icom) / 5b (Yaesu + rigctld-client + conformance rows) | `core/radio_protocol.py`, `runtime/radio.py` or `runtime/_civ_rx.py` / two backends, tests | [BP] additive | conformance: an ACK/setter echo/mis-addressed frame cannot satisfy the read (INV-13 mutation); `TX2` golden; rigctld-client `verified_readback=False` pin |
| 6 | ~~**Yaesu truth honesty**~~ — **DONE, merged `a1cf9f48`** (#2773, MOR-1941): `tx_state_map`-driven parse (subsumes 0c's predicate into the table), typed refusal surfaced, and the `set_ptt` self-mutation deleted — all three shipped; correction 2026-08-21, the row read "delete the `set_ptt` self-mutation (`radio.py:994`)" as future work after `a1cf9f48` had already deleted it | `backends/yaesu_cat/radio.py`, `observations.py`, tests | [BC]: FTX-1 PTT indicator follows readback only (one poll-cycle lag). Bench: front-panel key on FTX-1 → UI shows TX with `attributed="tx_other"` | unmapped value ≠ RX; self-write emits no observation → restore either → red |
| 7 | **Icom admission**: construct the authority in **both** Icom connect paths — `CoreRadio.connect()` (the LAN path the SDK facade builds directly, `sync.py:37,136-145`) and the serial base — so INV-15 holds from day one; admission calls at the GATED-map methods, with the vfo-family admissions at the outer methods themselves — `equalize_main_sub`, `swap_vfo_ab`, `equalize_vfo_ab`, **`swap_main_sub`**, **`select_receiver`** and `_set_vfo_slot_impl` — six members (correction, 2026-08-21: this row named four, silently contradicting §3.3 and Q13, which flagged the other two as unclassified; an owner ruling of 2026-08-21 put them in the hazard family and §3.3 now carries all six — that ruling postdates the §9 record and is not in it, provenance at Q13. `swap_main_sub` does not reach `_set_vfo_wire` at all — it builds its own `_CMD_VFO` frame — so no seat below it would cover it) (§3.2: `_set_vfo_wire`, `runtime/radio.py:3945`, is neither necessary nor sufficient and is **not** the seat), leaving the cross-module profile-restore caller `radio_state_snapshot.py:105` as a named residual. **KEYING entries admit-and-record but arm no deadline until rows 12a/12b bring the drivers** — otherwise a deadline exists for four rows with nothing to fire it. Declared deviation, and the file inventory is **five src files, not three** (correction, 2026-08-21 — the earlier three-file list omitted two modules holding four of the five members INV-1 names as prefix-rule-proof, and understated what the serial base defines). `CoreRadio`'s public non-read async surface, introspected over the MRO, spans four modules: `runtime/radio.py` (118 members: freq/mode/ptt/antenna/tuner/cw/powerstat/memory + LAN construction), `runtime/_scope_runtime.py` (18, including `enable_scope`/`disable_scope`/`set_scope_during_tx`), `runtime/_audio_runtime_mixin.py` (17, including `start_tx`/`push_tx`/`stop_tx`), `runtime/_dual_rx_runtime.py` (6: `equalize_main_sub:328`, `swap_vfo_ab:347`, `equalize_vfo_ab:378`, `swap_main_sub:309`, `select_receiver:447`, `set_vfo_slot:525` over `_set_vfo_slot_impl:545`). The fifth file is `backends/_icom_serial_base.py`, and it is not construction-only: **the serial base defines write methods of its own** — `enable_scope`, `disable_scope` (which issues an extra `_scope_off_cmd` CI-V frame *after* `super().disable_scope()`, so gating the parent does not cover it), `start_rx`/`stop_rx`, `start_tx`, `push_tx`, `stop_tx` and the audio-TX push surface. What is true, and is all the earlier sentence should have claimed: the *per-model* subclasses `Ic705SerialRadio`, `Ic7300SerialRadio` and `Ic9700SerialRadio` define none, and `Icom7610SerialRadio` defines exactly one, `stop_audio_rx_pcm`. The declared deviation is therefore against five src files plus tests, not three; whether that is one PR or several is the row owner's call, but it has to be made against the real number. **Seat the slot admission at `_set_vfo_slot_impl`, never at the `set_vfo_slot:525` alias.** The alias pair has two entry points and one shared body: `set_vfo_slot:525-538` and `_set_vfo_slot_confirmed:540-543` both delegate to `_set_vfo_slot_impl`. Admitting at `set_vfo_slot` leaves the confirmed path ungated, and that path is live — `web/radio_poller.py:4305` resolves `_set_vfo_slot_confirmed` by `getattr` and awaits it at `:4312`, never going through `set_vfo_slot`. **No pin would catch the gap:** both `_set_vfo_slot_impl` and `_set_vfo_slot_confirmed` are underscore-private, so INV-1's public-member enumeration skips them, and the INV-2 pin only visits methods listed in the GATED map — this paragraph is the only guard there is. **Note (corrected 2026-08-21 — this row previously said "Icom has no `set_band`", which is false and told its builder there was nothing to gate):** Icom *does* expose `set_band`, as a backward-compatibility alias `set_band = set_bsr` in `runtime/radio.py`'s alias block. It has no `async def` of its own, so nothing that scans definitions will find it, but `inspect.getattr_static(CoreRadio, "set_band")` resolves to the coroutine `set_bsr` and a caller reaches a real band-select write — though not through the protocol's signature, which is a separate live defect described in §3.3 and filed on its own. Two consequences for this row: the BAND family needs an entry for `set_band` in its own right, **and** the whole alias block is a category — `set_frequency = set_freq`, `set_power = set_rf_power`, `set_band_stack = set_bsr`, `start_scan = scan_start`, `stop_scan = scan_stop` — that the map and the INV-1 enumeration must enumerate (§3.2, "A public alias is a category"). Separately and additionally, the web UI's composed `SetBand` command path gates through its `set_freq` (§3.3) | 3 src + tests | [BC]: previously-ungated Icom paths (CLI, SDK, HTTP, CW, raw-adjacent typed writes) now gated. Bench: full normal session on IC-7300 at RX — indistinguishable; CLI `set-tuner 0` during a front-panel key → refused with evidence | INV-2 call-site pin live for Icom; remove the admission (either accepted form) from any one method → red; seat the slot admission at `set_vfo_slot` instead of `_set_vfo_slot_impl` → the confirmed-path conformance row goes red |
| 8 | **Yaesu + rigctld-client admission**: same, at each backend's **real** write surface — on Yaesu that means the backend-only bodies the queue drain actually calls (`set_tuner`, `set_vfo_select`, …), with alias chains (`set_tuner_status → set_tuner` — real method delegation) admitting exactly once at the innermost body (§3.2). **Do not extend that to a shared command template** (added 2026-08-21): `select_receiver` and `set_vfo_slot` each emit `VS` through `self._write("set_vfo_select", …)` without calling the method `set_vfo_select`, so all three bodies need their own entries — seating only at `set_vfo_select` leaves a ruled HAZARD member ungated on five live call paths. §3.2, "A shared command template is not an alias chain". Declared deviation, split **8a (Yaesu)** / **8b (rigctld-client)**: the pinned per-backend method maps cover ~97 and ~12 write methods respectively, and the Yaesu map literal alone exceeds 200 LOC | 8a: `backends/yaesu_cat/radio.py` + tests; 8b: `backends/rigctld_client/radio.py` + tests | [BC]: FTX-1 and hamlib-provider writes gated on every path incl. the web queue. Bench: FTX-1 tuner write from web during front-panel key → refused | conformance queue-path rows go green; drive a write through the poller queue with the admission removed → red; double-admit on the alias chain → the one-decision-per-write pin red |
| 9 | **Web seat deletion**: `_enforce_tx_interlock`, `_WEB_IMMEDIATE_BLOCK_FAMILIES`, `_current_rf_state`, the staging lane + instance (taking the `heldBy:"tx_interlock"` emitter at `radio_poller.py:818` with it); `control.py`: `_observed_rf_state`, the tuner seat, the CW-auto-tune seat. **Retained, rewired, not deleted**: the shipped raw-during-TX refusal (the `RAW_CIV` arm of the immediate-block set, covering web `send_civ` at `:2271-2281`) survives as a named ingress-side strictness check reading `TransmitTruth` — refusing when truth reads TX **or is unknown/stale** (fail-closed, today's web semantics; Q12); and the bootstrap `connection_epoch_bootstrap` exemption stays exactly as shipped — but see the **open Q15** in §3.5: keeping it here gives the bootstrap `SelectVfo` no cover at the backend admission rows 7/8 add, because the flag is a parameter of this poller's `_execute` and cannot cross into `TransmitAuthority.admit`. This row must not be read as having settled that. Declared deviation: + `tests/test_radio_poller_tx_interlock.py`, `tests/conftest.py`, `tests/test_web_teardown_unkey_gate.py` (measured blast radius) — 2 src + 3 test files | `web/radio_poller.py`, `web/handlers/control.py`, tests ×3 | [BC]: **mode/split** during TX now pass on web (mode on FTX-1: radio refuses, reason shown). Correction, 2026-08-21: this cell said frequency too; frequency has passed on web since MOR-1940 (`9fc90943`) made `FREQUENCY` `TX_SAFE` — it is neither in `_WEB_IMMEDIATE_BLOCK_FAMILIES` nor base-DEFER, so `_enforce_tx_interlock` returns before consulting RF truth. Bench: split work while keyed on IC-7300 — dial moves; tuner button during front-panel key — refused with reason | matrix web column; re-add a local FRESH-only read → conformance red; drop the raw-during-TX check → its characterisation pin red |
| 9b | **Web refusal wire + held-surface retirement**: widen the sanitized `failed` envelope additively with `{code, evidence}` — extending the two-code `{session_id, blockedBy, reason}` shape #2745 shipped (`web/server.py:2161-2187`) to the full vocabulary (other failures still reach the browser as prose, `:2089-2098`); new i18n namespace `core.commandRefusal.*` mapped per `TxRefusalCode`; delete the dead held surface — the `queued`/`heldBy` whitelist branch (`web/server.py:2126-2141`), the frontend parser (`ws-client.ts:599-602`) and `kind: 'held'` (`commands.svelte.ts:34,272-277`) — dead once rows 9/11 delete both lanes. Declared deviation: 1 src + 2 frontend files + tests; **frontend CI block (R7)** | `web/server.py`, `frontend/src/lib/transport/ws-client.ts`, `frontend/src/lib/stores/commands.svelte.ts`, tests | [BC] additive envelope; frontend | wire-map web column goes real; emit a refusal without a typed code → red |
| 10 | **rigctld seat deletion + rendering**: `_defer_write_gate` + 8 call sites, `_classify_rigctld_tx_intent`, `_resolve_rigctld_rf_state`; the executor BLOCK pre-gate is **narrowed, not deleted**: its raw arm survives as the rigctld raw-during-TX check reading `TransmitTruth`, refusing at TX **and at unknown/stale truth** (fail-closed — today's armed-gate semantics; the latent no-store fail-open branch is deleted as the one declared flip, Q12), everything else goes; renderer maps `TxRefusal` (one `except` clause in the typed ladder, `handler.py:1328-1345`) and radio-refusals per §3.8; **rewrite the `## rigctld write handling during transmit` section of `known-limitations.md`** — the whole section, including the MOR-1940 exemption bullet appended to it after this row was written; correction 2026-08-21, the row cited `:67-84`, a range that now stops short of that bullet. Declared deviation: + `tests/test_rigctld_tx_interlock.py` and the docs file — 1 src + 1 doc + tests | `rigctld/handler.py`, docs, tests | [BC]: mode-during-TX on FTX-1 answers `RPRT 0` with no write (hamlib-identical). Correction, 2026-08-21: this cell also claimed WSJT-X "Fake It" while keyed "now moves the dial (was dropped)" — that already happened at MOR-1940 (`9fc90943`), and the known-limitations file records it as shipped, so it is not this row's behaviour change. Bench: WSJT-X full QSO cycle on IC-7300; `T 1` latency unchanged | fake-rigctld wire matrix incl. freq-during-TX-passes row and RPRT-0-only-when-TX-confirmed row; render an unbounded refusal as `RPRT 0` → red |
| 11 | **Yaesu poller seat deletion**: private resolver, second lane — **both of its construction sites**, `yaesu_cat/poller.py: YaesuCatPoller.__init__` and the fresh lane rebuilt inside `yaesu_cat/poller.py: YaesuCatPoller._cancel_deferred_entry` (correction, 2026-08-21: this row and §5 both named only the first, and a PR that deletes one leaves a live `DeferredTxCommandLane()` construction that row 15b's module deletion then breaks) — taking the `heldBy` emitter at `poller.py:856` with it, drain defer condition, execute raise condition, both override call sites; `rigctld/server.py`'s `_derive_tx_active` → `TransmitTruth` | `backends/yaesu_cat/poller.py`, `rigctld/server.py`, tests (+ `tests/test_rigctld_server.py` — declared) | [BC]: base-DEFER-at-UNKNOWN no longer executes on FTX-1 (family now PASS or hazard-gated). Bench: FTX-1 frequency write with CAT idle — applied | matrix Yaesu column; restore the `rf_state is TX` guard → red |
| 12a | **Deadline, web drivers — KEYING deadlines go live here**: the Icom-branch driver arms `loop.call_later` from the authority's deadline (keeping today's firing mechanism, `radio_poller.py:1023-1027` — independent of a wedged drain) whose callback enqueues `PttOff` (audio teardown + identity preserved — this branch's pin); the `ObservationPollable` branch's poller drives the deadline from its drain and enqueues `PttOff` into the queue it drains (correction, 2026-08-21: this read "its arm is a plain `set_ptt(False)` today … semantics preserved", which implied an existing key-down bound to preserve. There is none — `backends/yaesu_cat/poller.py` has no `call_later`, no max-key-down constant and no backstop of any kind. `:1018-1019` is the drain's `case PttOff(): await radio.set_ptt(False)` command arm, i.e. the rail the new effect rides, not an existing timer. Row 12a **introduces** the bound on this branch); the private timer *state* (`:263` constant, arming bookkeeping in `:1016-1052,2488`) moves into the authority | `web/radio_poller.py`, `backends/yaesu_cat/poller.py`, tests | [BC]: web-CW keys now bounded, on both branches. Bench: web key on IC-7300, kill the tab — unkeys ≤180 s as today, via the same call-later + enqueue path; same on FTX-1 | per-branch driver test on fake clock: effect → enqueued `PttOff`; on the Icom branch, fire the OFF directly instead of enqueuing → the audio-teardown pin red; wedge the drain → the call-later still fires (MOR-1181 pin) |
| 12b | **Deadline, rigctld + last-resort drivers**: rigctld server drain drives `poll()` (absorbs MOR-1904's timer; declared-profile scope stated in §3.6); Icom transport idle-loop / serial-watchdog hooks as last-resort driver; the Yaesu/rigctld-client no-delivery residual and the SDK while-loop-runs honesty documented in code | `rigctld/server.py` or `handler.py`, `runtime/radio.py` hook, tests | [BC]: rigctld- and SDK-issued keys bounded (SDK: while its loop runs). Bench: key IC-7300 via rigctld, kill the client — unkeys ≤180 s (MOR-1904 parity) | per-delivery bound matrix; skip the arm on one path → red |
| 13a | **Delete dead truth**: `StateCache.ptt/ptt_ts` + its `update_ptt` + the `_civ_rx.py:1656` writer. (`rigctld/handler.py:436` *defines* `_FallbackRigState`'s own `update_ptt` — a different, retained object (§5); no `handler.py` site calls the StateCache one) | `core/_state_cache.py`, `runtime/_civ_rx.py`, tests | [BP] — zero readers, verified | grep-pin; re-add a reader → red |
| 13b | **Delete the launders**: SDK ptt mint (`sync.py:92-101` scoped to ptt), the `split` launder (`handler.py:2699-2704`, MOR-1901), the web legacy-mirror TX rows | `runtime/sync.py`, `rigctld/handler.py`, `web/server.py`, tests | [BC] for store consumers of those rows — release-note flagged | store-silent-on-self-write conformance row; restore the mint → red |
| 13c | **`TransmitTruth` consumer cutover + producer tightening**: `tx_safety_view`, the meter spelling, the scheduler `tx_only` hint read the one view; the `civ_unsolicited` tag gains the `from_addr == radio_addr` condition (§3.7, `_civ_rx.py:2635-2637`); the retained `command_service.py:241-249` docstring — whose rationale names the deleted interlock and the MOR-1892 casualty — is rewritten to its surviving generic-readback justification | `web/tx_safety_view.py`, `web/radio_poller.py`, `runtime/_civ_rx.py` (+ the docstring file), tests — declared 4-file deviation, three of them one-paragraph edits | [BP] | provenance pin consumed at every remaining reader; un-tighten the unsolicited tag → sources-pin conformance red |
| 14 | **ptt max-age → profile**, two independent halves in two backends (correction, 2026-08-21 — the row read as one mechanism and seated the FTX-1 change in the Icom file). (a) `_civ_rx.py:101`'s ptt row profile-sourced with the hardcode as fallback: that table is `_OBSERVATION_MAX_AGE_SECONDS` on the **Icom CI-V** path, its ptt row is **already 1.0 s**, and `rigs/ic7300.toml` already declares `freshness_ttl_seconds = 1.0` for `global.tx_state.ptt` — so on the bench Icom this half is value-neutral and buys consistency, not a window change. (b) FTX-1 is the **Yaesu** backend and never enters `_civ_rx.py`; its 8.0 s window is `rigs/ftx1.toml`'s `[state_acquisition] default_freshness_ttl_seconds`, reached because the profile declares no field policy for `global.tx_state.ptt`. Closing it means adding `[state_acquisition.field_policies."global.tx_state.ptt"]` with `freshness_ttl_seconds = 1.0` | `runtime/_civ_rx.py`, `rigs/ftx1.toml`, tests | [BC] ftx1 display truth window 8.0→1.0 s — delivered entirely by half (b). Bench: FTX-1 UI PTT flicker rate under idle CAT | golden gates; ignore the profile → red |
| 15a | **Remove the `[tx_interlock]` hook**: loader parse/validation (`rig_loader.py:575,746,1632,1682-1734,1761,2219-2221,2295`) + carrier field (`profiles/__init__.py:334-336`) | `profiles/rig_loader.py`, `profiles/__init__.py`, tests | [BP] — zero shipped users, verified | loader test updates |
| 15b | **Terminal deletion**: `runtime/tx_interlock.py` (437 LOC — corrected from 435, 2026-08-21) + `core/tx_interlock_contract.py` (105 LOC) — the four importers of `runtime/tx_interlock.py` are deleted by rows 9-11 (verified importer census: `web/radio_poller.py`, `web/handlers/control.py`, `backends/yaesu_cat/poller.py`, `rigctld/handler.py`), and `core/tx_interlock_contract.py` has four importers of its own, two of which (`profiles/rig_loader.py`, `profiles/__init__.py`) are removed by row **15a**, not by 9-11 — so 15b cannot run before 15a; test files (`test_tx_interlock_policy.py` et al.) retired with it. Declared deviation: ~1000 LOC of deletions, 2 src + ~4 test files | module + test deletions | [BP] | import of the deleted modules anywhere → suite red |
| 16 | **Docs + supersession**: guide updates (PR #2760 already touches `docs/guide/cli.md`/`web-ui.md` — extend); the 2026-08-19 ADR is untracked, so there is nothing to "delete" from git — **move it to `rigplane-archives/`**, beside the three evidence documents this ADR cites there, preserving its reasoning: the facade refutation survives in §3.2 here, and the causal model's argument should stay readable in the archive, because the case for not building it rests on one two-evening, two-radio measurement with bench items still open; as-built header on this document | docs + archives | — | — |

Rollback unit is one row: rows 9–11 are call-site deletions around a
still-present authority (revert restores the seat above it — safe, monotone);
rows 7–8 revert to ungated methods with the old seats still standing.

## §5 What is deleted — the ledger, by name

Everything below either dies in a named row or is retained with its reason.
Silence would be a ninth mechanism; there is none.

| Mechanism | Fate |
|---|---|
| The previous ADR's causal model — boundary ledger, identity-bound solicitation, pending budgets, clearing rules, `RfTruthState.PENDING` | **Not built.** The defect it addressed (MOR-1892) has no member once frequency is ungated; the document is superseded and moved to `rigplane-archives/` (row 16) |
| The first draft of *this* ADR's `GuardedRadio` facade | **Not built** — refuted by its own adversarial review (§3.2); recorded so nobody re-proposes it without answering the same findings |
| The first revision's Hamlib-parity **unkey-first band sequence** (unkey, settle 200 ms, confirm, write) | **Not built** — owner ruling Q2: a band change under key is refused, never made safe by auto-unkeying; B5 loses its consumer |
| The first revision's **`RELAY_HOLDOFF_SECONDS` cached-TX holdoff** | **Not built** — it could not be implemented from `TransmitTruth` (newest observation only, no last-TX high-water mark), and the owner deleted the mechanism rather than repairing it (which also spares `TransmitTruth` the extra field); the accepted residual is stated in §3.5 |
| `TxInterlockDisposition.DEFER` + `_DEFER_TYPES` (**14** dataclasses — corrected from 17, 2026-08-21: MOR-1940 (`9fc90943`) removed the frequency and RIT/XIT entries) | **Deleted** (rows 9–11, 15b) — the class is empty after reclassification |
| `DeferredTxCommandLane` + all **three** construction sites (`radio_poller.py:723`, `yaesu_cat/poller.py: YaesuCatPoller.__init__`, and `yaesu_cat/poller.py: YaesuCatPoller._cancel_deferred_entry`, which rebuilds a fresh lane on cancel) + TTL/quiet constants. Corrected from "both instances", 2026-08-21: there are two lanes but three constructions, and a row-11 PR that deletes the two named sites leaves a live `DeferredTxCommandLane()` for row 15b's module deletion to break | **Deleted** (rows 9, 11, 15b) — nothing holds writes anymore |
| The six RF-resolver spellings (§1.3) | **Deleted** (rows 9–11, 13c) → one `TransmitTruth` view; the hazard gate reads the radio instead |
| `_defer_write_gate` + 8 call sites + `_classify_rigctld_tx_intent` + executor BLOCK pre-gate + the no-store fail-open branch | **Deleted** (row 10) — except the pre-gate's raw arm, retained-rewired (see the raw row below); the fail-open dies with the seat, not by flipping its default |
| `_enforce_tx_interlock` + `_WEB_IMMEDIATE_BLOCK_FAMILIES` + `_stage_tx_interlocked_entries` | **Deleted** (row 9) — except the raw arm, which is retained-rewired (see the raw row below) |
| The `connection_epoch_bootstrap` exemption (`radio_poller.py:2246,2256-2257,4024`) | **Retained** — one named constant, one call site, same pin; a timed-out connect-time read must not make VFO/RF identity unobservable (§3.5). **Open (Q15):** retaining it here does not extend it to the backend admission — the flag is a parameter of the web poller's `_execute` and `TransmitAuthority.admit` has no channel for it, while the write it exempts dispatches into `_set_vfo_slot_impl`, the seat §3.2 designates. Unresolved by this document |
| The `heldBy:"tx_interlock"` held surface — emitters (`radio_poller.py:818`, `yaesu_cat/poller.py:856`), the whitelist branch (`web/server.py:2126-2141`), the frontend parser (`ws-client.ts:599-602`), `kind:'held'` (`commands.svelte.ts:34,272-277`) | **Deleted** (rows 9, 9b, 11) — dead the moment both lanes die |
| Web tuner seat + CW-auto-tune seat + `_observed_rf_state` (`control.py`) | **Deleted** (row 9) |
| Yaesu private resolver, drain/execute conditions, override call sites | **Deleted** (row 11) |
| `evaluate_tx_interlock`, `classify_tx_interlock`, `RfState`, `TxInterlockDecision`, `runtime/tx_interlock.py`, `core/tx_interlock_contract.py`, the `[tx_interlock]` profile hook | **Deleted** (rows 15a/15b) — not on the Pro export pin (verified: no `tx_interlock` in `tests/test_public_api_surface.py` or `tests/contracts/test_lazy_imports.py`); superseded by `[tx_policy]` and `core/tx_authority.py` |
| `StateCache.ptt/ptt_ts` + `update_ptt` + writer | **Deleted** (row 13a) — zero readers, verified |
| rigctld `t`-poll launder; `split` launder; SDK ptt self-write mint; web legacy-mirror TX rows; Yaesu `set_ptt` self-mutation | **Deleted** (rows 0a, 6, 13b) — of these the `t`-poll launder (`6bdb5846`) and the Yaesu self-mutation (`a1cf9f48`) have already shipped; the `split` launder, the SDK mint and the web mirror rows remain |
| Web max-key-down timer machinery (`radio_poller.py:1016-1052`); MOR-1904's rigctld timer | **Absorbed**: the deadline state moves into the authority; the web keeps its enqueue routing as the driver (row 12a), rigctld its unkey path (row 12b) |
| MOR-1892's shipped re-observe (`command_service.py:228`, `_request_write_confirmation`) | **Retained, reclassified**: generic post-write readback reconciliation, not transmit machinery; outside this boundary |
| `TxSafetySupervisor` + `_Boundary` + `managed_tx_ingress` + `ManagedTxApi`/`PrivilegedTxApi` | **Retained unchanged** — the keying reducer for managed radios; the authority consults the lease read-only for attribution, never duplicates it |
| MOR-1878 keyer identity + teardown bias rules | **Retained** seat-side (ingress may add strictness) |
| `RadioState.ptt` | **Retained with reason**: broad display surface, gate-invisible (no gate reads it once rows 9–11 land) |
| `_FallbackRigState` (`handler.py:397-450`) | **Retained with reason**: wire-compat fallback for the mode/data-mode rendering — the only field of it that anything **reads** is `data_mode` (`handler.py:1279`, written at `:1668-1670`). It is written more widely than it is read: `rigctld/routing.py:236,247,252` also update `s_meter`/`rf_power`/`swr` on the same instance (handed over at `handler.py:694,697`), into fields no reader in `src/` consults. Correction (2026-08-21): it is **not** the `t` mirror — `t` answers from the canonical projection first (`handler.py:1740-1744`) and falls back to `RadioState.ptt` via `_radio_state()` (`:924-926`, consumed `:1746-1768`), never to this class. Nothing in `src/` calls `_FallbackRigState.update_ptt` (`:436-438`) or `is_fresh` (`:417-421`), so `ptt`/`ptt_ts` (`:408-409`) are permanently `False`/`0.0` and `is_fresh("ptt", ttl)` is always false — apparent dead code, recorded here for the cutover epic and deliberately not deleted by this documentation change (MOR-1902 stays a wire-owner decision, outside) |
| Raw CI-V paths (`web/server.py:411`, `handler.py:547-561`, `hamlib_bridge.py:304`) — and **the shipped raw-during-TX BLOCK** at the web immediate-block seat and the rigctld executor pre-gate | Paths: **excluded by name** (`RAW_EXCLUDED`, §3.3) — bytes cannot be classified, and the rigctld `w` and hamlib-bridge paths sit below the typed layer where the authority cannot even observe them; the read-only/authorization gap stays its own ticket, and T6 names raw as a bound residual. The BLOCK: **retained, rewired to `TransmitTruth`** at both seats (rows 9/10, Q12) — a shipped transmit-time protection is not silently loosened |
| `RPRT 0` for the **policy-dropped** write during known TX (`_defer_write_gate`, `handler.py:770-833` — a pre-`CommandService` drop, not a refusal by the radio, which never sees the write; row 10 keeps the rendering and changes the trigger); the raw-during-TX refusal (Q12); the one-sided unkey **of the interlock** — scope, not a system-wide guarantee: on a managed radio `_route_ptt` answers `RPRT 0` and writes nothing when `managed.set_ptt(False)` returns `TxOutcome.STALE`, the lease being another owner's (`handler.py:1991-2003`, deliberate per MOR-1175, pinned by `tests/test_rigctld_managed_tx.py:223-244`; the web twin discards the same outcome at `web/radio_poller.py:2528-2532`); `t` answered from the canonical projection where it has one (`handler.py:1740-1744`) and from `RadioState.ptt` **beneath** it (`:924-926`, consumed `:1746-1768`) — the mirror is the fallback, not the primary; teardown biased toward OFF; ungated `force_unkey`; read-only EACCESS; watchdog-honesty view rules | **Retained deliberately**, each pinned at row 2 (§3.10 item 6 — the same list) |

## §6 Invariants

Each with its test and the mutation that must go red; "usually" does not
appear. (An earlier framing claimed every row is phrased as *the system never
X* — several are positively phrased, and the totality hole hid exactly behind
a positive phrasing, so the claim is dropped rather than the phrasing forced.)

| # | Invariant | Test | Mutation → red |
|---|---|---|---|
| INV-1 | The classification is total over the real write surface, **deny-by-default**: every public `async def` member of `Radio`, of every capability protocol, and of each backend class (Yaesu ships 29 backend-only writes — corrected from 25, 2026-08-21; the enumeration rule is stated in §3.2) is either in that backend's GATED map, in the pinned non-write allow-list (reads, lifecycle, audio-RX subscriptions), or in `RAW_EXCLUDED` — a name-prefix rule is forbidden (it misses `reset_clarifier`, `enable_scope`/`disable_scope`, `start_tx`/`push_tx`, the whole vfo-select family) — and neither may a definition-scanning rule, which misses the public aliases that have no `async def` of their own (`set_band = set_bsr` and its neighbours, §3.2), and a new member anywhere cannot default to PASS by omission | source-level totality test per backend: enumerate all public `async def` members, assert map ∪ allow-list ∪ `RAW_EXCLUDED` covers them exactly | add a write method under **any** name without a map entry — including one no `set_*` prefix would catch |
| INV-2 | Every GATED-map method admits exactly once per write, and the admission is **statically discoverable at the method itself**, in either of exactly two accepted forms. **Form A — the decorator, named `@tx_admit`**, to be exported from `core/tx_authority.py` beside the shipped `TransmitAuthority.admit` (`core/tx_authority.py: TransmitAuthority.admit`) — the decorator itself does not exist in the tree yet; row 7 adds it. It is named here because the AST pin has to match some identifier, and leaving that unnamed was the gap in the first reformulation. It is **bare — zero arguments, no parentheses**: `@tx_admit`, never `@tx_admit(family=…)` or any other parameterised spelling. That restriction is the whole point of naming it: a decorator that accepted a family or class argument would satisfy every other word of this invariant while quietly becoming a second source of classification competing with the pinned per-backend map. `tx_admit` resolves the method's family at call time from the map, keyed by the wrapped function's own `__name__`, and from nothing else. **Ordering under stacking is required, not optional:** `@tx_admit` must be the **outermost** decorator, `decorator_list[0]` in the AST, so no other wrapper can reach the transport before the admission runs — the pin asserts the position, not merely the presence. **Form B — the in-body block**, `async with self._tx_authority.admit(...)` as the first awaited statement of the body, wrapping the write; `admit` is an async context manager whose lock spans the write (`core/tx_authority.py: TransmitAuthority.admit`), so this form is a `with` block and never a bare `await`. Form B is for methods whose body needs the yielded `TxAdmission` (`core/tx_authority.py: TxAdmission`) in scope. The innermost named body of each gated method/alias chain carries one of the two; an alias onto another gated body never adds a second; a gated method carrying neither form is a violation. The decorator names the method for the pinned per-backend map and is never a second, competing source of classification. **Reformulated by owner ruling, 2026-08-21** (the first revision required the in-body form only): row 7 gates ~100 Icom write methods, where the literal reading cost 800–1000 changed lines of almost pure re-indentation. The cost of the decorator form is accepted knowingly and stated rather than papered over — the two forms are **not** equivalent to a reader: with a decorator the admission is no longer visible to someone reading only the method body | AST call-site pin per backend, accepting either form, rejecting neither-form, rejecting a parameterised `@tx_admit`, and asserting `decorator_list[0]` for form A (the MOR-1884 pin pattern) + a one-decision-per-write log assertion | remove **both** forms from any one method; add a second admission to an alias; give `@tx_admit` an argument; or move it below another decorator |
| INV-3 | PASS-class writes never consult transmit truth: with a poisoned truth provider and a poisoned read callable, every PASS-family call still succeeds. (A regression pin: PASS methods carry no admission today by construction — this keeps it that way when someone "helpfully" adds one) | poisoned-provider test over the full PASS table | add a truth read to the PASS path |
| INV-4 | A HAZARD write is sent only after a solicited RX answer obtained inside the same admission, under the per-radio admission lock, with **no KEYING admission and no TX observation between the read and the write** — never from cache, never on a read another admission obtained | fake-wire ordering test (read precedes write; no read → no write) + an interleaving test (a KEYING admission injected between read and write invalidates the read) | satisfy the gate from `TransmitTruth`; or let a concurrent admission share the read; or admit `set_ptt(True)` between read and write without invalidation |
| INV-5 | **The authority never refuses an unkey**: the UNKEY dispatch branch has no refusal path and clears the deadline unconditionally. Scope, stated rather than widened (correction, 2026-08-21 — the earlier bare phrasing "an unkey is never refused" claimed more than the tree does): a separate ownership mechanism above the authority can still decline one. On a managed radio `_route_ptt` answers `RPRT 0` and writes nothing when `managed.set_ptt(False)` returns `TxOutcome.STALE` — the lease is another owner's and rigctld holds no privileged force (`handler.py:1991-2003`, deliberate per MOR-1175, pinned by `tests/test_rigctld_managed_tx.py:223-244`; the web twin discards the same outcome at `web/radio_poller.py:2528-2532`). This invariant constrains the authority's dispatch and does not claim that behaviour away | branch-enumeration test + poisoned-table variant | add a refusal branch to UNKEY |
| INV-6 | A corrupt or incomplete classification table never makes a de-key, power-off or `stop_cw_text` harder — nor an unreadable `set_ptt` / `set_powerstat` argument, which the same short-circuit resolves to the strict twin, PTT_ON or POWER_ON, neither of which has a refusal path (MOR-1954): the T5 short-circuit precedes all table and profile data. **Scope, stated rather than widened:** it covers those three methods only. For every other family an unreadable argument fails *closed* and may well refuse — an unreadable `set_freq` is classified BAND and refused at TX where the readable in-band value passes | poisoned-table test, driven positionally, by keyword, and with an argument the engine cannot read | reorder the short-circuit after the table; or answer the off twin for an unreadable argument |
| INV-7 | Every admitted unmanaged key — and every admitted tune start and CW send (the other own-transmit initiators) — from every ingress, arms the one deadline; every shipped driver executes the OFF effect by **enqueuing** `PttOff` on its web branch (Icom branch additionally preserving the audio-teardown/identity arm — that pin is Icom-branch-scoped, §3.6) or through the rigctld unkey path / backend last resort. The named residuals — bare Yaesu/rigctld-client with no delivery, a dead SDK process, raw CI-V — are listed in §3.6/T6, not hidden by this row | per-delivery matrix on a fake clock (web×2 branches, rigctld, Icom backend-loop last resort) + the Icom-branch enqueue pin | skip the arm on any path; or fire the Icom-branch web OFF directly instead of enqueuing |
| INV-8 | `TransmitTruth` accepts only radio-readback provenance: no observation outside `RADIO_READBACK_SOURCES`, and no lease/ACK/write outcome, changes it | sources-pin test + conformance self-write row | add `"command_response"` or `"state_poller"` to the frozenset |
| INV-9 | RX is only ever produced by a positive mapping: an unmapped transmit-state wire value never reads as receiving, on any backend | per-backend parse tests incl. the `TX2` golden | restore `== "1"` (MOR-1905's own mutation) |
| INV-10 | The bounded lie never widens: the rigctld renderer answers `RPRT 0` without an applied write only when family ∈ `refused_during_tx` ∧ the radio refused ∧ `TransmitTruth` reads TX within its max-age (a bare `?;` can mean "unsupported command" — `observations.py:1097-1099` — and success for that would never clear); every `TxRefusal` renders as an error | wire-map totality pin + the `?;`-outside-TX row | render `TX_TRUTH_UNAVAILABLE` as `RPRT 0`; or render a `?;` at unknown truth as `RPRT 0` |
| INV-11 | The authority performs no I/O except through its two injected callables (the solicited read; the last-resort unkey) and the one structured refusal log line (§3.4); it returns effects as data, emits no events and opens no sockets | import/awaitable allowlist pin on `core/tx_authority.py` (no `asyncio.create_task`, no transport/socket imports, awaits limited to the two injected callables; `logging` permitted for the refusal line only) | add any other I/O |
| INV-12 | Headless isolation: `core/tx_authority.py` imports nothing above `core`; the authority's full suite passes with `rigplane.web` poisoned out of `sys.modules` | `lint-imports` + isolation test | import `rigplane.web` |
| INV-13 | The solicited read cannot be satisfied by an ACK, a setter echo, or an unrelated frame — on **all three backend families**: the CI-V implementation validates the directed-exact-reply shape; the Yaesu implementation a typed parse; the rigctld-client implementation is permanently `verified_readback=False` and never admits a hazard | conformance rows with scripted wrong-frame replies, incl. the rigctld-client row | accept a `command_response`-shaped reply; or set `verified_readback=True` on rigctld-client |
| INV-14 | Every refusal carries evidence, **no exceptions**: `TxRefusal.evidence` is non-optional, and a no-answer refusal carries the `failure` tag instead of an exemption | decision-record construction test | strip evidence from any refusal |
| INV-15 | No gated write executes without a constructed authority: the admission helper raises (fail-closed) when the backend's authority is absent, and every shipped connect path — including the LAN path the SDK builds directly (`sync.py:37,136-145`) — constructs one | per-backend construction test + an admission-with-no-authority test | skip construction on one connect path; or make the helper pass through when the authority is `None` |
| INV-16 | The authority never believes "receiving" while it holds its own transmission: an admitted key without an unkey, a CW message inside its computed duration, or a tune we started refuses every HAZARD admission regardless of the radio's report (the B6 blind spot: the IC-7300 reports RX mid-CW) | the B6 golden — scripted RX answer during an own CW window must still refuse, and the refusal's record carries `own_transmit_hold="cw"` | let a mid-CW RX report clear the hold |

## §7 Boundary table — what stays outside, honestly

| Concern | Verdict |
|---|---|
| Lease/ownership arbitration details (MOR-1878/1885/1014/1214), supervisor arming (MOR-1219/1190) | **Outside** — consulted; the authority reads the lease for attribution and never re-implements it. Row 12 removes the *urgency* of arming (the deadline covers unmanaged keys), not the epic |
| Audio-path liveness, arm-before-key ordering (MOR-1218/1207/1215), frontend fault lifecycle (MOR-1792, MOR-1784) | **Outside** — seat-side ordering above the authority. MOR-1792's divergence surface *shrinks* (the server no longer gates frequency at all, so the browser reducer's freshness defect stops affecting writes) but the ticket is not claimed fixed |
| The browser reducer and the frequency permit (`lib/utils/tx-permit.ts`) | **Outside** — client-local facts (local audio capture, link liveness, band-plan permit) the server cannot observe. The reducer remains a UX pre-filter; the authority below the server is the safety floor. Two distinct band objects, kept distinct: band-plan *legality* stays browser-owned and absent headless (a feature gap, not transmit safety); band-*edge geometry* for the `set_freq` crossing check is profile data (§3.3) and fully headless |
| Wire-protocol owner decisions: `t` at never-observed (MOR-1902), rate-limit errno (MOR-1814) | **Outside** |
| Read-only vs raw writes (`--read-only` does not stop a raw frame) | **Outside** — authorization axis, its own ticket; named residual of T6 |
| The CI-V frame router dropping foreign-addressed frames unlogged (`_civ_rx.py:1491` — the ptt-push measurement's standing finding, confirmed at HEAD) | **Outside, endorsed for its own ticket** — a diagnostics blind spot independent of this design; becomes relevant again only if a second-cable [REMOTE] listener is ever built |
| Freshness epic (MOR-1800: one TTL authority, store decay) | **Adjacent, not absorbed** — the store, `mark_stale_due`, TTL universes stay where they are; this design *reduces* what depends on them |
| `get_freq`/`get_mode` cache honesty (MOR-1812), dead rigctld poller (MOR-1813) | **Outside** — same defect class, different fields |
| Hardware-evidence tickets (MOR-1891, 1033, 1202) and the bench items (§10) | **Outside** — inputs, not components |
| MOR-1903 (five profiles without ptt acquisition) | **Outside and defused** — display-grade truth benefits from the repair, but no gate verdict depends on it anymore (T3 + row 5) |
| `rigplane.IcomRadio` as a public export (`__init__.py:112,352`) | **Covered by construction** — the admission lives inside the class, so even an externally constructed instance is gated (a facade design could not say this) |

## §8 Risks

- **R1 — The authority is a new single point under every write.** A defect
  hits every consumer at once. Mitigation: the engine is pure, lands consumed
  by nothing (rows 1–6), goes live per backend (rows 7–8) with one-PR
  reverts, and the default path (PASS) is a table lookup returning
  immediately — the smallest possible blast surface for the common case.
- **R2 — Admission-by-convention held by pins.** The in-backend placement
  trades the facade's structural airtightness (which proved illusory — §3.2)
  for a table plus two source-level pins. The pins are mechanical (AST +
  protocol-surface enumeration) and run on every PR; the residual is a
  genuinely new write path added outside the protocol surface *and* outside
  the table — the same residual every design here has, now named.
- **R3 — The solicited read adds a round-trip to hazard commands.** Typical
  cost: one command RTT (tens of ms serial, less on LAN), ceilinged by the
  admission's own 300 ms deadline (§3.5 — never the backend's generic 2.0 s
  GET bound), on antenna/tuner/cross-band operations issued a few times per
  session; worst-case rigctld in-band occupancy ≈0.3 s, stated next to
  MOR-1881's 2.003 s in §3.5. If a read times out the command is refused — a
  radio that answers nothing is a radio you should not be throwing relays
  on. Accepted. A plain `set_freq` never pays it (§3.3).
- **R4 — Liberalizing mode/split during TX is user-visible.** It
  restores manufacturer-sanctioned behavior, but any client that *relied* on
  our drop is affected. The known-limitations rewrite lands in the same PR
  (row 10); the release notes flag it. Correction, 2026-08-21: this risk used
  to include frequency and to claim the WSJT-X "Fake It" un-break as its own.
  Both shipped ahead of it at MOR-1940 (`9fc90943`), which carried its own
  known-limitations paragraph — so the residual risk here is mode and split.
- **R5 — Deliberately stricter than both reference implementations.** A
  band change under key succeeds in Hamlib (unkey-then-write); here it is
  refused (owner ruling Q2). A rigctld client that band-hops mid-key —
  none is known to — would see an error where Hamlib gives success. The
  strictness is the intended position, stated in §3.3 with its evidence
  (B1: the radio throws its filter relays under RF and does not protect
  itself); the release notes must say it plainly (row 10's rewrite).
- **R6 — Double-gating window (rows 7/8 → 11).** Old seats above, authority
  below. Composition is monotone (strictest wins); no contradiction is
  possible because the old seats only refuse more. The window is bounded by
  three deletion rows.
- **R7 — CI shape.** Rows touching `src/rigplane/web/**` — **9, 9b, 12a, 13b,
  13c** — pull the full frontend CI block into the 10-minute `quick.yml`
  ceiling; they are deletion-heavy and sized small. Two corrections,
  2026-08-21. First, the row list omitted **9b**, which touches
  `web/server.py` plus two frontend files and whose own cell already declares
  the frontend CI block — so R7 contradicted row 9b. Second, this risk used to
  end "`rigs/**` is in no `quick.yml` filter — row 3b fixes it before the
  profile rows matter"; row 3b has since merged (`9a05ecb0`) and `rigs/**` is
  an entry of the `core:` filter, so that half is discharged rather than
  pending. And the
  facade-refuting Python-version hazard generalizes: any future capability
  probing added around the authority must remember `quick.yml` is 3.11-only
  while `isinstance`-on-protocol semantics changed in 3.12.
- **R8 — New protocol.** `read_transmit_state()` lands on a **new capability
  protocol `TransmitStateReadable`** — never on the runtime-checkable
  `Radio`, where a required member would silently break `isinstance` for
  lacking implementers, the very failure mode §3.2 documents
  (`open-core-policy.md:178-180` classes a new required method as breaking).
  Genuinely additive, issue-first, listed in the public-surface pins per
  `core/LAYER.md:57-63`; the declared fail direction for a backend without
  it is fail-closed on hazard families (§3.9).

## §9 Owner decisions — settled 2026-08-20, plus three later questions

Every question this document raised **as of 2026-08-20** is decided. Of the
questions raised on 2026-08-21, Q13 was ruled the same day and is closed here;
Q14 and the newly raised Q15 are open and marked as such in the table below.

**Provenance of the three later questions, stated because the cited record does
not carry them.** `rigplane-archives/tx-authority-owner-decisions.md` is the
record of the rulings settled on 2026-08-20. Q13, Q14 and Q15 were all raised
after it was written, and it has not been updated with them — so a reader who
opens that record will not find Q13's ruling there, and should conclude that
the record is behind, not that this document invented a ruling. Q13's ruling
was given by the owner directly on 2026-08-21, in response to the question as
raised. Writing it into the record is the owner's to do and is outside this
repository. This paragraph exists because closing a question is the
higher-stakes direction: Q15 is opened here with careful hedging, and a closure
should carry at least as much provenance as an opening. The record is
`rigplane-archives/tx-authority-owner-decisions.md`, and the rulings that
change the body of the document (the four-family rule; the own-commands rule;
CW atomicity) are incorporated in §3.3, §3.5, §3.6 and §3.7 above. The table
below is the disposition of each question as asked.

| # | Question | Decision | By |
|---|---|---|---|
| Q1 | Fate of PR #2745 (web `ptt_on` immediate-block) | Ruled "not whether but when — once the bench clears, with the MOR-1792 first-key baseline taken before merge". **Overtaken: the owner merged it `b3ab76b1` (2026-08-20) with no on-record discharge of that gate** (§1.6). Its mechanism is deleted at row 9; its rule (web `ptt_on` refused unless RF truth reads fresh RX) survives as seat policy | Coordinator; merge = Owner |
| Q2 | Band change while keyed, ours: auto-unkey (Hamlib parity) or refuse? | **Refuse. Do not auto-unkey to make a band change safe.** The unkey-first sequence is not built | **Owner** |
| Q3 | Foreign/unattributable TX on a band write | Refuse — already settled by the standing MOR-1175 ruling (a CAT unkey cannot release a mic PTT; another owner's transmission is not ours to end). With Q2 also refusing, **Q2 and Q3 collapse into one rule** — one fewer branch | Coordinator |
| Q4 | Tune start while already keyed | **Refuse permanently**, not "until measured" — subsumed by the four-family rule. B2 is now informational (it measured: accepted, tuner ran; and both radios drop to minimum power before a tune cycle — operator knowledge found in no manufacturer source) | **Owner** (by the rule) |
| Q5 | Scan-start BLOCK→PASS | **PASS.** No documented hazard; scan is a receive function; the gate protected nothing and consumed truth to do it | Coordinator |
| Q6 | rigctld rendering of a HAZARD refusal | **`RPRT -9`** (error). A silently "successful" antenna switch that never moved is a worse lie than an error, and no digital-mode client sends these families mid-sequence | Coordinator |
| Q7 | rigctld rendering of a refusal **by the radio itself** on mode/split | **`RPRT 0`** (success). Forced by the non-negotiable — a non-zero response tears the dominant client down — and identical to what Hamlib already does. Bounded-only, per INV-10's three-part condition | Coordinator |
| Q8 | Arm the deadline on a CW message — would it truncate a legitimate message? | **Dissolved, not decided.** B6 measured that a CAT unkey does not stop an in-progress CW message at all — there is nothing to truncate with, so there is no trade-off. The underlying behaviour is an owner ruling: **a CW message is an atomic action by design** — committed on enter, not cancellable, as intended when the feature was built. A documented contract, not a defect (§3.6). The duration computation proposed for the deadline changes purpose: it now feeds the own-transmit hold (§3.7) | **Owner** + B6 |
| Q9 | The deadline's OFF against a CW/tune transmission | **Fire it regardless; worst case it is inert.** B6 confirms: for CW it is inert | Coordinator |
| Q10 | Raw CI-V outside the authority | **Yes** — classifying arbitrary bytes is a fiction, and the rigctld raw path sits below the typed layer where the authority cannot observe it honestly. Distinct from Q12, which is about the *refusal*, not the classification | Coordinator |
| Q11 | vfo-select during TX | **Refuse — joins the four-family rule.** A same-band swap is harmless, but distinguishing it would cost an extra read and branch for the other VFO, and nobody swaps VFOs mid-transmission. Closed by ruling, not measurement — **B8 dropped from the bench list** | **Owner** |
| Q12 | The shipped raw-during-TX BLOCK | **Retain**, rewired to the new truth type at both seats (rows 9/10), fail-closed at unknown | **Owner** |
| Q13 | Class of the two unclassified vfo-family members — `swap_main_sub` (`runtime/_dual_rx_runtime.py:309-326`) and `select_receiver` (`:447-471`) | **CLOSED — owner ruling, 2026-08-21: both join the vfo-select HAZARD family.** Provenance, because it matters more for a closure than for an opening: the ruling was given by the owner directly on 2026-08-21, after the record above was written, and **that record has not been updated with it** — do not expect to find Q13 there. They are now in the §3.3 table and in row 7's admission list. The wire evidence behind the ruling: `swap_main_sub` sends the same `_CMD_VFO` frame as its already-ruled twin `equalize_main_sub`, and `select_receiver`'s MAIN/SUB select is indistinguishable on the wire from `set_vfo_slot`. **Still open under the same heading**, and deliberately not classified: `set_bsr`, `set_tx_source`, `set_cross_band_split`, `vfo_a_to_b`, `vfo_b_to_a` (§3.3) | **Owner** |
| Q15 | Does the retained `connection_epoch_bootstrap` exemption reach the backend admission? | **OPEN — raised 2026-08-21, not decided; postdates the §9 record and is not in it.** It does not, and this document does not resolve it: the flag is a keyword parameter of `web/radio_poller.py::_execute`, `TransmitAuthority.admit` has no channel for it, and the exempted `SelectVfo(vfo="A")` dispatches into `_set_vfo_slot_impl` — the seat §3.2 designates. §3.5 states both halves; rows 7-9 must not be read as having settled it | **Owner** — pending |
| Q14 | Does the Icom path parse its transmit-state answer through the profile's `tx_state_map`? | **OPEN — raised 2026-08-21, not decided; like Q13 and Q15 it postdates the §9 record and is not in it.** §3.7's positive-mapping bullet and §3.9 item 1 read differently and neither has been amended to match the other. The merged row-5 code implements §3.7's reading: `IcomRadio.read_transmit_state` (`runtime/radio.py:3674-3731`) never consults `profile.tx_policy`, validating through `_observations_from_frame` and the `0x00/0x01` allowlist (`_civ_rx.py:1563-1566`), while Yaesu does consult the map (`backends/yaesu_cat/radio.py:1083-1090`) — leaving `rigs/ic7300.toml:1613`'s `tx_state_map` unread on the Icom path. Row 5 has shipped either way; the question is which passage the design means | **Owner** — pending |

## §10 Bench status

The 2026-08-20 morning session (IC-7300, dummy load, low power; full records
in `rigplane-archives/tx-authority-owner-decisions.md`) closed most of what
this document once listed as open. Nothing in the design now waits on a
measurement.

| # | Measurement | Status | Finding / consequence |
|---|---|---|---|
| B1 | Band change while keyed, both relays watched | **CLOSED** | Accepted (`FB`), applied, band-filter relays **audibly thrown under RF** — the radio provides no protection of its own. No SWR excursion expected or seen (a dummy load stays matched; the harm is cumulative contact wear, invisible in a single event). The relay throw under load is itself the finding: HAZARD is real and does not collapse into PASS |
| B2 | Tune start while already keyed | **CLOSED — informational** | Accepted; the tuner ran. Operator knowledge in no manufacturer source we surveyed: **both radios drop to minimum power before a tune cycle** — a vendor mitigation that makes tune-start far less hazardous than band change. Moot for policy under the four-family rule (Q4: refuse permanently) |
| B3 | RIT enable and offset while keyed | **CLOSED** | Both accepted (`FB`) during transmit; read-back after unkey confirmed RIT on. Supports the PASS classification |
| B4 | QSK duty cycle sampled at 10 ms | **DROPPED — low value** (per the owner-relayed revision instructions; the decisions record itself does not mention B4) | The B6 finding (the radio's transmit-state report is unreliable during its own CW) already answers the question qualitatively; §3.5 states the accepted residual |
| B5 | Whether the IC-7300's PTT line de-asserts before RF ceases | Open — **gates nothing** | Its only consumer was the deleted unkey-first settle (Q2: refuse) |
| B6 | Does a CAT unkey cancel an in-progress CW message? | **CLOSED — NEGATIVE** | Fifteen single-tone characters sent; unkey landed mid-message; **all fifteen played** — counted by ear after three instrumented designs failed (two produced confident wrong verdicts). Alongside it: **`1C 00` reported "receiving" while the rig was still sending** — transmit truth via that command is unreliable during a CAT-issued CW message, the fail-open blind spot that forced the §3.7 own-commands rule |
| B7 | FTX-1: does a frequency write during TX land on both VFOs or only the transmitting one | Open — **gates nothing** | Split-operation UX only |
| B8 | VFO select/swap while keyed | **DROPPED — by ruling** | Q11 closed it without measurement: the family is refused outright |
| B9 | Does `stop_cw_text` (CI-V `0x17` data `0xFF`) stop an *in-progress* CW message? | Open — **informational** (new, from the C1 review finding) | B6 tested only the PTT unkey. The atomicity contract is an owner ruling and does not wait on this; a *yes* would let the deadline's CW effect upgrade from an inert `set_ptt(False)` to `stop_cw_text` (§3.6) |

## Appendix A — input-document claims that did not survive verification

Reported per the brief's instruction; each checked against `fb7a86da` and
re-checked at the `769bfc71` re-anchor (historical "at `fb7a86da`" wording
in items below is deliberate — it records what was true when checked), and
this document's own first draft was subjected to the same treatment (its two
refuted mechanisms are recorded in §3.2 and §5).

1. **Brief v2: "Five ingresses currently bound a key five different ways, and
   two do not bound it at all."** The tree is worse: **three** bounding
   mechanisms exist, and **seven of nine** keying paths have no bound at all
   on the (unmanaged) bench radios — rigctld, SDK, CW×2, raw×3 including the
   hamlib bridge (at `fb7a86da`; MOR-1904 has since bounded the rigctld row,
   §1.5). The correction strengthens the brief's own case.
2. **Brief v2: "if a command can only enter through it, a ninth mechanism is
   impossible to add by accident, and no source-scanning completeness test is
   needed."** The premise does not survive this tree, twice over: (a) on
   FTX-1 and hamlib-provider radios the web write path enters the backend as
   a **queue handoff** (`web_startup.py:126-128` →
   `create_observation_poller` → a poller bound to raw `self` that drains the
   web `CommandQueue`), which no object-level filter in front of the backend
   can see; (b) the capability model resolves members via
   `getattr_static`-semantics `isinstance` on Python 3.12/3.13 and via
   `__mro__`/`__self__`/`__dict__` introspection, which any delegating facade
   breaks silently — on a CI that gates PRs with 3.11 only. The filter
   therefore lives inside the backend write layer, and a small totality +
   call-site pin (INV-1/INV-2) is the honest minimum under any placement.
3. **Brief v2 cites the Yaesu inversion at `backends/yaesu_cat/radio.py:1001`.**
   At `fb7a86da` the predicate was at **`:1003`**
   (`return bool(result["state"] == "1")`; the query at `:1002`), and `get_ptt`
   funnelled through it. MOR-1905's own description cited `:1001` too — same
   drift. Past-tensed 2026-08-21: `c87c59c3` deleted that predicate, so this
   item is now a record of the brief's error, not a location in the tree.
4. **The input documents treat `send_cw_text` as a footnote.** It is a
   first-class hole: a transmitter-keying write that is structurally
   invisible to the entire interlock framework on every surface (web
   `control.py:1573-1587`, CLI `:3090-3093`), with no ownership and no bound.
   This design classifies it (KEYING) and bounds it.
5. **Several input passages assume a PTT read exists everywhere.** When
   checked, they were wrong: the `Radio` protocol has **no** PTT-read member
   (still true), `read_ptt`/`get_ptt` existed only on the Yaesu and
   rigctld-client backends, and the entire Icom family, the bench IC-7300
   included, had no public read primitive — without which every
   read-before-throw design was unimplementable on five of seven backends.
   **Discharged 2026-08-21:** row 5 shipped (`24eac81d`) and
   `read_transmit_state` now exists on all three backends behind
   `TransmitStateReadable` (§1.4). The item stands as the record of why the
   row was needed.
6. **Dossier/previous ADR reference "MOR-1809" and "MOR-1189" as tickets.**
   Neither identifier exists in the tree or Linear-visible history; each
   names a real *mechanism* (the raw read-only gap; the HTTP throwaway
   request id at `managed_tx_ingress.py:76-77`) that this document
   references by mechanism, not by number. (An earlier draft of this item
   also listed MOR-1177 — wrongly: it is in the tree at
   `tests/test_tx_safety_diagnostics.py:20`, naming the ACK-is-not-RF class
   §3.4 leans on.)
7. **`permitted-during-transmit.md`'s second in-tree defect claim is stale.**
   It says `rigs/ic7300.toml:50` declares an `"antenna"` feature with "no
   CI-V antenna-select command and no backend implementation" — but at
   `fb7a86da` the profile has a full `[antenna]` section with
   `get_antenna`/`set_antenna` on CI-V `0x12` (`ic7300.toml:655,929-930`) and
   `runtime/radio.py:4629-4653` implements `set_antenna_1/2` against it. The
   claim did not survive; whether a one-SO-239 radio should *declare* the
   feature is a profile-data question, not a missing-implementation one.
8. **Stale memory note ("only 3/8 radios are on the new scheduler").**
   `ic7300.toml` has a full `[state_acquisition]` block at HEAD (line 110) —
   4/8 profiles have the block, 4/8 have none.
9. Minor drifts corrected in place: the known-limitations section was
   `:67-84` (not `:60-90`) — and has since grown past that range, so §3.8 and
   row 10 now name it by its `## rigctld write handling during transmit`
   heading instead (2026-08-21); the `rig_loader` hook ends at `:1734` (not
   `:1730`); `CatCommandRejected` is at `transport.py:71`; the rigctld
   known-TX drop block was `:804-811` at `fb7a86da` (`:803` the UNKNOWN
   branch; now `:826-833` and `:824-825` at the re-anchor); the
   `_SyncCommandExecutor` no-gate evidence is `sync.py:49-90`; the sync
   facade's direct `IcomRadio` construction is `sync.py:37,136-145`; the
   Yaesu poller's `SelectVfo` arm
   head is `poller.py:975` with the `set_vfo_select` call at `:991`; the
   Yaesu backend-only write count is **29** (corrected from 25, 2026-08-21 —
   enumeration rule in §3.2).
10. **One "correction" in the consolidated revision list was itself wrong.**
   It amended the `quick.yml` core-filter citation `:43-51` → `:45-53`;
   verified at `fb7a86da`, the filter is at `:43-51` (`core:` at `:43`, its
   last entry at `:51`) — the original citation stood and the amendment did
   not survive. (The substantive claim at the time — no `rigs/**` in the
   filter — was right either way. Correction, 2026-08-21: it is no longer
   true of the tree. Row 3b merged at `9a05ecb0`, adding `rigs/**` and
   `contracts/**`, and the `core:` filter now runs past `:51`.)

## Summary

The measurement changed the axis: the hazard is a **discrete contact under
RF**, not "writing while transmitting" — and the owner closed it with one
rule: band, tuner, antenna and VFO select are refused while transmitting or
while transmit state is unknown, no sub-cases, deliberately stricter than
both reference implementations. The gate asks the radio directly before each
hazard write (through a read primitive this row-set finally gives the Icom
family), holds "transmitting" for every transmission rigplane itself starts —
because B6 proved the radio lies about exactly those — and stops obstructing
everything the manufacturers explicitly permit. The structure follows the
tree as it actually is: one `TransmitAuthority` per radio, admitted **inside
the backend write layer** — below the web queue, the backend-internal
pollers, rigctld, the CLI, the SDK and CW alike — held total by two small
pins, with the seven seats, six resolvers, two lanes, the DEFER class and
the previous design's entire causal machinery deleted by name — save the two
raw arms, retained deliberately and rewired to the one truth view (Q12).
What survives is exactly what the evidence says must: one key-down deadline
whose OFF rides each delivery's own unkey rails, the managed-TX supervisor
untouched, a provenance-pinned truth view whose RX can only be produced by
an explicit per-radio mapping, a two-code refusal vocabulary that carries
its evidence, and a decision log that lets anyone ask the authority why —
and get the evidence back. Every owner question raised as of 2026-08-20 is
settled, and Q13 was ruled on 2026-08-21 — but **two are open: Q14** (does the
Icom path parse through `tx_state_map`) **and Q15** (the connect-time bootstrap
exemption cannot reach the backend admission the design creates, §3.5). This
sentence claimed all of them were settled until 2026-08-21; it was already
wrong about Q14 and Q15 is newly opened, and Q15 in particular must be visible
from wherever a reader arrives (§9).
