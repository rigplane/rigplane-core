/**
 * MOR-2907 — the MOR-2215 final audit's two unconditional Standard-face
 * closure blockers, pinned per rendering:
 *
 * F7 — four Standard-only renderings bypassed the shared confirmed getter
 *   (`bindToggleInstrument.confirmed` / the honest `selected === undefined`
 *   choice form / MOR-2690's "aria-pressed only when known"):
 *   `DspInstrumentHost.compactDspButton`, `compactAutoNotch`, the
 *   `CwKeyerSurface` Standard APF key, and `FilterInstrumentHost.standardMode`
 *   (no aria state at all). Every test below kills the fabricated
 *   `aria-pressed="false"` on an UNREAD reading — the same class MOR-2690
 *   retired for the break-in keys.
 *
 * F3 — Standard marked nothing pending for MODE / AGC / ATT clicks, and the
 *   compact NB/NR/NOTCH keys dropped the pending marker `toggle()` and
 *   `notchMode()` already render. The pending source is the ARMED-SIGNAL
 *   CONTRACT (`panel-adapters.ts`: `getModeArmed`/`getAgcArmed`/
 *   `getAttenuatorArmed`, `getPendingNbOn`/`getPendingNrOn`/
 *   `getPendingNotchMode`) — display only; the confirmed reading stays the
 *   sole selection source. Visual vocabulary is the shared armed seat
 *   (`control-button-armed.css`: `data-armed` on the actual key) and the
 *   compact `.dsp-toggle[data-pending-status]` rule this file already ships.
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no stubbed
 * globals. The real wiring seam (`SemanticRadioSurfaces` -> host props) is
 * pinned by the source-scan case last, the same discipline
 * `CwKeyerSurface.test.ts` uses for its own wiring pins.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { DspViewModel, RadioViewModel } from '../radio-view-model';
import {
  topologyFixtures, withCwKeyer, withDsp, withFilterPassband, withModeFilter, withRfFrontEnd,
} from '../fixtures/topologies';
import DspInstrumentHostFixture from './fixtures/DspInstrumentHostFixture.svelte';
import FilterInstrumentHostFixture from './fixtures/FilterInstrumentHostFixture.svelte';
import RfFrontEndInstrumentHostFixture, {
  rfTestAuthorityPublication,
} from './fixtures/RfFrontEndInstrumentHostFixture.svelte';
import CwKeyerInstrumentHostFixture from './fixtures/CwKeyerInstrumentHostFixture.svelte';

const PENDING_ANNOUNCEMENT = 'Pending, not yet confirmed';

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});
afterEach(() => target.remove());

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

function dspField(
  view: RadioViewModel, name: 'nbActive' | 'nrActive' | 'notchMode', reading: unknown,
): RadioViewModel {
  return {
    ...view,
    dsp: { ...view.dsp!, [name]: { ...view.dsp![name], reading } } as DspViewModel,
  } as RadioViewModel;
}

describe('MOR-2907 F7 — honest pressed state on the Standard compact keys', () => {
  it('compact NB/NR keys omit aria-pressed while unread and follow behavior.confirmed when known', () => {
    const view = dspField(withDsp(topologyFixtures['1/single']), 'nrActive', { status: 'unknown' });
    const component = mount(DspInstrumentHostFixture, {
      target, props: { view, presentation: 'standard' },
    });
    flushSync();
    // Unread NR: no aria-pressed at all — never a fabricated "false".
    expect(q('[data-testid="dsp-compact-nr"]')!.getAttribute('aria-pressed')).toBeNull();
    // Known NB (fixture reads false): the honest confirmed value.
    expect(q('[data-testid="dsp-compact-nb"]')!.getAttribute('aria-pressed')).toBe('false');
    unmount(component);
  });

  it('compact NOTCH and A-NOTCH keys omit aria-pressed while the notch mode is unread', () => {
    const view = dspField(withDsp(topologyFixtures['1/single']), 'notchMode', { status: 'unknown' });
    const component = mount(DspInstrumentHostFixture, {
      target, props: { view, presentation: 'standard' },
    });
    flushSync();
    expect(q('[data-testid="dsp-compact-notch"]')!.getAttribute('aria-pressed')).toBeNull();
    expect(q('[data-testid="dsp-compact-autoNotch"]')!.getAttribute('aria-pressed')).toBeNull();
    unmount(component);
  });

  it('compact NOTCH and A-NOTCH keys expose the confirmed choice when it is known', () => {
    const view = dspField(
      withDsp(topologyFixtures['1/single']), 'notchMode', { status: 'known', value: 'manual' },
    );
    const component = mount(DspInstrumentHostFixture, {
      target, props: { view, presentation: 'standard' },
    });
    flushSync();
    expect(q('[data-testid="dsp-compact-notch"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(q('[data-testid="dsp-compact-autoNotch"]')!.getAttribute('aria-pressed')).toBe('false');
    unmount(component);
  });
});

describe('MOR-2907 F7 — honest pressed state on the Standard APF key', () => {
  const ON = { structural: true, operational: true };
  type ApfReading = { status: 'unknown' } | { status: 'known'; value: number };
  const renderStandardCw = (reading: ApfReading) => {
    const base = withCwKeyer(topologyFixtures['1/single']);
    const view: RadioViewModel = withModeFilter({
      ...base, cwKeyer: { ...base.cwKeyer!, apf: { availability: ON, reading } },
    });
    const component = mount(CwKeyerInstrumentHostFixture, {
      target,
      props: {
        view,
        standard: true,
        breakInChoices: [{ value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' }],
      },
    });
    flushSync();
    return component;
  };

  it('omits aria-pressed while the APF reading is unread (MOR-2690 form)', () => {
    const component = renderStandardCw({ status: 'unknown' });
    expect(q('[data-testid="cw-keyer-apf-on"]')!.getAttribute('aria-pressed')).toBeNull();
    unmount(component);
  });

  it('exposes the confirmed APF state when it is known', () => {
    const component = renderStandardCw({ status: 'known', value: 1 });
    expect(q('[data-testid="cw-keyer-apf-on"]')!.getAttribute('aria-pressed')).toBe('true');
    unmount(component);
  });
});

describe('MOR-2907 F7 — Standard mode keys expose the confirmed selection', () => {
  it('renders each mode key as a radio with aria-checked from the confirmed mode', () => {
    const component = mount(FilterInstrumentHostFixture, {
      target,
      props: { view: withFilterPassband(withModeFilter(topologyFixtures['1/single'])), presentation: 'standard' },
    });
    flushSync();
    const usb = q('[data-testid="standard-mode-USB"] button')!;
    const cw = q('[data-testid="standard-mode-CW"] button')!;
    expect(usb.getAttribute('role')).toBe('radio');
    expect(usb.getAttribute('aria-checked')).toBe('true');
    expect(cw.getAttribute('aria-checked')).toBe('false');
    unmount(component);
  });
});

describe('MOR-2907 F3 — pending targets on the Standard face', () => {
  it('arms the requested mode key while set_mode is in flight, confirmed selection untouched', () => {
    const component = mount(FilterInstrumentHostFixture, {
      target,
      props: {
        view: withFilterPassband(withModeFilter(topologyFixtures['1/single'])),
        presentation: 'standard', pendingMode: 'CW',
      },
    });
    flushSync();
    const cw = q('[data-testid="standard-mode-CW"]')!;
    const cwButton = cw.querySelector('button')!;
    expect(cw.getAttribute('data-pending')).toBe('true');
    expect(cwButton.getAttribute('data-armed')).toBe('true');
    expect(cwButton.getAttribute('aria-describedby')).toBeTruthy();
    expect(document.getElementById(cwButton.getAttribute('aria-describedby')!)?.textContent)
      .toBe(PENDING_ANNOUNCEMENT);
    // Confirmed reading stays the sole selection source: still USB.
    expect(q('[data-testid="standard-mode-USB"] button')!.getAttribute('aria-checked')).toBe('true');
    expect(q('[data-testid="standard-mode-USB"] button')!.getAttribute('data-armed')).toBeNull();
    unmount(component);
  });

  it('arms the requested AGC key while set_agc is in flight, confirmed selection untouched', () => {
    const component = mount(DspInstrumentHostFixture, {
      target,
      props: { view: withDsp(topologyFixtures['1/single']), presentation: 'standard', pendingAgcMode: 1 },
    });
    flushSync();
    const armed = q('[data-testid="dsp-agcMode-1"]')!;
    expect(armed.getAttribute('data-armed')).toBe('true');
    expect(armed.getAttribute('aria-describedby')).toBeTruthy();
    expect(document.getElementById(armed.getAttribute('aria-describedby')!)?.textContent)
      .toBe(PENDING_ANNOUNCEMENT);
    // Confirmed AGC (fixture reads 2) stays the checked key; it is not armed.
    const confirmed = q('[data-testid="dsp-agcMode-2"]')!;
    expect(confirmed.getAttribute('aria-checked')).toBe('true');
    expect(confirmed.getAttribute('data-armed')).toBeNull();
    unmount(component);
  });

  it('marks the requested attenuator value pending while set_attenuator is in flight', () => {
    const publication = rfTestAuthorityPublication('separate');
    const component = mount(RfFrontEndInstrumentHostFixture, {
      target,
      props: {
        publication,
        view: withRfFrontEnd(topologyFixtures['1/single']),
        controlModel: 'separate',
        attenuatorCompact: true,
        pendingAtt: 12,
        subscribeControlAuthority: (handler) => {
          handler(publication);
          return () => undefined;
        },
        onLevelChange: () => undefined,
      },
    });
    flushSync();
    const row = q('[data-testid="rf-front-end-attenuator"]')!;
    expect(row.getAttribute('data-att-status')).toBe('pending');
    expect(q('[data-testid="rf-front-end-attenuator-12"]')!.getAttribute('data-armed')).toBe('true');
    // The confirmed reading (fixture reads 6 dB) stays the selected key.
    expect(q('[data-testid="rf-front-end-attenuator-6"]')!.getAttribute('aria-checked')).toBe('true');
    expect(q('[data-testid="rf-front-end-attenuator-6"]')!.getAttribute('data-armed')).toBeNull();
    const describedBy = row.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBe(PENDING_ANNOUNCEMENT);
    unmount(component);
  });

  it('marks the compact NB key pending while set_nb is in flight, NR untouched', () => {
    const component = mount(DspInstrumentHostFixture, {
      target,
      props: { view: withDsp(topologyFixtures['1/single']), presentation: 'standard', pendingNb: true },
    });
    flushSync();
    const nb = q('[data-testid="dsp-compact-nb"]')!;
    expect(nb.getAttribute('data-pending-status')).toBe('pending');
    expect(nb.getAttribute('aria-describedby')).toBeTruthy();
    expect(document.getElementById(nb.getAttribute('aria-describedby')!)?.textContent)
      .toBe(PENDING_ANNOUNCEMENT);
    expect(q('[data-testid="dsp-compact-nr"]')!.getAttribute('data-pending-status')).toBe('confirmed');
    unmount(component);
  });

  it('attributes the compact notch pair pending marker to the key the click targets', () => {
    const manualView = dspField(
      withDsp(topologyFixtures['1/single']), 'notchMode', { status: 'known', value: 'manual' },
    );
    const component = mount(DspInstrumentHostFixture, {
      target,
      props: { view: manualView, presentation: 'standard', pendingNotch: 'auto' },
    });
    flushSync();
    // Requested target AUTO arms the A-NOTCH key alone.
    expect(q('[data-testid="dsp-compact-autoNotch"]')!.getAttribute('data-pending-status')).toBe('pending');
    expect(q('[data-testid="dsp-compact-notch"]')!.getAttribute('data-pending-status')).toBe('confirmed');
    unmount(component);

    // Requested target OFF from a confirmed MANUAL reading is the MANUAL
    // key's own click (the toggle-off path `compactNotch` invokes).
    const target2 = document.createElement('div');
    document.body.appendChild(target2);
    const component2 = mount(DspInstrumentHostFixture, {
      target: target2,
      props: { view: manualView, presentation: 'standard', pendingNotch: 'off' },
    });
    flushSync();
    expect(target2.querySelector('[data-testid="dsp-compact-notch"]')!.getAttribute('data-pending-status'))
      .toBe('pending');
    expect(target2.querySelector('[data-testid="dsp-compact-autoNotch"]')!.getAttribute('data-pending-status'))
      .toBe('confirmed');
    unmount(component2);
    target2.remove();
  });
});

describe('MOR-2907 F3 — the wiring seam passes each armed signal to its host', () => {
  // Same source-scan discipline as `CwKeyerSurface.test.ts`'s wiring pins: a
  // dropped or misnamed prop between `SemanticRadioSurfaces`'s `$derived`s and
  // the host mounts would pass every host-level case above unnoticed
  // (MOR-1473's original test-gap finding).
  const SOURCE = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');

  it('derives the mode/AGC/attenuator armed facts through the panel-adapters accessors', () => {
    expect(SOURCE).toMatch(/getModeArmed\(\)/);
    expect(SOURCE).toMatch(/getAgcArmed\(\)/);
    expect(SOURCE).toMatch(/getAttenuatorArmed\(\)/);
  });

  it('passes pendingMode, pendingAgcMode and pendingAtt at the three host mounts', () => {
    const filterMount = SOURCE.split('<FilterInstrumentHost')[1]!.split('>')[0]!;
    expect(filterMount).toContain('{pendingMode}');
    const dspMount = SOURCE.split('<DspInstrumentHost')[1]!.split('>')[0]!;
    expect(dspMount).toContain('{pendingAgcMode}');
    const rfMount = SOURCE.split('<RfFrontEndInstrumentHost')[1]!.split('>')[0]!;
    expect(rfMount).toContain('{pendingAtt}');
  });
});
