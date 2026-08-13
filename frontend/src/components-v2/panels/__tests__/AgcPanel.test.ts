import { describe, it, expect } from 'vitest';
import { buildAgcOptions } from '../agc-utils';

// ---------------------------------------------------------------------------
// buildAgcOptions
//
// MOR-1522: the AGC option set is radio-specific profile data. This function
// must render ONLY the declared `modes` — never invent an option the profile
// did not declare (the bug: IC-7300 has no AGC OFF, but the UI showed one).
// ---------------------------------------------------------------------------

describe('buildAgcOptions', () => {
  // --- No synthetic options ---

  it('does not invent an OFF option for a domain that has none (IC-7300/IC-7610 FAST/MID/SLOW)', () => {
    const options = buildAgcOptions([1, 2, 3], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    expect(options).toEqual([
      { value: 1, label: 'FAST' },
      { value: 2, label: 'MID' },
      { value: 3, label: 'SLOW' },
    ]);
    expect(options.some((o) => o.value === 0)).toBe(false);
  });

  it('returns an empty list for an empty modes list (no invented fallback)', () => {
    const options = buildAgcOptions([], {});
    expect(options).toEqual([]);
  });

  it('keeps a declared OFF option for a domain that has one (X6200 OFF/FAST/SLOW/AUTO)', () => {
    const options = buildAgcOptions(
      [0, 1, 2, 3],
      { '0': 'OFF', '1': 'FAST', '2': 'SLOW', '3': 'AUTO' },
    );
    expect(options).toEqual([
      { value: 0, label: 'OFF' },
      { value: 1, label: 'FAST' },
      { value: 2, label: 'SLOW' },
      { value: 3, label: 'AUTO' },
    ]);
  });

  it('renders exactly the declared set, nothing more, nothing less', () => {
    const options = buildAgcOptions([2], { '2': 'MID' });
    expect(options).toEqual([{ value: 2, label: 'MID' }]);
  });

  // --- Labels ---

  it('maps mode values to labels from the labels record', () => {
    const options = buildAgcOptions([1, 2, 3], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    expect(options.map((o) => o.label)).toEqual(['FAST', 'MID', 'SLOW']);
  });

  it('falls back to String(mode) when label is missing', () => {
    const options = buildAgcOptions([1, 99], { '1': 'FAST' });
    const unknown = options.find((o) => o.value === 99);
    expect(unknown).toEqual({ value: 99, label: '99' });
  });

  it('falls back to String(mode) for all modes when labels is empty', () => {
    const options = buildAgcOptions([1, 2], {});
    expect(options).toEqual([
      { value: 1, label: '1' },
      { value: 2, label: '2' },
    ]);
  });

  // --- Order preservation ---

  it('preserves the declared order of modes', () => {
    const options = buildAgcOptions([3, 1, 2], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    expect(options.map((o) => o.value)).toEqual([3, 1, 2]);
  });

  it('returns a total length equal to modes.length', () => {
    const options = buildAgcOptions([1, 2, 3], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    expect(options).toHaveLength(3);
  });

  // --- Value types ---

  it('all option values are numbers', () => {
    const options = buildAgcOptions([1, 2, 3], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    options.forEach((o) => expect(typeof o.value).toBe('number'));
  });

  // --- Per-profile domains (MOR-1522 domain table) ---

  it('IC-7300/IC-7610/IC-705/IC-9700: exactly FAST/MID/SLOW, no OFF', () => {
    const options = buildAgcOptions([1, 2, 3], { '1': 'FAST', '2': 'MID', '3': 'SLOW' });
    expect(options.map((o) => o.value)).toEqual([1, 2, 3]);
  });

  it('X6200: OFF/FAST/SLOW/AUTO (PDF page 8 domain, not the Icom FAST/MID/SLOW shape)', () => {
    const options = buildAgcOptions(
      [0, 1, 2, 3],
      { '0': 'OFF', '1': 'FAST', '2': 'SLOW', '3': 'AUTO' },
    );
    expect(options.map((o) => o.label)).toEqual(['OFF', 'FAST', 'SLOW', 'AUTO']);
  });

  it('FTX-1: full 7-value domain including manual OFF/FAST/MID/SLOW and auto variants', () => {
    // MOR-1547: mirrors rigs/ftx1.toml's real [agc.labels] — the auto-mode
    // labels (4/5/6) are the short "A-F"/"A-M"/"A-S" form, not
    // "A-FAST"/"A-MID"/"A-SLOW" (shortened to keep the amber-lcd skin's
    // "AGC "-prefixed status chip within its established single-row width
    // budget, see AmberIndStrip.svelte:68-128 and
    // tests/test_rig_loader.py::test_ftx1_agc_auto_labels_are_short_form for
    // the real-profile witness). Asserting every option here (not just
    // length + options[0]) so this stays a real regression pin instead of a
    // stale document that silently drifts from the profile data.
    const options = buildAgcOptions(
      [0, 1, 2, 3, 4, 5, 6],
      {
        '0': 'OFF',
        '1': 'FAST',
        '2': 'MID',
        '3': 'SLOW',
        '4': 'A-F',
        '5': 'A-M',
        '6': 'A-S',
      },
    );
    expect(options).toEqual([
      { value: 0, label: 'OFF' },
      { value: 1, label: 'FAST' },
      { value: 2, label: 'MID' },
      { value: 3, label: 'SLOW' },
      { value: 4, label: 'A-F' },
      { value: 5, label: 'A-M' },
      { value: 6, label: 'A-S' },
    ]);
  });
});
