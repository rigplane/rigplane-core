/**
 * MOR-1442 — regression coverage for the tuning-STEP store.
 *
 * Reported symptom: during a live walkthrough, the tuning STEP control
 * (STEP arrows in SpectrumToolbar.svelte, backed by
 * `$lib/stores/tuning.svelte`) spontaneously changed value (5kHz -> 1kHz)
 * while the operator was interacting with the spectrum/waterfall, with no
 * click on the STEP control itself.
 *
 * Root cause: `setTuningStepFromCompanion` (the RC-28 hardware companion's
 * step sync, wired from `ws-client.ts`'s `companion_state` handler) updated
 * `_step` WITHOUT disabling `_autoStep` — unlike the browser's own STEP
 * control (`setTuningStep`), which always disables auto-step on a manual
 * override. Because auto-step stayed on, the very next radio-reported mode
 * change replayed `applyModeDefault(mode)` — wired as a `$effect` on
 * `activeMode` in both RadioLayout.svelte and LcdLayout.svelte, firing on
 * ANY mode update pushed from the backend, including a radio's own
 * band-stack-register mode recall while the operator jump-tunes across the
 * waterfall via SpectrumPanel.svelte's `handleTune` click-to-tune — and
 * silently discarded the companion-set step back to the per-mode default.
 * No control in the browser session was ever clicked.
 *
 * This file exercises the real store module (not a mock, unlike every
 * other test that touches `$lib/stores/tuning.svelte`) so the actual
 * interaction between the companion path and the mode-driven auto-step
 * path is under test, not stubbed away. `applyModeDefault` and
 * `setTuningStepFromCompanion` are the exact functions the real wiring
 * calls (RadioLayout.svelte:220 / LcdLayout.svelte:49, and
 * ws-client.ts:815 respectively) — calling them directly here is
 * equivalent to driving the bug through the actual component effects,
 * without the overhead of mounting them.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { radio } from '../radio.svelte';

let tuning: typeof import('../tuning.svelte');

beforeAll(async () => {
  // Stub fetch before the module's first evaluation: `tuning.svelte.ts`
  // fires a `_syncToCompanion` PUT at module top level and on every
  // `_persistState()` call. A dynamic import inside `beforeAll` (after the
  // stub is installed) guarantees this runs before that top-level call —
  // a plain top-of-file static import would NOT, since static imports are
  // always evaluated before any of this file's own statements.
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true } as Response)));
  tuning = await import('../tuning.svelte');
});

describe('tuning step store — companion override vs. mode-driven auto-step (MOR-1442)', () => {
  beforeEach(() => {
    // Deterministic starting point for every test, independent of
    // execution order within this file: a real STEP-control click always
    // lands on a known value and always disables auto-step, so this is a
    // legitimate way to reset both `_step` and `_autoStep` without an
    // exported test-only reset hook. `radio.current` is null in this
    // environment, so re-enabling auto-step here does not itself touch
    // `_step` (see `setAutoStep`'s mode lookup guard).
    tuning.setTuningStep(1_000);
    tuning.setAutoStep(true);
  });

  it('bug repro: a companion-set step must survive a later mode change', () => {
    tuning.setTuningStepFromCompanion(5_000);
    expect(tuning.getTuningStep()).toBe(5_000);

    // The mode-driven auto-step effect, as wired in RadioLayout.svelte and
    // LcdLayout.svelte, fires on every radio-reported mode change.
    tuning.applyModeDefault('USB');

    expect(tuning.getTuningStep()).toBe(5_000);
  });

  it('a companion step change disables auto-step, matching the browser STEP control', () => {
    expect(tuning.isAutoStep()).toBe(true);
    tuning.setTuningStepFromCompanion(5_000);
    expect(tuning.isAutoStep()).toBe(false);
  });

  it('control case: the browser STEP control already protected itself from this', () => {
    tuning.setTuningStep(5_000);
    expect(tuning.isAutoStep()).toBe(false);
    tuning.applyModeDefault('USB');
    expect(tuning.getTuningStep()).toBe(5_000);
  });

  it('auto-step still tracks mode when the operator never overrode the step at all', () => {
    expect(tuning.isAutoStep()).toBe(true);
    tuning.applyModeDefault('CW');
    expect(tuning.getTuningStep()).toBe(10);
    tuning.applyModeDefault('USB');
    expect(tuning.getTuningStep()).toBe(1_000);
  });

  it('a companion echo matching the already-current step is a no-op, no spurious auto-step disable', () => {
    expect(tuning.isAutoStep()).toBe(true);
    tuning.setTuningStepFromCompanion(1_000); // matches the beforeEach baseline exactly
    expect(tuning.getTuningStep()).toBe(1_000);
    expect(tuning.isAutoStep()).toBe(true);
  });
});

/**
 * MOR-1486 — the re-enable path itself was never unreachable from the
 * store's point of view (`setAutoStep(true)` already reapplies the current
 * mode's default step); the bug was that no UI control ever called it. This
 * suite pins the store contract the new SpectrumToolbar toggle relies on:
 * flip `_autoStep` back on and the step must snap to the live mode's
 * default, exactly like a fresh browser profile would show.
 */
describe('tuning step store — setAutoStep(true) re-enable semantics (MOR-1486)', () => {
  beforeEach(() => {
    radio.current = null;
    tuning.setTuningStep(1_000);
  });

  it('re-enabling auto-step with no active receiver leaves the step untouched', () => {
    tuning.setAutoStep(true);
    expect(tuning.isAutoStep()).toBe(true);
    expect(tuning.getTuningStep()).toBe(1_000);
  });

  it('re-enabling auto-step snaps the step to the active receiver mode default', () => {
    radio.current = { active: 'MAIN', main: { mode: 'CW' } } as any;
    tuning.setAutoStep(true);
    expect(tuning.isAutoStep()).toBe(true);
    expect(tuning.getTuningStep()).toBe(10);
  });

  it('re-enabling auto-step reads the SUB receiver mode when SUB is active', () => {
    radio.current = { active: 'SUB', main: { mode: 'USB' }, sub: { mode: 'FM' } } as any;
    tuning.setAutoStep(true);
    expect(tuning.getTuningStep()).toBe(25_000);
  });

  it('a manual step change followed by re-enable is a full round trip back to mode-follow', () => {
    radio.current = { active: 'MAIN', main: { mode: 'AM' } } as any;
    tuning.setAutoStep(true);
    expect(tuning.getTuningStep()).toBe(1_000); // AM default

    tuning.setTuningStep(50); // manual override — disables auto-step
    expect(tuning.isAutoStep()).toBe(false);
    expect(tuning.getTuningStep()).toBe(50);

    tuning.setAutoStep(true); // the new toggle's "re-enable" path
    expect(tuning.isAutoStep()).toBe(true);
    expect(tuning.getTuningStep()).toBe(1_000); // back to AM's default
  });
});
