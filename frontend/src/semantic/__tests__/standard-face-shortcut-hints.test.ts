/**
 * MOR-2793 — the Standard face's keyboard-bound controls carry the same
 * `data-shortcut-hint` hosts the legacy panels (ModePanel, RitXitPanel,
 * RfFrontEnd, FilterPanel, RxAudioPanel) already expose. The hint CSS in
 * `KeyboardHandler.svelte` keys on `body[data-shortcut-hints='true']
 * [data-shortcut-hint]::after`; without the attribute the Standard face
 * draws no hints at all once those panels are suppressed by the layout
 * manifest. These tests pin the attribute on the Standard face's hosts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { Capabilities, KeyboardConfig } from '$lib/types/capabilities';
import { topologyFixtures, withFilterPassband, withModeFilter, withRitXit } from '../fixtures/topologies';
import type { RadioViewModel } from '../radio-view-model';
import FilterInstrumentHostFixture from './fixtures/FilterInstrumentHostFixture.svelte';
import RitXitScanInstrumentHostFixture from './fixtures/RitXitScanInstrumentHostFixture.svelte';

const HOST_SOURCES = {
  filter: readFileSync('src/semantic/FilterInstrumentHost.svelte', 'utf8'),
  ritXit: readFileSync('src/semantic/RitXitScanInstrumentHost.svelte', 'utf8'),
  rf: readFileSync('src/semantic/RfFrontEndInstrumentHost.svelte', 'utf8'),
  rxAudio: readFileSync('src/semantic/RxAudioInstrumentHost.svelte', 'utf8'),
} as const;

const keyboard: KeyboardConfig = {
  leaderKey: 'g',
  leaderTimeoutMs: 1000,
  altHints: true,
  helpTitle: 'Shortcuts',
  bindings: [
    { id: 'mode-usb', section: 'Mode', label: 'USB', sequence: ['1'], action: 'mode_select', params: { mode: 'USB' } },
    { id: 'mode-lsb', section: 'Mode', label: 'LSB', sequence: ['2'], action: 'mode_select', params: { mode: 'LSB' } },
    { id: 'cycle-data', section: 'Mode', label: 'DATA', sequence: ['d'], action: 'cycle_data_mode' },
    { id: 'cycle-filter', section: 'Filter', label: 'Filter', sequence: ['f'], action: 'cycle_filter' },
    { id: 'cycle-filter-down', section: 'Filter', label: 'Filter down', sequence: ['F'], action: 'cycle_filter', params: { step: -1 } },
    { id: 'toggle-rit', section: 'RIT', label: 'RIT', sequence: ['r'], action: 'toggle_rit' },
    { id: 'toggle-xit', section: 'RIT', label: 'XIT', sequence: ['x'], action: 'toggle_xit' },
    { id: 'clear-rit-xit', section: 'RIT', label: 'Clear', sequence: ['c'], action: 'clear_rit_xit' },
    { id: 'adjust-rf', section: 'RF', label: 'RF gain', sequence: ['g'], modifiers: ['ALT'], action: 'adjust_rf_gain' },
    { id: 'cycle-att', section: 'RF', label: 'ATT', sequence: ['a'], action: 'cycle_att' },
    { id: 'cycle-pre', section: 'RF', label: 'PRE', sequence: ['p'], action: 'cycle_preamp' },
    { id: 'adjust-af', section: 'Audio', label: 'AF', sequence: ['v'], modifiers: ['ALT'], action: 'adjust_af_level' },
    { id: 'toggle-monitor', section: 'Audio', label: 'Monitor', sequence: ['m'], action: 'toggle_monitor' },
  ],
};

function caps(): Capabilities {
  return {
    stateContractVersion: 1,
    providerGeneration: 1,
    model: 'IC-7300',
    scope: false,
    audio: true,
    tx: true,
    capabilities: ['audio', 'tx', 'cw'],
    receivers: 1,
    vfoScheme: 'single',
    freqRanges: [],
    modes: ['USB', 'LSB', 'CW', 'RTTY', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false },
    txBands: null,
    keyboard,
  };
}

const base = (): RadioViewModel =>
  withFilterPassband(withModeFilter(withRitXit(topologyFixtures['1/single'])));

let target: HTMLDivElement;
beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  setCapabilities(caps());
});
afterEach(() => {
  clearCapabilities();
  target.remove();
});

describe('MOR-2793 Standard face shortcut-hint hosts', () => {
  it('puts data-shortcut-hint on the Standard mode and DATA buttons', () => {
    const props = proxy({ view: base(), presentation: 'standard' as const });
    const component = mount(FilterInstrumentHostFixture, { target, props });
    flushSync();
    const usb = target.querySelector<HTMLButtonElement>('[data-testid="standard-mode-USB"] button');
    const lsb = target.querySelector<HTMLButtonElement>('[data-testid="standard-mode-LSB"] button');
    expect(usb?.getAttribute('data-shortcut-hint')).toBe('1');
    expect(lsb?.getAttribute('data-shortcut-hint')).toBe('2');
    const data = target.querySelector<HTMLButtonElement>('[data-testid="standard-data-mode-0"] button');
    expect(data?.getAttribute('data-shortcut-hint')).toBe('d');
    unmount(component);
  });

  it('puts data-shortcut-hint on the grouped mode, filter and DATA buttons', () => {
    const props = proxy({ view: base(), presentation: 'grouped' as const });
    const component = mount(FilterInstrumentHostFixture, { target, props });
    flushSync();
    expect(target.querySelector('[data-testid="filter-mode-USB"]')?.getAttribute('data-shortcut-hint')).toBe('1');
    // FilterPanel's cycle-filter hint joins both step directions.
    expect(target.querySelector('[data-testid="filter-select-1"]')?.getAttribute('data-shortcut-hint')).toBe('f / F');
    expect(target.querySelector('[data-testid="filter-data-mode-0"]')?.getAttribute('data-shortcut-hint')).toBe('d');
    unmount(component);
  });

  it('puts data-shortcut-hint on RIT, XIT and CLEAR', () => {
    const props = proxy({ view: base(), presentation: 'grouped' as const });
    const component = mount(RitXitScanInstrumentHostFixture, { target, props });
    flushSync();
    expect(target.querySelector('[data-testid="ritxit-rit-toggle"]')?.getAttribute('data-shortcut-hint')).toBe('r');
    expect(target.querySelector('[data-testid="ritxit-xit-toggle"]')?.getAttribute('data-shortcut-hint')).toBe('x');
    expect(target.querySelector('[data-testid="ritxit-clear"]')?.getAttribute('data-shortcut-hint')).toBe('c');
    unmount(component);
  });

  it('wires getShortcutHint into the Standard RF, AF and filter hosts', () => {
    expect(HOST_SOURCES.filter).toContain("from '../components-v2/layout/shortcut-hints'");
    expect(HOST_SOURCES.filter).toContain("getShortcutHint('mode_select'");
    expect(HOST_SOURCES.filter).toContain("getShortcutHint('cycle_data_mode'");
    expect(HOST_SOURCES.filter).toContain('cycleFilterShortcut');
    expect(HOST_SOURCES.ritXit).toContain("getShortcutHint('toggle_rit'");
    expect(HOST_SOURCES.ritXit).toContain("getShortcutHint('toggle_xit'");
    expect(HOST_SOURCES.ritXit).toContain("getShortcutHint('clear_rit_xit'");
    expect(HOST_SOURCES.rf).toContain("getShortcutHint('adjust_rf_gain'");
    expect(HOST_SOURCES.rf).toContain("getShortcutHint('cycle_att'");
    expect(HOST_SOURCES.rf).toContain("getShortcutHint('cycle_preamp'");
    expect(HOST_SOURCES.rxAudio).toContain("getShortcutHint('adjust_af_level'");
  });
});
