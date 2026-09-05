import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import Harness from './SemanticControlPanelHarness.svelte';
import { topologyFixtures, withRfFrontEnd, withScopeControls, withScopeDisplay } from '../../../semantic/fixtures/topologies';
import { SEMANTIC_SURFACE_NAMES, type SemanticSurfaceName } from '../../../presentation/layouts/contract';
import type { RadioViewModel } from '../../../semantic/radio-view-model';

const base = () => withRfFrontEnd(topologyFixtures['1/single']);
function render(surface: SemanticSurfaceName, view = base(), handlers = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(Harness, { target, props: { surface, view, ...handlers } });
  flushSync();
  return { target, dispose: async () => { await unmount(component); target.remove(); } };
}

const TITLES = {
  rxTx: 'TX', txAux: 'TX CONTROLS', meters: 'STATION METERS', rxAudio: 'RX AUDIO',
  filter: 'MODE / FILTER', dsp: 'DSP', rfFrontEnd: 'RF FRONT END', band: 'BAND',
  antenna: 'ANTENNA', ritXitScan: 'RIT / XIT / SCAN', cwKeyer: 'CW',
};

describe('desktop semantic control frames', () => {
  it.each(Object.entries(TITLES))('gives %s one real panel title without hiding the child', async (surface, title) => {
    const r = render(surface as SemanticSurfaceName);
    const headers = r.target.querySelectorAll('.panel-header .title');
    expect([...headers].map((node) => node.textContent)).toEqual([title]);
    expect(r.target.querySelector('.collapsible-panel')?.getAttribute('data-collapsed')).toBe('false');
    expect(r.target.querySelector('.panel-header')?.getAttribute('disabled')).not.toBeNull();
    if (surface !== 'rfFrontEnd') expect(r.target.querySelectorAll('[data-testid="child"]')).toHaveLength(1);
    await r.dispose();
  });

  it('passes the upper VFO through without control framing', async () => {
    const r = render('vfo');
    expect(r.target.querySelector('.semantic-control-panel')).toBeNull();
    expect(r.target.querySelectorAll('[data-testid="child"]')).toHaveLength(1);
    await r.dispose();
  });

  it.each(['scopeControls', 'scopeDisplay'] as const)('keeps %s in its unframed toolbar/status host', async (surface) => {
    const view = withScopeDisplay(withScopeControls(base()));
    const r = render(surface, view);
    expect(r.target.querySelector('.collapsible-panel')).toBeNull();
    expect(r.target.querySelectorAll(surface === 'scopeControls' ? '.scope-controls-surface' : '.scope-display-surface')).toHaveLength(1);
    expect(r.target.querySelector(surface === 'scopeControls' ? '.desktop-scope-controls' : '.desktop-scope-status')).not.toBeNull();
    await r.dispose();
  });

  it('covers every registered surface and reserves the meter dock hook', async () => {
    expect(Object.keys(TITLES).concat(['vfo', 'scopeControls', 'scopeDisplay']).sort()).toEqual([...SEMANTIC_SURFACE_NAMES].sort());
    const r = render('meters');
    expect(r.target.querySelector('.desktop-station-meters')).not.toBeNull();
    await r.dispose();
  });

  it('preserves native inputs and emits the existing RF intent once', async () => {
    const onPreampChange = vi.fn();
    const onLevelChange = vi.fn();
    const r = render('rfFrontEnd', base(), { onPreampChange, onLevelChange });
    (r.target.querySelector('[data-testid="rf-front-end-preamp-1"]') as HTMLButtonElement).click();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(1);
    const input = r.target.querySelector('[data-testid="rf-front-end-rfGain"] input') as HTMLInputElement;
    expect(input.type).toBe('range');
    input.value = '0.7';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    expect(onLevelChange).toHaveBeenCalledExactlyOnceWith('rfGain', 0.7);
    expect(r.target.querySelectorAll('.rf-front-end-surface')).toHaveLength(1);
    await r.dispose();
  });

  it('retains unknown and disabled RF facts inside the frame', async () => {
    const view = base();
    const unread = { reading: { status: 'unknown' as const }, availability: { structural: true, operational: false } };
    const degraded: RadioViewModel = { ...view, rfFrontEnd: { ...view.rfFrontEnd!, rfGain: unread, preamp: unread } };
    const onPreampChange = vi.fn();
    const onLevelChange = vi.fn();
    const r = render('rfFrontEnd', degraded, { onPreampChange, onLevelChange });
    expect(r.target.querySelector('[data-testid="rf-front-end-preamp-value"]')?.textContent).toBe('?');
    const button = r.target.querySelector('[data-testid="rf-front-end-preamp-1"]') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    button.click();
    const input = r.target.querySelector('[data-testid="rf-front-end-rfGain"] input') as HTMLInputElement;
    expect(input.disabled).toBe(true);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    expect(onPreampChange).not.toHaveBeenCalled();
    expect(onLevelChange).not.toHaveBeenCalled();
    expect(r.target.querySelector('[data-testid="rf-front-end-rfGain"] output')?.textContent).toBe('?');
    await r.dispose();
  });

  it('keeps pending preamp distinct from the observed choice', async () => {
    const r = render('rfFrontEnd', base(), { pendingPreamp: 2 });
    const pending = r.target.querySelector('[data-testid="rf-front-end-preamp-2"]');
    expect(pending?.getAttribute('data-pending')).toBe('true');
    expect(pending?.getAttribute('aria-checked')).toBe('false');
    const row = r.target.querySelector('[data-testid="rf-front-end-preamp"]');
    const describedBy = row?.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy!)?.textContent).toBeTruthy();
    await r.dispose();
  });
});
