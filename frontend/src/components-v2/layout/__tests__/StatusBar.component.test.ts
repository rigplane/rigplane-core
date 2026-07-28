import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const {
  runtimeScope,
  acquireHardwareScopeSpy,
  releaseHardwareScopeSpy,
  subscribeHardwareSpy,
  sendSpy,
  connectSpy,
  disconnectSpy,
  powerOnSpy,
  powerOffSpy,
  identifyFrequencySpy,
  legacyScopeConnectedSpy,
  radioPowerOnSpy,
  hasAnyScopeSpy,
} = vi.hoisted(() => ({
  runtimeScope: { hardwareScopeConnected: false },
  acquireHardwareScopeSpy: vi.fn(),
  releaseHardwareScopeSpy: vi.fn(),
  subscribeHardwareSpy: vi.fn(),
  sendSpy: vi.fn(),
  connectSpy: vi.fn(),
  disconnectSpy: vi.fn(),
  powerOnSpy: vi.fn(),
  powerOffSpy: vi.fn(),
  identifyFrequencySpy: vi.fn(),
  legacyScopeConnectedSpy: vi.fn(),
  radioPowerOnSpy: vi.fn(),
  hasAnyScopeSpy: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    scope: {
      get hardwareScopeConnected() {
        return runtimeScope.hardwareScopeConnected;
      },
      subscribeHardware: subscribeHardwareSpy,
    },
    acquireHardwareScope: acquireHardwareScopeSpy,
    releaseHardwareScope: releaseHardwareScopeSpy,
    send: sendSpy,
    system: {
      connect: connectSpy,
      disconnect: disconnectSpy,
      powerOn: powerOnSpy,
      powerOff: powerOffSpy,
      identifyFrequency: identifyFrequencySpy,
    },
  },
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioStatus: vi.fn(() => 'connected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isScopeConnected: legacyScopeConnectedSpy,
  isAudioConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => true),
  getRadioPowerOn: radioPowerOnSpy,
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: hasAnyScopeSpy,
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getFrequency: vi.fn(() => 0),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/i18n', () => ({
  t: (key: string, params?: Record<string, unknown>) =>
    params?.state ? `${key}:${params.state}` : key,
}));

vi.mock('../../controls/ThemePicker.svelte', () => ({
  default: function ThemePickerStub() { return {}; },
}));
vi.mock('../../dialogs/SendReportDialog.svelte', () => ({
  default: function SendReportDialogStub() { return {}; },
}));
vi.mock('lucide-svelte', () => {
  const IconStub = function () { return {}; };
  return {
    Radio: IconStub,
    Cable: IconStub,
    Activity: IconStub,
    Volume2: IconStub,
    ArrowDownUp: IconStub,
    Power: IconStub,
    Unplug: IconStub,
    Monitor: IconStub,
    Tv: IconStub,
    Settings: IconStub,
    Bug: IconStub,
  };
});

import StatusBar from '../StatusBar.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountStatusBar(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(StatusBar, { target }));
  flushSync();
  return target;
}

function scopeIndicator(target: HTMLElement): HTMLElement | null {
  return target.querySelector(
    '[role="status"][title^="core.statusbar.indicator.scope:"]',
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  components = [];
  runtimeScope.hardwareScopeConnected = false;
  legacyScopeConnectedSpy.mockReturnValue(false);
  radioPowerOnSpy.mockReturnValue(true);
  hasAnyScopeSpy.mockReturnValue(true);
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

describe('StatusBar scope health', () => {
  it('renders runtime-connected scope health when the legacy store is disconnected', () => {
    runtimeScope.hardwareScopeConnected = true;
    legacyScopeConnectedSpy.mockReturnValue(false);

    expect(scopeIndicator(mountStatusBar())?.title)
      .toBe('core.statusbar.indicator.scope:connected');
  });

  it('renders runtime-disconnected scope health when the legacy store is connected', () => {
    runtimeScope.hardwareScopeConnected = false;
    legacyScopeConnectedSpy.mockReturnValue(true);

    expect(scopeIndicator(mountStatusBar())?.title)
      .toBe('core.statusbar.indicator.scope:disconnected');
  });

  it('forces the scope indicator disconnected while radio power is off', () => {
    runtimeScope.hardwareScopeConnected = true;
    radioPowerOnSpy.mockReturnValue(false);

    expect(scopeIndicator(mountStatusBar())?.title)
      .toBe('core.statusbar.indicator.scope:disconnected');
  });

  it('hides the scope indicator when no scope capability is available', () => {
    runtimeScope.hardwareScopeConnected = true;
    hasAnyScopeSpy.mockReturnValue(false);

    expect(scopeIndicator(mountStatusBar())).toBeNull();
  });

  it('observes scope health without demand or transport work', () => {
    runtimeScope.hardwareScopeConnected = true;
    mountStatusBar();

    expect(acquireHardwareScopeSpy).not.toHaveBeenCalled();
    expect(releaseHardwareScopeSpy).not.toHaveBeenCalled();
    expect(subscribeHardwareSpy).not.toHaveBeenCalled();
    expect(sendSpy).not.toHaveBeenCalled();
    expect(connectSpy).not.toHaveBeenCalled();
    expect(disconnectSpy).not.toHaveBeenCalled();
    expect(powerOnSpy).not.toHaveBeenCalled();
    expect(powerOffSpy).not.toHaveBeenCalled();
    expect(identifyFrequencySpy).not.toHaveBeenCalled();
  });
});
