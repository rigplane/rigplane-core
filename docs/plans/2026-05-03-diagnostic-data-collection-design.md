# Diagnostic Data Collection — icom-lan (open-core) — Design

**Date:** 2026-05-03
**Tracking issue:** [morozsm/icom-lan#1385](https://github.com/morozsm/icom-lan/issues/1385)
**Cross-repo epic:** [morozsm/icom-lan-pro#583](https://github.com/morozsm/icom-lan-pro/issues/583) (Pro client + DO backend + AI triage)
**Contract:** [morozsm/icom-lan-pro PR #584](https://github.com/morozsm/icom-lan-pro/pull/584) (`license-authority-v0` extension)

---

## 1. Goal

Provide a one-click "Send report" UX for icom-lan users that collects everything typically useful for debugging any icom-lan issue (given access to source code), packages it as a redacted bundle, and — strictly opt-in, on explicit user action — uploads it to a maintainer-operated triage service.

The feature must satisfy three project-level constraints simultaneously:

1. **Open-core "no telemetry ever" principle.** No automatic, background, or first-run data collection. The user must take an unambiguous action every time, with full visibility into what is being sent.
2. **Headless mode sacred.** CLI works without TTY prompts (pipes/CI/cron). Web UI / Pro Tauri UI provides the interactive flow.
3. **Pro-extensibility.** icom-lan-pro must be able to layer its own contributors (Tauri logs, Rust crash dumps, RC-28, DSP) on top of the open-core bundle without proprietary code leaking into core.

## 2. Scope

This spec is the **A** subsystem of a 4-subsystem feature:

| Subsystem | Repo | Owner |
|---|---|---|
| **A. icom-lan core** — bundle generator, extension point, anonymous upload client | morozsm/icom-lan | this spec |
| **B. icom-lan-pro client** — Pro contributors, signed upload, Tauri UI | morozsm/icom-lan-pro | epic #583 / sub-issues #588–#590 |
| **C. DO backend** — `/v1/diagnostics/upload` endpoint, Spaces storage, anti-abuse | morozsm/icom-lan-pro | sub-issues #585–#587 |
| **D. AI triage** — bundle ingestion, summarisation, ticket drafting | morozsm/icom-lan-pro | sub-issue #591 |

Open-core (this spec) defines the public contract that **B** layers on top of and **C** receives.

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Trigger surfaces (all call build_bundle / build_and_upload) │
│                                                              │
│   CLI: `icom-lan diagnose [--upload]`                        │
│   Web UI: Settings → "Send diagnostic report"                │
│   Pro Tauri UI: own "Send report" → /api/local/v1/diagnose   │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  icom_lan.diagnostics.build_bundle(ctx, output_dir)          │
│                                                              │
│   1. Discover contributors                                   │
│      - Built-in (system, invocation, radio, audio,           │
│        logs, state, errors, dependencies, config)            │
│      - Setuptools entry points (`icom_lan.diagnostics`)      │
│      - Runtime-registered (testing / dynamic)                │
│   2. For each contributor:                                   │
│      - Wrap in try/except — failures land in manifest        │
│        `warnings`, never crash the bundle                    │
│      - Apply PII redaction utilities                         │
│      - Write to `<output_dir>/<contributor_name>/...`        │
│   3. Assemble manifest.json                                  │
│   4. ZIP everything                                          │
└─────────────────────────┬────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  icom_lan.diagnostics.build_and_upload(ctx, request_signer)  │
│                                                              │
│   - Calls build_bundle to produce zip                        │
│   - Multipart POST to ICOM_LAN_REPORT_ENDPOINT               │
│     (default: https://reports.msmsoft.net/v1/diagnostics/    │
│      upload)                                                 │
│   - request_signer (optional callable from Pro) injects      │
│     Authorization: Bearer <activation_token>                 │
│   - Open-core: signer=None → anonymous upload                │
│   - Returns ReportSubmitted{report_id, support_url, ...}     │
└──────────────────────────────────────────────────────────────┘
```

**Always-on diagnostic logging** runs underneath all of this: a `SafeRotatingFileHandler` is added to the root logger early in process startup so the `logs` contributor has data even after a crash or short CLI invocation.

## 4. Components

### 4.1 SafeRotatingFileHandler (always-on logging)

**Location:** `src/icom_lan/diagnostics/_logging.py`

A subclass of `logging.handlers.RotatingFileHandler` that **never raises** on init or emit. Failures (no permissions, disk full, FS read-only, sandbox blocks, dir mid-deleted) are silently swallowed; the application continues to log via stdout/stderr as before.

```python
class SafeRotatingFileHandler(RotatingFileHandler):
    _unhealthy = False

    def emit(self, record):
        if self._unhealthy:
            return
        try:
            super().emit(record)
        except Exception:
            self._unhealthy = True  # cheap drop on subsequent records
```

**Init behaviour** (`configure_diagnostic_logging()`):

```python
def configure_diagnostic_logging() -> None:
    """Best-effort. Any failure is silent; app continues with stdout/stderr."""
    if os.environ.get("ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING") == "1":
        return
    try:
        log_dir = platformdirs.user_cache_path("icom-lan") / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingFileHandler(
            log_dir / "icom-lan.log",
            maxBytes=5 * 1024 * 1024,   # 5 MiB
            backupCount=2,              # keep 2 rotations → ~15 MiB total
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_DIAGNOSTIC_FORMATTER)
        # Attach to the `icom_lan` logger, NOT root — see "Logger scope" below.
        logging.getLogger("icom_lan").addHandler(handler)
    except Exception as exc:
        sys.stderr.write(
            f"icom-lan: diagnostic logging disabled: {exc}\n"
        )

# Globally
logging.raiseExceptions = False
```

Called once, near the top of `icom_lan/__init__.py` (or whichever entry point runs earliest in every code path: CLI, web, library import). Test isolation via `ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING=1` (set by `conftest.py` autouse fixture).

**Logger scope:** the handler is attached to `logging.getLogger("icom_lan")`, **not** the root logger. This is deliberate — when icom-lan is imported as a library by a third-party app (a common Pro use case), the host application's own loggers (`myapp.foo`, `gunicorn`, `aiohttp.access`, etc.) must not be captured into icom-lan's diagnostic file. icom-lan emits via `logger = logging.getLogger(__name__)` where `__name__` always starts with `icom_lan.`, so propagation up to the `icom_lan` logger is automatic for our own code; foreign loggers stop at root and never reach our file. Pro contributors that want their own emissions captured can either add their own handler to `logging.getLogger("icom_lan_pro")` or emit through `logging.getLogger("icom_lan.diagnostics.contributor.<name>")` to ride the existing channel.

This is also why this section says "no automatic submission" rather than "no automatic data collection": local rotating logs are *bounded local storage*, scoped to icom-lan's own loggers, capped at ~15 MiB, never transmitted without an explicit user action. Submission to a remote endpoint is what the open-core policy forbids automating, not the local logs themselves.

**Cache-dir resolution** uses `platformdirs.user_cache_path("icom-lan")`: `~/.cache/icom-lan/logs/` on Linux, `~/Library/Caches/icom-lan/logs/` on macOS, `%LOCALAPPDATA%\icom-lan\Cache\logs\` on Windows.

### 4.2 DiagnosticContributor protocol

**Location:** `src/icom_lan/diagnostics/contributor.py`

```python
@runtime_checkable
class DiagnosticContributor(Protocol):
    """A pluggable source of diagnostic data."""

    name: str   # used for bundle subdirectory and manifest entry

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        """Write diagnostic data files into `output_dir`. May raise on
        recoverable failure; the bundler will log to manifest.warnings
        and continue with other contributors."""

@dataclass(frozen=True)
class BundleContext:
    radio: Any | None              # AudioCapable | None — running radio if any
    config_dir: Path               # ~/.config/icom-lan/ (platformdirs)
    log_dir: Path                  # ~/.cache/icom-lan/logs/
    user_description: str | None
    issue_ref: str | None
    contact_email: str | None      # opt-in
    contact_callsign: str | None   # opt-in
    submission_id: str             # uuid4, populated by orchestrator
    generated_at_unix: int
```

`Any | None` for `radio` avoids a circular import (`icom_lan.runtime` -> `icom_lan.diagnostics`). Contributors that need radio-specific access do `isinstance(ctx.radio, AudioCapable)` themselves.

### 4.3 Discovery (entry points + runtime register)

**Location:** `src/icom_lan/diagnostics/_discovery.py`

```python
_RUNTIME_REGISTERED: list[type[DiagnosticContributor]] = []

def register(contributor_cls: type[DiagnosticContributor]) -> None:
    """Programmatic registration (testing, dynamic plugins)."""
    _RUNTIME_REGISTERED.append(contributor_cls)

def discover() -> list[DiagnosticContributor]:
    """Return instances of all built-in + entry-point + runtime-registered
    contributors, deduplicated by `name`."""
    instances: dict[str, DiagnosticContributor] = {}
    # Built-in
    for cls in _BUILT_IN_CONTRIBUTORS:
        instances[cls.name] = cls()
    # Entry points
    for ep in importlib.metadata.entry_points(group="icom_lan.diagnostics"):
        try:
            cls = ep.load()
            instance = cls()
            instances[instance.name] = instance
        except Exception:
            logger.warning("failed to load contributor %s", ep.name, exc_info=True)
    # Runtime-registered
    for cls in _RUNTIME_REGISTERED:
        instance = cls()
        instances[instance.name] = instance
    return list(instances.values())
```

**Pro plug-in pattern** (in `icom-lan-pro/pyproject.toml`):

```toml
[project.entry-points."icom_lan.diagnostics"]
pro_tauri_logs    = "icom_lan_pro.diagnostics:ProTauriLogsContributor"
pro_rust_crashes  = "icom_lan_pro.diagnostics:ProRustCrashesContributor"
pro_rc28_state    = "icom_lan_pro.diagnostics:ProRc28StateContributor"
pro_dsp_state     = "icom_lan_pro.diagnostics:ProDspStateContributor"
pro_commercial    = "icom_lan_pro.diagnostics:ProCommercialContributor"
```

icom-lan core knows nothing about these. Pip install Pro → contributors auto-discovered.

### 4.4 Built-in contributors

| Name | Output | Source | Redaction |
|------|--------|--------|-----------|
| `system` | `system/system.json` | OS, arch, Python ver, icom-lan ver, install method | abs paths |
| `invocation` | `invocation/invocation.json` | `sys.argv` (filtered), env (allowlist) | passwords/tokens → `***` |
| `radio` | `radio/radio.json` | `ctx.radio` model, FW ver, backend, capabilities, audio_codec | IPs/hostnames masked |
| `audio` | `audio/audio.json` | codec, channels, sample rate, devices, bridge state | macOS device names with usernames → masked |
| `logs` | `logs/icom-lan.log{,.1,.2}` | copies of files in `ctx.log_dir` | path/IP/cred regex pass on copies |
| `state` | `state/state.json` | current freq/mode/meters, last N CI-V exchanges (ring) | optional callsign masking |
| `errors` | `errors/recent-tracebacks.json` | recent exceptions ring (in-process) | path scrubbed |
| `dependencies` | `dependencies/pip-freeze.txt` | `importlib.metadata` enumeration | none |
| `config` | `config/config-summary.json` | `~/.icom-lan/*.toml` content | passwords/creds → `***`, drop `password` keys entirely |

**Never included** (drop, do not mask):

- LAN username/password
- Audio PCM samples
- Contents of `.env` files in CWD
- macOS Keychain secrets

### 4.5 PII redaction utilities

**Location:** `src/icom_lan/diagnostics/redaction.py`

Module-level pure functions:

- `redact_paths(text: str) -> str` — replaces `/Users/<name>/...`, `/home/<name>/...`, `C:\Users\<name>\...` with `<HOME>/...`.
- `redact_ips(text: str) -> str` — IPv4 and IPv6, except RFC 1918 private ranges (those are kept since they're the radio's address and useful for triage).
- `redact_credentials(text: str) -> str` — patterns: `password=...`, `pwd=...`, `Authorization:\s*Bearer\s+\S+`, `aws_access_key_id`, `aws_secret_access_key`, raw activation codes (`code_[A-Z0-9]{26}`), `BEGIN ... PRIVATE KEY` blocks.
- `redact_tokens(text: str) -> str` — generic high-entropy token shapes (`[A-Za-z0-9_-]{32,}` near `token=`/`Bearer`/`api_key=` keywords).

Built-in contributors use these directly. Extension contributors (Pro) can import the same module — it lives in `icom_lan.diagnostics.redaction` and is part of the public extension surface.

`manifest.redactions_applied` lists which scrubbers ran for the bundle (`["paths", "ips", "credentials", "tokens"]`).

### 4.6 Bundle assembler

**Location:** `src/icom_lan/diagnostics/bundle.py`

```python
def build_bundle(ctx: BundleContext, output_path: Path) -> Path:
    """Collect contributions, assemble manifest, write a zip at output_path.
    Returns the absolute path of the created zip."""
    with tempfile.TemporaryDirectory() as staging:
        staging_dir = Path(staging)
        manifest = _Manifest(ctx)

        for contributor in discover():
            contributor_dir = staging_dir / contributor.name
            contributor_dir.mkdir(parents=True, exist_ok=True)
            try:
                contributor.contribute(ctx, contributor_dir)
                manifest.record_success(contributor, contributor_dir)
            except Exception as exc:
                manifest.record_warning(contributor, str(exc))

        manifest.write(staging_dir / "manifest.json")
        return _zip_directory(staging_dir, output_path)
```

`_Manifest` produces `manifest.json` per the schema in §5.

### 4.7 HTTP upload client

**Location:** `src/icom_lan/diagnostics/upload.py`

```python
@dataclass(frozen=True)
class ReportSubmitted:
    report_id: str
    support_url: str
    received_at_unix: int
    auth_class: str  # 'anonymous' | 'authenticated'

HeaderProvider = Callable[[], Awaitable[dict[str, str]]]

async def upload_bundle(
    bundle_path: Path,
    metadata: dict[str, Any],
    *,
    endpoint: str | None = None,
    header_provider: HeaderProvider | None = None,
    timeout_s: float = 60.0,
) -> ReportSubmitted:
    """POST multipart {bundle, metadata} to endpoint.

    `header_provider` is the extension hook for Pro: an async callable that
    returns additional HTTP headers to merge into the request (e.g.
    `{"Authorization": "Bearer <token>"}`). Open-core invocation passes
    `None` (anonymous upload, no extra headers).

    The hook is intentionally HTTP-client-agnostic — a coroutine returning a
    plain `dict[str, str]` of header name to value. Pro implements it using
    whatever client and token-refresh logic it wants, without coupling
    icom-lan core to aiohttp internals (or any HTTP client at all). On 401
    the upload client calls `header_provider` a second time and retries
    once, so Pro's implementation can do refresh-on-call without external
    coordination.
    """
    endpoint = endpoint or os.environ.get(
        "ICOM_LAN_REPORT_ENDPOINT",
        DEFAULT_ENDPOINT,  # https://reports.msmsoft.net/v1/diagnostics/upload
    )
    ...
```

- Single attempt with 60s timeout, plus one retry on 401 if `header_provider` is set (calls it again before retry; Pro's implementation refreshes the token).
- `429 rate_limited` → raise typed exception with `retry_after_seconds` so CLI / Web UI can show correct guidance.
- `413 bundle_too_large`, `422 forbidden_content`, `400 metadata_invalid` → typed exceptions.

### 4.8 CLI command

**Location:** `src/icom_lan/cli/__init__.py` (subcommand registration) + `src/icom_lan/cli/_diagnose.py`

```
icom-lan diagnose
  [--upload]              # POST after preview (default: save file only)
  [--output PATH]         # default: ~/icom-lan-report-<timestamp>.zip
  [--include CATEGORY]    # repeatable; default: all
  [--exclude CATEGORY]    # repeatable
  [--description TEXT]    # bypass interactive prompt
  [--issue-ref URL]       # bypass interactive prompt
  [--email EMAIL]         # bypass interactive prompt (opt-in)
  [--callsign CS]         # bypass interactive prompt (opt-in)
  [--endpoint URL]        # override env default
  [--no-confirm]          # skip interactive consent
  [--bundle-id UUID]      # explicit submission_id (for retry/dedup)
```

**Default behaviour without `--upload` flag (any context):** build the bundle, save it to `--output` path, print the path. **Never upload, never prompt for upload.** `icom-lan diagnose` is fundamentally a local-bundle generator; sending is a separate explicit step that requires `--upload`.

**Default behaviour with `--upload` on a TTY:** walk the user through prompts (description / issue ref / contact opt-in), show preview with file list and total size, then ask the final consent prompt:

```
Send to https://reports.msmsoft.net/v1/diagnostics/upload? [y/N]
```

The default is **save locally** — pressing Enter does not transmit anything; it saves to `--output` and prints the path. Only an explicit `y` triggers upload. This keeps the privacy-sensitive action off the default keystroke.

**Non-interactive (no TTY) with `--upload`:** must also pass `--no-confirm` to actually upload. Without `--no-confirm` on a non-TTY, the command saves locally and prints the path with a message explaining that confirmation cannot be obtained without a TTY. This prevents headless wrappers from accidentally transmitting.

**Mixed:** any flag bypasses the prompt for that field. `--upload --no-confirm` enables fully-scripted submission for CI / cron use; the user has explicitly accepted the consequences by passing both flags.

### 4.9 Web UI handler + frontend

**Location:**
- `src/icom_lan/web/handlers/diagnostics.py` — REST handlers
- `frontend/src/components-v2/dialogs/SendReportDialog.svelte` — modal UI

REST endpoints under `/api/v1/diagnose`:

- `POST /api/v1/diagnose/preview` — body: form fields (description, issue_ref, opt-in contact, category includes/excludes). Returns: `{preview_id, csrf_token, manifest, files: [{path, size}], total_size_bytes, redactions_applied, endpoint_url}`. Server generates the bundle into a session-scoped temp path keyed by `preview_id` and mints a one-shot `csrf_token` (random opaque string) bound to it.
- `POST /api/v1/diagnose/send` — body: `{preview_id, consent: true}`, header: `X-Diagnostic-CSRF: <csrf_token>`. Looks up the previewed bundle, uploads, returns `ReportSubmitted` JSON.
- `POST /api/v1/diagnose/save` — body: `{preview_id}`, header: `X-Diagnostic-CSRF: <csrf_token>`. Returns the bundle as a download (`Content-Disposition: attachment; filename="icom-lan-report-<timestamp>.zip"`).
- `DELETE /api/v1/diagnose/preview/{preview_id}` — header: `X-Diagnostic-CSRF: <csrf_token>`. Discards a previewed bundle (cancel button or page navigation).

Preview bundles auto-expire after 10 minutes if not sent / saved / deleted. Cleanup runs on a background task. `preview_id` is opaque to the client and required for any follow-up action — prevents replay of previously-generated bundles.

**Abuse resistance.** The web server may bind to `0.0.0.0` and serve clients on the LAN. The diagnostic endpoints handle privacy-sensitive operations and must not be triggerable by unrelated LAN clients. The handler enforces:

- **Same-origin check on follow-up endpoints.** `/api/v1/diagnose/{send,save}` and `DELETE /preview/{id}` require an `Origin` header that matches the server's bound host (or loopback). Cross-origin requests return `403 origin_mismatch`. The initial `POST /preview` is allowed cross-origin (no privacy operation yet) but its response cannot be used without the CSRF token from it.
- **Preview-bound CSRF token.** `send`, `save`, and `DELETE /preview/{id}` require the `X-Diagnostic-CSRF` header. Token is single-use for `send` (consumed on successful upload), reusable for `save` and `delete` until preview expiry. Mismatched/missing token: `403 csrf_missing`.
- **Loopback exception for Origin.** When the server is bound to `127.0.0.1` / `::1`, the same-origin check is skipped — the loopback boundary itself is the security boundary, and dev tools / curl (which often omit `Origin`) need to work. CSRF token is still required.
- **API auth inheritance.** When the server has API token auth configured (existing feature for remote-bound deployments), the diagnostic endpoints inherit the same requirement; the diagnostics surface does not bypass existing auth.

Net effect: a malicious page on another LAN host cannot drive a CSRF-style upload (no token, blocked by origin check); a curl-from-LAN cannot drive an upload without first observing the CSRF token (which requires the same origin restriction); and a misconfigured 0.0.0.0 deployment with API auth stays protected by the existing auth layer.

**Frontend:** Settings panel section "Diagnostics" with a "Send diagnostic report" button. Click opens `SendReportDialog`:

1. Form screen — description / issue URL / opt-in contact / category checkboxes / "Generate preview".
2. Preview screen — file tree, sizes, endpoint URL, redactions, "I understand" checkbox, `[Cancel] [Save locally] [Send]`.
3. Result screen — success: `support_url` with copy button. Failure: typed error (rate-limited / network / bundle too large / forbidden content) with actionable next step.

**Error overlay (v2)** — a separate sub-issue. When an unhandled exception bubbles up to the web UI, show a banner "Something went wrong. Send a report?" linking to the same dialog with the recent traceback pre-included.

## 5. Bundle layout & manifest

### 5.1 ZIP layout

```
icom-lan-report-<timestamp>.zip
├── manifest.json
├── system/system.json
├── invocation/invocation.json
├── radio/radio.json
├── audio/audio.json
├── logs/
│   ├── icom-lan.log
│   ├── icom-lan.log.1
│   └── icom-lan.log.2
├── state/state.json
├── errors/recent-tracebacks.json
├── dependencies/pip-freeze.txt
├── config/config-summary.json
└── extensions/
    └── <pro-contributor>/...   # populated by Pro entry points
```

### 5.2 manifest.json schema (`icom-lan-bundle-v1`)

**Required fields** (server returns `metadata_invalid` if absent):

- `schema_version` — always `"icom-lan-bundle-v1"`
- `submission_id` — UUID v4 generated client-side
- `generated_at_unix` — `int(time.time())` at bundle creation
- `app.name` — `"icom-lan"` (open-core) or `"icom-lan-pro"` (Pro)
- `app.version` — `importlib.metadata.metadata("icom-lan")["Version"]`
- `platform.os` — `"darwin" | "linux" | "windows"`
- `platform.arch` — `"arm64" | "x86_64" | ...`

**Optional fields** (omit from JSON, do not send `null`, when unavailable):

- `app.build_id` — `git describe --always` if running from a git repo, else absent
- `platform.python_version` — `sys.version`
- `user_description`, `issue_ref` — user-supplied
- `contact.email`, `contact.callsign` — opt-in user-supplied
- `contributors[]` — list of `{name, files, size_bytes}`
- `redactions_applied[]` — list of scrubber names that ran
- `warnings[]` — list of `{contributor, message}` for non-fatal failures

Server-side: required-field validation only; unknown JSON fields ignored; missing optional fields tolerated.

### 5.3 Open-core public contract doc

`docs/contracts/diagnostic-bundle-v1.md` (created as part of this implementation) mirrors the anonymous request shape from `license-authority-v0`. It contains:

- Multipart request shape
- Metadata schema with required/optional split (per §5.2)
- Success / error response shapes
- Anonymous-tier behaviour (rate limits, retention)
- A pointer to the proprietary `license-authority-v0` for authenticated-tier extensions (deliberately opaque)

This doc is the contract open-core builds against. Pro-only extensions (Authorization header semantics, `support_id` linkage, customer-tier privileges) stay in the proprietary contract and are not documented here.

## 6. Data flow

```
Trigger (CLI/WebUI/Tauri) ──┐
                            │ user fills form, opts in to send
                            ▼
                  build_bundle(ctx, path)
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       built-in       entry-point   runtime-registered
       contributors   contributors  contributors
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                manifest.json + zip assembly
                            │
                            ├──── --upload absent ──► save file, print path
                            │
                            └──── --upload set ──────► build_and_upload
                                                              │
                                                              ▼
                                                  request_signer(req) (Pro)
                                                              │
                                                              ▼
                                          POST /v1/diagnostics/upload
                                                              │
                                                              ▼
                                              ReportSubmitted {report_id,
                                                support_url, auth_class}
```

## 7. Privacy invariants

- **No automatic submission, ever.** Every upload is preceded by an explicit user action (CLI confirmation prompt, Web UI consent checkbox, Tauri "Send" click).
- **Headless mode unchanged.** Without TTY and without `--upload` flag, no prompts, no upload, only optional local file save.
- **Contact fields opt-in.** `contact.email`, `contact.callsign` are never auto-populated from env vars, system identity, config, or prior reports.
- **Preview before send.** All trigger surfaces show full bundle file list + sizes + endpoint URL before submission.
- **Local-first.** Bundle is always available as a local file. User can save without sending.
- **Endpoint override.** `ICOM_LAN_REPORT_ENDPOINT` env var redirects to a different host (self-hosting, audit, dev). Default is hardcoded but transparent (visible in source and in preview).
- **No first-run prompts, no install-time consent dialogs, no nagging.** Diagnostic flow is invisible until the user navigates to it.

## 8. Error handling / fail-safe behaviour

- `SafeRotatingFileHandler` swallows all I/O errors silently (lazy init + emit-time guard, see §4.1).
- Per-contributor `try/except` wrapping; failures land in `manifest.warnings` but don't abort bundle generation.
- Bundle is generated even with all contributors failed (manifest + warnings only). User can still send what is essentially a "the diagnostic system itself is broken" report.
- Upload errors are typed (`RateLimited`, `BundleTooLarge`, `ForbiddenContent`, `MetadataInvalid`, `NetworkError`) so trigger surfaces can render actionable messages and offer "save locally" fallback.
- Pro signer failure (no license, refresh failed): falls back to anonymous upload with a warning surfaced in the UI.

## 9. Testing strategy

- **Unit:** redaction utilities (golden patterns), `SafeRotatingFileHandler` behaviour under failure modes (mocked I/O), `_Manifest` JSON shape, contributor protocol.
- **Integration:** `build_bundle` end-to-end with a mock radio + temp filesystem; assert manifest shape, file presence, redaction stats. Parametrised over scenarios: full radio session / no radio / partial contributor failure / oversized log file.
- **Upload:** synthetic local HTTP server that conforms to the contract; verifies multipart shape, error code handling, signer hook.
- **CLI:** parametrised over interactive vs non-interactive (`pty` simulation), with-flags vs without, `--upload` vs `--save-only`.
- **Web UI:** Vitest tests for `SendReportDialog` component states (form / preview / sending / success / each failure type). Playwright e2e against a localhost mock receiver.
- **Privacy invariant tests:** assert that bundles never contain forbidden patterns even when synthetic input includes them. One test per pattern (parameterised), plus a fuzzed test that injects random secrets and verifies redaction.
- **Test isolation:** `ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING=1` set autouse in `conftest.py`. Tests that exercise logging explicitly enable it via fixture.

## 10. Open-core boundary

- icom-lan core knows nothing about icom-lan-pro types or symbols. No `icom_lan_pro.*` imports.
- The extension point is a pure protocol (`DiagnosticContributor`) discovered via stdlib `importlib.metadata.entry_points`.
- The upload `request_signer` callable is generic — receives an `aiohttp.ClientRequest` and may set headers. Open-core has no concept of "license token".
- Default endpoint URL is hardcoded but transparent and overridable. Endpoint behaviour is documented in `docs/contracts/diagnostic-bundle-v1.md` (open-core sub-contract).
- Pro extensions are pip-installed alongside icom-lan and discovered automatically; no editing of icom-lan source required.

## 11. Cross-repo coordination

- Contract: morozsm/icom-lan-pro PR #584 — must merge before backend implementation begins. Commits land on `feat/diagnostics-contract` branch.
- Backend infrastructure: morozsm/icom-lan-pro #585 (subdomain), #586 (endpoint), #587 (anti-abuse). Provisioned in parallel with this open-core implementation; not a hard dependency for shipping the bundle generator (which can save locally).
- Pro client: morozsm/icom-lan-pro #588 (signer) and #589 (contributors) depend on this open-core epic shipping the extension point. Once `DiagnosticContributor` and `request_signer` callable are merged, Pro can build against them.
- AI triage: morozsm/icom-lan-pro #591 — depends on backend (data source). Independent of open-core.

## 12. Sub-issues breakdown (autonomous-pipeline atomic tasks)

To be filed after this spec is approved. Expected breakdown:

1. **Always-on rotating file logging** — `SafeRotatingFileHandler`, `configure_diagnostic_logging`, `platformdirs` integration, test isolation env var, autouse fixture. ~2 files / ~120 LOC.
2. **PII redaction utilities** — `redaction.py` with the 4 scrubbers + tests. ~2 files / ~150 LOC.
3. **Contributor protocol + discovery** — `contributor.py`, `_discovery.py`, `BundleContext` dataclass, runtime register helper. ~2 files / ~100 LOC.
4. **Built-in contributors batch 1** — `system`, `invocation`, `dependencies`, `config`. ~2 files / ~150 LOC.
5. **Built-in contributors batch 2** — `radio`, `audio`. ~2 files / ~120 LOC.
6. **Built-in contributors batch 3** — `logs`, `state`, `errors`. ~2 files / ~150 LOC.
7. **Bundle assembler + manifest** — `bundle.py`, `_manifest.py`, ZIP layout. ~2 files / ~150 LOC.
8. **Upload client + typed errors** — `upload.py`, `_errors.py`, `request_signer` hook. ~2 files / ~150 LOC.
9. **CLI subcommand: interactive default + flags** — `cli/_diagnose.py`, registration in `cli/__init__.py`. ~2 files / ~180 LOC.
10. **Web UI backend handler** — `web/handlers/diagnostics.py`, route registration, preview lifecycle. ~2 files / ~150 LOC.
11. **Web UI frontend dialog** — `SendReportDialog.svelte` + Settings entry point. ~3 files / ~250 LOC.
12. **Public contract doc** — `docs/contracts/diagnostic-bundle-v1.md`. ~1 file / ~150 LOC docs.
13. **Privacy invariant test suite** — parametrised redaction tests + manifest assertions. ~2 files / ~200 LOC tests.
14. **Integration / e2e tests** — synthetic mock receiver, full pipeline. ~2 files / ~200 LOC tests.
15. **User-facing documentation** — `docs/guide/diagnostic-reports.md` covering: when to use the feature, what gets included by default, privacy expectations and the explicit-consent model, how to opt in to upload (`--upload` / Web UI consent / Pro Tauri button), how `support_url` works, what `--endpoint` env override is for, troubleshooting (cache dir, rate limits, large bundles). Cross-link from the main user guide and from the README. ~1 file / ~250 LOC docs.

Some of these may need a documented file-count breach (per pattern #788/#1363) — to be decided per task during PLAN phase. Total surface ≈ 13-15 atomic PRs.

## 13. Out of scope (future)

- **Error overlay** (D in §4.9). Worth its own sub-issue when triggered by a real exception flow; deferred to v2.
- **Crash hook** — capturing a bundle from a previous crashed session via `atexit` / signal-handler dump. The current logging architecture already spills to disk continuously, so a fresh `diagnose` invocation after a crash gets the prior session's logs anyway. A formal crash hook (auto-prompt "we noticed icom-lan crashed, send report?") is a future polish.
- **Auto-bundling on PTT errors** etc. — same opt-in opt-in opt-in principle: never automatic.
- **Multi-language UI** — English first, l10n later via the existing frontend i18n machinery.
- **Pro Tauri UI** (covered by morozsm/icom-lan-pro #590) — a separate frontend; this spec only defines the open-core REST endpoint contract Pro will call.
- **AI triage agent** (covered by morozsm/icom-lan-pro #591) — server-side concern.

## 14. References

- Open-core boundary policy: `docs/architecture/open-core-policy.md`
- Pattern #691 — defer codec/rate-dependent objects until rate is known. Applied to `BundleContext`-driven contributor execution: `radio.audio_codec` is read at `build_bundle` time, not at module import.
- Pattern #788 — open-core boundary review for cross-repo features.
- Pattern #1381 — verify subagent diagnoses against consumer contract (dual-RX downmix). Applied here: each design decision was checked against open-core principles before adoption.
- License authority contract: morozsm/icom-lan-pro `docs/contracts/license-authority-v0.md` (post PR #584 merge).
- `platformdirs` library: standard cross-platform user-cache resolution.
