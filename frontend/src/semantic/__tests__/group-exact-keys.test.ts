/**
 * MOR-1303 — widening a group's own exactKeys list is invisible to the
 * top-level allow-list pin. This file freezes each optional group's own
 * list, derived from the shipped validator source, against a literal.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync('src/semantic/radio-view-model.ts', 'utf8');

function groupKeys(fn: string): string[] {
  const start = source.indexOf(`function ${fn}(`);
  if (start < 0) throw new Error(`group-exact-keys: ${fn} not found`);
  const next = source.indexOf('\nfunction ', start + 1);
  const body = source.slice(start, next < 0 ? source.length : next);
  const match = body.match(/exactKeys\(\s*\w+\s*,\s*(?:\(\s*)?\[([\s\S]*?)\]/);
  if (!match) throw new Error(`group-exact-keys: ${fn} has no exactKeys list`);
  return match[1].split(',').map((entry) => entry.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
}

const FROZEN: Record<string, readonly string[]> = {
  validateTxAux: [
    'atu', 'vox', 'voxGain', 'antiVoxGain', 'voxDelay', 'compressor', 'compressorLevel',
    'monitor', 'monitorLevel', 'rfPower', 'micGain', 'driveGain',
  ],
  validateMeters: [
    'rfState', 'signal', 'power', 'swr', 'alc', 'compression', 'drainVoltage', 'drainCurrent',
  ],
  validateRxAudio: [
    'monitorMode', 'liveAudio', 'afLevel', 'receiverAfLevels', 'routingFocus', 'routingSplit',
    'modInputSource', 'modInputChoices', 'modInputReadiness',
  ],
  validateModeFilter: [
    'currentMode', 'modeChoices', 'currentFilter', 'filterChoices', 'filterWidth',
    'filterWidthMin', 'filterWidthMax', 'activeFilterConfiguration',
  ],
  validateFilterPassband: [
    'filterShape', 'filterShapeControlStructural', 'ifShift', 'ifShiftControlStructural',
    'ifShiftDomain', 'pbtDomain', 'pbtInner', 'pbtOuter', 'narrow', 'dataMode', 'dataModeChoices',
    'modInputSource', 'modInputChoices',
  ],
  validateDsp: [
    'nrActive', 'nrLevel', 'nrLevelProjection', 'nbActive', 'nbLevel', 'nbDepth', 'nbWidth',
    'notchMode', 'notchFreq', 'notchFreqDomain', 'manualNotchWidth', 'agcMode', 'agcModes',
    'agcTimeConstant',
  ],
  validateRfFrontEnd: [
    'preamp', 'preValues', 'attenuator', 'attValues', 'rfGain', 'squelch', 'digiSel', 'ipPlus',
  ],
  validateBand: [
    'currentBand', 'receiverBands', 'bandChoices', 'currentBandTx', 'tuneMinHz', 'tuneMaxHz',
  ],
  validateRepeater: ['main', 'sub', 'shiftChoices'],
  validateRitXit: ['ritActive', 'ritOffset', 'xitActive', 'xitOffset'],
  validateAntenna: ['txAntenna', 'rxAnt', 'antennaCount'],
  validateScan: ['scanning', 'scanType', 'scanResumeMode'],
  validateCwKeyer: [
    'breakIn', 'breakInDelay', 'keyerSpeed', 'pitchHz', 'pitchDomain', 'keySpeedDomain',
    'reversePaddle', 'apf', 'twinPeak',
  ],
  validateScopeControls: [
    'mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver',
    'duringTx', 'centerType', 'vbwNarrow', 'rbw',
  ],
  validateScopeDisplay: ['source', 'health', 'hardwareConnected'],
  validateRadioWideIndicators: [
    'rfState', 'antenna', 'atu', 'dialLock', 'ritActive', 'ritOffset', 'xitActive', 'xitOffset', 'actions',
  ],
};

describe('each optional group exactKeys list is frozen (MOR-1303)', () => {
  it.each(Object.entries(FROZEN))('%s allow-list equals the frozen literal', (fn, frozen) => {
    expect(groupKeys(fn)).toEqual([...frozen]);
  });
});
