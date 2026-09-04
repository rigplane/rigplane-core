import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type {
  DisplayFact,
  IcomTouchscreenDisplayModel,
  ReceiverInstrumentDisplayModel,
  ReceiverScopeDisplay,
} from '../../../semantic/icom-touchscreen-display-model';
import IcomTouchscreenFace from '../IcomTouchscreenFace.svelte';

const unknownScope: DisplayFact<ReceiverScopeDisplay> = { status: 'unknown' };

function receiver(
  id: 'MAIN' | 'SUB',
  frequencyHz: number,
  scope: DisplayFact<ReceiverScopeDisplay> = unknownScope,
): ReceiverInstrumentDisplayModel {
  return {
    receiver: id,
    receiverActivity: id === 'MAIN' ? 'active' : 'inactive',
    frequencyHz: { status: 'known', value: frequencyHz },
    mode: { status: 'known', value: id === 'MAIN' ? 'LSB' : 'USB' },
    filter: { status: 'known', value: id === 'MAIN' ? 'FIL2' : 'FIL1' },
    bandwidthHz: { status: 'known', value: id === 'MAIN' ? 2_400 : 3_100 },
    sMeter: { status: 'unknown' },
    sMeterTicks: [],
    scope,
  };
}

const model = (): IcomTouchscreenDisplayModel => ({
  receivers: [receiver('MAIN', 7_070_000), receiver('SUB', 14_250_000)],
});

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null;
const context = {
  clearRect: vi.fn(),
  setTransform: vi.fn(),
};

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    context as unknown as CanvasRenderingContext2D,
  );
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('IcomTouchscreenFace', () => {
  it('mounts exactly two instances of the same cluster without crossing receiver facts', () => {
    component = mount(IcomTouchscreenFace, { target, props: { model: model() } });
    flushSync();
    const clusters = target.querySelectorAll('[data-receiver-cluster]');
    expect(clusters).toHaveLength(2);
    expect(clusters[0].getAttribute('data-receiver-cluster')).toBe('MAIN');
    expect(clusters[1].getAttribute('data-receiver-cluster')).toBe('SUB');
    expect(clusters[0].querySelector('[data-frequency-state]')?.textContent).toContain('7 070 000');
    expect(clusters[1].querySelector('[data-frequency-state]')?.textContent).toContain('14 250 000');
    expect(clusters[0].textContent).toContain('2400 Hz');
    expect(clusters[1].textContent).toContain('3100 Hz');
  });

  it('keeps absent scope frames empty and never draws decorative peaks', () => {
    component = mount(IcomTouchscreenFace, { target, props: { model: model() } });
    flushSync();
    expect(target.querySelectorAll('[data-scope-empty]')).toHaveLength(2);
    expect(target.querySelectorAll('[data-supplied-pixels="0"]')).toHaveLength(2);
    expect(context.clearRect).toHaveBeenCalled();
    expect('lineTo' in context).toBe(false);
  });

  it('keeps meter geometry stable across known, unknown, unsupported, and inactive truth', () => {
    const states: ReceiverInstrumentDisplayModel['sMeter'][] = [
      { status: 'known', value: { fraction: 0.4, label: 'S5' } },
      { status: 'unknown' },
      { status: 'unsupported' },
      { status: 'inactive' },
    ];
    for (const state of states) {
      const base = model();
      const candidate: IcomTouchscreenDisplayModel = {
        receivers: [{ ...base.receivers[0], sMeter: state }, base.receivers[1]],
      };
      component = mount(IcomTouchscreenFace, { target, props: { model: candidate } });
      flushSync();
      const meter = target.querySelector('[data-receiver-cluster="MAIN"] [data-meter-state]');
      expect(meter?.getAttribute('data-meter-state')).toBe(state.status);
      expect(meter?.getAttribute('viewBox')).toBe('0 0 240 112');
      expect(meter?.querySelector('[data-needle]') !== null).toBe(state.status === 'known');
      if (component) unmount(component);
      component = null;
      target.innerHTML = '';
    }
  });

  it('marks every visual control as disabled, fixture-only, and unwired', () => {
    component = mount(IcomTouchscreenFace, { target, props: { model: model() } });
    flushSync();
    const controls = [...target.querySelectorAll<HTMLButtonElement>('button')];
    expect(controls.length).toBeGreaterThan(0);
    expect(controls.every((control) => control.disabled)).toBe(true);
    expect(controls.every((control) => control.dataset.controlContract === 'unwired')).toBe(true);
    expect(controls.every((control) => control.dataset.fixtureOnly === 'true')).toBe(true);
    expect(target.querySelector('[data-tx-authority="external"]')).not.toBeNull();
    expect(target.textContent).not.toContain('PTT');
  });

  it('delegates sibling and scope placement to the face while clusters remain coordinate-free', () => {
    const faceSource = readFileSync('src/skins/icom-touchscreen/IcomTouchscreenFace.svelte', 'utf8');
    const clusterSource = readFileSync('src/skins/icom-touchscreen/ReceiverInstrumentCluster.svelte', 'utf8');
    expect(faceSource.match(/<ReceiverInstrumentCluster/g)).toHaveLength(2);
    expect(faceSource).toContain('grid-area: main');
    expect(faceSource).toContain('grid-area: sub');
    expect(faceSource).toContain('grid-area: scope-main');
    expect(faceSource).toContain('grid-area: scope-sub');
    expect(clusterSource).not.toMatch(/position:\s*absolute|grid-area:/);
  });

  it('pins the component-local style blocks and the passive import boundary', () => {
    const files = [
      'IcomTouchscreenFace.svelte',
      'ReceiverInstrumentCluster.svelte',
      'ReceiverNeedleSMeter.svelte',
      'ReceiverScopePane.svelte',
    ];
    for (const file of files) {
      const source = readFileSync(`src/skins/icom-touchscreen/${file}`, 'utf8');
      expect(source).toContain('<style>');
      expect(source).not.toMatch(/\$lib\/(stores|transport)|lib\/runtime\/commands|dispatchRadioIntent|requestKey|requestUnkey|from ['"][^'"]*fixtures/i);
    }

    const scopeSource = readFileSync(
      'src/skins/icom-touchscreen/ReceiverScopePane.svelte',
      'utf8',
    );
    expect(scopeSource).toContain('spanHz: 0');
    expect(scopeSource).toContain('centerHz: 0');
  });
});
