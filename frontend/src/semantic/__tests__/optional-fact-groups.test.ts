import { describe, expect, it } from 'vitest';
import { optionalGroup } from '../radio-view-model';
import { record, exactKeys, str } from '../validator-primitives';

/**
 * MOR-1264 slice 0: validator support for optional fact groups. No group is
 * actually added to `RadioViewModel` in this slice (`txAux` is MOR-1244) —
 * this harness exercises the mechanism in isolation, using a synthetic,
 * test-only group built from the same primitives
 * (`record`/`exactKeys`/`str` from `validator-primitives.ts`, `optionalGroup`
 * from `radio-view-model.ts`) a real group validator will use. See
 * `radio-view-model.ts`'s `optionalGroup` doc comment for the exact one-liner
 * a future slice writes.
 */
interface SyntheticGroupViewModel {
  widget: string;
}

function validateSyntheticGroup(value: unknown, path: string): SyntheticGroupViewModel {
  const v = record(value, path);
  exactKeys(v, ['widget'], path);
  return { widget: str(v.widget, `${path}.widget`) };
}

interface SyntheticModel {
  required: string;
  group?: SyntheticGroupViewModel;
}

function validateSyntheticModel(value: unknown): SyntheticModel {
  const v = record(value, '$');
  exactKeys(v, ['required', 'group'], '$');
  return {
    required: str(v.required, '$.required'),
    group: optionalGroup(v.group, '$.group', validateSyntheticGroup),
  };
}

describe('optional fact groups (MOR-1264 mechanism)', () => {
  it('still throws on an unknown extra top-level key — the exactKeys discipline is not weakened', () => {
    expect(() => validateSyntheticModel({ required: 'x', bogus: 1 })).toThrow(TypeError);
  });

  it('passes validation when the optional group is entirely absent — the family is structurally unavailable', () => {
    const model = validateSyntheticModel({ required: 'x' });
    expect(model).toEqual({ required: 'x', group: undefined });
    expect(() => validateSyntheticModel({ required: 'x' })).not.toThrow();
  });

  it('throws with a precise error path when a present optional group is malformed', () => {
    expect(() => validateSyntheticModel({ required: 'x', group: { widget: 42 } }))
      .toThrow(/\$\.group\.widget/);
  });

  // ── N1 (adversarial verification, mor-1264-verify.md §7): pin the
  // absent-vs-malformed boundary. `optionalGroup` treats ONLY `undefined` as
  // absent — JSON has no `undefined`, so a backend emitting `"txAux": null`
  // must be rejected as malformed, not silently degraded to "structurally
  // unavailable". These two cases already throw in shipped code; without a
  // pin, a future `=== undefined` → `== null` slip (or an "empty object
  // means absent" slip) would go undetected because every existing test
  // uses either a fully-omitted key or a non-empty malformed value.
  it('rejects an explicit null optional group — null is not absent (JSON has no undefined)', () => {
    expect(() => validateSyntheticModel({ required: 'x', group: null })).toThrow(TypeError);
  });

  it('rejects an explicit empty-object optional group — {} is present, not absent, and must satisfy the group\'s own shape checks', () => {
    expect(() => validateSyntheticModel({ required: 'x', group: {} })).toThrow(TypeError);
  });
});
