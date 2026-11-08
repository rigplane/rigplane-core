import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { audioManager } from '../audio-manager';
import { RxPlayer } from '../rx-player';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { validateCapabilities } from '$lib/types/capabilities';
import stateWireGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

class RoutingSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: RoutingSocket[] = [];
  readyState = 0;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  constructor() { RoutingSocket.instances.push(this); }
  send(value: string) { this.sent.push(JSON.parse(value)); }
  close() { this.readyState = 3; }
  open() { this.readyState = RoutingSocket.OPEN; this.onopen?.(); }
}

const neutral = { focus: 'both', split_stereo: false, main_gain_db: 0, sub_gain_db: 0 } as const;
const stored = { focus: 'main', split_stereo: true, main_gain_db: -27, sub_gain_db: -3 } as const;
const capabilities = (model: keyof typeof stateWireGoldens, routing = false) => {
  const wire = structuredClone(stateWireGoldens[model].capabilities);
  return validateCapabilities({
    ...wire,
    capabilities: routing ? [...wire.capabilities, 'lan_dual_rx_audio_routing'] : wire.capabilities,
  });
};

describe('RX routing follows the accepted capability contract', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', RoutingSocket);
    RoutingSocket.instances = [];
    clearCapabilities();
  });
  afterEach(() => {
    audioManager.destroy();
    clearCapabilities();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it.each(['IC-705', 'IC-7300', 'FTX-1'] as const)('keeps %s at unity despite a valid persisted dual-RX trim', (model) => {
    setCapabilities(capabilities(model));
    audioManager.setAudioConfig(stored);
    expect(audioManager.getAudioConfig()).toEqual(neutral);
    expect(RoutingSocket.instances).toHaveLength(0);
  });

  it('keeps unknown capabilities neutral and does not open an audio socket', () => {
    audioManager.setAudioConfig(stored);
    expect(audioManager.getAudioConfig()).toEqual(neutral);
    expect(RoutingSocket.instances).toHaveLength(0);
  });

  it('reconciles transitions locally and clears pending transport work', () => {
    setCapabilities(capabilities('IC-7610', true));
    audioManager.setAudioConfig(stored);
    expect(audioManager.getAudioConfig()).toEqual(stored);
    setCapabilities(capabilities('IC-705'));
    expect(audioManager.getAudioConfig()).toEqual(neutral);
    expect(audioManager.getAppliedAudioConfig()).toEqual(neutral);
    RoutingSocket.instances[0]!.open();
    expect(RoutingSocket.instances[0]!.sent).not.toContainEqual(expect.objectContaining({ type: 'audio_config' }));
    clearCapabilities();
    setCapabilities(capabilities('IC-7610', true));
    expect(audioManager.getAudioConfig()).toEqual(stored);
    expect(audioManager.getAppliedAudioConfig()).toEqual(stored);
    expect(RoutingSocket.instances).toHaveLength(1);
    expect(RoutingSocket.instances[0]!.sent).not.toContainEqual(expect.objectContaining({ type: 'audio_config' }));

    const focus = vi.spyOn(RxPlayer.prototype, 'setFocus');
    const split = vi.spyOn(RxPlayer.prototype, 'setSplitStereo');
    const gain = vi.spyOn(RxPlayer.prototype, 'setChannelGainDb');
    const notify = vi.fn();
    const unsubscribe = audioManager.onChange(notify);
    setCapabilities(capabilities('IC-7610', true));
    setCapabilities(capabilities('IC-7610', true));
    expect(focus).not.toHaveBeenCalled();
    expect(split).not.toHaveBeenCalled();
    expect(gain).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();
    expect(RoutingSocket.instances).toHaveLength(1);
    expect(RoutingSocket.instances[0]!.sent).not.toContainEqual(expect.objectContaining({ type: 'audio_config' }));
    unsubscribe();
    vi.restoreAllMocks();
  });

  it('unsubscribes on destroy and rearms when reused', () => {
    setCapabilities(capabilities('IC-7610', true));
    audioManager.setAudioConfig(stored);
    audioManager.destroy();
    setCapabilities(capabilities('IC-705'));
    expect(audioManager.getAudioConfig()).toEqual(stored);
    audioManager.setAudioConfig({ main_gain_db: -27 });
    expect(audioManager.getAudioConfig()).toEqual(neutral);
    setCapabilities(capabilities('IC-7610', true));
    expect(audioManager.getAudioConfig()).toEqual(stored);
  });
});
