import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { readFileSync } from 'node:fs';

type Slot =
  | { kind: 'slotted'; id: 'A' | 'B' }
  | { kind: 'relative'; role: 'selected' | 'unselected' }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };

const h = vi.hoisted(() => ({
  freqKHz: 14_074.125 as unknown,
  read: vi.fn(),
  onFreqChange: vi.fn(),
  onModeChange: vi.fn(),
  rawSend: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getVfoHandlers: () => ({
    onFreqChange: h.onFreqChange,
    onModeChange: h.onModeChange,
  }),
  bindVfoTunerContext: () => Object.freeze({ read: h.read }),
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.rawSend,
}));

import EiBiBrowser from '../EiBiBrowser.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

function view(
  receiver: 'MAIN' | 'SUB' = 'MAIN',
  slot: Slot = { kind: 'slotted', id: 'A' },
  frequencyHz: number | null = receiver === 'MAIN' ? 14_074_000 : 7_100_000,
) {
  return {
    activeReceiver: { status: 'known', receiver },
    vfos: [{
      receiver, slot, label: receiver, frequencyHz, mode: 'USB', filter: 'FIL1',
      isActive: true, isActiveSlot: true, isTxTarget: false,
    }],
    disabledReasons: [],
  };
}

async function mountBrowser(freqKHz: unknown = 14_074.125): Promise<HTMLTableRowElement> {
  h.freqKHz = freqKHz;
  component = mount(EiBiBrowser, { target, props: { visible: true } });
  flushSync();
  await vi.waitFor(() => {
    expect(target.querySelector('.station-row')).not.toBeNull();
  });
  return target.querySelector<HTMLTableRowElement>('.station-row')!;
}

function doubleClick(row: HTMLTableRowElement): void {
  row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  h.read.mockReset();
  h.read.mockReturnValue({ view: view() });
  h.onFreqChange.mockReset();
  h.onModeChange.mockReset();
  h.rawSend.mockReset();
  h.fetch.mockReset();
  h.fetch.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/status')) {
      return { ok: true, json: async () => ({ loaded: true, languages: [], countries: [] }) } as Response;
    }
    if (url.includes('/bands')) {
      return { ok: true, json: async () => ({ bands: [] }) } as Response;
    }
    if (url.includes('/stations')) {
      return {
        ok: true,
        json: async () => ({
          stations: [{
            freq_khz: h.freqKHz,
            station: 'Authority Test', language_name: 'English', language: 'E',
            time_str: '0000-2400', days: '', target: 'Test', country: 'T',
            band: 'test', remarks: '', on_air: true,
          }],
          total: 1,
          pages: 1,
        }),
      } as Response;
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal('fetch', h.fetch);
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
  vi.unstubAllGlobals();
});

describe('MOR-1409 A05b EiBi tune authority', () => {
  it('has no direct transport/command edge and binds the reviewed VFO seam', () => {
    const source = readFileSync('src/components/spectrum/EiBiBrowser.svelte', 'utf8');
    expect(source).toContain('getVfoHandlers');
    expect(source).toContain('bindVfoTunerContext');
    expect(source).not.toMatch(/(?:transport\/ws-client|sendCommand|runtime\.send|dispatchRadioIntent)/);
    expect(source).not.toMatch(/(?:panel-commands|command-bus|radio-intents|\$lib\/stores)/);
  });

  it.each([
    ['MAIN slotted', 'MAIN', { kind: 'slotted', id: 'A' }, 0],
    ['real SUB unslotted', 'SUB', { kind: 'unslotted' }, 1],
    ['one-RX MAIN relative-selected', 'MAIN', { kind: 'relative', role: 'selected' }, 0],
  ] as const)('reads once and emits one exact rounded frequency for %s', async (_name, receiver, slot, expected) => {
    h.read.mockReturnValue({ view: view(receiver, slot) });
    const row = await mountBrowser(14_074.1254);
    doubleClick(row);
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_074_125, expected);
    expect(h.onModeChange).not.toHaveBeenCalled();
    expect(h.rawSend).not.toHaveBeenCalled();
  });

  it('keeps the real expanded-row Tune button on the same one-callback path', async () => {
    const row = await mountBrowser(7_155.4996);
    row.click();
    await tick();
    const tune = target.querySelector<HTMLButtonElement>('.tune-btn');
    expect(tune).not.toBeNull();
    tune!.click();
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).toHaveBeenCalledExactlyOnceWith(7_155_500, 0);
  });

  it('performs a fresh single read for every successive double-click', async () => {
    h.read
      .mockReturnValueOnce({ view: view('MAIN', { kind: 'unslotted' }) })
      .mockReturnValueOnce({ view: view('SUB', { kind: 'unslotted' }) });
    const row = await mountBrowser(7_100.5);
    doubleClick(row);
    doubleClick(row);
    expect(h.read).toHaveBeenCalledTimes(2);
    expect(h.onFreqChange.mock.calls).toEqual([[7_100_500, 0], [7_100_500, 1]]);
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
  ])('rejects %s with zero callbacks', async (_name, candidate) => {
    h.read.mockReturnValue({ view: candidate });
    const row = await mountBrowser();
    doubleClick(row);
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).not.toHaveBeenCalled();
    expect(h.onModeChange).not.toHaveBeenCalled();
    expect(h.rawSend).not.toHaveBeenCalled();
  });

  it.each([
    ['numeric string', '7100.5'],
    ['NaN', Number.NaN],
    ['positive infinity', Number.POSITIVE_INFINITY],
    ['negative infinity', Number.NEGATIVE_INFINITY],
    ['zero', 0],
    ['negative', -1],
    ['positive rounded zero', 0.0001],
    ['unsafe result', Number.MAX_SAFE_INTEGER / 1000 + 1],
  ])('rejects %s before any callback', async (_name, freqKHz) => {
    const row = await mountBrowser(freqKHz);
    doubleClick(row);
    expect(h.read).toHaveBeenCalledOnce();
    expect(h.onFreqChange).not.toHaveBeenCalled();
    expect(h.onModeChange).not.toHaveBeenCalled();
    expect(h.rawSend).not.toHaveBeenCalled();
  });
});
