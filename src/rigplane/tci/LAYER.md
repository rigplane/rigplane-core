# `tci` layer

## Charter

Radio-agnostic TCI (Transceiver Control Interface) protocol package:
the command grammar (`tci/protocol.py`), binary stream frames with
sample-type conversion (`tci/frames.py`), and the listener with
per-session broadcast plumbing (`tci/server.py`, `tci/handler.py`).
The RFC 6455 framing and server-side upgrade live in the shared
stdlib-only lowest-layer module `rigplane.ws`, which this package
imports. The package carries no radio vocabulary: a command reaches
`TciHandler.on_command` as a name plus a tuple of strings, and the
adapter decides what it means.

## Allowed dependencies

Standard library, `rigplane.tci` itself, and the shared lowest layer
`rigplane.ws` only. No other `rigplane` package or module, and no
third-party package (not `numpy`, not `aiohttp`). Enforced twice: the
import-linter `tci-is-a-leaf` forbidden contract and
`tests/test_tci_leaf_boundary.py` (the AST scan is the complete rule;
`rigplane.ws` is the one allowed edge). Needs that appear to require
Core are met by injection: the Origin/Host predicate is a callable the
caller passes in (`rigplane.ws: AllowHeaders`); the session handler is
the `tci/handler.py: TciHandler` Protocol the leaf defines and the
adapter implements.

The package is lifted into its own MIT package only when a second
user outside Core appears; no separate repository exists now.

## Forbidden patterns

- Any `from rigplane.<other layer>` import (including under
  `TYPE_CHECKING`), except the single allowed edge to `rigplane.ws`.
- Radio vocabulary: model names, bands, modes, power scaling. Those
  belong to `rigplane.tci_adapter`.
- Numpy or any binary dependency: conversion uses `struct`/`array`.

## See also

- `docs/superpowers/specs/2026-10-27-tci-server-design.md` §4, §4.1.
- `src/rigplane/ws.py` (the shared framing this package builds on).
