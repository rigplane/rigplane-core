# CAT manual vs implementation — gap audits

Per-radio comparison of each transceiver's **official CAT/CI-V reference manual**
against what rigplane-core actually implements, across three surfaces:

1. **Backend** — does a method exist (`src/rigplane/backends/<driver>/`)?
2. **Profile** — does `rigs/<radio>.toml` declare the capability + command strings?
3. **Validation** — does `src/rigplane/validation/registry/` have a round-trip
   (RMVR) check, and what is its live status?

Each audit produces a **5-column command matrix** (CAT cmd · Documented · Backend ·
Profile · Validation+live) plus **four gap lists**:

- **A. Under-declared** — backend implements it, but profile/registry can't see it.
- **B. Validation gaps** — implemented + declared, but no round-trip check (presence-only or nothing).
- **C. Missing backend** — documented operator command, no backend method at all.
- **D. Mismatch / wrong** — declared/checked but behaves differently, or a live FAIL tracing to a real mismatch.

Each gap row cross-references an existing or NEW Linear ticket (team RigPlane, key `MOR`).

## Audits

| Radio | Driver | Manual | Status |
|---|---|---|---|
| FTX-1 (Yaesu) | `yaesu_cat` | CAT OM v2507-B | ✅ [ftx1.md](ftx1.md) — done, live-validated |
| IC-7610 (Icom) | `icom_civ` | CI-V Reference Guide rev 1a | ✅ [ic7610.md](ic7610.md) — done, live-validated (3 scope FAILs → MOR-664) |
| IC-7300 (Icom) | `icom_civ` (shared) | Full Manual v6 §19 | ✅ [ic7300.md](ic7300.md) — done, static/template (no live run) |
| Xiegu X6200 | `ic705` serial (CI-V-like) | Radioddity CI-V V1.0.6 | ✅ [x6200.md](x6200.md) — done, doc-vs-code (live not run) |
| Discovery TX-500 (lab599) | **none** (Kenwood-CAT, unimplemented) | Lab599 CAT Protocol rev.2 | ✅ [tx500.md](tx500.md) — done, doc-vs-code (no backend, no hw) |

## Source manuals

Downloaded vendor PDFs and their extracted `.txt` are kept locally under `manuals/`,
which is **git-ignored** — these are copyrighted Icom / Yaesu / Xiegu / lab599
documents and this is a public repo, so we do not redistribute them. Each audit
`.md` cites the exact manual revision + source URL so anyone can re-fetch it.
Command mnemonics / opcodes referenced in the audits are interface facts, not
reproductions of the manuals.
