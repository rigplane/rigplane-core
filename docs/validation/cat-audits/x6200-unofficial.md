# Xiegu X6200 — UNOFFICIAL / community CI-V references

Companion to `x6200.md` (which audits against the official Radioddity **"XIEGU X6200 CI-V
implementation V1.0.6"** table). This file collects **community / reverse-engineered** knowledge
that fills the gaps where the official doc is silent — most importantly the opcodes that our live
harness proved the radio honors even though they are absent from the V1.0.6 table.

## Summary

**There is one strong community reference, and it is code, not prose: the Hamlib `xiegu.c`
backend.** The X6200 (`RIG_MODEL_X6200 = 3091`) shares the exact same private caps struct as the
X6100 (`x6100_priv_caps`), so the X6100 backend *is* the de-facto X6200 reference. It is a thin
profile over Hamlib's generic Icom driver (`icom_*` functions), which means the X6200 is treated as
a **standard Icom CI-V rig** for almost everything: freq/mode/level/func/PTT all go through the
ordinary Icom opcode builders. This independently corroborates our live finding that the radio is a
partial Icom clone whose firmware honors far more than the V1.0.6 table publishes.

The caveat: Hamlib declares its capability masks **optimistically** by reusing the `X108G_FUNCS` /
`X108G_LEVELS` macros (inherited from the Xiegu X108G). Those masks over-advertise — they do **not**
prove the X6200 firmware implements every level/func. Where Hamlib's mask and our live timeouts
disagree (tone family, some `0x14` levels), **trust the live harness** — Hamlib is asserting a
capability it never round-trip-verified on an X6200. So Hamlib is authoritative for *opcode
mapping* (which byte sequence a function uses) but not for *which functions the firmware actually
answers*.

No GitHub repo, Reddit thread, forum, or wiki was found that publishes a *fuller opcode table* than
the official V1.0.6 doc. The reverse-engineering repos (AetherRadio et al.) target the radio's
internal STM32 base controller over an internal bus — **not** external CI-V serial — so they are
**not** CI-V/CAT references (see "Sources that do NOT help" below). The community value is entirely:
(1) Hamlib's opcode mapping, and (2) scattered forum confirmation of specific quirks (PTT ack, model
ID, CI-V address).

---

## Opcode evidence table

Credibility: **A** = working code in maintained rig backend · **B** = forum/list report with
detail · **C** = inference from Icom lineage. "vs-live" compares to our harness ground truth.

| Opcode | Function | Source | Cred | vs our live |
|---|---|---|---|---|
| `0x19 0x00` | Read transceiver ID (X6200 → `0x00A4`; X6100 → `0x6100`) | Hamlib `xiegu.c` `xiegu_rig_open()` | A | NEW (model-disambiguation; we use `0x1D 0x19`/`0x19` — confirms an ID path exists) |
| `0x1C 0x00` (`C_CTL_PTT`,`S_PTT`) | PTT on/off; data byte `01`/`00` | Hamlib `xiegu.c` `x108g_set_ptt()` | A | AGREES (PTT works) |
| `0x03` / `0x05` | Read / set operating freq (legacy) | Hamlib generic `icom_get_freq`/`icom_set_freq` (no override in `xiegu.c`) | A | AGREES (live: legacy freq works, NOT in V1.0.6 table) |
| `0x04` / `0x06` | Read / set mode (legacy) | Hamlib generic `icom_get_mode`/`icom_set_mode` | A | AGREES (live: legacy mode works, NOT in V1.0.6 table) |
| `0x25` / `0x26` | Selected/unselected freq & mode (dual-edge) | Hamlib `x6100_priv_caps`: `.x25x26_possibly = 1` (probed, not forced) | A | AGREES (V1.0.6 documents these; Hamlib treats as best-effort) |
| `0x07` (+`0x00/0x01/0xB0`) | VFO A/B select & swap | Hamlib generic `icom_set_vfo` | A | AGREES |
| `0x0F 0x00/0x01` | Split set | Hamlib generic `icom_set_split_*` | A | AGREES |
| `0x21 0x00/0x01/0x02` | RIT offset / RIT on / XIT on | X6100 firmware notes (Radioddity X6100 FW changelog) + Icom lineage; Hamlib `max_rit/max_xit = 9999` | A/B | AGREES (live: `0x21` works, NOT in V1.0.6 table) — strong corroboration |
| `0x11` | Attenuator on/off | Hamlib `X108G_LEVELS` → `RIG_LEVEL_ATT` | A | AGREES |
| `0x16 0x02` | Preamp | Hamlib `RIG_LEVEL_PREAMP` via `0x16` | A | AGREES |
| `0x16 0x12` | AGC | Hamlib `RIG_LEVEL_AGC` | A | AGREES |
| `0x16 0x22` | Noise blanker on/off | Hamlib `RIG_FUNC_NB` | A | AGREES |
| `0x16 0x40` | NR on/off | Icom lineage / V1.0.6 | A | AGREES |
| `0x16 0x44` | Compressor on/off | V1.0.6 / Hamlib `RIG_FUNC_COMP` | A | AGREES (on/off only) |
| `0x16 0x50` | Key/dial lock | V1.0.6 / Hamlib `RIG_FUNC_LOCK` | A | AGREES |
| `0x14 0x01/0x02/0x03` | AF / RF gain / squelch | Hamlib `RIG_LEVEL_AF/RF/SQL` | A | AGREES |
| `0x14 0x06` | NR level | Hamlib mask `RIG_LEVEL_NR` | A | **CONTRADICTS** — Hamlib advertises it; **live: TIMES OUT.** Firmware does not implement the `0x14` NR-level read. |
| `0x14 0x07` (comp level) | COMP level | Hamlib mask `RIG_LEVEL_COMP` | A | **CONTRADICTS** — advertised by mask; **live: unsupported** (comp is on/off-only via `0x16 0x44`). |
| `0x14` (nb level) | NB level | Hamlib mask implies a level | A | **CONTRADICTS** — advertised; **live: unsupported** (NB on/off-only via `0x16 0x22`). |
| `0x16 0x42` / `0x16 0x43` | Repeater tone / CTCSS tone | Hamlib `RIG_FUNC_TONE/TSQL`, `set_ctcss_tone` | A (mask) | **CONTRADICTS** — Hamlib declares `RIG_FUNC_TONE|TSQL` + `icom_set_ctcss_*`; **live: TIMES OUT.** Tone family not implemented on X6200 firmware. |
| `0x1B 0x00/0x01` | DTCS / tone squelch code | Hamlib `set_dcs_code` / `dcs_list` | A (mask) | **CONTRADICTS** — declared; **live: TIMES OUT.** DTCS not implemented. |
| `0x27 ...` | Scope / waterfall / spectrum data | **NOT issued by Hamlib `xiegu.c`** (no `.set_spectrum`/scope hooks) | — | UNKNOWN from community — see gaps. (Firmware V1.0.7 adds a 3D bandscope *display*, but no CI-V scope-stream opcode is community-documented.) |
| `0x0F 0x01` | Split-on variant | Hamlib comment: *"testing with X6100 showed it rejected the 0x0f 0x01 command"* | A | NEW quirk — X6100 (and by shared caps, presumably X6200) **rejects** `0x0F 0x01`; Hamlib routes split through the generic path instead. |

### Hamlib capability masks (verbatim, for reference)

These are what the backend *advertises* (shared by X6100 + X6200). They are the source of the
"CONTRADICTS" rows above — masks, not verified firmware behavior:

- `X108G_FUNCS = NB|COMP|VOX|TONE|TSQL|SBKIN|FBKIN|NR|MON|MN|ANF|VSC|LOCK|ARO`
- `X108G_LEVELS = PREAMP|ATT|AGC|COMP|BKINDL|NR|PBT_IN|PBT_OUT|CWPITCH|RFPOWER|MICGAIN|KEYSPD|NOTCHF_RAW|SQL|RAWSTR|AF|RF|VOXGAIN|VOXDELAY|SWR|ALC`
- `X108G_PARMS = BACKLIGHT|APO|TIME|BEEP`
- `X108G_VFO_OPS = CPY|XCHG|FROM_VFO|TO_VFO|MCL|TUNE`
- `priv: addr 0xa4; x25x26_possibly=1; x1ax03_supported=0; mode_with_filter=1; data_mode_supported=1`
- serial max 19200, 8N1, write_delay 3, retry 3, timeout 1000ms — matches our profile.

Note `x1ax03_supported = 0`: Hamlib explicitly marks the X6100/X6200 as **not** supporting the
`0x1A 0x03` (read filter/edge) Icom extension — consistent with the firmware being a *subset*.

---

## Best single reference

**Hamlib `rigs/icom/xiegu.c` @ master — the `x6100_caps` / `x6200_caps` structs and
`x6100_priv_caps`.**
<https://github.com/Hamlib/Hamlib/blob/master/rigs/icom/xiegu.c>
(raw: <https://raw.githubusercontent.com/Hamlib/Hamlib/master/rigs/icom/xiegu.c>)

Why: it is maintained, it is the code thousands of operators actually use against the radio, and
the X6200 reuses the X6100 caps verbatim. Read it together with the generic Icom driver it delegates
to (`rigs/icom/icom.c`, `icom_defs.h` for the `C_*`/`S_*` opcode constants). Treat its capability
masks as an upper bound, not a guarantee.

Secondary: Radioddity's **X6100** firmware changelogs document the firmware additions of `0x1A 0x06`,
`0x21 00/01/02` (RIT/XIT) and `0x26` — historically useful because they show *when* the Icom-style
opcodes (incl. the RIT family our V1.0.6 X6200 doc omits) entered the shared firmware lineage.
<https://www.radioddity.com/blogs/all/xiegu-x6100-firmware-upgrade>

---

## Sources that do NOT help (recorded so nobody re-checks them)

- **AetherRadio `X6100Control` / `X6100Study` / `X6100Buildroot`, `TemporarilyOffline/X6100-TOADs`,
  `jcyfkimi/X6100_Study`** (linked from `AetherRadio/awesome-x6100`,
  <https://github.com/AetherRadio/awesome-x6100>): these reverse-engineer the radio's **internal
  STM32 base controller** (the `X6100Control` RPC server sits between the STM32 and user-space Linux
  apps on the radio's own SoC). That is an *internal* bus, **not** external CI-V serial. No CI-V
  opcode tables. Not a CAT reference.
- **JTDX / WSJT-X forum threads** (<https://jtdx.freeforums.net/thread/430/...>,
  <https://jtdx.freeforums.net/thread/468/jtdx-hamlib-rejected-xiegu-x6200>): no opcode detail. Value
  is operational only: (1) older Hamlib (≤4.5.5) rejected **PTT** on the X6200 because it lacked the
  `RIG_MODEL_X6200=3091` profile and mis-handled the `0x00A4` ID vs the X6100's `0x6100` — fixed in
  **Hamlib 4.6**; (2) workaround is `rigctld` NET mode; (3) confirms X6200 CI-V addr `0xA4` and that
  generic CAT (freq/mode) works while PTT was the sole failure on old Hamlib. Corroborates our PTT
  `0x1C 0x00` mapping indirectly.
- **groups.io xiegu-x6100** CAT threads: gated (HTTP 402) / no opcode tables; operational Q&A only.
- **flrig / CHIRP / gqrx**: no dedicated X6200 backend found that adds opcodes beyond Hamlib; they
  consume Hamlib or use the generic Icom/IC-705 profile.

---

## Gaps still open (nobody documents these)

1. **Scope / waterfall / spectrum (`0x27`).** No community source documents a CI-V scope-data-stream
   opcode for the X6200. Hamlib issues nothing on `0x27`. Firmware V1.0.7 adds a *3D bandscope
   display* on the radio itself, but whether any spectrum data is exposed over CI-V (à la IC-705
   `0x27 0x00`) is **undocumented and untested**. This matches our harness not exercising `0x27`.
2. **Whether `0x14` sub-levels beyond AF/RF/SQL work at all.** Hamlib's mask claims MICGAIN, RFPOWER,
   CWPITCH, KEYSPD, VOXGAIN/DELAY, ALC, SWR, BKINDL, NOTCHF_RAW — none independently confirmed on an
   X6200. Our live data only positively confirmed AF/RF/SQL and *negatively* confirmed NR/comp/NB
   levels time out. The middle set is unverified by anyone.
3. **DATA-mode sub-byte semantics on `0x26`.** `data_mode_supported=1` in Hamlib, but the exact
   filter/data-mode C-byte encoding for the X6200 is not community-documented beyond Icom analogy.
4. **`0x21` RIT/XIT exact framing on X6200.** Confirmed *present* live and in X6100 firmware notes,
   but no source publishes the X6200's exact `0x21 00` offset byte order / sign encoding — assume
   IC-705 semantics until live-verified.
5. **Tone/DTCS — settled as UNSUPPORTED.** Not a gap to fill but to record: Hamlib *advertises*
   TONE/TSQL/DTCS; live proves the X6200 firmware does **not** implement `0x16 0x42/0x43` or `0x1B`.
   Any tool trusting Hamlib's mask here will hang. This is the single most important
   community-vs-reality discrepancy.

---

*Compiled from web research 2026-06-11. No manual files downloaded or committed; opcode facts only,
no verbatim manual prose. Live ground truth from the rigplane X6200 validation harness (USB serial,
CI-V `0xA4`, 19200 8N1).*
