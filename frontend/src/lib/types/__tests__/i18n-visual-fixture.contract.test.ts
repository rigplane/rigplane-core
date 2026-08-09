import { describe, expect, it } from 'vitest';

import { mockCapabilities, mockState } from '../../../../tests/e2e/i18n/fixtures';
import { validateCapabilities } from '../capabilities';

describe('RP-ML-006 i18n visual capability fixture', () => {
  it('carries the required B2 provider epoch through state and capabilities', () => {
    expect(mockState.stateContractVersion).toBe(1);
    expect(mockState.providerGeneration).toBe(0);
    expect(mockCapabilities.stateContractVersion).toBe(1);
    expect(mockCapabilities.providerGeneration).toBe(0);
  });

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
