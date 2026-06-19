# Consumer-driven contract (CDC) expectations

This directory holds **consumer-authored** expectation files that core's own CI
runs against `HEAD`. Each file declares one concrete dependency a downstream
consumer (`rigplane-pro`, `rigplane-station`) has on a public core producer
surface. If a core change would break a fielded consumer, the
`consumer-contracts-gate` workflow (and the in-suite
`tests/contracts/test_consumer_contracts.py`) turns core's PR **red**.

This is the public-core half of epic MOR-707 / MOR-883. It deliberately mirrors
the established team pattern (vendored static JSON + a `--check`-style drift
gate), not a CDC framework, submodule, or published package — no cross-repo
version pin, no network, no extra runtime dependency.

## How a file is structured

```jsonc
{
  "contract": "pro/info_proto",            // <consumer>/<contract>, matches the path
  "source": "rigplane-pro",                // which consumer authored this
  "source_file": "...probe.py",            // where the dependency lives in the consumer
  "description": "...",                     // human prose: what the consumer relies on
  "producer_artifact": "info",             // which core artifact the runner builds
  "speaks_version": { "field": "proto", "value": 1 },  // optional version assertion
  "schema": { /* JSON Schema the producer artifact must satisfy */ }
}
```

`producer_artifact` selects an in-process builder in
`scripts/run_consumer_contracts.py` (`info` → `GET /api/v1/info` body via the
public `WebServer`; `discovery` → the `DiscoveryResponder` datagram). The runner
builds the **real** artifact from public `rigplane.*` symbols, validates the
`schema`, and (if present) asserts the `speaks_version` field equals what the
consumer declares it speaks.

## Authoring workflow

1. **Author in the consumer repo.** The consumer (pro/station) is the source of
   truth: it writes the expectation alongside the code that depends on the
   surface, scoped to the exact fields/types/enums/version it relies on.
2. **Vendor a byte-identical copy here** under
   `contracts/consumer-expectations/<consumer>/<contract>.json` via a small PR to
   core. The PR commit message references the consumer source file.
3. **Keep it minimal and public.** Only encode what the consumer truly requires
   (a missing field the consumer reads defensively with a default is *not* a hard
   dependency and must not be required). **Never** encode private/licensing
   (tower) surfaces here — those belong in the private tower CDC job (MOR-884).
4. A future **freshness gate** will diff the vendored copy against the consumer's
   authoring source to guard drift. For this seed the file lives here marked
   `source: <consumer>`; freshness is by review convention until that gate lands.

## Running locally

```bash
uv sync --extra codegen           # installs jsonschema (dev/CI-only)
uv run python scripts/run_consumer_contracts.py
```

Exit code is non-zero if any expectation is violated.
