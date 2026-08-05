import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

// Mock audio-manager so we can spy on setAudioConfig calls.
const setAudioConfigSpy = vi.fn();
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: (...args: unknown[]) => setAudioConfigSpy(...args),
    getAudioConfig: () => ({
      focus: 'both',
      split_stereo: false,
      main_gain_db: 0,
      sub_gain_db: 0,
    }),
  },
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  receiverLabel: (id: 'MAIN' | 'SUB') => id,
}));

import AudioRoutingControl from '../AudioRoutingControl.svelte';

let target: HTMLElement;
let component: ReturnType<typeof mount>;
let originalLocalStorage: Storage | undefined;

// Install a fresh, in-memory localStorage for each test.  Under vitest's
// shared-context config (isolate: false) some earlier tests stub out
// globals; this guarantees a clean slate regardless of sibling files.
function installLocalStorage(): Map<string, string> {
  const store = new Map<string, string>();
  const stub: Storage = {
    get length() { return store.size; },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => { store.set(k, String(v)); },
    removeItem: (k: string) => { store.delete(k); },
    clear: () => { store.clear(); },
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: stub, writable: true, configurable: true,
  });
  return store;
}

beforeEach(() => {
  setAudioConfigSpy.mockClear();
  originalLocalStorage = (globalThis as any).localStorage;
  installLocalStorage();
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  if (component) unmount(component);
  if (target && target.parentNode) target.parentNode.removeChild(target);
  if (originalLocalStorage !== undefined) {
    Object.defineProperty(globalThis, 'localStorage', {
      value: originalLocalStorage, writable: true, configurable: true,
    });
  }
});

function mountControl() {
  component = mount(AudioRoutingControl, { target });
  flushSync();
}

describe('AudioRoutingControl', () => {
  it('renders three focus buttons with radiogroup a11y', () => {
    mountControl();
    const radiogroup = target.querySelector('[role="radiogroup"]');
    expect(radiogroup).not.toBeNull();
    const radios = target.querySelectorAll('[role="radio"]');
    expect(radios.length).toBe(3);
  });

  it('click on MAIN fires setAudioConfig({focus: "main"}) and persists', async () => {
    mountControl();
    const radios = Array.from(target.querySelectorAll('[role="radio"]')) as HTMLButtonElement[];
    const mainBtn = radios[0];
    mainBtn.click();
    flushSync();
    expect(setAudioConfigSpy).toHaveBeenCalledWith({ focus: 'main' });
    expect(localStorage.getItem('icom.audio.focus')).toBe('main');
    // aria-checked reflects new state
    expect(mainBtn.getAttribute('aria-checked')).toBe('true');
  });

  it('split toggle flips aria-pressed and fires setAudioConfig', () => {
    mountControl();
    const toggle = target.querySelector('.split-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(setAudioConfigSpy).toHaveBeenCalledWith({ split_stereo: true });
    expect(localStorage.getItem('icom.audio.split_stereo')).toBe('1');
  });

  it('MAIN slider dispatches setAudioConfig({main_gain_db: …}) and persists', () => {
    mountControl();
    const sliders = target.querySelectorAll('input[type="range"]');
    const mainSlider = sliders[0] as HTMLInputElement;
    mainSlider.value = '-12';
    mainSlider.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(setAudioConfigSpy).toHaveBeenCalledWith({ main_gain_db: -12 });
    expect(localStorage.getItem('icom.audio.main_gain_db')).toBe('-12');
  });

  it('SUB slider dispatches setAudioConfig({sub_gain_db: …})', () => {
    mountControl();
    const sliders = target.querySelectorAll('input[type="range"]');
    const subSlider = sliders[1] as HTMLInputElement;
    subSlider.value = '-3';
    subSlider.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(setAudioConfigSpy).toHaveBeenCalledWith({ sub_gain_db: -3 });
  });

  it('rehydrates from localStorage on mount', () => {
    localStorage.setItem('icom.audio.focus', 'sub');
    localStorage.setItem('icom.audio.split_stereo', '1');
    localStorage.setItem('icom.audio.main_gain_db', '-6');
    localStorage.setItem('icom.audio.sub_gain_db', '2');

    mountControl();

    // setAudioConfig called once by restoreFromStorage with the stored values.
    const restoreCall = setAudioConfigSpy.mock.calls.find((c) =>
      c[0] && typeof c[0] === 'object'
      && 'focus' in c[0] && 'split_stereo' in c[0]
      && 'main_gain_db' in c[0] && 'sub_gain_db' in c[0]
    );
    expect(restoreCall).toBeDefined();
    expect(restoreCall?.[0]).toEqual({
      focus: 'sub',
      split_stereo: true,
      main_gain_db: -6,
      sub_gain_db: 2,
    });

    // UI reflects restored values.
    const radios = Array.from(target.querySelectorAll('[role="radio"]')) as HTMLButtonElement[];
    const subRadio = radios[1];
    expect(subRadio.getAttribute('aria-checked')).toBe('true');

    const toggle = target.querySelector('.split-toggle') as HTMLButtonElement;
    expect(toggle.getAttribute('aria-pressed')).toBe('true');

    const sliders = target.querySelectorAll('input[type="range"]');
    expect((sliders[0] as HTMLInputElement).value).toBe('-6');
    expect((sliders[1] as HTMLInputElement).value).toBe('2');
  });
});
