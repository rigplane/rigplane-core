import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import { projectPeerSplitDisplay } from '../../../semantic/radio-display-model';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import { topologyFixtures, withAudioOnlyScope } from '../../../semantic/fixtures/topologies';
import PeerSplitDisplay from '../PeerSplitDisplay.svelte';
import DominantUnifiedDisplay from '../DominantUnifiedDisplay.svelte';
import CenterstageDisplay from '../CenterstageDisplay.svelte';
import PanadapterDisplay from '../PanadapterDisplay.svelte';

const variants = [PeerSplitDisplay, DominantUnifiedDisplay, CenterstageDisplay, PanadapterDisplay];
const names = ['Peer Split', 'Unified', 'Centerstage', 'Panadapter'];
let component: ReturnType<typeof mount> | undefined;
afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  document.body.innerHTML = '';
});

function render(index: number, view: RadioViewModel) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const model = projectPeerSplitDisplay(view);
  const frame = {
    source: 'audio-fft', receiver: 'MAIN', freshness: 'fresh',
    startHz: 0, endHz: 4_000, normalizedBins: [0, 0.5, 1],
  };
  component = mount(variants[index], {
    target,
    props: {
      model, normalizedFftBins: { MAIN: frame.normalizedBins },
      audioFftFrame: frame, spectrumFrame: frame,
    },
  });
  return target;
}

for (const [index, name] of names.entries()) {
  describe(`${name} topology display slots`, () => {
    it.each(['A', 'B'] as const)('keeps fixed A/B frequencies and one physical FFT with %s active', (active) => {
      const fixture = withAudioOnlyScope(topologyFixtures['1/ab']);
      const target = render(index, {
        ...fixture,
        vfos: fixture.vfos.map((vfo) => ({
          ...vfo,
          isActive: vfo.slot.kind === 'slotted' && vfo.slot.id === active,
          isActiveSlot: vfo.slot.kind === 'slotted' && vfo.slot.id === active,
        })),
      });
      const frequencies = [...target.querySelectorAll('[data-testid^="lcd-frequency-"]')];
      expect(frequencies.map((node) => node.getAttribute('data-testid'))).toEqual([
        'lcd-frequency-A', 'lcd-frequency-B',
      ]);
      expect(frequencies.map((node) => node.textContent?.replace(/\s/g, ''))).toEqual([
        '7.100.000', '7.150.000',
      ]);
      expect(target.querySelectorAll('[data-fft-mode="live"]')).toHaveLength(1);
      expect(target.querySelectorAll('[data-receiver="SUB"]')).toHaveLength(0);
    });

    it('renders an unknown B frequency without substituting A', () => {
      const fixture = topologyFixtures['1/ab'];
      const target = render(index, {
        ...fixture,
        vfos: fixture.vfos.map((vfo) => ({
          ...vfo, frequencyHz: vfo.slot.kind === 'slotted' && vfo.slot.id === 'B' ? null : vfo.frequencyHz,
        })),
      });
      expect(target.querySelector('[data-testid="lcd-frequency-B"]')?.getAttribute('data-state')).toBe('unknown');
      expect(target.querySelector('[data-testid="lcd-frequency-A"]')?.getAttribute('data-state')).toBe('known');
    });

    it('preserves dual receiver frequency identities', () => {
      const target = render(index, topologyFixtures['2/main_sub']);
      expect([...target.querySelectorAll('[data-testid^="lcd-frequency-"]')]
        .map((node) => node.getAttribute('data-testid'))).toEqual([
        'lcd-frequency-MAIN', 'lcd-frequency-SUB',
      ]);
    });
  });
}
