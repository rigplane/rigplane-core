import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('DualSdrFaceSkin production entrypoint', () => {
  const source = readFileSync('src/skins/dual-sdr-face/DualSdrFaceSkin.svelte', 'utf8');

  it('mounts the exact face through the canonical read-only semantic view', () => {
    expect(source).toContain("import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte'");
    expect(source).toContain("import DualSdrFace from './DualSdrFace.svelte'");
    expect(source).toMatch(/<SemanticRadioSurfaces\s+\{readonlyDisplay\}\s*\/>/);
    expect(source).toMatch(/<DualSdrFace\s+\{view\}\s+scopeSource=\{hardwareScope\}\s*\/>/);
  });

  it('uses the canonical hardware scope and grants no command callback', () => {
    expect(source).toContain('runtime.scope.subscribeHardware(listener)');
    expect(source).not.toContain('onPreChange=');
  });
});
