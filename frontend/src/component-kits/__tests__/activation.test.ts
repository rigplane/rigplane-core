import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  ComponentKitDeclaration,
  FiniteControlAppearance,
  FrequencyRenderer,
  MeterAppearance,
  ScalarAppearance,
} from '../../../component-kit-api/src/index';

const renderer = (() => ({})) as unknown as FrequencyRenderer;
const scalarRenderer = (() => ({})) as unknown as NonNullable<ScalarAppearance['knob']>;
const actionRenderer = (() => ({})) as unknown as FiniteControlAppearance['action'];
const toggleRenderer = (() => ({})) as unknown as FiniteControlAppearance['toggle'];
const choiceRenderer = (() => ({})) as unknown as FiniteControlAppearance['choice'];
const signalMeterRenderer = (() => ({})) as unknown as MeterAppearance['signal'];
const levelMeterRenderer = (() => ({})) as unknown as MeterAppearance['level'];

function appearance(name: string): ScalarAppearance {
  return { name, knob: scalarRenderer };
}

function finiteAppearance(): FiniteControlAppearance {
  return { action: actionRenderer, toggle: toggleRenderer, choice: choiceRenderer };
}

function meterAppearance(): MeterAppearance {
  return { signal: signalMeterRenderer, level: levelMeterRenderer };
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
    expect(activation.getSelectedFiniteControlAppearance()).toBeUndefined();
    expect(activation.getSelectedMeterAppearance()).toBeUndefined();
  });

  it('loads every kit, then commits every selected appearance in one snapshot', async () => {
    const activation = await subject();
    const finite = finiteAppearance();
    const meter = meterAppearance();
    const declaration = kit('field-kit', {
      finiteControlAppearances: { 'field-kit-finite': finite },
      meterAppearances: { 'field-kit-meter': meter },
    });

    await activation.activateComponentKits(config([declaration], {
      scalarAppearance: 'field-kit-scalar',
      frequencyReadout: 'field-kit-frequency',
      finiteControlAppearance: 'field-kit-finite',
      meterAppearance: 'field-kit-meter',
    }));

    expect(activation.getSelectedScalarAppearance()).toEqual(appearance('field-kit scalar'));
    expect(activation.getSelectedFrequencyReadout()).toBe(renderer);
    expect(activation.getSelectedFiniteControlAppearance()).toEqual(finite);
    expect(Object.isFrozen(activation.getSelectedFiniteControlAppearance())).toBe(true);
    expect(activation.getSelectedMeterAppearance()).toEqual(meter);
    expect(Object.isFrozen(activation.getSelectedMeterAppearance())).toBe(true);
  });

  it('keeps scalar/frequency-only API 1 declarations compatible', async () => {
    const activation = await subject();
    await activation.activateComponentKits(config([kit('legacy-api-one')]));

    expect(activation.getSelectedFiniteControlAppearance()).toBeUndefined();
    expect(activation.getSelectedMeterAppearance()).toBeUndefined();
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

  it('copies a meter appearance before committing it', async () => {
    const activation = await subject();
    const selectedAppearance = { signal: signalMeterRenderer, level: levelMeterRenderer };
    const meters: Record<string, MeterAppearance> = { selected: selectedAppearance };

    await activation.activateComponentKits(config([
      kit('copy-meter-kit', { meterAppearances: meters }),
    ], { meterAppearance: 'selected' }));
    selectedAppearance.level = signalMeterRenderer as MeterAppearance['level'];
    delete meters.selected;

    expect(activation.getSelectedMeterAppearance()?.level).toBe(levelMeterRenderer);
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
    ['non-record finite declarations', { ...kit('bad-finite-map'), finiteControlAppearances: [] }],
    ['finite appearance missing a member', { ...kit('bad-finite-missing'), finiteControlAppearances: { bad: { action: actionRenderer, toggle: toggleRenderer } } }],
    ['finite appearance with an extra member', { ...kit('bad-finite-extra'), finiteControlAppearances: { bad: { ...finiteAppearance(), extra: true } } }],
    ['finite appearance with a non-component member', { ...kit('bad-finite-component'), finiteControlAppearances: { bad: { ...finiteAppearance(), choice: 'nope' } } }],
    ['empty finite appearance id', { ...kit('bad-finite-id'), finiteControlAppearances: { '': finiteAppearance() } }],
    ['non-record meter declarations', { ...kit('bad-meter-map'), meterAppearances: [] }],
    ['meter appearance missing a member', { ...kit('bad-meter-missing'), meterAppearances: { bad: { signal: signalMeterRenderer } } }],
    ['meter appearance with an extra member', { ...kit('bad-meter-extra'), meterAppearances: { bad: { ...meterAppearance(), extra: true } } }],
    ['meter appearance with a non-component member', { ...kit('bad-meter-component'), meterAppearances: { bad: { ...meterAppearance(), level: 'nope' } } }],
    ['empty meter appearance id', { ...kit('bad-meter-id'), meterAppearances: { '': meterAppearance() } }],
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
    ['finite appearance', () => {
      const selectedAppearance = finiteAppearance();
      return {
        configured: config([kit('exact-finite-kit', { finiteControlAppearances: { exact: selectedAppearance } })]),
        target: selectedAppearance,
      };
    }],
    ['meter appearance', () => {
      const selectedAppearance = meterAppearance();
      return {
        configured: config([kit('exact-meter-kit', { meterAppearances: { exact: selectedAppearance } })]),
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
    ['finite appearance', () => {
      const selectedAppearance = finiteAppearance();
      Reflect.defineProperty(selectedAppearance, 'choice', { value: choiceRenderer, enumerable: false });
      return config([kit('descriptor-finite-kit', { finiteControlAppearances: { descriptor: selectedAppearance } })]);
    }],
    ['meter appearance', () => {
      const selectedAppearance = meterAppearance();
      Reflect.defineProperty(selectedAppearance, 'level', { value: levelMeterRenderer, enumerable: false });
      return config([kit('descriptor-meter-kit', { meterAppearances: { descriptor: selectedAppearance } })]);
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

  it('rejects a finite appearance accessor without invoking it', async () => {
    const activation = await subject();
    const finite = finiteAppearance();
    const getter = vi.fn(() => choiceRenderer);
    Reflect.defineProperty(finite, 'choice', { get: getter, enumerable: true });

    await expect(activation.activateComponentKits(config([
      kit('accessor-finite-kit', { finiteControlAppearances: { accessor: finite } }),
    ]))).rejects.toThrow(/enumerable data property/i);
    expect(getter).not.toHaveBeenCalled();
  });

  it('rejects a meter appearance accessor without invoking it', async () => {
    const activation = await subject();
    const meter = meterAppearance();
    const getter = vi.fn(() => levelMeterRenderer);
    Reflect.defineProperty(meter, 'level', { get: getter, enumerable: true });

    await expect(activation.activateComponentKits(config([
      kit('accessor-meter-kit', { meterAppearances: { accessor: meter } }),
    ]))).rejects.toThrow(/enumerable data property/i);
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
    ['finiteControlAppearances', 'symbol'],
    ['finiteControlAppearances', 'non-enumerable'],
    ['meterAppearances', 'symbol'],
    ['meterAppearances', 'non-enumerable'],
  ] as const)('rejects a %s map with a %s entry', async (mapName, kind) => {
    const activation = await subject();
    const declarations: Record<PropertyKey, unknown> = {};
    const key = kind === 'symbol' ? Symbol('hidden') : 'hidden';
    Reflect.defineProperty(declarations, key, {
      value: mapName === 'scalarAppearances'
        ? appearance('Hidden')
        : mapName === 'frequencyReadouts' ? renderer
          : mapName === 'finiteControlAppearances' ? finiteAppearance() : meterAppearance(),
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
    ['duplicate finite id', [kit('finite-a', { finiteControlAppearances: { shared: finiteAppearance() } }), kit('finite-b', { finiteControlAppearances: { shared: finiteAppearance() } })]],
    ['duplicate meter id', [kit('meter-a', { meterAppearances: { shared: meterAppearance() } }), kit('meter-b', { meterAppearances: { shared: meterAppearance() } })]],
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
    ['finiteControlAppearance', 'missing-finite'],
    ['meterAppearance', 'missing-meter'],
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
