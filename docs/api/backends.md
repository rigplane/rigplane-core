---
robots: noindex, follow
---

# Backends

Radio backend implementations (multi-radio architecture).

## Overview

The `backends` package contains radio-specific implementations of the abstract `Radio` protocol. Each backend handles connection, CI-V commands, and audio for a specific radio family.

## Subpackages

- `icom7610` — Icom IC-7610 backend (LAN + serial)

## See Also

- [Radio Protocol](../radio-protocol.md)
- [Public API Surface](public-api-surface.md)
