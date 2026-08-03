/** Shared valid-manifest fixture for MOR-1066 contract tests (not itself a test file). */
import type { Component } from 'svelte';
import type { LayoutManifest } from '../contract';

export function validLayoutManifest(overrides: Partial<LayoutManifest> = {}): LayoutManifest {
  return {
    schemaVersion: 1,
    id: 'testlayout',
    displayName: 'Test Layout',
    loader: () => Promise.resolve({ default: {} as unknown as Component }),
    zones: [{ id: 'main', surfaces: ['vfo', 'rxTx'] }],
    compatibleTopologies: ['1/single'],
    requiredSemanticSurfaces: ['vfo', 'rxTx'],
    sizing: { mode: 'fluid', responsiveBreakpoints: [] },
    fallbackLayoutId: null,
    ...overrides,
  };
}
