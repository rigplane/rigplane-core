import { beforeEach, describe, expect, it, vi } from 'vitest';

const events: string[] = [];
const mount = vi.fn((_component: unknown, _options: { target: HTMLElement }) => ({ mounted: true }));

function installMocks(options: { activationError?: Error } = {}): void {
  vi.doMock('../lib/migrate-legacy-storage', () => {
    events.push('migration');
    return {};
  });
  vi.doMock('../../component-kits.config', () => {
    events.push('config');
    return {
      default: {
        kits: [async () => {
          events.push('kit');
          return { default: { apiVersion: 1, id: 'boot-kit' } };
        }],
      },
    };
  });
  vi.doMock('../component-kits/activation', () => ({
    activateComponentKits: async (config: { kits: readonly (() => Promise<unknown>)[] }) => {
      await Promise.all(config.kits.map((load) => load()));
      events.push('activation');
      if (options.activationError) throw options.activationError;
    },
  }));
  vi.doMock('../App.svelte', () => {
    events.push('app');
    return { default: function App() {} };
  });
  vi.doMock('../app.css', () => {
    events.push('css');
    return {};
  });
  vi.doMock('svelte', () => ({
    mount: (component: unknown, options: { target: HTMLElement }) => {
      events.push('mount');
      return mount(component, options);
    },
  }));
}

beforeEach(() => {
  vi.resetModules();
  vi.clearAllMocks();
  events.length = 0;
  document.body.innerHTML = '<main id="app"></main>';
});

describe('component-kit application bootstrap', () => {
  it('migrates, loads configuration, activates kits, then evaluates and mounts App once', async () => {
    installMocks();

    const { default: started } = await import('../main');
    await started;

    expect(events[0]).toBe('migration');
    expect(events.indexOf('config')).toBeGreaterThan(events.indexOf('migration'));
    expect(events.indexOf('kit')).toBeGreaterThan(events.indexOf('config'));
    expect(events.indexOf('app')).toBeGreaterThan(events.indexOf('activation'));
    expect(events.indexOf('css')).toBeGreaterThan(events.indexOf('activation'));
    expect(events.at(-1)).toBe('mount');
    expect(mount).toHaveBeenCalledOnce();
    expect(mount.mock.calls[0][1]).toEqual({ target: document.getElementById('app') });
  });

  it('renders one host-owned failure surface and never evaluates or mounts App after activation fails', async () => {
    installMocks({ activationError: new Error('bad component kit') });
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    const { default: started } = await import('../main');
    await expect(started).rejects.toThrow('bad component kit');

    expect(events).not.toContain('app');
    expect(events).not.toContain('css');
    expect(events).not.toContain('mount');
    expect(mount).not.toHaveBeenCalled();
    expect(document.querySelectorAll('[data-component-kit-startup-error]')).toHaveLength(1);
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('RigPlane could not start');
    expect(consoleError).toHaveBeenCalledWith('[rigplane] startup failed:', expect.any(Error));
  });
});
