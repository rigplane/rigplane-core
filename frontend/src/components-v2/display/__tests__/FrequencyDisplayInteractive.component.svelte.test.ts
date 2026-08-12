import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import FrequencyDisplayInteractive from '../../../primitives/frequency/FrequencyDisplayInteractive.svelte';
// Static import while the stores below are imported dynamically per test:
// safe ONLY while `panel-props` stays a pure state→props module with no store
// imports. If a projection ever starts reading a store, move this into the
// per-test dynamic import too — a split module graph here would quietly
// reintroduce the exact fabrication bug this file exists to pin.
import { toVfoProps } from '$lib/runtime/props/panel-props';

// This test exercises the REAL radio store + REAL `panel-props` projection so
// the freq fix (MOR-475/MOR-1403) is verified end-to-end: VFO frequency is
// StateStore-owned truth exclusively — click-to-tune steps from the last
// CONFIRMED server frequency, never a local intent (MOR-1409 A09b removed the
// last optimistic machinery; there is no overlay left to drop).
//
// MOR-1409 A15 finding, and its follow-up (done here): this file used to
// project through the deleted `wiring/state-adapter` twin and assert
// `14074000`. That assertion passed for the WRONG reason: `setRadioState()`
// rejected the fixture (it gates on a capability epoch/topology the fixture
// never established), so `getRadioState()` was `null` and the twin's `!state`
// branch returned its hard-coded `14074000`/`'USB'`/`'FIL1'` stand-in. The
// test was reading a fabrication while its own comment claimed StateStore
// truth. A15 re-pointed the projection to the honest `panel-props.toVfoProps`
// (`NaN` for an unobserved VFO) and deferred the store round-trip; this file
// now carries the capabilities/epoch harness that round-trip needs, and every
// `setRadioState()` call asserts acceptance so the trap cannot silently
// return.
//
// We import the real stores dynamically per test to reset their module state
// between cases.

let store: typeof import('$lib/stores/radio.svelte');
let capabilities: typeof import('$lib/stores/capabilities.svelte');

const PROVIDER_GENERATION = 0;

function makeCapabilities(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
    ...overrides,
  };
}

function makeMinimalState(overrides: Partial<ServerState> = {}): ServerState {
  const revision = overrides.stateRevision ?? overrides.revision ?? 1;
  return {
    stateContractVersion: 1,
    providerGeneration: PROVIDER_GENERATION,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    revision,
    stateRevision: revision,
    freshnessRevision: overrides.freshnessRevision ?? 1,
    observationSeq: overrides.observationSeq ?? revision,
    updatedAt: '2026-03-07T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 50,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 100,
      rfGain: 255,
      squelch: 0,
      ...(overrides.main ?? {}),
    },
    sub: {
      freqHz: 7100000,
      mode: 'LSB',
      filter: 2,
      dataMode: 0,
      sMeter: 20,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 80,
      rfGain: 255,
      squelch: 0,
      ...(overrides.sub ?? {}),
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    ...overrides,
  } as ServerState;
}

let components: ReturnType<typeof mount>[] = [];

function mountDisplay(props: ComponentProps<typeof FrequencyDisplayInteractive>): HTMLElement {
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(FrequencyDisplayInteractive, { target: t, props }));
  flushSync();
  return t;
}

beforeEach(async () => {
  vi.resetModules();
  // Same module registry: radio.svelte's own capabilities import resolves to
  // this instance, so the epoch we establish here is the one the gate checks.
  capabilities = await import('$lib/stores/capabilities.svelte');
  store = await import('$lib/stores/radio.svelte');
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('FrequencyDisplayInteractive click-to-tune over the radio store (MOR-475)', () => {
  it('scroll steps from the last StateStore frequency, never an unconfirmed intent', () => {
    // The capabilities epoch must exist BEFORE any state is offered, or the
    // store rejects every observation (capabilitiesMatchGeneration fails).
    expect(capabilities.setCapabilities(makeCapabilities())).toBe(true);

    // Initial server freq — the gate must ACCEPT it, otherwise the test is
    // projecting `null` and the 14074000 below would be a fabrication.
    expect(store.setRadioState(makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 1,
      freshnessRevision: 1,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    }))).toBe(true);

    // A causally-newer StateStore observation confirming the same frequency
    // (analogous to an in-flight poll captured before any local click lands).
    expect(store.setRadioState(makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 2,
      freshnessRevision: 2,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    }))).toBe(true);

    // The StateStore observation is the sole VFO truth seen by the projection
    // — and it is OBSERVED, not fabricated: the honest projection returns
    // `NaN` when it is not (A15's negative control, kept), which is exactly
    // what these assertions distinguish.
    const state = store.getRadioState();
    expect(state).not.toBeNull();
    const vfo = toVfoProps(state, 'main');
    expect(vfo.freq).toBe(14074000);
    expect(Number.isNaN(toVfoProps(null, 'main').freq)).toBe(true);

    const onFreqChange = vi.fn();
    const t = mountDisplay({ freq: vfo.freq, onFreqChange });

    // Digits in DOM order for 14074000: MHz[1,4] kHz[0,7,4] Hz[0,0,0].
    // The 1 kHz digit (multiplier 1000) remains index 4.
    const digits = Array.from(t.querySelectorAll<HTMLElement>('.digit'));
    const oneKhzDigit = digits[4];
    expect(oneKhzDigit).toBeDefined();

    oneKhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    flushSync();

    // Relative tune steps from confirmed 14074000.
    expect(onFreqChange).toHaveBeenCalledTimes(1);
    expect(onFreqChange).toHaveBeenCalledWith(14075000);
  });
});

// ── MOR-1441: pending-target affordance ─────────────────────────────────
describe('FrequencyDisplayInteractive pending-target marker (MOR-1441)', () => {
  // Kills: rendering a pending target with no distinguishing marker — the
  // readout would then present an unconfirmed value as confirmed truth.
  it('marks the group data-freq-status="pending" when pendingDisplayHz is set', () => {
    const t = mountDisplay({ freq: 14250000, pendingDisplayHz: 14260000 });
    const group = t.querySelector<HTMLElement>('.freq')!;
    expect(group.dataset.freqStatus).toBe('pending');
  });

  // Kills: defaulting to "pending" — the plain confirmed readout (every
  // existing caller, pre-MOR-1441) must stay marked "confirmed".
  it('marks the group data-freq-status="confirmed" by default', () => {
    const t = mountDisplay({ freq: 14074000 });
    const group = t.querySelector<HTMLElement>('.freq')!;
    expect(group.dataset.freqStatus).toBe('confirmed');
  });

  // B2 (screen-reader honesty): the italic/opacity marker is a VISUAL-only
  // channel. Kills: a pending frequency read aloud by a screen reader as
  // though it were confirmed, with no distinguishing word at all.
  it('exposes the pending state to assistive tech via a rendered, linked word', () => {
    const t = mountDisplay({
      freq: 14250000,
      pendingDisplayHz: 14260000,
      pendingAnnouncement: 'Pending, not yet confirmed',
    });
    const group = t.querySelector<HTMLElement>('.freq')!;
    const describedById = group.getAttribute('aria-describedby');
    expect(describedById).toBeTruthy();
    const description = t.querySelector<HTMLElement>(`#${describedById}`)!;
    expect(description).toBeTruthy();
    expect(description.textContent).toBe('Pending, not yet confirmed');
    expect(description.classList.contains('sr-only')).toBe(true);
  });

  // Kills: rendering the marker attribute/DOM but skipping the aria link
  // when the confirmed (non-pending) readout is shown — no phantom
  // describedby pointing at nothing.
  it('carries no aria-describedby when confirmed', () => {
    const t = mountDisplay({ freq: 14074000 });
    const group = t.querySelector<HTMLElement>('.freq')!;
    expect(group.hasAttribute('aria-describedby')).toBe(false);
  });

  // ── MOR-1441 REVIEW FIX: the display→gesture→accumulator seam ──────────
  // Reproduced defect: an earlier revision fed the PENDING display value
  // into the SAME `freq` prop the gesture handlers use for arithmetic, so
  // `adjustFreqByDigit` computed off an already-drifted base and every hot
  // tick fed a growing excess back into the MOR-1425 tuning accumulator
  // (positive feedback — 10 ticks of +10 Hz intent measured out to
  // +1910 Hz actual against the real accumulator). THE kill: a wheel tick
  // must request `confirmed + step`, never `pendingDisplayHz + step`.
  it('MUTATION KILL: a wheel tick on a pending display computes from CONFIRMED, not the pending value', () => {
    const CONFIRMED = 14250000;
    const PENDING = 14260000; // already 10 kHz ahead of confirmed
    const onFreqChange = vi.fn();
    const t = mountDisplay({ freq: CONFIRMED, pendingDisplayHz: PENDING, onFreqChange });

    // Digits render the PENDING value (14260000): confirm the fixture is
    // genuinely showing a pending target, not accidentally testing the
    // confirmed-only path.
    const digitsText = Array.from(t.querySelectorAll<HTMLElement>('.digit')).map((d) => d.textContent).join('');
    expect(digitsText).toBe('14260000');

    // The 1 kHz digit — same DOM position/multiplier convention as the
    // MOR-475 test above.
    const oneKhzDigit = t.querySelectorAll<HTMLElement>('.digit')[4];
    oneKhzDigit.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    flushSync();

    expect(onFreqChange).toHaveBeenCalledTimes(1);
    // THE assertion: confirmed + 1kHz step. The pre-fix code would have
    // requested 14261000 (pending + step) here instead.
    expect(onFreqChange).toHaveBeenCalledWith(CONFIRMED + 1000);
    expect(onFreqChange).not.toHaveBeenCalledWith(PENDING + 1000);
  });

  // MOR-1441 round-2 review: the wheel pin above does not cover the
  // click-to-select + ArrowUp/ArrowDown path, a separate reachable gesture
  // (`handleKeyDown`, lines ~100/104) with its OWN `adjustFreqByDigit(freq, ...)`
  // call sites — a keyboard-only regression of the same bug (sourcing from
  // `pendingDisplayHz` instead of `freq`) would reproduce the identical
  // runaway on this path alone and slip past the wheel-only pin.
  it('MUTATION KILL: arrow keys on a pending display compute from CONFIRMED, not the pending value', () => {
    const CONFIRMED = 14250000;
    const PENDING = 14260000; // already 10 kHz ahead of confirmed
    const onFreqChange = vi.fn();
    const t = mountDisplay({ freq: CONFIRMED, pendingDisplayHz: PENDING, onFreqChange });

    const digits = t.querySelectorAll<HTMLElement>('.digit');
    digits[digits.length - 1].click(); // select the 1 Hz digit
    const group = t.querySelector<HTMLElement>('.freq')!;
    group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
    group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
    flushSync();

    expect(onFreqChange).toHaveBeenCalledTimes(2);
    // THE assertion: confirmed ± 1 Hz. The pre-fix code would have computed
    // from PENDING (14260001/14259999) here instead.
    expect(onFreqChange.mock.calls[0][0]).toBe(CONFIRMED + 1);
    expect(onFreqChange.mock.calls[1][0]).toBe(CONFIRMED - 1);
    expect(onFreqChange).not.toHaveBeenCalledWith(PENDING + 1);
  });
});
