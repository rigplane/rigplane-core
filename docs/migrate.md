---
description: "Migrate from icom-lan to RigPlane: import paths, async API differences, configuration changes, and a checklist for existing scripts and Web UI deployments."
---

# Migrating from `icom-lan` to `rigplane`

`rigplane` is the new name of the project formerly known as `icom-lan` (v1.x).
The rename shipped in v2.0.0 (May 2026). The old name was misleading — the
project has supported Yaesu, Discovery, and Xiegu radios alongside Icom since
v1.0 — and the rename also cleared a trademark risk around carrying a vendor
name into the paid Pro tier.

If you have v1.x code in production: **your existing scripts keep working**.
A deprecation shim re-exports the old import paths so v1 code runs against v2
without modification. You'll see a `DeprecationWarning` on first import. This
page is the short guide for moving fully to the new names.

## TL;DR for users

```bash
pip install --upgrade rigplane     # replaces `pip install icom-lan`
rigplane <args>                    # replaces `icom-lan <args>`
```

```python
# Old (still works, emits DeprecationWarning):
from icom_lan import IcomRadio, LanBackendConfig, create_radio

# New canonical form:
from rigplane import IcomRadio, LanBackendConfig, create_radio
```

User data — Web UI panel layouts, theme, auth tokens, memory channels, log
directories — is migrated **automatically** on first launch. No re-login,
no reconfigure, nothing to back up.

## Breaking changes

| Surface | v1 (`icom-lan`) | v2 (`rigplane`) | Compatibility shim |
|---|---|---|---|
| PyPI package | `icom-lan` | `rigplane` | `icom-lan` frozen at v1.1.0; no future releases under the old name |
| Python import path | `icom_lan.*` | `rigplane.*` | `icom_lan.*` still importable, emits `DeprecationWarning` |
| CLI binary | `icom-lan` | `rigplane` | `icom-lan` retained as deprecated alias of `rigplane` |
| Exception class | `IcomLanError` | `RigplaneError` | Re-exported from `icom_lan` under both names |
| Env vars | `ICOM_LAN_REPORT_ENDPOINT`, `ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING`, `ICOM_LAN_LOG_DIR` | `RIGPLANE_REPORT_ENDPOINT`, `RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING`, `RIGPLANE_LOG_DIR` | Old names still honoured for one major release |
| LAN discovery wire | `b"ICOM_LAN_DISCOVER\n"` | `b"RIGPLANE_DISCOVER\n"` | Server accepts both request tokens |
| Diagnostic bundle | `icom-lan-bundle-v1` | `rigplane-bundle-v2` (default) | Triage service accepts both for at least 12 months |
| Docs site | `morozsm.github.io/icom-lan/` | `rigplane.dev` | Old GitHub Pages URL still redirects |
| GitHub repo | `morozsm/icom-lan` | `rigplane/rigplane-core` | GitHub auto-redirect active |

The `icom_lan` shim will be removed in a future major release (no specific
date). Move to canonical names when convenient.

## Preserved (intentionally not renamed)

Vendor identifiers stay vendor identifiers — they describe hardware, not the
product brand. Nothing changes here.

- **Vendor classes**: `IcomRadio`, `IcomBackend`, `IcomCommander`,
  `Icom7610Profile`, `YaesuRadio`, `YaesuCatRadio`, etc.
- **Backend directories**: `src/rigplane/backends/icom7610/`,
  `…/yaesu_cat/`, etc.
- **Vendor-config env vars**: `ICOM_HOST`, `ICOM_USER`, `ICOM_PASS`,
  `ICOM_PORT`, `ICOM_AUDIO_*`, `ICOM_CIV_*`, etc.

If your scripts use `IcomRadio` or set `ICOM_HOST=...`, that code is
**unchanged in v2** — no migration needed.

## Pro and local-extensions

If you embed rigplane in a Tauri/Pro shell using extension hooks, the
primary global is now `window.rigplaneExtensionHost`. The legacy alias
`window.icomLanExtensionHost` is preserved for v1.x extensions.

## When to actually update your code

The deprecation shim has no scheduled removal date. You can keep running
v1 imports against v2 indefinitely in the short term. Move to canonical
names when:

- You're already touching the imports for another reason.
- Your CI starts treating `DeprecationWarning` as an error.
- You ship a new release and want to drop the warning in your own logs.

There's no urgency. The shim exists precisely so the rename is a non-event
for downstream users.

## Full release notes

For the complete v2.0.0 entry — including new features, brand assets,
the `rigplane-bundle-v2` diagnostic schema, and CI/grep gates — see the
[CHANGELOG](CHANGELOG.md).
