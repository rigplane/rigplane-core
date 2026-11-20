/**
 * MOR-4604 — audio_fft surfaces subscribe to a follow view as wide as the
 * drawn passband plus a small margin (the source anchors it on the passband)
 * instead of a fixed 48 kHz follow view. The hub grants at most the source's native bins across the
 * whole view (no upsampling), so only a passband-sized view delivers native
 * resolution to the trapezoid.
 */
import { describe, expect, it } from 'vitest';
import {
  audioScopeCarrier,
  audioScopePassbandView,
  audioScopeProjection,
} from '$lib/runtime/adapters/scope-adapter';
import type { ScopeGeometry } from '$lib/runtime/scope-frame';
import type { ServerState } from '$lib/types/state';

type FieldStatus = NonNullable<ServerState['fieldStatus']>[string];
import {
  IC7300_STATE,
  IC7300_CAPABILITIES,
} from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import {
  FTX1_STATE,
  FTX1_CAPABILITIES,
} from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';

/** Marks an existing-or-new main leaf freshly observed, the strictlySeen shape. */
function observe(state: ServerState, path: string): void {
  state.fieldStatus![path] = {
    ...state.fieldStatus!['main.freqHz']!,
    observed: true, freshness: 'fresh', availability: 'available',
  } as FieldStatus;
}

function audioState(base: ServerState, patch: {
  freqHz?: number; mode?: string; filterWidth?: number | null; cwPitch?: number;
}): ServerState {
  const state = structuredClone(base);
  if (patch.freqHz !== undefined) { state.main.freqHz = patch.freqHz; observe(state, 'main.freqHz'); }
  if (patch.mode !== undefined) { state.main.mode = patch.mode; observe(state, 'main.mode'); }
  if (patch.filterWidth !== undefined) {
    state.main.filterWidth = patch.filterWidth;
    observe(state, 'main.filterWidth');
    if (patch.filterWidth === null) {
      state.fieldStatus!['main.filterWidth'] = {
        ...state.fieldStatus!['main.filterWidth']!, observed: false,
        freshness: 'unknown', availability: 'missing',
      } as FieldStatus;
    }
  }
  if (patch.cwPitch !== undefined) { state.cwPitch = patch.cwPitch; observe(state, 'cwPitch'); }
  return state;
}

function geometryOver(view: { startHz: number; endHz: number }): ScopeGeometry {
  return {
    source: 'audio_fft', sourceId: 'audio_fft', receiver: null, mode: 0, width: 200,
    view, coverage: view, outOfRange: false, hasPixels: true, freshness: 'fresh',
  };
}

describe('audioScopePassbandView (MOR-4604)', () => {
  it('requests a follow view as wide as the drawn passband plus margin', () => {
    const state = audioState(IC7300_STATE, { filterWidth: 2400 });
    // 2400 Hz drawn, margin 2400/8 = 300 on both sides.
    expect(audioScopePassbandView(state, IC7300_CAPABILITIES)).toEqual({ follow: 'vfo', spanHz: 3000 });
  });

  it('does not change while tuning, across sidebands or the CW pitch carrier', () => {
    const base = audioScopePassbandView(audioState(IC7300_STATE, { filterWidth: 2400 }), IC7300_CAPABILITIES);
    for (const patch of [
      { freqHz: 14_188_500, filterWidth: 2400 },
      { freqHz: 7_074_000, filterWidth: 2400 },
      { mode: 'LSB', filterWidth: 2400 },
      { mode: 'CW-U', filterWidth: 2400, cwPitch: 700 },
    ]) {
      expect(audioScopePassbandView(audioState(IC7300_STATE, patch), IC7300_CAPABILITIES)).toEqual(base);
    }
  });

  it('follows the filter width: AM 6 kHz on the FTX-1', () => {
    const state = audioState(FTX1_STATE, { mode: 'AM', filterWidth: 6000 });
    expect(audioScopePassbandView(state, FTX1_CAPABILITIES)).toEqual({ follow: 'vfo', spanHz: 7500 });
  });

  it('clamps the drawn range at the audio Nyquist for broadcast-wide filters', () => {
    const state = audioState(FTX1_STATE, { mode: 'WFM', filterWidth: 150000 });
    // drawn = min(150000, 48000/2) = 24000, margin 3000 each side.
    expect(audioScopePassbandView(state, FTX1_CAPABILITIES)).toEqual({ follow: 'vfo', spanHz: 30000 });
  });

  it('keeps the 48 kHz follow view while the filter width is unobserved', () => {
    // The IC-7300 capture never observed main.filterWidth.
    expect(audioScopePassbandView(IC7300_STATE, IC7300_CAPABILITIES))
      .toEqual({ follow: 'vfo', spanHz: 48000 });
  });

  it('maps the granted passband window through the observed carrier (USB and LSB)', () => {
    for (const [mode, lower] of [['USB', false], ['LSB', true]] as const) {
      const state = audioState(IC7300_STATE, { mode, filterWidth: 2400 });
      const carrier = audioScopeCarrier(state);
      expect(carrier).toEqual({ carrierHz: 14188000, lower });
      // The source anchors the 3000 Hz follow window on the passband midpoint.
      const view = lower ? { startHz: 14_185_300, endHz: 14_188_300 } : { startHz: 14_187_700, endHz: 14_190_700 };
      const projection = audioScopeProjection(geometryOver(view), state)!;
      const span = view.endHz - view.startHz;
      expect(view.startHz + projection.zero * span).toBeCloseTo(14188000, 6);
      expect(projection.perHz).toBeCloseTo((lower ? -1 : 1) / span, 12);
      expect(projection.spanHz).toBeCloseTo(lower ? 14188000 - view.startHz : view.endHz - 14188000, 6);
    }
  });
});
