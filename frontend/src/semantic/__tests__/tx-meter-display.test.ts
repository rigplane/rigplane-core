import { describe, expect, it } from 'vitest';
import { projectTxMeterDisplay } from '../tx-meter-display';
import type { DisplayObservation, DisplayObservedMeterField, MeterRfState } from '../radio-view-model';

const states: MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];
const observations: DisplayObservation<number>[] = [
  { state: 'current', value: 170 }, { state: 'stale', value: 170 },
  { state: 'unknown', reason: 'not-observed' },
];
describe('TX display projection', () => {
  for (const structural of [false, true]) for (const rf of states)
    for (const relevant of [false, true]) for (const display of observations) {
      it(`${structural}/${rf}/${relevant}/${display.state}`, () => {
        const field: DisplayObservedMeterField = {
          availability: { structural, operational: false }, relevant,
          reading: { status: 'unknown' }, display,
        };
        const before = structuredClone(field);
        const result = projectTxMeterDisplay(field, rf);
        expect(result).toEqual(structural ? {
          supported: true,
          relevance: rf === 'receiving' && !relevant ? 'idle'
            : rf === 'transmitting' && relevant ? 'relevant' : 'indeterminate',
          observation: display,
        } : { supported: false });
        expect(field).toEqual(before);
      });
    }
  it('supports legacy current zero but never overrides an explicit unknown facet', () => {
    const field: DisplayObservedMeterField = { availability: { structural: true, operational: true },
      relevant: true, reading: { status: 'known', value: 0 } };
    expect(projectTxMeterDisplay(field, 'transmitting')).toMatchObject({ observation: { state: 'current', value: 0 } });
    expect(projectTxMeterDisplay({ ...field, display: observations[2] }, 'transmitting'))
      .toMatchObject({ observation: observations[2] });
    expect(projectTxMeterDisplay({ ...field, availability: { structural: true, operational: false } }, 'transmitting'))
      .toMatchObject({ observation: { state: 'unknown', reason: 'not-observed' } });
    expect(projectTxMeterDisplay(undefined, 'receiving')).toEqual({ supported: false });
  });
});
