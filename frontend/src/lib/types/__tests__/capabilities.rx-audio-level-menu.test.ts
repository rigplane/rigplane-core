import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';
import stateWireGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

const goldens = stateWireGoldens as Record<string, { capabilities: Record<string, unknown> }>;

describe('removed RX audio menu capability', () => {
  it.each(['IC-705', 'IC-7300', 'IC-7610'])('omits the field for %s', (model) => {
    const reply = structuredClone(goldens[model].capabilities);
    expect(reply).not.toHaveProperty('rxAudioLevelMenu');
    expect(validateCapabilities(reply)).not.toHaveProperty('rxAudioLevelMenu');
  });
});
