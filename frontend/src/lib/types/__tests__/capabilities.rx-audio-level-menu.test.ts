import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';
import stateWireGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

// MOR-4210: `rxAudioLevelMenu` is the profile's `[audio].rx_audio_level_menu`.
// The replies come from the producer: scripts/gen_state_wire_goldens.py
// serves each shipped profile through WebServer._serve_capabilities
// (pinned against the server by tests/web/test_state_wire_goldens.py).
const goldens = stateWireGoldens as Record<string, { capabilities: Record<string, unknown> }>;

function reply(model: string): Record<string, unknown> {
  return structuredClone(goldens[model].capabilities);
}

describe('rxAudioLevelMenu capability field (MOR-4210)', () => {
  it('keeps the menu string a profile declares', () => {
    const ic705: string | null | undefined = validateCapabilities(reply('IC-705')).rxAudioLevelMenu;
    expect(ic705).toBe('SET > Connectors > USB AF/IF Output > AF Output Level');
    expect(validateCapabilities(reply('IC-7300')).rxAudioLevelMenu)
      .toBe('SET > Connectors > ACC/USB AF Output Level');
  });

  it('keeps null for a profile that declares none', () => {
    expect(reply('IC-7610')).toHaveProperty('rxAudioLevelMenu', null);
    expect(validateCapabilities(reply('IC-7610')).rxAudioLevelMenu).toBeNull();
  });

  it('stays optional for older servers and profile-less backends', () => {
    const payload = reply('IC-7300');
    delete payload.rxAudioLevelMenu;
    expect(validateCapabilities(payload).rxAudioLevelMenu).toBeUndefined();
  });

  it.each([[42], [true], [{}], [['SET']]])('rejects a non-string, non-null value %j', (value) => {
    expect(() => validateCapabilities({ ...reply('IC-7300'), rxAudioLevelMenu: value }))
      .toThrow(/\$\.rxAudioLevelMenu: expected a string or null/);
  });
});
