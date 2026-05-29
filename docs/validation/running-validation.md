# Running the radio validation matrix

`rigplane validate` exercises a radio's CAT control surface against the
capabilities its profile declares, and reports — per check — whether the radio
actually does what the profile claims. It is a **debugging instrument**: after a
run, every `fail` is a real discrepancy between the profile and the hardware, not
a quirk of the tool.

The check list is **generated from the radio's profile** (`rigs/<model>.toml`) via
the capability→check-spec registry — you do **not** hand-author a template. Pass
`--model <MODEL>` and the matrix is built in memory from that profile's declared
capabilities.

> **Safety first.** Hardware execution is opt-in and read-modify-verify-restore
> (RMVR): every write reads the original, writes a different value, verifies the
> readback, and **always restores** the original. TX (PTT) and the antenna tuner
> are **never** auto-actuated — they report `blocked`/`manual_required` unless you
> explicitly authorize them, and even then they are operator-verified, never keyed
> by the tool. Unknown/undeclared controls default to read-only.

---

## Quick start

```bash
# Dry-run (no hardware): generate + plan the matrix from the profile
uv run rigplane --model IC-7610 validate

# Against real hardware, read-only (safe: all writes SKIP, only reads run)
RIGPLANE_VALIDATION_ALLOW_HARDWARE=1 uv run rigplane \
  --model IC-7610 validate --hardware --allow-hardware --read-only

# Against real hardware, write-enabled (RMVR writes with restore; no TX/tuner)
RIGPLANE_VALIDATION_ALLOW_HARDWARE=1 uv run rigplane \
  --model IC-7610 validate --hardware --allow-hardware
```

`--hardware` **and** `--allow-hardware` **and** the env var
`RIGPLANE_VALIDATION_ALLOW_HARDWARE=1` must all be present to touch a radio; any
one missing keeps the run a dry-run plan. This triple gate is deliberate.

Add `--json --output report.json` for a machine-readable artifact, or omit `--json`
for a human-readable summary.

---

## Recipes

### Xiegu X6200 — serial

Control port `/dev/cu.usbmodem58910181093` @ **19200** baud, CI-V address **0xA4**.

```bash
RIGPLANE_VALIDATION_ALLOW_HARDWARE=1 uv run rigplane \
  --backend serial --serial-port /dev/cu.usbmodem58910181093 --serial-baud 19200 \
  --model X6200 --radio-addr 0xA4 --timeout 6 \
  validate --hardware --allow-hardware --provider native \
  --json --output /tmp/x6200.json
```

Start with `--read-only` (above, drop the flag to enable RMVR writes). The X6200's
RIT, XIT, and manual-notch controls accept a SET but never answer the GET; they are
declared **write-only** in `rigs/x6200.toml` and validated with **set-and-observe**
(set a test value, confirm it is accepted with no NAK/timeout, restore a benign
default) rather than a read-back that would falsely time out.

> The serial port is exclusive. If RigPlane Pro (or any other client) holds it you
> will see `Commander stopped` / `multiple access on port`. Close the other client
> first — check with `pgrep -fl usbmodem58910181093`.

### Icom IC-7610 — LAN

Address **0x98**, control port **50001**. Supply host/user/password via flags, env
(`ICOM_HOST` / `ICOM_USER` / `ICOM_PORT`), or `--pass-file` (avoids exposing the
password in `ps`). Never commit credentials.

```bash
ICOM_HOST=192.168.55.40 ICOM_USER=<user> ICOM_PORT=50001 \
RIGPLANE_VALIDATION_ALLOW_HARDWARE=1 uv run rigplane \
  --backend lan --pass-file /path/to/secret \
  --model IC-7610 --radio-addr 0x98 --timeout 6 \
  validate --hardware --allow-hardware --provider native \
  --json --output /tmp/ic7610.json
```

---

## Flags

Global (before the `validate` subcommand):

| Flag | Purpose |
|---|---|
| `--model <NAME>` | Radio model → selects the profile the matrix is generated from. |
| `--backend {lan,serial,yaesu-cat,rigctld}` | Transport (auto-inferred from `--serial-port`). |
| `--serial-port` / `--serial-baud` | Serial device + baud. |
| `--host` / `--control-port` / `--user` / `--pass-file` | LAN connection (env: `ICOM_HOST`/`ICOM_PORT`/`ICOM_USER`). |
| `--radio-addr` | CI-V address. |
| `--timeout` | Per-operation timeout (s). |

`validate` subcommand:

| Flag | Purpose |
|---|---|
| `--template <path>` | Optional. Use a hand-authored template instead of generating from `--model`. |
| `--hardware` | Execute against the radio (otherwise a dry-run plan). |
| `--allow-hardware` | Second gate; with `RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`, permits hardware writes. |
| `--read-only` | Run reads only; every write check is `SKIP`. |
| `--tx-allowed` / `--tuner-allowed` | Authorize the TX/tuner checks (still operator-verified, never auto-keyed). |
| `--provider {native,hamlib}` | `native` = RigPlane's own backend; `hamlib` = via the rigctld bridge. |
| `--compare <artifact.json>` | Diff this run against a prior artifact. |
| `--operator-id <id>` | Record the operator in the artifact. |
| `--json` / `--output <path>` | Emit JSON (to stdout or a file) instead of the human summary. |

---

## Reading the results

Each check reports one status:

| Status | Meaning |
|---|---|
| `pass` | The control behaved as the profile declares (readback matched, or — for write-only controls — the SET was accepted). |
| `fail` | A real discrepancy: the control did not react, the readback disagreed, or a command errored/timed out. **This is a bug to investigate.** |
| `unsupported` | The capability is not declared for this radio (or the radio lacks the operation) — recorded, not hidden. |
| `manual_required` | Operator-verified out of band (e.g. RX audio, scope, and the never-auto-actuated TX/tuner). |
| `blocked` | A TX-adjacent check that was not authorized (`--tx-allowed`/`--tuner-allowed`). |
| `skip` | Skipped — typically a write check under `--read-only`. |

Every check carries an `evidence` object with the concrete values observed
(`original` / `changed` / `readback` for RMVR; `verification: set_observe` +
`set_accepted` for write-only controls). Use it to see exactly what the radio did.

Because the matrix is exhaustive over the profile's declared capabilities, a clean
run with no `fail` means the profile and the hardware agree. A `fail` points at a
real defect — a missing or mis-routed CI-V command, a stale readback, or a control
the profile over-claims.

---

## See also

- `docs/contracts/validation-matrix-v1.md` — the versioned artifact/template schema.
- `docs/plans/2026-05-28-universal-validation-matrix.md` — the profile-driven matrix
  design (registry, generators, comparison, converter).
