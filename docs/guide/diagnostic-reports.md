---
description: Generate redacted RigPlane diagnostic reports — bundle logs, radio state, runtime config, and environment into a single ZIP for support and triage.
---

# Diagnostic Reports

When something goes wrong with `rigplane` — a CAT timeout, a stuck audio
bridge, a Web UI panel that won't render, a frequency that won't tune —
the fastest way to get help is a diagnostic report. One command (or one
button click) bundles your logs, radio state, runtime config, and
environment into a redacted ZIP that a maintainer can read end-to-end.

This guide explains when to use the feature, what's in the bundle, what's
deliberately left out, and exactly what happens when you hit "Send".

!!! note "Privacy first"
    `rigplane` does not phone home. There is no automatic telemetry, no
    background crash reporter, no "anonymous usage statistics" toggle.
    Every diagnostic upload is the result of a single, explicit user
    gesture — see [Privacy](#privacy) below for the hard rules.

## When to use

Reach for a diagnostic report whenever you're about to:

- File a GitHub issue describing a bug.
- Reply to a maintainer who asked "can you send logs?".
- Support a feature request with concrete data ("here's what my radio
  reports for this command").
- Self-diagnose by inspecting a fresh, complete picture of the running
  system (the local-only mode produces a ZIP you can grep through).

You do **not** need a diagnostic report for a question — open a
discussion or issue and describe what you're trying to do. Reports are
for situations where logs and runtime state would help.

## Quick start

There are three ways to produce a report. Pick whichever is convenient.

### Interactive (TTY)

```bash
rigplane diagnose --upload
```

The CLI walks you through a description prompt, optional issue URL,
optional contact fields, then prints a preview (file list + total size +
destination URL) and asks for final consent. The default keystroke at
the consent prompt **saves locally** — pressing Enter never transmits
anything; only an explicit `y` triggers upload.

### Web UI

Open the Web UI, then **Settings → Diagnostics → "Send diagnostic
report"**. A dialog walks through the same flow:

1. Form — description, optional issue URL, optional contact fields,
   category include/exclude.
2. Preview — file tree, sizes, redactions applied, the endpoint URL
   that will receive the bundle, and an "I understand" checkbox.
3. Result — a `support_url` you can paste into a GitHub issue, or a
   typed error with an actionable next step.

You can cancel at any step. "Save locally" is always available as an
alternative to "Send".

### Save locally (no upload)

```bash
rigplane diagnose --output ~/rigplane-report.zip
```

Without `--upload`, `rigplane diagnose` builds the bundle, writes it to
the path you specify (or `~/rigplane-report-<timestamp>.zip` by
default), prints the path, and exits. No network. Inspect the ZIP, edit
it if needed, attach it manually to a GitHub issue or email.

## What's collected

The bundle is composed of named **contributors**, each writing into its
own subdirectory of the ZIP. The full schema lives in the design spec
([`docs/plans/2026-05-03-diagnostic-data-collection-design.md`](../plans/2026-05-03-diagnostic-data-collection-design.md))
§4.4; the table below summarises the open-core defaults.

| Category       | Contents                                                                  | Redaction applied                                            |
| -------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `system`       | OS, arch, Python version, `rigplane` version, install method              | Absolute home paths rewritten to `<HOME>/...`                |
| `invocation`   | Filtered `sys.argv`, environment allowlist                                | `password=`, `pwd=`, `Bearer …`, API tokens → `***`          |
| `radio`        | Model, firmware version, backend, capability matrix, audio codec          | Public IPs masked (RFC 1918 ranges kept — they're useful)    |
| `audio`        | Legacy codec/rate/channel summary, requested and effective radio-native audio contracts, configured Web RX emission policy, device names, bridge state | macOS device names containing usernames are masked           |
| `logs`         | Rolling diagnostic logs from `~/.cache/rigplane/logs/` (up to ~15 MiB)    | Path / IP / credential regex pass on copies                  |
| `state`        | Current freq, mode, meters, last N CI-V exchanges (ring buffer)           | Optional callsign masking                                    |
| `errors`       | Recent in-process tracebacks (ring buffer)                                | Paths scrubbed                                               |
| `dependencies` | `pip freeze`-equivalent enumeration via `importlib.metadata`              | None — all-public package names + versions                   |
| `config`       | Summary of `~/.rigplane/*.toml` files                                     | `password` keys dropped entirely; other creds → `***`        |
| `extensions/*` | Pro contributors (Tauri logs, Rust crash dumps, RC-28, DSP) when present  | Same redaction utilities; signed under the active license    |

The manifest (`manifest.json`) lists which contributors ran, which
files each produced, and which redaction passes were applied to the
bundle.

### Audio contract fields

For LAN audio issues, inspect `audio/audio.json` first. The file keeps the
legacy top-level `codec`, `sample_rate_hz`, and `channels` fields for older
tools, and may also include:

- `radio_native.requested` — RX/TX codec, sample rate, channel count, and
  source metadata selected before the Icom conninfo exchange.
- `radio_native.effective` — RX/TX values in use after conninfo. If the radio
  rejected a requested value, `fallback_reason` explains the downgrade.
- `web_rx` — configured browser/client emission policy. When the diagnostic
  bundle is created outside the WebSocket runtime, this reports profile policy
  such as `auto` plus the derived emission codec; it does not claim that a
  browser client was actively connected.

Source metadata uses stable strings such as `explicit`, `profile-default`,
`profile-codec-default`, `global-default`, and `fallback`. For example, an
IC-7610 can request radio-native `PCM_2CH_16BIT` at `48000` Hz from its profile
while the Web UI emits Opus as a browser transport. That means only the browser
leg is Opus; the direct radio LAN stream remains PCM.

## Privacy

The diagnostic report carve-out in
[`docs/architecture/open-core-policy.md`](../architecture/open-core-policy.md)
§2 codifies the rules. In short, every upload satisfies **all** of the
following:

- **Explicit user gesture, every time.** No "always send reports"
  toggle, no install-time consent that grants ongoing permission, no
  background queue. One report = one deliberate action (CLI command,
  Web UI consent click, Pro Tauri "Send" button).
- **CLI default never uploads.** `rigplane diagnose` (no flags) builds
  the bundle and saves it locally. `--upload` is opt-in.
- **TTY consent prompt defaults to "save locally".** With `--upload` on
  a terminal, the final prompt is `[y/N]` for upload — pressing Enter
  saves the ZIP locally and prints the path. Only an explicit `y`
  transmits.
- **Non-TTY requires double opt-in.** Without a TTY (CI, cron,
  systemd), `--upload` alone is not enough — you must also pass
  `--no-confirm`. Without it, the command saves locally and prints the
  path with a message explaining why upload was skipped. Headless
  wrappers cannot accidentally transmit.
- **Web UI requires explicit consent.** The "Send" button is
  disabled until the "I understand" checkbox is ticked, and the full
  destination URL is displayed in the preview pane next to the
  checkbox.
- **Contact fields are opt-in only.** Email and callsign are never
  auto-populated from environment variables, system identity,
  `~/.rigplane/*.toml`, or any prior report — only typed at submission
  time.
- **The bundle is always available locally.** The "Save locally"
  button (Web UI) and the default `--output` path (CLI) are always
  available, even after you've started an upload preview.

These constraints are enforced both by the open-core code paths and by
the open-core policy doc — a future change cannot relax them without
explicitly amending the policy first.

## What's NOT collected

The following data is **dropped, not redacted** — it never enters the
bundle in any form:

- LAN username and password.
- Audio PCM samples (raw RX or TX audio).
- Contents of any `.env` file in the current working directory.
- macOS Keychain secrets.
- Anything matched by the `password=` / `pwd=` / `Bearer …` /
  `aws_secret_access_key` / `BEGIN PRIVATE KEY` redaction passes.

If you spot something sensitive in a bundle that should be on this
list, that's a bug — please report it (with the offending pattern, not
the actual secret).

## Where reports go

By default, uploads target:

```
https://reports.msmsoft.net/v1/diagnostics/upload
```

This is a maintainer-operated triage endpoint. Anonymous (open-core)
uploads are unauthenticated, IP rate-limited, and retained for **90
days** before automatic deletion.

You can redirect to a different host (self-hosting, audit, or a private
support contract) with the `RIGPLANE_REPORT_ENDPOINT` environment
variable:

```bash
export RIGPLANE_REPORT_ENDPOINT=https://reports.example.com/v1/diagnostics/upload
rigplane diagnose --upload
```

The override is visible in every preview pane (CLI and Web UI), so you
can never upload to a redirected endpoint by accident.

If you self-host the receiver, your backend must implement the public
contract [`diagnostic-bundle-v1`](../contracts/diagnostic-bundle-v1.md)
— multipart shape, metadata schema, typed error responses, and the
anonymous-tier behaviour (rate limits, retention, content scanning).

## The `support_url`

A successful upload returns a `support_url` — a customer-safe link that
identifies your bundle without exposing its contents. Paste it into a
GitHub issue, a forum thread, or a support email; a maintainer will
follow it to the bundle on their side.

```
Sent. Reference this URL in your issue or reply:
  https://reports.msmsoft.net/r/2k9q-7j4f-8x3p
```

Anonymous reports are retained for **90 days** and then deleted
automatically. The `support_url` becomes a 404 after retention expires
— if you need a longer window, save the bundle locally as well.

## Local-only mode

`rigplane diagnose` (without `--upload`) is fundamentally a local
bundle generator:

```bash
rigplane diagnose
# → /home/you/rigplane-report-2026-05-03T19-04-12.zip
```

The default output path is `~/rigplane-report-<timestamp>.zip`;
override with `--output PATH`. No network is ever touched in this mode.

This is the right choice when:

- You want to inspect what the bundle contains before sending.
- You're on an air-gapped or restricted network.
- You're attaching the bundle to a private support channel rather than
  the public triage endpoint.
- You're saving a snapshot for later comparison.

You can `unzip -l rigplane-report-*.zip` to inspect the file list, or
extract and `cat manifest.json | jq` to see which contributors ran.

## Pro vs open-core

Open-core (`rigplane`) uploads are anonymous: no authentication, IP
rate-limited (5/hour, 10/day per IP), 90-day retention, no AI triage.
The bundle covers everything in §4.4 of the spec — system, radio,
audio, logs, state, errors, dependencies, config.

`rigplane-pro` adds its own contributors (Tauri logs, Rust crash
dumps, RC-28 controller state, DSP state contributors) under
`extensions/` in the same bundle, and signs the upload with the active
license. Customer-tier service applies: longer retention, higher rate
limits, AI-driven triage, and tracked support tickets. Open-core does
not see, ship, or depend on any of this — Pro contributors layer on
through `setuptools` entry points.

## Troubleshooting

### Cache directory permissions

`rigplane` keeps a rotating diagnostic log at:

- Linux: `~/.cache/rigplane/logs/`
- macOS: `~/Library/Caches/rigplane/logs/`
- Windows: `%LOCALAPPDATA%\rigplane\Cache\logs\`

If this directory cannot be created (read-only home, sandboxed runtime,
permission-denied) the diagnostic logger silently disables itself —
`rigplane` keeps running and continues to log to stdout/stderr, but the
`logs` contributor in the bundle will be empty or absent. To fix:

```bash
mkdir -p ~/.cache/rigplane/logs
chmod 700 ~/.cache/rigplane/logs
```

You can also set `RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING=1` to disable
the file logger explicitly (used by the test suite).

### Rate-limited (429)

Anonymous uploads are limited to **5 reports per hour and 10 per day
per IP**. If you hit the limit you'll see:

```
Upload rejected: rate_limited (retry after 1842 seconds).
Bundle saved locally at /home/you/rigplane-report-….zip
```

Wait for the retry window, or activate an `rigplane-pro` license for
authenticated uploads with higher limits. The bundle is always saved
locally on rate-limit, so you don't lose the report.

### Bundle too large (413)

The triage endpoint rejects bundles larger than **25 MiB**. The
default rotating log cap is 15 MiB, so you'll usually be fine, but a
long-running session that triggers many tracebacks plus a verbose
extension contributor can push you over. Mitigations:

```bash
# Drop the logs category — keeps state, radio, errors, dependencies
rigplane diagnose --upload --exclude logs

# Or split: send state-only first, then logs separately
rigplane diagnose --output state.zip --exclude logs
rigplane diagnose --output logs.zip --include logs
```

### Forbidden content rejected (422)

The receiver runs a content scanner against the bundle. If it finds a
secret-like pattern that the client-side redaction missed, the upload
is rejected:

```
Upload rejected: forbidden_content (matched pattern: aws_secret_access_key).
```

Two things to check:

- Inspect your local config — `~/.rigplane/*.toml` and any
  `.env`-style file in the directory you ran `rigplane` from. If raw
  credentials live there in plaintext, redact or delete them.
- The redaction utilities live at
  `src/rigplane/diagnostics/redaction.py` — if a legitimate value is
  being false-positive matched, please file an issue with the
  pattern (not the value).

## CLI reference

```
rigplane diagnose
  [--upload]                    # send after preview (default: save only)
  [--output PATH]               # default: ~/rigplane-report-<timestamp>.zip
  [--include CATEGORY ...]      # repeatable; default: all
  [--exclude CATEGORY ...]      # repeatable
  [--description TEXT]          # bypass interactive prompt
  [--issue-ref URL]             # bypass interactive prompt
  [--email EMAIL]               # bypass interactive prompt (opt-in)
  [--callsign CS]               # bypass interactive prompt (opt-in)
  [--endpoint URL]              # override env default
  [--no-confirm]                # skip interactive consent
  [--bundle-id UUID]            # explicit submission_id (for retry/dedup)
```

| Flag             | Default                                 | Notes                                                              |
| ---------------- | --------------------------------------- | ------------------------------------------------------------------ |
| `--upload`       | absent → save locally                   | Required to transmit. Combined with `--no-confirm` for headless.   |
| `--output`       | `~/rigplane-report-<timestamp>.zip`     | The bundle is always written to this path, upload or no upload.    |
| `--include`      | all categories                          | Repeatable. Mutually narrows: `--include radio --include logs`.    |
| `--exclude`      | none                                    | Repeatable. Removes a category from the default-all set.           |
| `--description`  | interactive prompt                      | Free text — explain what you were doing when the bug occurred.     |
| `--issue-ref`    | interactive prompt                      | Optional GitHub URL or issue number for context.                   |
| `--email`        | not collected                           | Opt-in. If set, the maintainer can reach you about the report.     |
| `--callsign`     | not collected                           | Opt-in. Helps if your radio behaviour is callsign-specific.        |
| `--endpoint`     | `$RIGPLANE_REPORT_ENDPOINT` or default  | Override the upload destination (self-hosting / audit / dev).      |
| `--no-confirm`   | absent (interactive)                    | Skip the consent prompt. Required for headless `--upload`.         |
| `--bundle-id`    | new UUID v4                             | Reuse a previous submission ID for retry / dedup on the receiver.  |

!!! tip "Scripted use"
    For CI / cron, the only fully-automated upload form is:

    ```bash
    rigplane diagnose --upload --no-confirm \
      --description "nightly canary failed at $(date -Is)" \
      --issue-ref https://github.com/example/repo/issues/42
    ```

    Without `--no-confirm`, headless `--upload` saves locally and
    exits — by design.

## See also

- [Open-core policy](../architecture/open-core-policy.md) — §2 carve-out
  defining the privacy invariants this feature is built around.
- [Diagnostic data collection design](../plans/2026-05-03-diagnostic-data-collection-design.md)
  — the full spec, including manifest schema, contributor protocol,
  and Pro extension boundary.
- [Diagnostic bundle contract](../contracts/diagnostic-bundle-v1.md) —
  the public `rigplane-bundle-v1` contract documenting the receiver's
  HTTP API for self-hosted backends.
- [Troubleshooting](troubleshooting.md) — general `rigplane`
  troubleshooting playbook.
