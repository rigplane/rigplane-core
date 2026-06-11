# FTX-1 (Yaesu) — CAT manual vs implementation

**Manual:** Yaesu FTX-1 CAT Operation Reference Manual **v2507-B** (67-command index + detail).
**Driver:** `src/rigplane/backends/yaesu_cat/radio.py` (146 `async def`).
**Profile:** `rigs/ftx1.toml`. **Validation:** `src/rigplane/validation/registry/*`.
**Live run:** `/tmp/ftx1.live.json` — 58 checks: 30 pass · 1 **fail** (`vfo_slot.set`) · 21 unsupported · 6 manual_required. (core 2.9.0, mode=hardware, tx_allowed, USB + antenna.)

Protocol: Yaesu ASCII CAT, `;`-terminated (NOT CI-V). CP2105 dual-UART.

---

## Gap lists (priority-ordered)

### A. UNDER-DECLARED — backend implements, profile/registry can't see it

| CAT | rigplane symbol | Fix | Ticket |
|---|---|---|---|
| **CN** CTCSS tone freq (+ **CT** type) | backend `get_sql_type`/`set_sql_type`, `get_ctcss_tone` (read-only) exist; registry tone round-trips resolve to `get_/set_repeater_tone`, `_tsql`, `_tone_freq`, `_tsql_freq` — **none exist on the backend** → 4 tone checks `unsupported` | Repoint registry tone checks at `get_sql_type`/`set_sql_type`+`get_ctcss_tone`, mark tone-freq read-only | **MOR-672** |
| **DT** Date & Time | `get/set_system_date`/`time` exist; **no `DT` command string** in `ftx1.toml` → `system_date/time.read` unsupported | Add `DT0;`/`DT1;` strings to `[commands]` | **MOR-672** |
| **VG** VOX Gain | `get/set_vox_gain` **raise NotImplementedError**, no `VG` string | Implement + add strings | **MOR-674** |
| **NA** Narrow | `get/set_narrow` work, folded into `filter_width` | Low — optional `narrow.set` | — |

### B. VALIDATION GAPS — implemented + declared, but no round-trip (presence-only)

| CAT | rigplane symbol | Live | Ticket |
|---|---|---|---|
| **IS** IF Shift | `get/set_if_shift` — only `if_shift.presence` | pass | **MOR-671** |
| **CO** Contour | `get/set_contour` (+apf) — only `contour.presence` | pass | **MOR-671** |
| **BI** Break-in | `get/set_break_in` — only `break_in.presence` | pass | **MOR-673** |
| **PR/PL** Compressor | `get/set_processor`(+level) — only `compressor.presence` | pass | **MOR-673** |
| **CT** SQL type | `get/set_sql_type` — only `sql_type.presence` | pass | **MOR-673** |
| **FR** Dual RX | `get/set_rx_func` — only `dual_rx.presence` | pass | — (low) |

### C. MISSING BACKEND — documented operator command, no backend method

| CAT | Function | Value | Ticket |
|---|---|---|---|
| **MX** | MOX SET (manual TX-on) | Med — alt TX trigger | **MOR-675** |
| **OS** | Repeater Offset/Shift | Med — FM repeater | **MOR-675** |
| **TS** | TXW (TX watch, split) | Med — operating | **MOR-675** |
| **VD** | VOX delay (methods exist, no `VD` string) | Med | **MOR-674** |
| MD/MC/MA/MB/MW/MR/MT/MS memory family | memory ops | Feature | **MOR-676** |
| KM / PB / LM | CW + voice message keyer | Feature | **MOR-676** |

### D. MISMATCH / WRONG — declared/checked but behaves differently

| CAT | rigplane symbol | Issue | Ticket |
|---|---|---|---|
| **VS** | `vfo_slot.set` (**FAIL**) | `set_vfo_slot` raises NotImplementedError on `vfo.scheme="ab_shared"`; backend DOES implement `VS` via `set_vfo_select` (MAIN/SUB) — harness calls wrong abstraction. The one red FAIL. | **MOR-670** (NotImplementedError→unsupported) |
| **ML** | `get/set_monitor` raise NIE | Correctly NOT declared (real radio returns `?;` — manual column optimistic vs hardware). No gap. | (note) |
| **CT** | `set_sql_type` writes `CT0{type:02d}`, live answers `CT0;` single-digit | Asymmetric width; flagged in-profile as "unconfirmed residual" | MOR-473 (open) |

### Intentionally OUT OF SCOPE (device front panel — nothing to mirror into browser UI)
- **DA** TFT contrast/brightness/LED · **SF** FUNC knob assignment · **EX** settings menu (Table 3, hundreds of params) · **MS** Meter SW (UI reads meters directly).

---

## Ticket coverage summary

- 1 red FAIL (`vfo_slot.set`) → **MOR-670**.
- presence-only IS/CO → **MOR-671**; BI/PR/CT → **MOR-673**.
- tone (CN/CT) + clock (DT) under-declaration → **MOR-672**.
- VG impl + VD wiring → **MOR-674**; MX/OS/TS missing backend → **MOR-675**.
- memory + keyer UI feature → **MOR-676**.
