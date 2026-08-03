/**
 * ESLint flat config — frontend architecture guardrails.
 *
 * Enforces import boundaries from ADR 2026-04-12 and the v3 composition ADR
 * (2026-07-25, MOR-1061). MOR-1061 additions:
 *   - semantic/ must not import skins/ (skins depend on semantic, not vice
 *     versa) or the runtime barrel/frontend-runtime (aggregates transport,
 *     audioManager, stores — April ADR's "runtime/" ban, review cycle 1 F1).
 *   - presentation/ (layouts, design languages) must not import transport,
 *     commands, stores/raw-capabilities, or the runtime barrel.
 *   - presentation/workspace/ additionally must not reference component
 *     module paths (stable IDs only).
 *   - primitives/ may import only themes/Svelte/types (April ADR, F3).
 *   - lib/runtime/** must not import from components-v2/, presentation/, or
 *     semantic/ (ADR invariant 1, F2; adapters keep full runtime access).
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md
 * @see docs/plans/2026-07-25-ui-composition-architecture-v3.md
 */

import tsParser from '@typescript-eslint/parser';
import tsPlugin from '@typescript-eslint/eslint-plugin';
import sveltePlugin from 'eslint-plugin-svelte';
import svelteParser from 'svelte-eslint-parser';

/** Modules that only the runtime/wiring layer may import. */
const FORBIDDEN_RUNTIME_IMPORTS = {
  paths: [
    {
      name: '$lib/audio/audio-manager',
      message:
        'Presentation components must not import audioManager directly. ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
  ],
  patterns: [
    {
      group: ['$lib/transport/*', '**/lib/transport/*'],
      message:
        'Presentation components must not import transport modules (sendCommand, getChannel). ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
    {
      group: ['**/lib/audio/audio-manager'],
      message:
        'Presentation components must not import audioManager directly (relative paths included). ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
  ],
};

/**
 * Runtime-internals lockdown (MOR-1061 review cycles 1-2, F1/C1-A/C1-B).
 * The barrel, frontend-runtime, system-controller, scope-controller, and
 * tx-controller/app-host (the sole TX authority — v3 ADR invariant 11) all
 * aggregate transport/audioManager/stores or own exclusive state behind one
 * import, so banning only the individual specifiers (transport, stores,
 * audioManager) is bypassable through them. NOT applied to lib/runtime/**
 * itself — adapters/props keep full access (see "lib/runtime isolation"
 * below).
 *
 * The relative-path form uses `regex`, not `group`: ESLint's gitignore-style
 * `group` glob matches a bare final segment as a DIRECTORY prefix, so
 * `**\/lib/runtime` also matched `lib/runtime/adapters/*` and
 * `lib/runtime/props/*` — the exact paths this ban must NOT touch (cycle-1
 * finding C1-A). `regex` with a `$`-anchored alternation avoids that; `!`
 * negation on `group` does not fix it.
 */
const RUNTIME_INTERNALS_MSG =
  'Must not import runtime internals directly (barrel, frontend-runtime, controllers, or the ' +
  'TX-authority app-host) — alias or relative. Consume adapters/view models instead. ' +
  'See v3 ADR invariants 5, 6, 11.';
const FORBIDDEN_RUNTIME_BARREL = {
  paths: [
    { name: '$lib/runtime', message: RUNTIME_INTERNALS_MSG },
    { name: '$lib/runtime/index', message: RUNTIME_INTERNALS_MSG },
    { name: '$lib/runtime/frontend-runtime', message: RUNTIME_INTERNALS_MSG },
    { name: '$lib/runtime/system-controller', message: RUNTIME_INTERNALS_MSG },
    { name: '$lib/runtime/scope-controller.svelte', message: RUNTIME_INTERNALS_MSG },
    { name: '$lib/runtime/tx-controller/app-host', message: RUNTIME_INTERNALS_MSG },
  ],
  patterns: [
    {
      regex:
        '(^|/)lib/runtime(/index|/frontend-runtime|/system-controller|' +
        '/scope-controller\\.svelte|/tx-controller/app-host)?$',
      message: RUNTIME_INTERNALS_MSG,
    },
  ],
};

/**
 * Semantic-specific lockdown (MOR-1061). Semantic UI depends on adapter
 * view models and primitives, never on a concrete skin — skins depend on
 * semantic, not the reverse. See v3 ADR invariant 3.
 */
const FORBIDDEN_SEMANTIC_IMPORTS = {
  paths: [...FORBIDDEN_RUNTIME_IMPORTS.paths, ...FORBIDDEN_RUNTIME_BARREL.paths],
  patterns: [
    ...FORBIDDEN_RUNTIME_IMPORTS.patterns,
    ...FORBIDDEN_RUNTIME_BARREL.patterns,
    {
      group: ['$lib/skins/*', '**/skins/**'],
      message:
        'Semantic UI must not import skins/manufacturer modules — skins depend on ' +
        'semantic, never the reverse. See v3 ADR.',
    },
  ],
};

/**
 * Panel-specific lockdown (Tier 2 — issue #1241).
 *
 * After all 18 panels migrated to adapters across batches 1-5
 * (#1244, #1245, #1246, #1247, #1248), the boundary is enforced at lint time.
 * Panels must route store reads through `lib/runtime/adapters/*` (state + commands).
 *
 * Note: this is panel-only on purpose. Other presentation layers (layout, display,
 * meters, vfo, controls, skins) have their own Tier-N migrations tracked under #1063.
 */
const FORBIDDEN_PANEL_IMPORTS = {
  patterns: [
    {
      group: ['$lib/stores/*', '$lib/stores', '**/lib/stores/*', '**/lib/stores'],
      message:
        'Panels must not import from $lib/stores/* — route via lib/runtime/adapters/* instead. ' +
        'See docs/plans/2026-04-29-panel-adapter-migration.md and ADR 2026-04-12.',
    },
    {
      group: ['$lib/transport/*', '**/lib/transport/*'],
      message:
        'Presentation components must not import transport modules (sendCommand, getChannel). ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
    {
      group: ['**/lib/audio/audio-manager'],
      message:
        'Presentation components must not import audioManager directly (relative paths included). ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
  ],
  paths: [
    {
      name: '$lib/audio/audio-manager',
      message:
        'Presentation components must not import audioManager directly. ' +
        'Use callback props from the adapter/wiring layer instead. ' +
        'See ADR 2026-04-12.',
    },
  ],
};

/** Shared: no direct command-module calls — consume bound adapter callbacks instead. */
const FORBIDDEN_COMMANDS_PATTERN = {
  group: ['$lib/runtime/commands/*', '**/lib/runtime/commands/*', '**/wiring/command-bus'],
  message:
    'Must not call command modules directly — consume bound callbacks from ' +
    'adapters/view models instead. See v3 ADR.',
};

/**
 * Presentation lockdown — layouts and design languages (MOR-1061). No
 * transport, stores (incl. raw capabilities), audioManager, runtime
 * internals (F1/C1-A/C1-B), or direct command calls — consume bound
 * callbacks from adapters/view models instead. See "Target ownership" in
 * the v3 ADR for what lives under presentation/ (layouts, design
 * languages, themes, workspace).
 */
const FORBIDDEN_PRESENTATION_IMPORTS = {
  paths: [...FORBIDDEN_PANEL_IMPORTS.paths, ...FORBIDDEN_RUNTIME_BARREL.paths],
  patterns: [
    ...FORBIDDEN_PANEL_IMPORTS.patterns,
    ...FORBIDDEN_RUNTIME_BARREL.patterns,
    FORBIDDEN_COMMANDS_PATTERN,
  ],
};

/**
 * Primitives lockdown (MOR-1061 review cycle 1 F3; cycle 2 minor 1 adds the
 * commands/semantic/components-v2 bans to match the row this zone cites).
 * April ADR: primitives are shared visual atoms and may import only
 * "Themes, Svelte, types" — forbidden from runtime, adapters, transport,
 * audio, stores. Also forbidden from commands (same reasoning as
 * presentation) and from semantic/components-v2, which depend on
 * primitives, never the reverse.
 */
const FORBIDDEN_PRIMITIVES_IMPORTS = {
  paths: [...FORBIDDEN_PANEL_IMPORTS.paths, ...FORBIDDEN_RUNTIME_BARREL.paths],
  patterns: [
    ...FORBIDDEN_PANEL_IMPORTS.patterns,
    ...FORBIDDEN_RUNTIME_BARREL.patterns,
    FORBIDDEN_COMMANDS_PATTERN,
    {
      group: ['$lib/runtime/adapters/*', '**/lib/runtime/adapters/**'],
      message:
        'Primitives are shared visual atoms — no adapters, runtime, transport, audio, or ' +
        'stores. Only themes, Svelte, and types. See ADR 2026-04-12 ("Primitives" row).',
    },
    {
      group: ['**/semantic/**', '**/components-v2/**'],
      message:
        'Primitives must not import semantic/ or components-v2/ — both depend on ' +
        'primitives, never the reverse. See ADR 2026-04-12 ("Primitives" row).',
    },
  ],
};

/**
 * Workspace lockdown (MOR-1061). Workspace preferences "reference stable
 * IDs, never component module paths" (v3 ADR invariant 6) — ID-to-component
 * resolution belongs to the layout/skin registry, not persisted workspace
 * data. Inherits the full presentation ban since workspace lives under
 * presentation/.
 */
const FORBIDDEN_WORKSPACE_IMPORTS = {
  paths: FORBIDDEN_PRESENTATION_IMPORTS.paths,
  patterns: [
    ...FORBIDDEN_PRESENTATION_IMPORTS.patterns,
    {
      group: ['**/skins/**', '**/semantic/**', '**/primitives/**', '**/components-v2/**'],
      message:
        'Workspace must reference stable layout/design-language IDs, never component ' +
        'module paths. See v3 ADR.',
    },
  ],
};

export default [
  // ── Global ignores ──
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      '.svelte-kit/**',
      '**/*.config.js',
      '**/*.config.ts',
    ],
  },

  // ── TypeScript files ──
  {
    files: ['src/**/*.ts'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'off',
    },
    rules: {},
  },

  // ── Svelte files ──
  {
    files: ['src/**/*.svelte'],
    languageOptions: {
      parser: svelteParser,
      parserOptions: {
        parser: tsParser,
        ecmaVersion: 'latest',
        sourceType: 'module',
      },
    },
    plugins: {
      svelte: sveltePlugin,
    },
    linterOptions: {
      reportUnusedDisableDirectives: 'off',
    },
    rules: {},
  },

  // ── Import boundary: presentation components ──
  // Panels, layouts, LCD, skins, and app entry — must NOT import runtime/transport directly.
  {
    files: [
      'src/App.svelte',
      'src/components-v2/panels/**/*.svelte',
      'src/components-v2/panels/**/*.ts',
      'src/components-v2/layout/**/*.svelte',
      'src/components-v2/layout/**/*.ts',
      'src/components-v2/display/**/*.svelte',
      'src/components-v2/meters/**/*.svelte',
      'src/components-v2/vfo/**/*.svelte',
      'src/components-v2/controls/**/*.svelte',
      // semantic and primitives get their own stricter blocks below (MOR-1061):
      'src/skins/**/*.svelte',
    ],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_RUNTIME_IMPORTS],
    },
  },

  // ── Import boundary: semantic (MOR-1061; F1 adds the runtime-barrel ban) ──
  // No skins/manufacturer modules (skins depend on semantic, never reverse).
  {
    files: ['src/semantic/**/*.svelte', 'src/semantic/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_SEMANTIC_IMPORTS],
    },
  },

  // ── Import boundary: primitives (MOR-1061 review cycle 1, F3) ──
  // Shared visual atoms — themes/Svelte/types only. See ADR 2026-04-12.
  {
    files: ['src/primitives/**/*.svelte', 'src/primitives/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_PRIMITIVES_IMPORTS],
    },
  },

  // ── Import boundary: presentation — layouts, design languages (MOR-1061) ──
  // No transport, stores/capabilities, audioManager, runtime barrel/
  // frontend-runtime (F1), or direct command calls.
  {
    files: ['src/presentation/**/*.svelte', 'src/presentation/**/*.ts'],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_PRESENTATION_IMPORTS],
    },
  },

  // ── Import boundary: workspace (MOR-1061; cycle 2 minor 2 adds .svelte
  // and the plural workspaces/ variant — cycle-0 finding F5) ──
  // Adds a module-path ban on top of the presentation ban above. Ordered
  // after the presentation block so this fully-merged rule wins for the
  // overlapping file set (flat config replaces, not merges, per rule key).
  {
    files: [
      'src/presentation/workspace/**/*.ts',
      'src/presentation/workspace/**/*.svelte',
      'src/presentation/workspaces/**/*.ts',
      'src/presentation/workspaces/**/*.svelte',
    ],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_WORKSPACE_IMPORTS],
    },
  },

  // ── Import boundary: panels (Tier 2 lockdown — issue #1241) ──
  // Adds `$lib/stores/*` to the panel-specific ban list. All 18 panels were migrated
  // to adapters across batches 1-5 (#1244, #1245, #1246, #1247, #1248); this block
  // freezes the boundary so it cannot regress. Other presentation layers retain the
  // looser FORBIDDEN_RUNTIME_IMPORTS rule until their own tier migrates (#1063).
  {
    files: [
      'src/components-v2/panels/**/*.svelte',
      'src/components-v2/panels/**/*.ts',
    ],
    rules: {
      'no-restricted-imports': ['error', FORBIDDEN_PANEL_IMPORTS],
    },
  },

  // ── Import boundary: lib/runtime isolation ──
  // lib/runtime must NOT import from components-v2 (circular deps, ADR
  // 2026-04-12, issue #1005) or from presentation/semantic (v3 ADR
  // invariant 1, MOR-1061 review cycle 1 F2).
  {
    files: [
      'src/lib/runtime/**/*.ts',
      'src/lib/runtime/**/*.svelte',
      'src/lib/runtime/**/*.svelte.ts',
    ],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/components-v2/**', '**/components-v2'],
              message:
                'lib/runtime must not import from components-v2. ' +
                'Use lib/runtime/props/ or lib/runtime/commands/ instead. ' +
                'See ADR 2026-04-12.',
            },
            {
              group: ['**/presentation/**', '**/presentation', '**/semantic/**', '**/semantic'],
              message:
                'lib/runtime must not import from presentation/ or semantic/. ' +
                'See v3 ADR invariant 1 (MOR-1061 F2).',
            },
          ],
        },
      ],
    },
  },

  // ── Tests may import anything (mocking is legitimate) ──
  {
    files: ['src/**/__tests__/**', 'src/**/*.test.ts'],
    rules: {
      'no-restricted-imports': 'off',
    },
  },
];
