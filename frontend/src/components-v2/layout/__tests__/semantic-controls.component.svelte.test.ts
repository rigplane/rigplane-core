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

function driveLevel(root: HTMLElement, value: number): HTMLElement {
  const slider = root.querySelector<HTMLElement>('[role="slider"]')!;
  const frame = slider.closest<HTMLElement>('.vc-hbar')!;
  frame.getBoundingClientRect = () => ({ left: 0, width: 100 } as DOMRect);
  Object.assign(slider, {
    setPointerCapture: vi.fn(), hasPointerCapture: () => true, releasePointerCapture: vi.fn(),
  });
  slider.dispatchEvent(new PointerEvent('pointerdown', {
    bubbles: true, clientX: value * 100, pointerId: 1,
  }));
  slider.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
  flushSync();
  return slider;
}

const TITLES = {
  rxTx: 'TX', txAux: 'TX CONTROLS', meters: 'STATION METERS', rxAudio: 'RX AUDIO',
  filter: 'MODE / FILTER', dsp: 'DSP', rfFrontEnd: 'RF FRONT END', band: 'BAND',
  antenna: 'ANTENNA', ritXitScan: 'RIT / XIT / SCAN', cwKeyer: 'CW', memory: 'MEMORY',
};

const PANEL_IDS = {
  rxTx: 'semantic-rx-tx', txAux: 'semantic-tx-aux', meters: 'semantic-meters',
  rxAudio: 'semantic-rx-audio', filter: 'semantic-filter', dsp: 'semantic-dsp',
  rfFrontEnd: 'semantic-rf-front-end', band: 'semantic-band',
  antenna: 'semantic-antenna', ritXitScan: 'semantic-rit-xit-scan',
  cwKeyer: 'semantic-cw', memory: 'semantic-memory',
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

  it.each(Object.keys(PANEL_IDS))('enables stable collapse and drag chrome for Standard %s', async (surface) => {
    const panelId = PANEL_IDS[surface as keyof typeof PANEL_IDS];
    const onDragStart = vi.fn();
    const r = render(surface as SemanticSurfaceName, base(), {
      chrome: { panelId, draggable: true, onDragStart, style: 'order:3;' },
    });
    const panel = r.target.querySelector<HTMLElement>('.collapsible-panel')!;
    expect(panel.dataset.panelId).toBe(panelId);
    expect(panel.closest<HTMLElement>('.semantic-control-panel')!.style.order).toBe('3');
    expect(r.target.querySelector('.drag-handle')).not.toBeNull();
    const header = r.target.querySelector<HTMLButtonElement>('.panel-header')!;
    expect(header.disabled).toBe(false);
    header.click();
    flushSync();
    expect(panel.dataset.collapsed).toBe('true');
    expect(JSON.parse(localStorage.getItem('rigplane:panel-collapsed')!))
      .toMatchObject({ [panelId]: true });
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

  it('preserves the rendered slider and emits the existing RF intent once', async () => {
    const onPreampChange = vi.fn();
    const onLevelChange = vi.fn();
    const r = render('rfFrontEnd', base(), { onPreampChange, onLevelChange });
    (r.target.querySelector('[data-testid="rf-front-end-preamp-1"]') as HTMLButtonElement).click();
    expect(onPreampChange).toHaveBeenCalledExactlyOnceWith(1);
    const level = r.target.querySelector('[data-testid="rf-front-end-rfGain"]') as HTMLElement;
    const slider = driveLevel(level, 0.7);
    expect(slider.closest<HTMLElement>('.vc-hbar')!.style
      .getPropertyValue('--vc-fill-percent')).toBe('70%');
    expect(onLevelChange).toHaveBeenCalledTimes(1);
    expect(onLevelChange.mock.calls[0][0]).toBe('rfGain');
    expect(onLevelChange.mock.calls[0][1]).toBeCloseTo(0.7, 12);
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
    const level = r.target.querySelector('[data-testid="rf-front-end-rfGain"]') as HTMLElement;
    const slider = driveLevel(level, 0.5);
    expect(slider.getAttribute('aria-disabled')).toBe('true');
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
