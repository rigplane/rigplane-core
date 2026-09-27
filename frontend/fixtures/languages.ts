/**
 * MOR-2676 — design-language overlay activation, shared by every
 * fixtures-server page (`fixtures/index.html`'s `main.ts`,
 * `mobile-witness.ts`, `skin-witness.ts`).
 *
 * Extracted from `main.ts`, which owned this table alone until MOR-2676:
 * the placeholder-guard smoke test loads EVERY face once per language
 * overlay, so the witness pages need the same `?language=`/`?mode=light`
 * contract the catalog entries already have (MOR-1073/MOR-1074: languages
 * are scoped to `[data-design-language]`, their only activation mechanism,
 * and the query param is the only opt-in).
 */
export const DESIGN_LANGUAGES = ['studioline', 'fieldline', 'segmentline'] as const;
export type DesignLanguage = (typeof DESIGN_LANGUAGES)[number];

const LANGUAGE_STYLESHEETS: Record<DesignLanguage, () => Promise<unknown>> = {
  studioline: () => import('../src/presentation/languages/studioline/studioline.css'),
  fieldline: () => import('../src/presentation/languages/fieldline/fieldline.css'),
  segmentline: () => import('../src/presentation/languages/segmentline/segmentline.css'),
};

/**
 * Activate the `?language=` overlay (an explicit id, or `fallback` when the
 * page carries a page-specific default, e.g. `peer-split`'s segmentline —
 * MOR-2153) and the explicit `?mode=light` variant. No-ops when neither
 * resolves. Throws on an unknown id so a typo'd URL fails loudly rather
 * than silently capturing an unstyled page.
 */
export async function applyDesignLanguage(
  params: URLSearchParams,
  fallback: DesignLanguage | null = null,
): Promise<void> {
  const language = params.get('language') ?? fallback;
  if (language === null) return;
  if (!(DESIGN_LANGUAGES as readonly string[]).includes(language)) {
    throw new Error(`MOR-1074 harness: unknown design language "${language}"`);
  }
  await LANGUAGE_STYLESHEETS[language as DesignLanguage]();
  document.documentElement.dataset.designLanguage = language;
  // Light is an explicit opt-in, never an OS-preference flip: the app's own
  // light/dark is a manual `[data-theme]` choice, so language and surface
  // have to be switched by the same deliberate act.
  if (params.get('mode') === 'light') document.documentElement.dataset.languageMode = 'light';
}
