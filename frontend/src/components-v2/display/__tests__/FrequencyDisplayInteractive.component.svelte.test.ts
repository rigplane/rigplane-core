import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { ServerState } from '$lib/types/state';
import FrequencyDisplayInteractive from '../../../primitives/frequency/FrequencyDisplayInteractive.svelte';
import { toVfoProps } from '$lib/runtime/props/panel-props';

// This test exercises the REAL `panel-props` projection so the freq fix
// (MOR-475/MOR-1403) is verified end-to-end: click-to-tune steps from the last
// CONFIRMED server frequency, never a local intent (MOR-1409 A09b removed the
// last optimistic machinery; there is no overlay left to drop).
//
// MOR-1409 A15 — authored re-point, and a finding worth recording. This file
// used to project through the deleted `wiring/state-adapter` twin and assert
// `14074000`. That assertion passed for the WRONG reason: `setRadioState()`
// rejects this fixture (it gates on a capability epoch/topology the fixture
// never establishes), so `getRadioState()` was `null` and the twin's `!state`
// branch returned its hard-coded `14074000`/`'USB'`/`'FIL1'` stand-in. The
// test was reading a fabrication while its own comment claimed StateStore
// truth. `panel-props.toVfoProps` is honest — it returns `NaN` for an
// unobserved VFO — which is what exposed this.
//
// The projection is therefore fed the constructed observation directly, so
// the value is genuinely observed rather than fabricated. Restoring a real
// store round-trip needs a capabilities/epoch harness this file never had and
// is out of A15's scope; it is recorded as a follow-up rather than smuggled
// into a closure gate.

function makeMinimalState(overrides: Partial<ServerState> = {}): ServerState {
  const revision = overrides.stateRevision ?? overrides.revision ?? 1;
  return {
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

beforeEach(() => {
  vi.resetModules();
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('FrequencyDisplayInteractive click-to-tune over the radio store (MOR-475)', () => {
  it('scroll steps from the last StateStore frequency, never an unconfirmed intent', () => {
    // A causally-newer observation confirming the frequency (analogous to an
    // in-flight poll captured before any local click lands).
    const observed = makeMinimalState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 2,
      freshnessRevision: 2,
      main: { ...makeMinimalState().main, freqHz: 14074000 },
    });

    // The observation is the sole VFO truth seen by the projection — and it is
    // OBSERVED, not fabricated: the honest projection returns `NaN` when it is
    // not, which is exactly what this assertion now distinguishes.
    const vfo = toVfoProps(observed, 'main');
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
});
