/**
 * Theme selection — since MOR-1081 the theme ID lives in the single workspace
 * store (`presentation/workspace/store.svelte.ts`); this module keeps the
 * catalogue and applies the choice to the DOM.
 *
 * SINGLE WRITER. `rigplane:theme` and `rigplane:theme-user-choice` are no
 * longer read or written here. MOR-1079's migration folded them into
 * `WorkspaceV1.theme` (user-choice first, applied-theme second) and MOR-1076's
 * rollback window only requires that older builds can still READ them — so
 * they stay frozen at their migration-time value, never rewritten and never
 * deleted, and the workspace object is the sole write target.
 *
 * `rigplane:vfo-theme` is deliberately untouched: MOR-1078 routed it
 * `retain-outside` (a per-VFO override with no field in the frozen v1 schema),
 * so its current owner is still this module.
 */
import type { WorkspaceThemeId } from '../../presentation/workspace/contract';
import {
  getWorkspace,
  isThemeExplicit,
  setTheme as setWorkspaceTheme,
} from '../../presentation/workspace/store.svelte';

export interface ThemeInfo {
  id: string;
  name: string;
  category: 'dark' | 'light' | 'special';
  preview: string[]; // 5 colors for swatch
}

const VFO_STORAGE_KEY = 'rigplane:vfo-theme';

const THEMES: ThemeInfo[] = [
  // Dark themes
  {
    id: 'default',
    name: 'Default Dark',
    category: 'dark',
    preview: ['#121720', '#00D4FF', '#FF6A00', '#00CC66', '#F2CF4A'],
  },
  {
    id: 'dracula',
    name: 'Dracula',
    category: 'dark',
    preview: ['#282a36', '#8be9fd', '#ff79c6', '#50fa7b', '#f1fa8c'],
  },
  {
    id: 'nord',
    name: 'Nord',
    category: 'dark',
    preview: ['#2e3440', '#88c0d0', '#81a1c1', '#a3be8c', '#ebcb8b'],
  },
  {
    id: 'catppuccin-mocha',
    name: 'Catppuccin Mocha',
    category: 'dark',
    preview: ['#1e1e2e', '#89b4fa', '#cba6f7', '#a6e3a1', '#f9e2af'],
  },
  {
    id: 'solarized-dark',
    name: 'Solarized Dark',
    category: 'dark',
    preview: ['#002b36', '#2aa198', '#268bd2', '#859900', '#b58900'],
  },
  {
    id: 'gruvbox-dark',
    name: 'Gruvbox Dark',
    category: 'dark',
    preview: ['#282828', '#689d6a', '#d65d0e', '#b8bb26', '#fabd2f'],
  },
  {
    id: 'tokyo-night',
    name: 'Tokyo Night',
    category: 'dark',
    preview: ['#1a1b26', '#7dcfff', '#7aa2f7', '#9ece6a', '#ff9e64'],
  },
  {
    id: 'one-dark',
    name: 'One Dark',
    category: 'dark',
    preview: ['#282c34', '#56b6c2', '#61afef', '#98c379', '#e5c07b'],
  },
  {
    id: 'ayu-dark',
    name: 'Ayu Dark',
    category: 'dark',
    preview: ['#0d1017', '#73d0ff', '#ff8f40', '#aad94c', '#e6b450'],
  },
  {
    id: 'amoled-black',
    name: 'AMOLED Black',
    category: 'dark',
    preview: ['#000000', '#00e5ff', '#ff9500', '#34c759', '#ffcc00'],
  },
  {
    id: 'high-contrast',
    name: 'High Contrast',
    category: 'dark',
    preview: ['#000000', '#00ffff', '#ffaa00', '#00ff00', '#ffff00'],
  },

  // Light themes
  {
    id: 'solarized-light',
    name: 'Solarized Light',
    category: 'light',
    preview: ['#fdf6e3', '#2aa198', '#268bd2', '#859900', '#b58900'],
  },
  {
    id: 'catppuccin-latte',
    name: 'Catppuccin Latte',
    category: 'light',
    preview: ['#eff1f5', '#1e66f5', '#8839ef', '#40a02b', '#df8e1d'],
  },
  {
    id: 'nord-light',
    name: 'Nord Light',
    category: 'light',
    preview: ['#eceff4', '#88c0d0', '#5e81ac', '#a3be8c', '#ebcb8b'],
  },
  {
    id: 'gruvbox-light',
    name: 'Gruvbox Light',
    category: 'light',
    preview: ['#fbf1c7', '#076678', '#af3a03', '#79740e', '#b57614'],
  },
  {
    id: 'github-light',
    name: 'GitHub Light',
    category: 'light',
    preview: ['#ffffff', '#0969da', '#8250df', '#1a7f37', '#9a6700'],
  },

  // Custom/Special themes
  {
    id: 'custom-ic7610',
    name: 'Custom IC-7610',
    category: 'special',
    preview: ['#121720', '#00D4FF', '#00CC66', '#FF6A00', '#FF2020'],
  },
  {
    id: 'nixie-tube',
    name: 'Nixie Tube',
    category: 'special',
    preview: ['#0a0a0a', '#ff9933', '#ff6600', '#66ff99', '#33cc66'],
  },
  {
    id: 'lcd-blue',
    name: 'LCD Blue',
    category: 'special',
    preview: ['#0a0f14', '#66ccff', '#3399cc', '#66ffcc', '#33cc99'],
  },
  {
    id: 'lcd-warm',
    name: 'LCD Warm',
    category: 'special',
    preview: ['#1f1a16', '#2a2520', '#3a312a', '#f0e6dc', '#8a7a6a'],
  },
  {
    id: 'crt-green',
    name: 'CRT Green',
    category: 'special',
    preview: ['#000000', '#33ff33', '#22aa22', '#ffaa33', '#cc8822'],
  },
];

export function getAvailableThemes(): ThemeInfo[] {
  return THEMES;
}

export function getTheme(): string {
  return getWorkspace().theme;
}

/**
 * True if the user has explicitly chosen a theme via the picker UI.
 * False when no user preference exists yet — lets skins pick a sensible
 * default without stomping an explicit user choice.
 *
 * MOR-1081 keeps v2's semantics exactly (KEY PRESENCE, any value — including
 * an explicit `default`) while retiring the second key: the store latches, at
 * load, whether the stored object carried a `theme` field at all, and
 * `setThemeUserChoice` below is what makes it carry one. The applied-theme
 * value alone could not answer this, which is precisely why v2 kept two keys.
 */
export function hasExplicitTheme(): boolean {
  return isThemeExplicit();
}

/**
 * Apply `id` to the DOM and record it as the current theme.
 *
 * NOT an explicit choice: this is the path a skin mount takes when it
 * re-applies the theme already in effect, so it must not turn "never chose"
 * into "chose" — see `setThemeUserChoice` for the operator's own pick.
 */
export function setTheme(id: string): void {
  applyTheme(id, false);
}

/**
 * Called by the theme picker UI when a user explicitly selects a theme.
 * Applies the theme and records that the choice came from the user so
 * skin auto-defaults (e.g. amber-lcd → lcd-warm) do not override it on
 * subsequent loads — including when the user picks `default` itself.
 */
export function setThemeUserChoice(id: string): void {
  applyTheme(id, true);
}

function applyTheme(id: string, explicit: boolean): void {
  // Validate theme ID
  if (!THEMES.some((theme) => theme.id === id)) {
    console.warn(`Unknown theme ID: ${id}`);
    return;
  }

  // An explicit pick always goes through — it is what latches explicitness and
  // makes the field persist, even when the value is unchanged. A non-explicit
  // re-apply of the theme already selected writes nothing.
  if (explicit || getWorkspace().theme !== id) {
    setWorkspaceTheme(id as WorkspaceThemeId, explicit);
  }

  if (typeof document === 'undefined') {
    return;
  }

  // Apply to DOM
  if (id === 'default') {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = id;
  }
}

export function getVfoTheme(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    return localStorage.getItem(VFO_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setVfoTheme(id: string | null): void {
  if (typeof window === 'undefined') {
    return;
  }

  // Store preference
  try {
    if (id === null) {
      localStorage.removeItem(VFO_STORAGE_KEY);
    } else {
      localStorage.setItem(VFO_STORAGE_KEY, id);
    }
  } catch (err) {
    console.warn('Failed to save VFO theme preference:', err);
  }

  // Apply to DOM
  if (id === null) {
    delete document.documentElement.dataset.vfoTheme;
  } else {
    document.documentElement.dataset.vfoTheme = id;
  }
}
