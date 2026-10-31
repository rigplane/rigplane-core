# `tci_adapter` layer

## Charter

Thin Core-facing side of the TCI server, sibling of `rigctld` and `web`.
It translates TCI text commands to the same `CommandIntent` pipeline the
other seats use, reads from the shared `StateStore`, broadcasts changes
to every session, and keys the transmitter only through
`ManagedTxAuthority` (never `Radio.set_ptt`, never raw frames).

## Allowed dependencies

`core`, `commands`, `profiles`, `audio`, `runtime`, the leaf
`rigplane.tci` and the shared `rigplane.ws` — what `web` may depend on,
because the adapter needs audio-route facts the way `web` does. No
`backends`, no `web`, no `rigctld` (the `independence-top` contract), no
third-party packages beyond what those layers already use.

## Forbidden patterns

- `from rigplane.web` / `from rigplane.rigctld` — independence contract.
- `from rigplane.backends` — radio facts come from profiles and
  capability protocols, never from a backend module.
- Backend-id or model branches (`model ==`, backend ids). Radio facts
  come from profiles and capability protocols; tests cover more than one
  radio where radio behaviour is touched.
- A second TX path: no `set_ptt`, no raw-frame writes. Grep for both in
  this package must stay empty.
- Telemetry or analytics. Open-core hard constraint.

## See also

- `docs/superpowers/specs/2026-10-27-tci-server-design.md` §4, §5, §7.
- `src/rigplane/rigctld/LAYER.md` — the sibling seat's charter.
