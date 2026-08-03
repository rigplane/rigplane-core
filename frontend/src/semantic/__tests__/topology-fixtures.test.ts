import { describe, expect, it } from 'vitest';
import { validateRadioViewModel } from '../radio-view-model';
import { topologyFixtures, withAudioOnlyScope, type TopologyFixtureId } from '../fixtures/topologies';

const ids: readonly TopologyFixtureId[] = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'];

/**
 * Structural signature "modulo pure identifiers" (review cycle 1, V2; review
 * cycle 2, V2 de-vacuization). A naive full JSON diff is vacuous two ways:
 * fixtures that were structurally identical copies differing only in
 * cosmetic `label` text, OR only in `topologyId` (a free-form name that
 * duplicates `vfoScheme`/receiver-count information already present
 * elsewhere in the object), would both still count as "distinct". Strip
 * both identifier fields before comparing so the test can only pass because
 * the topology/state axes themselves differ, never because of naming.
 */
const IDENTIFIER_KEYS = new Set(['label', 'topologyId']);
function stripIdentifiers(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripIdentifiers);
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (IDENTIFIER_KEYS.has(k)) continue;
      out[k] = stripIdentifiers(v);
    }
    return out;
  }
  return value;
}
const signature = (id: TopologyFixtureId) => JSON.stringify(stripIdentifiers(topologyFixtures[id]));

describe('topology fixtures (MOR-1062)', () => {
  it.each(ids)('%s is a valid RadioViewModel', (id) => {
    expect(() => validateRadioViewModel(topologyFixtures[id])).not.toThrow();
  });

  it('carries the topologyId that matches its own key', () => {
    for (const id of ids) expect(topologyFixtures[id].topologyId).toBe(id);
  });

  it('covers all four backend VFO schemes exactly once', () => {
    const schemes = ids.map((id) => topologyFixtures[id].vfoScheme).sort();
    expect(schemes).toEqual(['ab', 'ab_shared', 'main_sub', 'single']);
  });

  it('assigns single-receiver topologies exactly one receiver and dual-receiver topologies exactly two', () => {
    const receiverSetSize = (id: TopologyFixtureId) =>
      new Set(topologyFixtures[id].vfos.map((v) => v.receiver)).size;
    expect(receiverSetSize('1/single')).toBe(1);
    expect(receiverSetSize('1/ab')).toBe(1);
    expect(receiverSetSize('2/ab_shared')).toBe(2);
    expect(receiverSetSize('2/main_sub')).toBe(2);
  });

  it('exercises all three tri-state txPermit branches across the set (never just two)', () => {
    const statuses = new Set(ids.map((id) => topologyFixtures[id].txPermit.status));
    expect(statuses).toEqual(new Set(['allowed', 'unknown', 'denied']));
  });

  // ── V2 (review cycle 1 + 2): non-vacuous distinctness ─────────────────────
  it('fails if two fixtures collapse to the same structural signature modulo pure identifiers', () => {
    const signatures = ids.map(signature);
    expect(new Set(signatures).size).toBe(ids.length);
  });

  it('proves the signature is label-blind: two label-only-differing structures DO collapse to one signature', () => {
    // Meta-test on the guard above: if this failed, "modulo labels" would be a
    // no-op and the previous test would be exactly as vacuous as a raw JSON diff.
    const base = topologyFixtures['1/single'];
    const a = { ...base, vfos: base.vfos.map((v) => ({ ...v, label: 'X' })) };
    const b = { ...base, vfos: base.vfos.map((v) => ({ ...v, label: 'Y' })) };
    expect(JSON.stringify(stripIdentifiers(a))).toBe(JSON.stringify(stripIdentifiers(b)));
  });

  it('proves the signature is also topologyId-blind (review cycle 2, V2 de-vacuization): ' +
    'two topologyId-only-differing structures DO collapse to one signature', () => {
    // Before this fix, `topologyId` survived the strip, so two fixtures with
    // identical structure but different topologyId would still (correctly)
    // report as "distinct" for the WRONG reason — the id, not the structure.
    // That made the outer test pass even if a real structural duplication
    // ever crept in, as long as the fixture's key/id stayed unique.
    const base = topologyFixtures['1/single'];
    const a = { ...base, topologyId: 'x/one' };
    const b = { ...base, topologyId: 'x/two' };
    expect(JSON.stringify(stripIdentifiers(a))).toBe(JSON.stringify(stripIdentifiers(b)));
  });

  // ── S1 (review cycle 1): audio-only scope is a composable fifth condition ─
  it.each(ids)('withAudioOnlyScope composes onto %s: scope flips, every other fact is untouched', (id) => {
    const base = topologyFixtures[id];
    const variant = withAudioOnlyScope(base);
    expect(() => validateRadioViewModel(variant)).not.toThrow();
    expect(variant.scope).toEqual({
      hardwareScope: { structural: false, operational: false },
      audioFftScope: { structural: true, operational: true },
    });
    expect({ ...variant, scope: base.scope }).toEqual(base);
  });

  // ── Null/unknown preservation (ticket acceptance evidence) ───────────────
  it('preserves an unknown txTarget with its reason, distinct from a known one', () => {
    expect(topologyFixtures['1/ab'].txTarget).toEqual({ status: 'unknown', reason: 'not-observed' });
    expect(topologyFixtures['2/ab_shared'].txTarget).toEqual({
      status: 'known', receiver: 'SUB', slot: { kind: 'unslotted' }, frequencyHz: 3573000,
    });
  });

  it('keeps "no A/B slot" (unslotted) distinct from "slot not observed" (unknown) — B3', () => {
    // ab_shared has no A/B concept at all: every VFO is structurally unslotted,
    // never a placeholder for an unobserved slot.
    for (const vfo of topologyFixtures['2/ab_shared'].vfos) expect(vfo.slot).toEqual({ kind: 'unslotted' });
    // ab/main_sub are slotted schemes: every VFO carries a real observed id.
    for (const vfo of topologyFixtures['1/ab'].vfos) expect(vfo.slot.kind).toBe('slotted');
    for (const vfo of topologyFixtures['2/main_sub'].vfos) expect(vfo.slot.kind).toBe('slotted');
  });

  it('preserves activeReceiver as an explicit known fact, never a bare string (B1)', () => {
    for (const id of ids) expect(topologyFixtures[id].activeReceiver.status).toBe('known');
  });

  // ── B2: split and dualWatch are independent — the set proves all three
  // representable relationships (both false, one unknown, both true) ───────
  it('represents split and dualWatch orthogonally across the set, including both true at once', () => {
    expect(topologyFixtures['1/single'].split).toEqual({ status: 'known', value: false });
    expect(topologyFixtures['1/single'].dualWatch).toEqual({ status: 'known', value: false });
    expect(topologyFixtures['1/ab'].split).toEqual({ status: 'known', value: true });
    expect(topologyFixtures['1/ab'].dualWatch).toEqual({ status: 'unknown' });
    expect(topologyFixtures['2/main_sub'].split).toEqual({ status: 'known', value: true });
    expect(topologyFixtures['2/main_sub'].dualWatch).toEqual({ status: 'known', value: true });
  });

  // ── TX truthfulness: confirmed/uncertain must never collapse to a boolean ─
  it('kills a mutation collapsing tx permit "unknown" into "denied"', () => {
    const permit = topologyFixtures['1/ab'].txPermit;
    expect(permit).toEqual({ status: 'unknown', reason: 'tx-target-unknown' });
    expect(permit.status).not.toBe('denied');
    expect(permit.status).not.toBe('allowed');
  });

  it('kills a mutation collapsing tx permit "unknown" into "allowed" (the fail-open direction)', () => {
    expect(topologyFixtures['1/ab'].txPermit.status).not.toBe('allowed');
  });

  it('kills a mutation dropping the denied reason on 2/ab_shared', () => {
    expect(topologyFixtures['2/ab_shared'].txPermit).toEqual({
      status: 'denied', reason: 'outside-configured-ranges',
    });
  });

  // ── Capability-gated controls never smuggle raw capability data ─────────
  it('never smuggles a raw capabilities array or module path into a fixture', () => {
    // The contract's own `disabledReasons[].code` legitimately contains the word
    // "capability" (e.g. 'capability-unavailable'); this asserts against the raw
    // wire field `"capabilities":[...]` and module-path-shaped strings, not the word.
    for (const id of ids) {
      const json = JSON.stringify(topologyFixtures[id]);
      expect(json).not.toMatch(/"capabilities"\s*:/);
      expect(json).not.toMatch(/\$lib\//);
      expect(json).not.toMatch(/\.svelte/);
      expect(json).not.toMatch(/skins\//);
    }
  });

  it('gates hardware scope structurally and operationally, independently of audio FFT scope', () => {
    // 2/main_sub: hardware scope fully available; audio FFT structurally present
    // but not currently operational — the two-level gate must stay independent.
    const scope = topologyFixtures['2/main_sub'].scope;
    expect(scope.hardwareScope).toEqual({ structural: true, operational: true });
    expect(scope.audioFftScope).toEqual({ structural: true, operational: false });
  });
});
