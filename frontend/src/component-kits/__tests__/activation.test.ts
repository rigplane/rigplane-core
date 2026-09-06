import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  ComponentKitDeclaration,
  FrequencyRenderer,
  ScalarAppearance,
} from '../../../component-kit-api/src/index';

const renderer = (() => ({})) as unknown as FrequencyRenderer;
const scalarRenderer = (() => ({})) as unknown as NonNullable<ScalarAppearance['knob']>;

function appearance(name: string): ScalarAppearance {
  return { name, knob: scalarRenderer };
}

function kit(
  id: string,
  overrides: Partial<ComponentKitDeclaration> = {},
): ComponentKitDeclaration {
  return {
    apiVersion: 1,
    id,
    scalarAppearances: { [`${id}-scalar`]: appearance(`${id} scalar`) },
    frequencyReadouts: { [`${id}-frequency`]: renderer },
    ...overrides,
  };
}

function config(kits: readonly unknown[], selection: Record<string, unknown> = {}) {
  return {
    kits: kits.map((declaration) => async () => ({ default: declaration })),
    selection,
  };
}

function addUnknownOwnProperty(target: object, kind: 'symbol' | 'non-enumerable'): void {
  if (kind === 'symbol') {
    Reflect.defineProperty(target, Symbol('extra'), { value: true, enumerable: true });
  } else {
    Reflect.defineProperty(target, 'extra', { value: true, enumerable: false });
  }
}

async function subject() {
  return import('../activation');
}

beforeEach(() => {
  vi.resetModules();
});

describe('component-kit activation transaction', () => {
  it('keeps the empty configuration inert', async () => {
    const activation = await subject();
    await activation.activateComponentKits(config([]));

    expect(activation.getSelectedScalarAppearance()).toBeUndefined();
    expect(activation.getSelectedFrequencyReadout()).toBeUndefined();
  });

  it('loads every kit, then commits selected scalar and frequency renderers', async () => {
    const activation = await subject();
    const declaration = kit('field-kit');

    await activation.activateComponentKits(config([declaration], {
      scalarAppearance: 'field-kit-scalar',
      frequencyReadout: 'field-kit-frequency',
    }));

    expect(activation.getSelectedScalarAppearance()).toEqual(appearance('field-kit scalar'));
    expect(activation.getSelectedFrequencyReadout()).toBe(renderer);
  });

  it('copies declarations and selection before the single commit', async () => {
    const activation = await subject();
    const scalars: Record<string, ScalarAppearance> = { original: appearance('Original') };
    const frequencies: Record<string, FrequencyRenderer> = { original: renderer };
    const selection = { scalarAppearance: 'original', frequencyReadout: 'original' };
    const declaration = kit('copy-kit', {
      scalarAppearances: scalars,
      frequencyReadouts: frequencies,
    });
    const configured = config([declaration], selection);

    await activation.activateComponentKits(configured);
    scalars.original.name = 'Mutated';
    scalars.late = appearance('Late');
    delete frequencies.original;
    selection.scalarAppearance = 'late';
    configured.kits.push?.(async () => ({ default: kit('late-kit') }));

    const selected = activation.getSelectedScalarAppearance();
    expect(selected?.name).toBe('Original');
    expect(Object.isFrozen(selected)).toBe(true);
    expect(activation.getSelectedFrequencyReadout()).toBe(renderer);
  });

  it.each([
    ['wrong API version', { ...kit('bad-version'), apiVersion: 2 }],
    ['missing kit id', { ...kit('missing-id'), id: '' }],
    ['unknown kit property', { ...kit('unknown-key'), extra: true }],
    ['non-record scalar declarations', { ...kit('bad-scalars'), scalarAppearances: [] }],
    ['scalar without a name', { ...kit('bad-scalar'), scalarAppearances: { bad: { knob: scalarRenderer } } }],
    ['scalar with a non-component member', { ...kit('bad-scalar-component'), scalarAppearances: { bad: { name: 'Bad', knob: 'nope' } } }],
    ['non-record frequency declarations', { ...kit('bad-frequencies'), frequencyReadouts: [] }],
    ['non-component frequency declaration', { ...kit('bad-frequency'), frequencyReadouts: { bad: 'nope' } }],
  ])('rejects %s without committing', async (_name, declaration) => {
    const activation = await subject();

    await expect(activation.activateComponentKits(config([declaration]))).rejects.toThrow();
    expect(activation.getSelectedScalarAppearance()).toBeUndefined();
    expect(activation.getSelectedFrequencyReadout()).toBeUndefined();
  });

  it.each([
    ['non-object configuration', null],
    ['unknown configuration property', { kits: [], extra: true }],
    ['non-array kit loaders', { kits: {} }],
    ['non-function kit loader', { kits: [false] }],
    ['non-object selection', { kits: [], selection: [] }],
    ['unknown selection property', { kits: [], selection: { extra: true } }],
    ['non-string selection', { kits: [], selection: { scalarAppearance: 4 } }],
    ['module without a default export', { kits: [async () => ({ kit: kit('named-only') })] }],
  ])('rejects %s', async (_name, configured) => {
    const activation = await subject();

    await expect(activation.activateComponentKits(configured)).rejects.toThrow();
    expect(activation.getSelectedScalarAppearance()).toBeUndefined();
  });

  it.each([
    ['configuration', () => {
      const configured = config([]);
      return { configured, target: configured };
    }],
    ['selection', () => {
      const selection = {};
      return { configured: config([], selection), target: selection };
    }],
    ['kit declaration', () => {
      const declaration = kit('exact-kit');
      return { configured: config([declaration]), target: declaration };
    }],
    ['scalar appearance', () => {
      const selectedAppearance = appearance('Exact');
      return {
        configured: config([kit('exact-scalar-kit', { scalarAppearances: { exact: selectedAppearance } })]),
        target: selectedAppearance,
      };
    }],
  ] as const)('rejects symbol and non-enumerable unknown properties on %s', async (_name, makeCase) => {
    const activation = await subject();
    for (const kind of ['symbol', 'non-enumerable'] as const) {
      const { configured, target } = makeCase();
      addUnknownOwnProperty(target, kind);

      await expect(activation.activateComponentKits(configured)).rejects.toThrow(/unknown property/i);
      expect(activation.getSelectedScalarAppearance()).toBeUndefined();
    }
  });

  it('rejects an empty-string unknown property', async () => {
    const activation = await subject();
    const configured = config([]);
    Reflect.defineProperty(configured, '', { value: true, enumerable: true });

    await expect(activation.activateComponentKits(configured)).rejects.toThrow(/unknown property/i);
  });

  it.each([
    ['configuration', () => {
      const configured = config([]);
      Reflect.defineProperty(configured, 'kits', { value: configured.kits, enumerable: false });
      return configured;
    }],
    ['selection', () => {
      const selection = { scalarAppearance: 'professional' };
      Reflect.defineProperty(selection, 'scalarAppearance', { value: 'professional', enumerable: false });
      return config([], selection);
    }],
    ['kit declaration', () => {
      const declaration = kit('descriptor-kit');
      Reflect.defineProperty(declaration, 'id', { value: 'descriptor-kit', enumerable: false });
      return config([declaration]);
    }],
    ['scalar appearance', () => {
      const selectedAppearance = appearance('Descriptor');
      Reflect.defineProperty(selectedAppearance, 'name', { value: 'Descriptor', enumerable: false });
      return config([kit('descriptor-scalar-kit', { scalarAppearances: { descriptor: selectedAppearance } })]);
    }],
  ] as const)('rejects a non-enumerable allowed property on %s', async (_name, makeConfig) => {
    const activation = await subject();

    await expect(activation.activateComponentKits(makeConfig()))
      .rejects.toThrow(/enumerable data property/i);
  });

  it('rejects an accessor without invoking it', async () => {
    const activation = await subject();
    const configured: Record<string, unknown> = {};
    const getter = vi.fn(() => []);
    Reflect.defineProperty(configured, 'kits', { get: getter, enumerable: true });

    await expect(activation.activateComponentKits(configured))
      .rejects.toThrow(/enumerable data property/i);
    expect(getter).not.toHaveBeenCalled();
  });

  it('allows additional exports on the loader module namespace', async () => {
    const activation = await subject();

    await activation.activateComponentKits({
      kits: [async () => ({ default: kit('module-kit'), namedExport: true })],
    });
  });

  it.each([
    ['scalarAppearances', 'symbol'],
    ['scalarAppearances', 'non-enumerable'],
    ['frequencyReadouts', 'symbol'],
    ['frequencyReadouts', 'non-enumerable'],
  ] as const)('rejects a %s map with a %s entry', async (mapName, kind) => {
    const activation = await subject();
    const declarations: Record<PropertyKey, unknown> = {};
    const key = kind === 'symbol' ? Symbol('hidden') : 'hidden';
    Reflect.defineProperty(declarations, key, {
      value: mapName === 'scalarAppearances' ? appearance('Hidden') : renderer,
      enumerable: kind === 'symbol',
    });

    await expect(activation.activateComponentKits(config([
      kit(`hidden-${mapName}-${kind}`, { [mapName]: declarations }),
    ]))).rejects.toThrow(/property/i);
  });

  it.each(['designLanguages', 'layouts', 'instrumentGroups', 'presentations'] as const)(
    'explicitly rejects the unsupported %s property, including an empty array',
    async (property) => {
      const activation = await subject();
      const declaration = { ...kit(`unsupported-${property}`), [property]: [] };

      await expect(activation.activateComponentKits(config([declaration])))
        .rejects.toThrow(property);
    },
  );

  it.each([
    ['duplicate kit id', [kit('duplicate-kit'), kit('duplicate-kit')]],
    ['duplicate scalar id', [kit('scalar-a', { scalarAppearances: { shared: appearance('A') } }), kit('scalar-b', { scalarAppearances: { shared: appearance('B') } })]],
    ['duplicate frequency id', [kit('frequency-a', { frequencyReadouts: { shared: renderer } }), kit('frequency-b', { frequencyReadouts: { shared: renderer } })]],
  ])('rejects %s across the complete loaded set', async (_name, declarations) => {
    const activation = await subject();

    await expect(activation.activateComponentKits(config(declarations))).rejects.toThrow(/duplicate/i);
    expect(activation.getSelectedScalarAppearance()).toBeUndefined();
  });

  it('rejects a collision with the existing professional scalar owner', async () => {
    const activation = await subject();
    const declaration = kit('collision-kit', {
      scalarAppearances: { professional: appearance('Replacement') },
    });

    await expect(activation.activateComponentKits(config([declaration])))
      .rejects.toThrow(/professional.*built-in/i);
  });

  it.each([
    ['scalarAppearance', 'missing-scalar'],
    ['frequencyReadout', 'missing-frequency'],
  ])('rejects an unresolved %s selection', async (property, value) => {
    const activation = await subject();

    await expect(activation.activateComponentKits(config([kit('selection-kit')], {
      [property]: value,
    }))).rejects.toThrow(value);
  });

  it('leaves the default snapshot intact when a later loaded kit is invalid', async () => {
    const activation = await subject();
    const configured = config([
      kit('valid-kit'),
      { ...kit('invalid-second-kit'), apiVersion: 2 },
    ]);

    await expect(activation.activateComponentKits(configured)).rejects.toThrow('invalid-second-kit');
    expect(activation.getSelectedScalarAppearance()).toBeUndefined();
  });

  it('rejects a second successful activation for the page lifetime', async () => {
    const activation = await subject();
    await activation.activateComponentKits(config([]));

    await expect(activation.activateComponentKits(config([])))
      .rejects.toThrow(/already activated/i);
  });
});
