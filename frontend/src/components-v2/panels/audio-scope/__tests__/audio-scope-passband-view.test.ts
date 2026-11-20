/**
 * MOR-4604 — the AudioScope panel subscribes to the RF window it actually
 * draws (the audio passband plus a small margin) instead of a fixed 48 kHz
 * follow view. The hub grants at most the source's native bins across the
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
  } as ServerState['fieldStatus'][string];
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
      } as ServerState['fieldStatus'][string];
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
  it('requests the drawn passband plus margin for USB on the IC-7300', () => {
    const state = audioState(IC7300_STATE, { filterWidth: 2400 });
    expect(state.main.freqHz).toBe(14188000);
    // 2400 Hz drawn from the carrier, margin 2400/8 = 300 on both sides.
    expect(audioScopePassbandView(state, IC7300_CAPABILITIES))
      .toEqual({ startHz: 14187700, endHz: 14190700 });
  });

  it('mirrors the window below the carrier for LSB', () => {
    const state = audioState(IC7300_STATE, { mode: 'LSB', filterWidth: 2400 });
    expect(audioScopePassbandView(state, IC7300_CAPABILITIES))
      .toEqual({ startHz: 14185300, endHz: 14188300 });
  });

  it('anchors the window at the pitch-offset carrier for CW-U', () => {
    const state = audioState(IC7300_STATE, { mode: 'CW-U', filterWidth: 2400, cwPitch: 700 });
    // carrier = 14188000 - 700 = 14187300; the drawn range extends upward.
    expect(audioScopePassbandView(state, IC7300_CAPABILITIES))
      .toEqual({ startHz: 14187000, endHz: 14190000 });
  });

  it('requests the drawn positive sideband for AM on the FTX-1', () => {
    const state = audioState(FTX1_STATE, { mode: 'AM', filterWidth: 6000 });
    expect(state.main.freqHz).toBe(14074000);
    // 6000 Hz drawn, margin 750 each side.
    expect(audioScopePassbandView(state, FTX1_CAPABILITIES))
      .toEqual({ startHz: 14073250, endHz: 14080750 });
  });

  it('clamps the drawn range at the audio Nyquist for broadcast-wide filters', () => {
    const state = audioState(FTX1_STATE, { mode: 'WFM', filterWidth: 150000 });
    // drawn = min(150000, 48000/2) = 24000, margin 3000.
    expect(audioScopePassbandView(state, FTX1_CAPABILITIES))
      .toEqual({ startHz: 14071000, endHz: 14101000 });
  });

  it('keeps the 48 kHz follow view while the filter width is unobserved', () => {
    // The IC-7300 capture never observed main.filterWidth.
    expect(audioScopePassbandView(IC7300_STATE, IC7300_CAPABILITIES))
      .toEqual({ follow: 'vfo', spanHz: 48000 });
  });

  it('keeps the 48 kHz follow view while mode or frequency is unobserved', () => {
    const state = audioState(IC7300_STATE, { filterWidth: 2400 });
    state.fieldStatus!['main.freqHz'] = {
      ...state.fieldStatus!['main.freqHz']!, observed: false,
      freshness: 'unknown', availability: 'missing',
    } as ServerState['fieldStatus'][string];
    expect(audioScopePassbandView(state, IC7300_CAPABILITIES))
      .toEqual({ follow: 'vfo', spanHz: 48000 });
  });

  it('hands the projection a carrier that sits exactly where the view math places it', () => {
    const state = audioState(IC7300_STATE, { filterWidth: 2400 });
    const view = audioScopePassbandView(state, IC7300_CAPABILITIES);
    if (!('startHz' in view)) throw new Error('expected an explicit view');
    const projection = audioScopeProjection(geometryOver(view), state);
    expect(projection).not.toBeNull();
    const span = view.endHz - view.startHz;
    // zero/perHz locate the carrier inside the view; spanHz is the drawn side.
    expect(view.startHz + projection!.zero * span).toBeCloseTo(14188000, 6);
    expect(projection!.perHz).toBeCloseTo(1 / span, 12);
    expect(projection!.spanHz).toBeCloseTo(view.endHz - 14188000, 6);
  });

  it('derives the LSB projection against the same carrier helper', () => {
    const state = audioState(IC7300_STATE, { mode: 'LSB', filterWidth: 2400 });
    const carrier = audioScopeCarrier(state);
    expect(carrier).toEqual({ carrierHz: 14188000, lower: true });
    const view = audioScopePassbandView(state, IC7300_CAPABILITIES);
    if (!('startHz' in view)) throw new Error('expected an explicit view');
    const projection = audioScopeProjection(geometryOver(view), state)!;
    const span = view.endHz - view.startHz;
    expect(view.startHz + projection.zero * span).toBeCloseTo(14188000, 6);
    expect(projection.perHz).toBeCloseTo(-1 / span, 12);
    expect(projection.spanHz).toBeCloseTo(14188000 - view.startHz, 6);
  });
});
