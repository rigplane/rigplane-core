# icom-lan 1.1.0

**Release date:** May 4, 2026

Two headline items:

1. **Critical fix for the WSJT-X / JS8Call audio regression on IC-7610 LAN bridges (#1381).** Since v0.17.0 the LAN audio bridge fed stereo radio bytes (the new dual-RX `PCM_2CH_16BIT` default) into a mono PortAudio stream, halving the effective sample-rate. Users observed 1.5 kHz audio with the 3 kHz USB filter and TX silence. Fixed: the bridge now auto-detects the radio's negotiated channel count and downmixes (L+R)/2 → mono inside the bridge, mirroring the web UI broadcaster pattern. WSJT-X / JS8Call / fldigi see a clean mono stream at full sample-rate again. Single-RX rigs and the dual-RX web routing path are unaffected.

2. **Opt-in diagnostic reporting end-to-end** (epic #1385). New `icom-lan diagnose` CLI subcommand + Web UI "Send diagnostic report" dialog build a redacted bundle from 9 contributors and (only on explicit user action) post to a maintainer-operated triage service.

Plus frontend tech-debt closeout (#1369 cluster) and a configurable RX audio jitter buffer for unreliable LAN links (#1363).

## Added

- **Diagnostic data collection (epic #1385).** New `icom-lan diagnose` CLI subcommand and Web UI "Send diagnostic report" dialog (Settings panel) collect a structured ZIP from 9 contributors (`system`, `invocation`, `radio`, `audio`, `logs`, `state`, `errors`, `dependencies`, `config`) with PII redaction (paths, IPs, credentials, tokens). Default is local save; explicit `--upload` opt-in posts to `https://reports.msmsoft.net/v1/diagnostics/upload` (override via `ICOM_LAN_REPORT_ENDPOINT`). Privacy invariants per `docs/architecture/open-core-policy.md` §2 carve-out.
- **Public contract `docs/contracts/diagnostic-bundle-v1.md`** documents the anonymous-tier upload protocol; third-party self-hosters can implement against it (#1398).
- **`DiagnosticContributor` protocol + entry-point discovery** (`icom_lan.diagnostics` group) lets `icom-lan-pro` plug in additional data sources without touching open-core (#1389).
- **HTTP upload client** with typed errors (`RateLimited`, `BundleTooLarge`, `ForbiddenContent`, `MetadataInvalid`, `NetworkError`) and a `header_provider` hook for Pro-side signed uploads (#1394).
- **Always-on rotating diagnostic log** (`SafeRotatingFileHandler`, `~/.cache/icom-lan/logs/`, ~15 MiB cap) scoped to `logging.getLogger("icom_lan")` so library-mode use stays clean (#1387).
- **Configurable RX audio jitter buffer** for unreliable LAN links (#1363).
- **User documentation:** `docs/guide/diagnostic-reports.md` walkthrough with privacy invariants, troubleshooting, and CLI reference (#1401).

## Changed

- `pip install icom-lan` no longer transitively imports `aiohttp` — the `icom_lan.diagnostics.upload` module is lazy-loaded via PEP 562 `__getattr__` so all CLI commands work without aiohttp installed (a dev-only dependency). `icom-lan diagnose --upload` emits a friendly install hint instead of a Python traceback when aiohttp is missing (#1417, #1420).
- Open-core policy formally documents the **carve-out for user-initiated diagnostic support reports** alongside the existing "no telemetry" rule (`docs/architecture/open-core-policy.md` §2).

## Fixed

- **`AudioBridge` stereo-to-mono downmix for hamlib clients (#1381).** See headline #1 above. Critical regression fix for IC-7610 LAN bridge users running WSJT-X / JS8Call / fldigi via `icom-lan web --bridge`.
- IPv6 redaction handles compressed `::` forms correctly via `ipaddress.ip_address()` validation; previously public addresses ending in `::1` were misclassified as loopback (#1418).
- Path redaction skips URL-embedded paths via negative lookbehind (#1418).
- Web UI `handle_send` race condition: atomic CSRF check-and-set under the session lock prevents concurrent double-uploads (#1419).
- Web UI modal `handleCancel` respects `busy` state (Escape and backdrop click ignored while a request is in flight) (#1419).
- macOS device labels with embedded usernames scrubbed in the audio contributor (#1418).
- Hostname redaction (`*.example.com`, `*.local`) for the radio contributor's `host` field (#1418).
- Bundle assembler cleans up partial output from failed contributors so it doesn't leak into the ZIP (#1418).
- PATH env var redacted per-segment so the `:` separator no longer breaks the lookbehind guard (#1418).
- Threading exceptions captured by `_error_ring` via `threading.excepthook` alongside `sys.excepthook` (#1418).
- Frontend tech-debt cluster cleared (epic #1369): 173 svelte-check errors from self-wired panel migration (#1370), 8 misc type errors (#1374), nullable narrowing + Promise generic in HTTP client (#1372), vitest 4 mock signatures (#1371), invalid jitter env-var half-apply (#1366), ruff format sweep (#1368).
- CI gate: `npm run check` + vitest now blocks frontend regressions (#1375).

## Docs

- Diagnostic data collection design spec, 530 LOC (#1386).
- Public bundle contract `diagnostic-bundle-v1.md` (#1398).
- User guide for diagnostic reports (#1401).
- Open-core policy carve-out for user-initiated reports (#1418).

## Install / Upgrade

```bash
pip install icom-lan==1.1.0
# or upgrade:
pip install --upgrade icom-lan
```
