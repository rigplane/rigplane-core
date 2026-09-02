import type { MeterDisplay } from '../../presentation/languages/contract';

/** LinearSMeter's default segment geometry — 20 segments, 1px gap — matching
 *  the literal constants it drew from before this prop existed. */
export const DEFAULT_METER_DISPLAY: MeterDisplay = { segmentCount: 20, segmentGapPx: 1 };
