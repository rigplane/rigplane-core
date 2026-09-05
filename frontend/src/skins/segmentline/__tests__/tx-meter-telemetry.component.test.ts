import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { DisplayObservation, MeterRfState } from '../../../semantic/radio-view-model';
import { projectPeerSplitDisplay, type PeerSplitDisplayModel } from '../../../semantic/radio-display-model';
import { topologyFixtures, withMeters } from '../../../semantic/fixtures/topologies';
import PeerSplitDisplay from '../PeerSplitDisplay.svelte';
import DominantUnifiedDisplay from '../DominantUnifiedDisplay.svelte';
import CenterstageDisplay from '../CenterstageDisplay.svelte';
import PanadapterDisplay from '../PanadapterDisplay.svelte';

import { clearCapabilities } from '$lib/stores/capabilities.svelte';
afterEach(clearCapabilities);

const variants = [PeerSplitDisplay, DominantUnifiedDisplay, CenterstageDisplay, PanadapterDisplay];
const names = ['peer-split', 'dominant-unified', 'centerstage', 'panadapter'];
const targets = ['PWR', 'SWR', 'ALC'];
function model(rf: MeterRfState, relevant: boolean, observation: DisplayObservation<number>, structural = true) {
  const view = withMeters(topologyFixtures['2/main_sub'], rf);
  for (const key of ['power', 'swr', 'alc'] as const) view.meters![key] = {
    availability: { structural, operational: true }, reading: { status: 'known', value: 87.654 },
    relevant, display: observation,
  };
  return projectPeerSplitDisplay(view);
}
const observations = [{ state: 'current', value: 12.345 }, { state: 'stale', value: 87.654 },
  { state: 'unknown', reason: 'not-observed' }] as const;
function slot(root: HTMLElement, label: string): HTMLElement | undefined {
  return [...root.querySelectorAll('small')].find((node) => node.textContent === label)?.parentElement ?? undefined;
}
for (const [index, Component] of variants.entries()) describe(names[index], () => {
  for (const structural of [false, true]) for (const rf of ['receiving', 'transmitting', 'uncertain', 'unknown'] as MeterRfState[])
    for (const relevant of [false, true]) for (const observation of observations) {
      it(`${structural}/${rf}/${relevant}/${observation.state}`, () => {
        const root = document.createElement('div');
        document.body.append(root);
        const component = mount(Component, { target: root, props: { model: model(rf, relevant, observation, structural) } });
        flushSync();
        try {
          for (const label of targets) {
            const el = slot(root, label);
            if (!structural) {
              expect(el).toBeUndefined();
              continue;
            }
            expect(el).toBeDefined();
            const idle = rf === 'receiving' && !relevant;
            const indeterminate = !idle && !(rf === 'transmitting' && relevant);
            const expected = idle ? 'IDLE' : observation.state === 'stale' ? 'STALE'
              : observation.state === 'unknown' ? '?' : `12 raw${indeterminate ? ' ?' : ''}`;
            expect(el!.textContent?.trim()).toBe(`${label} ${expected}`);
            const description = el!.getAttribute('aria-label') ?? '';
            expect(description).toContain(label);
            expect(description).not.toMatch(/87.65/);
            if (idle) expect(description).toContain('Not measuring in RX');
            if (indeterminate) expect(description).toContain('RF relevance indeterminate');
            if (!idle && observation.state === 'stale') expect(description).toContain('Stale observation');
            if (!idle && observation.state === 'unknown') expect(description).toContain('Not observed');
            if (!idle && observation.state === 'current') expect(description).toContain('12 raw');
          }
          expect(root.querySelectorAll('[data-testid="lcd-tx-scales"]')).toHaveLength(1);
          expect(root.querySelectorAll('[data-tx-scale]')).toHaveLength(structural ? 3 : 0);
          expect(root.querySelectorAll('[data-tx-segment]')).toHaveLength(structural ? 60 : 0);
          expect(root.querySelectorAll('[data-tx-fill]')).toHaveLength(structural && !(rf === 'receiving' && !relevant) && observation.state === 'current' ? 3 : 0);
          expect(root.querySelectorAll('button,input,select,textarea')).toHaveLength(0);
        } finally { unmount(component); root.remove(); }
      });
    }
  it('retains the same slots through current → idle → stale → unknown → zero and preserves unrelated text', () => {
    const props: { model: PeerSplitDisplayModel } = proxy({ model: model('transmitting', true, observations[0]) });
    const root = document.createElement('div'); document.body.append(root);
    const component = mount(Component, { target: root, props }); flushSync();
    try {
      const nodes = targets.map((label) => slot(root, label)!);
      const tracks = [...root.querySelectorAll('[data-tx-segment], [data-tx-scale] svg')];
      const protectedNodes = [...root.querySelectorAll('.frequency,.s-meter,.scope-block,.rf-plot')];
      const other = ['VD', 'ID', 'COMP'].map((label) => slot(root, label)!.textContent);
      for (const [rf, relevant, observation, text] of [
        ['receiving', false, observations[0], 'IDLE'], ['transmitting', true, observations[1], 'STALE'],
        ['transmitting', true, observations[2], '?'], ['transmitting', true, { state: 'current', value: 0 }, '0 raw'],
      ] as const) {
        props.model = model(rf, relevant, observation); flushSync();
        expect([...tracks, ...protectedNodes].every((node) => node.isConnected)).toBe(true);
        expect(nodes.every((node, i) => node === slot(root, targets[i]) && node.isConnected)).toBe(true);
        expect(nodes.map((node) => node.textContent?.trim())).toEqual(targets.map((label) => `${label} ${text}`));
        expect(['VD', 'ID', 'COMP'].map((label) => slot(root, label)!.textContent)).toEqual(other);
      }
    } finally { unmount(component); root.remove(); }
  });
});
