/**
 * Characterization goldens for the `to*Props` projections in
 * `../panel-props.ts`, run against the two committed radio captures
 * (IC-7300 live `/api/v1/state` capture; FTX-1 bench capture) and compared
 * with a committed JSON golden per radio. A change to any label, default or
 * derived field of a pinned projection shows up as a golden diff.
 *
 * Serialization (`toGoldenValue`): `NaN`/`Infinity`/`-Infinity` become the
 * strings "NaN"/"Infinity"/"-Infinity"; an `undefined` member becomes the
 * string "undefined", so a member that stops existing is a visible diff
 * instead of a silent JSON drop; non-enumerable own properties are included,
 * which captures `toRitXitProps`'s `ritDomain`; functions — not expected in
 * props — are recorded as "<function>".
 *
 * Regenerate (from `frontend/`, paths resolve from the process cwd):
 *   UPDATE_GOLDENS=1 npx vitest run src/lib/runtime/props/__tests__/panel-props.goldens.test.ts
 */
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { FTX1_CAPABILITIES, FTX1_STATE } from '../../adapters/__tests__/fixtures/ftx1-profile';
import { IC7300_CAPABILITIES, IC7300_STATE } from '../../adapters/__tests__/fixtures/ic7300-profile';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { AudioUiState } from '../panel-props';
import {
  toAgcProps,
  toAmberTelemetryProps,
  toAntennaProps,
  toAudioSpectrumProps,
  toBandSelectorProps,
  toCwProps,
  toDspProps,
  toFilterProps,
  toMemoryPanelProps,
  toMeterProps,
  toModeProps,
  toRfFrontEndProps,
  toRitXitProps,
  toRxAudioProps,
  toScanProps,
  toTxProps,
  toVfoControlProps,
  toVfoOpsProps,
  toVfoProps,
} from '../panel-props';
import ic7300Golden from './fixtures/panel-props.ic7300.golden.json';
import ftx1Golden from './fixtures/panel-props.ftx1.golden.json';

function toGoldenValue(value: unknown): unknown {
  if (typeof value === 'function') return '<function>';
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return 'NaN';
    if (value === Infinity) return 'Infinity';
    if (value === -Infinity) return '-Infinity';
    return value;
  }
  if (value === undefined) return 'undefined';
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(toGoldenValue);
  const out: Record<string, unknown> = {};
  for (const key of Object.getOwnPropertyNames(value)) {
    out[key] = toGoldenValue((value as Record<string, unknown>)[key]);
  }
  return out;
}

/** Fixed audio input for `toRxAudioProps`: local listening, volume 42, link up. */
const GOLDEN_AUDIO_UI_STATE: AudioUiState = { muted: false, rxEnabled: true, volume: 42 };

interface Projection {
  name: string;
  run: (state: ServerState, caps: Capabilities) => unknown;
}

const PROJECTIONS: readonly Projection[] = [
  { name: 'toVfoProps(main)', run: (state) => toVfoProps(state, 'main') },
  { name: 'toVfoProps(sub)', run: (state) => toVfoProps(state, 'sub') },
  { name: 'toVfoOpsProps', run: (state, caps) => toVfoOpsProps(state, caps) },
  { name: 'toRfFrontEndProps', run: (state, caps) => toRfFrontEndProps(state, caps) },
  { name: 'toFilterProps', run: (state, caps) => toFilterProps(state, caps) },
  { name: 'toAgcProps', run: (state, caps) => toAgcProps(state, caps) },
  { name: 'toRitXitProps', run: (state, caps) => toRitXitProps(state, caps) },
  { name: 'toModeProps', run: (state, caps) => toModeProps(state, caps) },
  { name: 'toDspProps', run: (state, caps) => toDspProps(state, caps) },
  { name: 'toTxProps', run: (state, caps) => toTxProps(state, caps) },
  { name: 'toCwProps', run: (state, caps) => toCwProps(state, caps) },
  { name: 'toMeterProps', run: (state, caps) => toMeterProps(state, caps) },
  { name: 'toRxAudioProps', run: (state, caps) => toRxAudioProps(state, caps, GOLDEN_AUDIO_UI_STATE, true) },
  { name: 'toBandSelectorProps', run: (state) => toBandSelectorProps(state) },
  { name: 'toAntennaProps', run: (state, caps) => toAntennaProps(state, caps) },
  { name: 'toScanProps', run: (state) => toScanProps(state) },
  { name: 'toAudioSpectrumProps', run: (state, caps) => toAudioSpectrumProps(state, caps) },
  { name: 'toMemoryPanelProps', run: (state, caps) => toMemoryPanelProps(state, caps) },
  { name: 'toAmberTelemetryProps', run: (state) => toAmberTelemetryProps(state) },
  { name: 'toVfoControlProps', run: (state, caps) => toVfoControlProps(state, caps) },
];

interface RadioCase {
  id: 'ic7300' | 'ftx1';
  state: ServerState;
  caps: Capabilities;
  golden: Record<string, unknown>;
  goldenPath: string;
}

const RADIOS: readonly RadioCase[] = [
  {
    id: 'ic7300',
    state: IC7300_STATE,
    caps: IC7300_CAPABILITIES,
    golden: ic7300Golden,
    goldenPath: 'src/lib/runtime/props/__tests__/fixtures/panel-props.ic7300.golden.json',
  },
  {
    id: 'ftx1',
    state: FTX1_STATE,
    caps: FTX1_CAPABILITIES,
    golden: ftx1Golden,
    goldenPath: 'src/lib/runtime/props/__tests__/fixtures/panel-props.ftx1.golden.json',
  },
];

function buildGolden(state: ServerState, caps: Capabilities): Record<string, unknown> {
  const golden: Record<string, unknown> = {};
  for (const { name, run } of PROJECTIONS) {
    golden[name] = toGoldenValue(run(state, caps));
  }
  return golden;
}

describe('panel-props projections (golden captures)', () => {
  describe.each(RADIOS)('$id', ({ state, caps, golden, goldenPath }) => {
    if (process.env.UPDATE_GOLDENS) {
      writeFileSync(
        resolve(process.cwd(), goldenPath),
        `${JSON.stringify(buildGolden(state, caps), null, 2)}\n`,
      );
    }

    it('golden keys match the projection table in order', () => {
      expect(Object.keys(golden)).toEqual(PROJECTIONS.map(({ name }) => name));
    });

    it.each(PROJECTIONS)('$name matches the committed golden', ({ name, run }) => {
      expect(toGoldenValue(run(state, caps))).toEqual(golden[name]);
    });
  });
});
