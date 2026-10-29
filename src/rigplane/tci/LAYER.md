# `tci` layer

## Charter

Radio-agnostic TCI (Transceiver Control Interface) protocol package.
First module: RFC 6455 framing and server-side upgrade (`tci/ws.py`).
The package grows, per the design, a command grammar, binary stream
frames with sample-type conversion, and a listener with per-session
broadcast plumbing. It carries no radio vocabulary: a command reaches
the handler as a name plus a tuple of strings, and the adapter decides
what it means.

## Allowed dependencies

Standard library and `rigplane.tci` itself only. No other `rigplane`
package or module, and no third-party package (not `numpy`, not
`aiohttp`). Enforced twice: the import-linter `tci-is-a-leaf`
forbidden contract and `tests/test_tci_leaf_boundary.py` (the AST
scan is the complete rule). Needs that appear to require Core are met
by injection: the Origin/Host predicate is a callable the caller
passes in (`tci/ws.py: AllowHeaders`); the session handler is a
Protocol the leaf defines and the adapter implements.

The package is lifted into its own MIT package only when a second
user outside Core appears; no separate repository exists now.

## Forbidden patterns

- Any `from rigplane.<other layer>` import (including under
  `TYPE_CHECKING`).
- Radio vocabulary: model names, bands, modes, power scaling. Those
  belong to `rigplane.tci_adapter`.
- Numpy or any binary dependency: conversion uses `struct`/`array`.

## See also

- `docs/superpowers/specs/2026-10-27-tci-server-design.md` §4, §4.1.
