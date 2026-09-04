import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
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
  beginPath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  stroke: vi.fn(),
};

function cssRule(source: string, selector: string): string {
  const style = source.match(/<style>([\s\S]*?)<\/style>/)?.[1];
  expect(style, 'component must retain a local style block').toBeDefined();
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const declarations = style?.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1];
  expect(declarations, `missing critical CSS selector: ${selector}`).toBeDefined();
  return declarations ?? '';
}

function resolveRuntimeImport(fromFile: string, specifier: string): string | null {
  const unresolved = specifier.startsWith('.')
    ? resolve(dirname(fromFile), specifier)
    : specifier.startsWith('$lib/')
      ? resolve('src/lib', specifier.slice('$lib/'.length))
      : null;
  if (!unresolved) return null;
  const candidates = [
    unresolved,
    `${unresolved}.ts`,
    `${unresolved}.svelte`,
    resolve(unresolved, 'index.ts'),
    resolve(unresolved, 'index.svelte'),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function runtimeImportGraph(roots: readonly string[]): Set<string> {
  const visited = new Set<string>();
  const pending = roots.map((root) => resolve(root));
  const importPattern = /\bimport\s+(?!type\b)(?:[^'";]*?\sfrom\s*)?['"]([^'"]+)['"]/g;

  while (pending.length > 0) {
    const current = pending.pop();
    if (!current || visited.has(current)) continue;
    visited.add(current);
    const source = readFileSync(current, 'utf8');
    for (const match of source.matchAll(importPattern)) {
      const dependency = resolveRuntimeImport(current, match[1]);
      if (dependency && !visited.has(dependency)) pending.push(dependency);
    }
  }
  return visited;
}

beforeEach(() => {
  vi.clearAllMocks();
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

  it('keeps absent scope frames empty and never draws decorative peaks', async () => {
    component = mount(IcomTouchscreenFace, { target, props: { model: model() } });
    flushSync();
    await tick();
    await Promise.resolve();
    expect(target.querySelectorAll('[data-scope-empty]')).toHaveLength(2);
    expect(target.querySelectorAll('[data-supplied-pixels="0"]')).toHaveLength(2);
    expect(context.clearRect).toHaveBeenCalled();
    expect(context.lineTo).not.toHaveBeenCalled();
  });

  it('draws one supplied scope trace with backed bounds and no instrument decoration', async () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'clientWidth', 'get').mockReturnValue(100);
    vi.spyOn(HTMLCanvasElement.prototype, 'clientHeight', 'get').mockReturnValue(50);
    const knownScope: DisplayFact<ReceiverScopeDisplay> = {
      status: 'known',
      value: {
        pixels: Uint8Array.from([8, 24, 42, 16]),
        startHz: 7_020_000,
        endHz: 7_120_000,
      },
    };
    const candidate: IcomTouchscreenDisplayModel = {
      receivers: [receiver('MAIN', 7_070_000, knownScope), receiver('SUB', 14_250_000)],
    };

    component = mount(IcomTouchscreenFace, { target, props: { model: candidate } });
    flushSync();
    await tick();
    await Promise.resolve();

    expect(target.querySelector('[data-scope-receiver="MAIN"] [data-scope-axis]')?.textContent)
      .toContain('7.020 MHz');
    expect(target.querySelector('[data-scope-receiver="MAIN"] [data-scope-axis]')?.textContent)
      .toContain('7.120 MHz');
    expect(context.beginPath).toHaveBeenCalledTimes(1);
    expect(context.moveTo).toHaveBeenCalledTimes(1);
    expect(context.lineTo).toHaveBeenCalledTimes(99);
    expect(context.stroke).toHaveBeenCalledTimes(1);
    expect('fillRect' in context).toBe(false);
    expect('fillText' in context).toBe(false);
    expect('setLineDash' in context).toBe(false);
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

  it('pins critical face, cluster, meter, scope, and disabled-control styles', () => {
    const face = readFileSync('src/skins/icom-touchscreen/IcomTouchscreenFace.svelte', 'utf8');
    const faceRule = cssRule(face, '.face');
    expect(faceRule).toContain('display: grid');
    expect(faceRule).toContain('aspect-ratio: 1734 / 1000');
    expect(faceRule).toContain('grid-template-columns: 10.6% minmax(0, 1fr) minmax(0, 1fr)');
    expect(faceRule).toContain("'rail main sub'");
    expect(faceRule).toContain("'rail scope-main scope-sub'");
    expect(cssRule(face, '.left-rail')).toContain('repeat(7, minmax(0, 1fr))');
    expect(cssRule(face, '.cluster-placement--main')).toContain('grid-area: main');
    expect(cssRule(face, '.cluster-placement--sub')).toContain('grid-area: sub');
    expect(cssRule(face, '.scope-placement--main')).toContain('grid-area: scope-main');
    expect(cssRule(face, '.scope-placement--sub')).toContain('grid-area: scope-sub');
    expect(cssRule(face, '.bottom-rail')).toContain('repeat(7, minmax(0, 1fr))');
    expect(cssRule(face, "button:disabled[data-control-contract='unwired']"))
      .toContain('color: #9da9a8');

    const cluster = readFileSync('src/skins/icom-touchscreen/ReceiverInstrumentCluster.svelte', 'utf8');
    expect(cssRule(cluster, '.cluster')).toContain('display: grid');
    expect(cssRule(cluster, '.cluster')).toContain('grid-template-rows: auto minmax(5.4rem, 0.75fr)');
    expect(cssRule(cluster, ".cluster[data-receiver-activity='inactive']"))
      .toContain('border-color: #3d494b');
    expect(cssRule(cluster, ".cluster[data-receiver-activity='unknown']"))
      .toContain('border-style: dashed');
    expect(cssRule(cluster, '.unwired-row')).toContain('repeat(4, minmax(0, 1fr))');
    expect(cssRule(cluster, "button:disabled[data-control-contract='unwired']"))
      .toContain('background: #151c1d');

    const meter = readFileSync('src/skins/icom-touchscreen/ReceiverNeedleSMeter.svelte', 'utf8');
    expect(cssRule(meter, '.needle-meter')).toContain('min-height: 0');
    expect(cssRule(meter, ".needle-meter[data-meter-state='unknown'] .unavailable"))
      .toContain('fill: #7f9190');
    expect(cssRule(meter, ".needle-meter[data-meter-state='inactive']"))
      .toContain('opacity: 0.48');
    expect(cssRule(meter, ".needle-meter[data-meter-state='unsupported']"))
      .toContain('opacity: 0.28');

    const scope = readFileSync('src/skins/icom-touchscreen/ReceiverScopePane.svelte', 'utf8');
    expect(cssRule(scope, '.scope-pane')).toContain('overflow: hidden');
    expect(cssRule(scope, 'output')).toContain('position: absolute');
    expect(cssRule(scope, 'output')).toContain('inset: 0');
    expect(cssRule(scope, '.axis')).toContain('pointer-events: none');
  });

  it('keeps the complete generated production runtime graph dependency-clean', () => {
    const graph = runtimeImportGraph([
      'src/semantic/icom-touchscreen-display-model.ts',
      'src/skins/icom-touchscreen/IcomTouchscreenFace.svelte',
      'src/skins/icom-touchscreen/ReceiverInstrumentCluster.svelte',
      'src/skins/icom-touchscreen/ReceiverNeedleSMeter.svelte',
      'src/skins/icom-touchscreen/ReceiverScopePane.svelte',
      'src/skins/icom-touchscreen/receiver-scope-trace.ts',
    ]);
    const relativeGraph = [...graph].map((file) => file.replace(`${resolve('.')}\/`, ''));
    expect(relativeGraph).toContain('src/skins/icom-touchscreen/receiver-scope-trace.ts');
    expect(relativeGraph).not.toContain('src/lib/renderers/spectrum-renderer.ts');
    expect(relativeGraph.filter((file) => /\/(stores|runtime|transport|commands?)(\/|\.)/i.test(file)))
      .toEqual([]);
    expect(relativeGraph.filter((file) => /(^|\/)fixtures?\//i.test(file))).toEqual([]);
    for (const file of graph) {
      expect(readFileSync(file, 'utf8')).not.toMatch(
        /dispatchRadioIntent|sendCommand|requestKey|requestUnkey/i,
      );
    }
  });
});
