import { describe, expect, it } from 'vitest';

import { mockCapabilities } from '../../../../tests/e2e/i18n/fixtures';
import { validateCapabilities } from '../capabilities';

describe('RP-ML-006 i18n visual capability fixture', () => {
  it('conforms to the complete public capabilities contract', () => {
    expect(validateCapabilities(mockCapabilities)).toBe(mockCapabilities);
  });

  it('fails loudly when a required capability group drifts out of the fixture', () => {
    const { audioConfig: _audioConfig, ...missingAudioConfig } = mockCapabilities;

    expect(() => validateCapabilities(missingAudioConfig)).toThrow(
      'Invalid capabilities payload at $.audioConfig: expected an object',
    );
  });
});
