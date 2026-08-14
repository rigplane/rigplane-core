import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import FilterWidthLifecycleDerivedProbe from './support/FilterWidthLifecycleDerivedProbe.svelte';

type Command = {
  id: string; name: string; params: { width: number; receiver: 0 | 1 };
  createdAt: number; originalEpoch: number;
  status: 'pending' | 'acknowledged' | 'confirmed';
};
const h = vi.hoisted(() => ({ state: null as Record<string, unknown> | null, commands: [] as Command[] }));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: { get state() { return h.state; }, get caps() { return null; } } }));
vi.mock('$lib/stores/commands.svelte', () => ({
  getCommandLifecycles: () => h.commands,
  isCommandLifecycleSuperseded: () => false,
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({ toRadioViewModel: () => null }));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({ getAppTxController: () => null }));

function observed(width: number, active: 'MAIN' | 'SUB' = 'MAIN', otherWidth = 2400) {
  const path = active === 'MAIN' ? 'main.filterWidth' : 'sub.filterWidth';
  return {
    active,
    main: { filterWidth: active === 'MAIN' ? width : otherWidth },
    sub: { filterWidth: active === 'SUB' ? width : otherWidth },
    fieldStatus: { [path]: {
      observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 5,
    } },
  };
}
let mounted: ReturnType<typeof mount>[] = [];
function render() {
  const target = document.createElement('div'); document.body.appendChild(target);
  mounted.push(mount(FilterWidthLifecycleDerivedProbe, { target })); flushSync();
  const element = target.querySelector('output');
  if (!element) throw new Error('derived probe did not mount');
  return element as HTMLOutputElement;
}
afterEach(() => {
  mounted.forEach((component) => { void unmount(component); });
  mounted = []; document.body.innerHTML = '';
  h.state = null; h.commands = []; vi.restoreAllMocks();
});

describe('mounted Filter Width lifecycle projection (MOR-1667)', () => {
  it('reads the real accessor through $derived without mutation and shows canonical confirmed truth', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    h.state = observed(2400);
    const probe = render();
    const refresh = (mounted[0] as { refresh: () => void }).refresh;
    expect(probe.dataset).toMatchObject({ phase: 'idle', confirmed: '2400', target: '' });

    h.commands = [{ id: 'main', name: 'set_filter_width', params: { width: 3000, receiver: 0 }, createdAt: 1, originalEpoch: 7, status: 'acknowledged' }];
    refresh(); flushSync();
    expect(probe.dataset).toMatchObject({ phase: 'acknowledged', confirmed: '2400', target: '3000' });

    h.state = observed(3000); h.commands[0].status = 'confirmed';
    refresh(); flushSync();
    expect(probe.dataset).toMatchObject({ phase: 'confirmed', confirmed: '3000', target: '', outcome: 'confirmed' });

    h.commands = [];
    refresh(); flushSync();
    expect(probe.dataset).toMatchObject({ phase: 'idle', confirmed: '3000', outcome: '' });
    expect(error.mock.calls.flat().join(' ')).not.toMatch(/state_unsafe_mutation|mutation.*derived/i);
  });

  it('keeps SUB command evidence isolated from MAIN projection', () => {
    h.state = observed(2800, 'MAIN', 2400);
    h.commands = [{ id: 'sub', name: 'set_filter_width', params: { width: 2800, receiver: 1 }, createdAt: 1, originalEpoch: 7, status: 'acknowledged' }];
    expect(render().dataset).toMatchObject({ phase: 'idle', confirmed: '2800', target: '' });
  });
});
