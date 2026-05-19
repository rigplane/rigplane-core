<script lang="ts">
  /**
   * Compact language picker for the Core Settings modal (RP-ML-004).
   *
   * Design constraints (see issue #1522 and strategy glossary §5.4):
   *   - Operational, not marketing: a quiet inline label + native <select>.
   *     No flags, no marketing copy, no animation.
   *   - The selector's own labels resolve through the i18n runtime so the
   *     control reflects the language the user is about to leave.
   *   - The option list is derived from `SUPPORTED_LOCALES`, not a hardcoded
   *     array — a translator adding `frontend/src/lib/i18n/locales/<bcp47>.json`
   *     and registering it in the runtime barrel surfaces here automatically.
   *   - Option labels use the locale's own endonym ("English", "日本語")
   *     because that is the universally legible cue across all locales.
   *   - The pseudo-locale `qps-ploc` is selectable but clearly tagged as
   *     "developer pseudo-locale" so contributors do not confuse it with a
   *     real translation.
   *   - Persistence, browser-locale fallback, and the unsupported-locale
   *     guard already live in `$lib/i18n/store.svelte.ts`. This component
   *     only reads and writes through that store.
   */
  import { getLocale, setLocale, SUPPORTED_LOCALES, t } from '$lib/i18n';
  import { PSEUDO_LOCALE, type LocaleCode } from '$lib/i18n';

  /**
   * Endonym lookup. Kept small and explicit because endonyms are static
   * cultural data — they do not change per release and never need
   * translation. A community-contributed locale that lacks an entry here
   * falls back to its BCP-47 code, which is the most honest signal we can
   * surface until the contributor updates this table.
   *
   * Keep this list ASCII-sortable on the locale code so future contributors
   * know where to insert a new row.
   */
  const ENDONYMS: Record<string, string> = {
    'en-US': 'English',
    'ja-JP': '日本語',
    // qps-ploc gets its label assembled at render time so the
    // "developer pseudo-locale" suffix follows the active locale.
    'qps-ploc': 'qps-ploc',
  };

  function formatOptionLabel(code: LocaleCode): string {
    if (code === PSEUDO_LOCALE) {
      const suffix = t('core.settings.language.devLocaleSuffix');
      return `${ENDONYMS[code]} — ${suffix}`;
    }
    return ENDONYMS[code] ?? code;
  }

  // Reactive view of the current locale. Reading getLocale() inside a
  // $derived expression subscribes us to the Svelte 5 rune in the store.
  const current = $derived<LocaleCode>(getLocale());

  // Labels resolved through the runtime so they re-render on locale change.
  const labelText = $derived(t('core.settings.language.label'));
  const chooseText = $derived(t('core.settings.language.choose'));
  const srHintText = $derived(t('core.settings.language.srHint'));

  function handleChange(ev: Event): void {
    const value = (ev.currentTarget as HTMLSelectElement).value as LocaleCode;
    // The store already rejects unsupported codes; this is belt-and-braces
    // against a future DOM contributor wiring a stray <option>.
    if ((SUPPORTED_LOCALES as readonly string[]).includes(value)) {
      setLocale(value);
    }
  }
</script>

<div class="language-selector" data-testid="language-selector">
  <label class="lang-row">
    <span class="lang-label">{labelText}</span>
    <select
      class="lang-select"
      data-testid="language-select"
      aria-label={chooseText}
      title={chooseText}
      value={current}
      onchange={handleChange}
    >
      {#each SUPPORTED_LOCALES as code (code)}
        <option value={code} data-testid="language-option-{code}">
          {formatOptionLabel(code)}
        </option>
      {/each}
    </select>
  </label>
  <p class="lang-hint" data-testid="language-hint">{srHintText}</p>
</div>

<style>
  .language-selector {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 4px;
  }

  .lang-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .lang-label {
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--v2-text-secondary, #aaa);
  }

  .lang-select {
    appearance: none;
    min-width: 200px;
    padding: 6px 26px 6px 10px;
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    font-family: 'Roboto Mono', monospace;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
    background-image:
      linear-gradient(45deg, transparent 50%, currentColor 50%),
      linear-gradient(135deg, currentColor 50%, transparent 50%);
    background-position: calc(100% - 12px) 50%, calc(100% - 8px) 50%;
    background-size: 4px 4px;
    background-repeat: no-repeat;
  }

  .lang-select:hover,
  .lang-select:focus {
    border-color: var(--v2-accent-cyan, #06b6d4);
    outline: none;
  }

  .lang-select option {
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-primary, #fff);
  }

  .lang-hint {
    margin: 0;
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    color: var(--v2-text-dim, #888);
    line-height: 1.4;
  }
</style>
