/**
 * MOR-2688 (slice S4c) — the tie test between `ValueObservation<T>` (
 * `primitives/reading-text.ts`) and the two REAL types it stands for,
 * `DisplayObservation<T>` and `LevelMeterEvidence`. `primitives/` may not
 * import `semantic/`, so the union is restated over there and tied back
 * here, where both imports are allowed. The WHOLE union of each real type
 * is assigned, so a renamed state on EITHER side — or segmentline's
 * `DisplayValue` (`state: 'known'`) — fails to compile: `npm run check`
 * covers this file.
 */
import { describe, expect, it } from 'vitest';
import type { ValueObservation } from '../../primitives/reading-text';
import type { DisplayObservation } from '../radio-view-model';
import type { LevelMeterEvidence } from '../bar-meter-projector';
import type { DisplayValue } from '../radio-display-model';

describe('ValueObservation ties to the real types (MOR-2688 S4c)', () => {
  it('the whole DisplayObservation<T> union is assignable to ValueObservation<T>', () => {
    const tie: (value: DisplayObservation<number>) => ValueObservation<number>
      = (value) => value;
    expect(tie).toBeTypeOf('function');
  });

  it('the whole LevelMeterEvidence union is assignable to ValueObservation<number>', () => {
    const tie: (value: LevelMeterEvidence) => ValueObservation<number>
      = (value) => value;
    expect(tie).toBeTypeOf('function');
  });

  it("the segmentline vocabulary DisplayValue (state: 'known') is NOT a ValueObservation", () => {
    const displayValue: DisplayValue<number> = { state: 'known', value: 3.5 };
    // @ts-expect-error — 'known' resolves to nothing in the observation
    // rule; a silent pass would go dark (audit F2). Compile-time pin only.
    const wrong: ValueObservation<number> = displayValue;
    void wrong;
    expect(displayValue.state).toBe('known');
  });
});
