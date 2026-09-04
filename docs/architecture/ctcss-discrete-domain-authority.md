# Discrete semantic-domain authority for CTCSS

**Status:** Accepted. Records the MOR-2129 canonical migration ruling. This
document is limited to CTCSS and the shape of future shared discrete catalogs;
it does not introduce a general catalog implementation.

## Decision

For a radio feature whose legal values have a backend-neutral semantic unit,
the profile owns the legal ordered domain as a typed `RadioProfile` field in
that unit. For CTCSS, that field is `ctcss_tones_centihz`: `8850` means 88.5
Hz. The tuple order is the provider-table index mapping, but the public value
is the centiHz value, never that index.

A versioned named catalog may hold shared data and resolve a selected table
into the typed profile field. It is data authority only: it neither grants a
capability nor a command, and it is not confirmed radio state.

`ControlDomainSpec` has a different, non-exclusive responsibility. It is the
contract for a control's raw/display mapping, quantization, and restoration,
including encoded control choices. It is not the universal registry for every
selectable semantic domain. The existing `scope_span_presets_hz` profile tuple
is the direct precedent for a legal ordered value/index mapping outside
`[controls.*]`.

Provider codecs remain local. Icom BCD and Yaesu CAT/CN encoding and decoding
translate only at their respective provider boundaries; they do not become
alternate authorities for the legal CTCSS set. StateStore observations own the
confirmed selected state. Web projection and validation consume the resolved
profile tuple rather than copying a tone list or range check.

## Why this split exists

The profiles layer is the data-driven contract consulted by runtime, backend,
validation, and UI consumers (`src/rigplane/profiles/LAYER.md`). It already
uses typed profile domains for attenuator, preamp, AGC, scan, and scope values.
`RadioProfile.scope_span_presets_hz` is explicitly the sole Hz-to-index source
for its CI-V scope protocol mapping (`src/rigplane/profiles/__init__.py: RadioProfile`).

Conversely, the exact controls work (MOR-1707, MOR-1708, MOR-1709, MOR-1717,
MOR-1718, MOR-1722, MOR-1727) established validated public raw/display control
domains. Its purpose does not make it the authority for CTCSS provider table
membership. This preserves one semantic domain while allowing provider-specific
wire formats and UI-specific conversion behavior where each is appropriate.

The field-path promotion criterion separately confirms that CTCSS is a
backend-neutral semantic concept; capability, acquisition, and observation
remain separate contracts (`docs/architecture/field-path-promotion-criterion.md`).

## MOR-2129 migration status and invariants

PR #3147 is a **foundation, incomplete** MOR-2129 slice. Its versioned CTCSS
catalog and `ctcss_tones_centihz` profile field establish the target data
authority, but no provider/runtime/Web/state migration is complete merely from
that foundation.

The follow-up migration must preserve all of these invariants:

- Every command admission, provider encode/decode mapping, validation check,
  and Web selectable-value projection derives legal CTCSS values from the same
  resolved `ctcss_tones_centihz` tuple.
- CentiHz is the backend-neutral value across command and state boundaries;
  any Hz conversion is confined to the provider codec boundary that requires
  it.
- A profile table declaration never substitutes for capability declaration,
  command reachability, or a confirmed StateStore observation.
- Provider index/BCD/CAT details remain provider-local translations of the
  tuple; no backend retains an independent standard-tone table.
- Tests prove the tuple is actually consumed, including an intentionally
  different valid synthetic table, not merely equal to a legacy constant.

## Catalog growth rule

Do not add a second bespoke named table file plus feature-specific loader.
Before a second domain with the same lifecycle needs a named shared catalog,
introduce one generic named discrete-domain/catalog resolver with explicit
schema, validation, profile-reference, and ownership rules. Until then, the
MOR-2129 CTCSS catalog is the single permitted exception.

## Non-goals

This ruling does not require migration of existing controls to typed semantic
fields, does not make every profile metadata list Web-visible, and does not
alter PTT/TX admission or completion authority. Those remain governed by their
own contracts.
