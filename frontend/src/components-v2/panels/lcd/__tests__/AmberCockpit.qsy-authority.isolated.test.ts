import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createSubscriber } from 'svelte/reactivity';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';

type Slot =
  | { kind: 'slotted'; id: 'A' | 'B' }
  | { kind: 'relative'; role: 'selected' | 'unselected' }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };

const h = vi.hoisted(() => ({
  props: {
    radioState: {
      active: 'MAIN',
      main: { freqHz: 14_074_000, mode: 'USB', filter: 1, sMeter: 0 },
      sub: { freqHz: 7_100_000, mode: 'LSB', filter: 1, sMeter: 0 },
    },
    caps: null,
    hasAudioFft: false,
    hasDualReceiver: false,
    hasCapability: () => false,
  } as Record<string, unknown>,
  recent: [{ freqHz: 14_250_000, mode: '', at: 1 }],
  read: vi.fn(),
  onFreqChange: vi.fn(),
  onModeChange: vi.fn(),
  onTuningChange: vi.fn(),
  rawSend: vi.fn(),
  derive: () => h.props,
  notify: () => {},
  acquire: vi.fn(() => Object.freeze({ resource: 'audio-fft', consumer: 'AmberCockpit' })),
  release: vi.fn(),
  subscribe: vi.fn(() => vi.fn()),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberCockpitProps: () => h.derive(),
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: h.onTuningChange }),
  getVfoHandlers: () => ({
    onFreqChange: h.onFreqChange,
    onModeChange: h.onModeChange,
  }),
  bindVfoTunerContext: () => Object.freeze({ read: h.read }),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => h.recent,
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: h.acquire, release: h.release },
  runtime: {
    send: h.rawSend,
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: h.subscribe,
    },
  },
}));

import AmberCockpit from '../AmberCockpit.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

const available = Object.freeze({ structural: true, operational: true });

function view(
  receiver: 'MAIN' | 'SUB' = 'MAIN',
  slot: Slot = { kind: 'slotted', id: 'A' },
  frequencyHz: number | null = receiver === 'MAIN' ? 14_074_000 : 7_100_000,
  modeChoices: readonly string[] = ['USB', 'LSB'],
) {
  return {
    activeReceiver: { status: 'known', receiver },
    vfos: [{
      receiver, slot, label: receiver, frequencyHz, mode: 'USB', filter: 'FIL1',
      isActive: true, isActiveSlot: true, isTxTarget: false,
    }],
    disabledReasons: [],
    modeFilter: {
      currentMode: { reading: { status: 'known', value: 'USB' }, availability: available },
      modeChoices,
    },
  };
}

function mountCockpit(freqHz = 14_250_000, mode = ''): HTMLButtonElement {
  h.recent = [{ freqHz, mode, at: 1 }];
  component = mount(AmberCockpit, { target });
  flushSync();
  const button = target.querySelector<HTMLButtonElement>('.slot-qsy');
  if (!button) throw new Error('reachable QSY chip did not mount');
  return button;
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  h.props = {
    radioState: {
      active: 'MAIN',
      main: { freqHz: 14_074_000, mode: 'USB', filter: 1, sMeter: 0 },
      sub: { freqHz: 7_100_000, mode: 'LSB', filter: 1, sMeter: 0 },
    },
    caps: null,
    hasAudioFft: false,
    hasDualReceiver: false,
    hasCapability: () => false,
  };
  let update = () => {};
  const subscribe = createSubscriber((notify) => {
    update = notify;
    return () => {};
  });
  h.derive = () => {
    subscribe();
    return h.props;
  };
  h.notify = () => update();
  h.read.mockReset();
  h.read.mockReturnValue({ view: view() });
  for (const mock of [
    h.onFreqChange, h.onModeChange, h.onTuningChange, h.rawSend,
    h.acquire, h.release, h.subscribe,
  ]) mock.mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
});

describe('MOR-1409 A05b Amber QSY authority', () => {
  it('retains the FFT runtime owner but routes QSY only through adapter-bound VFO callbacks', () => {
    const source = readFileSync('src/components-v2/panels/lcd/AmberCockpit.svelte', 'utf8');
    expect(source).toContain('getVfoHandlers');
    expect(source).toContain('bindVfoTunerContext');
    expect(source).toContain('runtime.scope.registerPresentationDriver');
    expect(source).not.toMatch(/\bruntime\.send\s*\(/);
    expect(source).not.toMatch(/(?:sendCommand|dispatchRadioIntent|panel-commands|command-bus)/);
    expect(source).not.toMatch(/(?:getCommandLifecycles|onCommandDelivery|onControlSessionTransition)/);
  });

  it.each([
    ['MAIN slotted', 'MAIN', { kind: 'slotted', id: 'A' }, 0],
    ['SUB unslotted', 'SUB', { kind: 'unslotted' }, 1],
    ['one-RX MAIN relative-selected', 'MAIN', { kind: 'relative', role: 'selected' }, 0],
  ] as const)('reads once and sends one empty-mode frequency callback for %s', (_name, receiver, slot, expected) => {
    h.read.mockReturnValue({ view: view(receiver, slot) });
    mountCockpit(14_250_000, '').click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_250_000, expected);
    expect(h.onModeChange).not.toHaveBeenCalled();
    expect(h.rawSend).not.toHaveBeenCalled();
  });

  it('preflights a non-empty mode atomically, then emits exact frequency-before-mode order to one receiver', () => {
    const order: string[] = [];
    h.onFreqChange.mockImplementation(() => { order.push('freq'); });
    h.onModeChange.mockImplementation(() => { order.push('mode'); });
    h.read.mockReturnValue({ view: view('SUB', { kind: 'slotted', id: 'B' }) });
    mountCockpit(7_155_000, 'LSB').click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).toHaveBeenCalledExactlyOnceWith(7_155_000, 1);
    expect(h.onModeChange).toHaveBeenCalledExactlyOnceWith('LSB', 1);
    expect(order).toEqual(['freq', 'mode']);
    expect(h.rawSend).not.toHaveBeenCalled();
  });

  it('performs a fresh single read for every successive click', () => {
    h.read
      .mockReturnValueOnce({ view: view('MAIN', { kind: 'unslotted' }) })
      .mockReturnValueOnce({ view: view('SUB', { kind: 'unslotted' }) });
    const qsy = mountCockpit(14_250_000, '');
    qsy.click();
    qsy.click();
    expect(h.read).toHaveBeenCalledTimes(2);
    expect(h.onFreqChange.mock.calls).toEqual([[14_250_000, 0], [14_250_000, 1]]);
  });

  it.each([
    ['null view', null],
    ['unknown receiver', { ...view(), activeReceiver: { status: 'unknown' } }],
    ['impossible SUB', {
      ...view('SUB'), disabledReasons: [{ field: 'receiver.SUB', code: 'capability-unavailable' }],
    }],
    ['zero active positions', { ...view(), vfos: view().vfos.map((v) => ({ ...v, isActive: false })) }],
    ['multiple active positions', { ...view(), vfos: [...view().vfos, { ...view().vfos[0] }] }],
    ['null active frequency', view('MAIN', { kind: 'slotted', id: 'A' }, null)],
    ['unknown slot', view('MAIN', { kind: 'unknown' })],
    ['relative unselected', view('MAIN', { kind: 'relative', role: 'unselected' })],
  ])('rejects %s with zero callbacks', (_name, candidate) => {
    h.read.mockReturnValue({ view: candidate });
    mountCockpit().click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).not.toHaveBeenCalled();
    expect(h.onModeChange).not.toHaveBeenCalled();
    expect(h.rawSend).not.toHaveBeenCalled();
  });

  it.each([
    Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 0, -1, 1.5,
    Number.MAX_SAFE_INTEGER + 1,
  ])('rejects invalid QSY frequency %s before any callback', (freqHz) => {
    mountCockpit(freqHz, '').click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).not.toHaveBeenCalled();
    expect(h.onModeChange).not.toHaveBeenCalled();
  });

  it.each([
    ['missing group', undefined],
    ['unavailable current mode', {
      currentMode: { reading: { status: 'known', value: 'USB' }, availability: { structural: true, operational: false } },
      modeChoices: ['USB'],
    }],
    ['mode outside exact choices', {
      currentMode: { reading: { status: 'known', value: 'USB' }, availability: available },
      modeChoices: ['USB'],
    }],
  ])('rejects non-empty mode atomically for %s', (_name, modeFilter) => {
    h.read.mockReturnValue({ view: { ...view(), modeFilter } });
    mountCockpit(14_250_000, _name === 'mode outside exact choices' ? 'LSB' : 'USB').click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).not.toHaveBeenCalled();
    expect(h.onModeChange).not.toHaveBeenCalled();
  });

  it('does not turn click/lifecycle-shaped noise into QSY history and records only a later view observation', () => {
    const qsy = mountCockpit(14_250_000, 'USB');
    const beforeClick = h.onTuningChange.mock.calls.length;
    qsy.click();
    expect(h.onTuningChange).toHaveBeenCalledTimes(beforeClick);

    h.props = {
      ...h.props,
      radioState: {
        ...(h.props.radioState as Record<string, unknown>),
        main: { freqHz: 14_300_000, mode: 'USB', filter: 1, sMeter: 0 },
      },
    };
    h.notify();
    flushSync();
    expect(h.onTuningChange).toHaveBeenLastCalledWith(14_300_000, 'USB');
    expect(h.onTuningChange.mock.calls.length).toBe(beforeClick + 1);
  });
});
