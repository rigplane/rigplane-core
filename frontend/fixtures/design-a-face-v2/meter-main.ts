import { flushSync, mount } from 'svelte';
import IcomTouchNeedleMeter from '../../src/components-v2/meters/IcomTouchNeedleMeter.svelte';

// Fixture-only sample: no production module owns or imports these values.
mount(IcomTouchNeedleMeter, {
  target: document.querySelector('#app')!,
  props: {
    value: 0.38,
    displayValue: '38 W',
    selectedScale: 'Po',
    structural: true,
    operational: true,
    relevant: true,
  },
});
flushSync();
document.body.dataset.fixtureReady = 'true';
