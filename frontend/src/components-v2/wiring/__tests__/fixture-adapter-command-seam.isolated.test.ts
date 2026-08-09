import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const config = readFileSync('vite.fixtures.config.ts', 'utf8');
const stub = readFileSync('fixtures/stubs/panel-adapters.ts', 'utf8');

describe('fixture semantic command seam (MOR-1409 A04a)', () => {
  it('intercepts the adapter binder and records every canonical semantic family', () => {
    expect(config).toContain("'src/lib/runtime/adapters/panel-adapters.ts': 'fixtures/stubs/panel-adapters.ts'");
    expect(stub).toContain('bindSemanticSurfaceHandlers');
    for (const family of ['agc', 'antenna', 'audioRouting', 'band', 'cw', 'dsp', 'filter', 'mode', 'rfFrontEnd', 'ritXit', 'rxAudio', 'scan', 'scopeControls', 'tx', 'vfo', 'vox']) {
      expect(stub).toContain(`${family}:`);
    }
  });
});
