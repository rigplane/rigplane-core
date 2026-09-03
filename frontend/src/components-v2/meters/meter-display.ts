import type { MeterDisplay } from '../../presentation/languages/contract';
import { DEFAULT_ZONES } from './bar-gauge-utils';

/**
 * LinearSMeter's default segment geometry — 20 segments, 1px gap — matching
 * the literal constants it drew from before this prop existed. `toneBelowS9`/
 * `toneAboveS9` are empty strings, not real colors: MOR-2250's hard
 * constraint is that no `display` prop (or one falling back to this default)
 * must render byte-identical to the pre-MOR-2250 gradient/hex fill, so
 * `LinearSMeter` treats an empty tone string the same as "no language
 * supplied one" and never paints it.
 *
 * MOR-2255 made `zones` a required `MeterDisplay` field. `LinearSMeter` — the
 * only consumer of this constant — never reads it; it is `DEFAULT_ZONES`
 * itself, the same fallback `BarGauge`'s own `zones` prop defaults to, so this
 * default cannot disagree with that one.
 */
export const DEFAULT_METER_DISPLAY: MeterDisplay = {
  segmentCount: 20, segmentGapPx: 1, toneBelowS9: '', toneAboveS9: '',
  zones: DEFAULT_ZONES,
};
