# Diagnostic Bundle Contract — `icom-lan-bundle-v1`

**Schema name:** `icom-lan-bundle-v1`
**Status:** stable, public, open-source contract.
**Last updated:** 2026-05-03

## Purpose

This contract documents the public, anonymous-tier shape of diagnostic-bundle
uploads from open-core icom-lan to a maintainer-operated triage service. It is
the spec that `icom_lan.diagnostics.upload_bundle` builds against and that any
third-party server that wants to receive icom-lan reports must implement.

## Boundary

This document is open-source and authoritative for the **anonymous tier** of
bundle submissions — open-core users with no licence context.

The icom-lan-pro distribution adds an authenticated tier (signed uploads tied
to a customer support id, longer retention, customer-tier triage) on top of
the same endpoint. Those Pro-only extensions are governed by a private
contract and are deliberately out of scope here. Pro builds may set additional
headers; their behaviour is documented in a private contract.

## Endpoint

```
POST https://reports.msmsoft.net/v1/diagnostics/upload
```

The endpoint is overridable via the `ICOM_LAN_REPORT_ENDPOINT` environment
variable. This is intended for two use-cases:

1. **Self-hosting** — point clients at a third-party backend that implements
   this contract (see [Self-hosting](#self-hosting) below).
2. **Testing** — integration tests and local development against a stub
   server.

When the override is unset, the default maintainer-operated endpoint is used.

## Request

`Content-Type: multipart/form-data`

| Field      | Type            | Required | Notes                                          |
| ---------- | --------------- | -------- | ---------------------------------------------- |
| `bundle`   | file            | yes      | ZIP archive, maximum 25 MiB after compression. |
| `metadata` | string (JSON)   | yes      | Bundle metadata, schema below.                 |

The anonymous tier sends no authentication headers.

### Metadata schema

`metadata` is a JSON string with `schema_version: "icom-lan-bundle-v1"`:

```json
{
  "schema_version": "icom-lan-bundle-v1",
  "submission_id": "uuid-v4-generated-by-client",
  "app": {
    "name": "icom-lan",
    "version": "0.20.0",
    "build_id": "2026-05-03.1"
  },
  "platform": {
    "os": "darwin",
    "arch": "arm64",
    "python_version": "3.11.14"
  },
  "user_description": "optional free text",
  "issue_ref": "https://github.com/morozsm/icom-lan/issues/1234",
  "contact": {
    "email": "ham@example.com",
    "callsign": "DL9EAC"
  }
}
```

`app.name` is `"icom-lan"` for open-core uploads.

**Required fields** — server returns `metadata_invalid` (HTTP 400) if absent:

- `schema_version`
- `submission_id`
- `generated_at_unix` — bundle assembly time as Unix epoch seconds.
- `app.name`
- `app.version`
- `platform.os`
- `platform.arch`

**Optional fields** — omitted (not `null`) when data is unavailable. The
server must accept absence without error:

- `app.build_id` — git describe / build pipeline identifier; absent for pip
  installs without git context.
- `platform.python_version` — best-effort, derived from `sys.version_info`.
- `user_description`, `issue_ref` — user-supplied at submission time.
- `contact.email`, `contact.callsign` — opt-in user-supplied (see
  [Privacy invariants](#privacy-invariants)).
- Any field added in future schema versions.

Unknown JSON fields must be ignored. Clients should never send `null` for an
unavailable field; they must omit the key entirely.

## Request rules

- **Idempotency** — `submission_id` provides idempotency: if the same
  `submission_id` arrives within 24 hours, the server returns the existing
  `report_id` with HTTP 200.
- **Content scanning** — the server runs a forbidden-pattern regex pass on
  the uncompressed text content of the bundle. Detected patterns (see
  [Forbidden content patterns](#forbidden-content-patterns)) cause the bundle
  to be rejected with `forbidden_content` and not stored.
- **Bundle size cap** — `bundle` size > 25 MiB → `bundle_too_large`.
- **Anonymous rate limit** — 5 reports per source IP per hour, 10 per IP per
  day. Exceeding either limit → `rate_limited` with `retry_after_seconds`.
- **Retention** — anonymous bundles are retained for 90 days, then storage
  objects are purged.

## Success response

```json
{
  "report_id": "rpt_01JZ7F8A0M5W6X3DKQNHFVHRE0",
  "received_at_unix": 1777670400,
  "support_url": "https://reports.msmsoft.net/r/rpt_01JZ7F8A0M5W6X3DKQNHFVHRE0",
  "auth_class": "anonymous"
}
```

| Field              | Type     | Notes                                                       |
| ------------------ | -------- | ----------------------------------------------------------- |
| `report_id`        | string   | ULID. Customer-safe identifier returned to the client.      |
| `received_at_unix` | integer  | Server-side receive timestamp (UTC, Unix seconds).          |
| `support_url`      | string   | Customer-safe link the user can paste into a GitHub issue.  |
| `auth_class`       | string   | `"anonymous"` for open-core uploads.                        |

`auth_class` is `"anonymous"` for every bundle submitted by open-core
icom-lan. The `support_url` is a customer-safe link and never exposes
internal identifiers, source IP, or contact fields.

## Stable error responses

All non-2xx responses follow a single envelope:

```json
{
  "error": {
    "code": "metadata_invalid",
    "message": "field 'app.version' is required",
    "field": "app.version",
    "retry_after_seconds": null
  }
}
```

The relevant `error.code` values for the diagnostic-bundle endpoint:

| HTTP | Code                  | Extra fields            | Client action                                              |
| ---- | --------------------- | ----------------------- | ---------------------------------------------------------- |
| 400  | `metadata_invalid`    | `field`                 | Reject; client should regenerate the bundle.               |
| 413  | `bundle_too_large`    | —                       | Reject; client must trim or split the bundle.              |
| 422  | `forbidden_content`   | `pattern`               | Reject; bundle contained patterns barred by privacy rules. |
| 429  | `rate_limited`        | `retry_after_seconds`   | Show retry later; honour `retry_after_seconds`.            |
| 5xx  | `service_unavailable` | —                       | Surface a retry path; do not retry aggressively.           |

`error.message` is safe to display, but clients may choose local copy.
`retry_after_seconds` is `null` when not applicable.

## Privacy invariants

These invariants are binding for the anonymous tier and form the basis of the
maintainer-operated server's privacy posture.

- `contact.email` and `contact.callsign` are **opt-in user-provided** fields.
  They are stored only when supplied by the user via an explicit "Send report"
  consent form on the client. They must **never** be auto-populated from
  system state, environment variables, or local config.
- Source IP is collected only for rate-limit enforcement. It is hashed and
  dropped (set to `NULL` in storage) after 24 hours.
- Anonymous bundles must not be cross-correlated with license records by IP,
  machine fingerprint, or any other field.
- Public GitHub issues opened by the AI triage agent must reference reports
  by `report_id` / `support_url` only — never by source IP, contact fields,
  or any other identifying material.

## Forbidden content patterns

The server rejects bundles whose uncompressed text content contains any of:

- `AWS_SECRET_ACCESS_KEY`, `AWS_ACCESS_KEY_ID` and similar cloud-credential
  variable names.
- `Authorization: Bearer` headers in captured logs or HTTP transcripts.
- Raw activation codes or licence-code-shaped strings.
- PEM-encoded private keys (`-----BEGIN ... PRIVATE KEY-----`).
- `password=`, `passwd=`, and similar credential assignments in plain text.

Clients should perform the same redaction pass locally before submission so
that legitimate bundles are not rejected; the server check is a defence in
depth, not a primary filter.

## Versioning

- `schema_version` is `"icom-lan-bundle-v1"` for this contract revision.
- Unknown JSON fields must be ignored by the server. Clients may begin
  emitting new optional fields without coordination.
- **Non-breaking changes** — adding a new optional metadata field, adding a
  new optional response field. These do not bump the schema version.
- **Breaking changes** — removing or renaming a required field, changing a
  field's type or semantics, removing a stable error code. These mint a new
  `schema_version` (e.g. `icom-lan-bundle-v2`) and the server must continue
  to accept `icom-lan-bundle-v1` for a documented deprecation window.

Clients must include `schema_version` in every submission so the server can
route to the correct validator.

## Self-hosting

The default endpoint is operated by the icom-lan maintainer. To redirect
uploads to a self-hosted backend that implements this contract, set:

```sh
export ICOM_LAN_REPORT_ENDPOINT=https://reports.example.org/v1/diagnostics/upload
```

A conforming self-hosted backend must:

1. Accept `multipart/form-data` with `bundle` and `metadata` fields.
2. Validate `metadata` against the schema in this document and return
   `metadata_invalid` (HTTP 400) on missing required fields.
3. Enforce the 25 MiB bundle cap and return `bundle_too_large` (HTTP 413).
4. Honour `submission_id` idempotency for at least 24 hours.
5. Return the success-response envelope documented above with
   `auth_class: "anonymous"`.
6. Use the stable error envelope and codes in
   [Stable error responses](#stable-error-responses).

A self-hosted backend may apply its own rate-limit and retention policy, but
SHOULD honour the anonymous-tier defaults to avoid surprising users.

## See also

- Open-core spec: `docs/plans/2026-05-03-diagnostic-data-collection-design.md` §5.3
- icom-lan upload client: `src/icom_lan/diagnostics/upload.py`
- Open-core policy: `docs/architecture/open-core-policy.md` §2 carve-out
