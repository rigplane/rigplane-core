<script lang="ts">
  import { onMount } from 'svelte';
  import { Palette } from 'lucide-svelte';
  import { getAvailableThemes, getTheme, setThemeUserChoice, getVfoTheme, setVfoTheme, type ThemeInfo } from '../theme/theme-switcher';

  let isOpen = $state(false);
  let currentTheme = $derived(getTheme());
  let currentVfoTheme = $state<string | null>(null);
  let dropdownElement = $state<HTMLElement | null>(null);
  let buttonElement = $state<HTMLElement | null>(null);

  const themes = getAvailableThemes();
  const vfoThemeOptions = [
    { id: 'nixie-tube', name: 'Nixie Tube' },
    { id: 'lcd-blue', name: 'LCD Blue' },
    { id: 'crt-green', name: 'CRT Green' },
  ];

  onMount(() => {
    currentVfoTheme = getVfoTheme();

    function handleClickOutside(event: MouseEvent) {
      if (
        isOpen &&
        dropdownElement &&
        buttonElement &&
        !dropdownElement.contains(event.target as Node) &&
        !buttonElement.contains(event.target as Node)
      ) {
        isOpen = false;
      }
    }

    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  });

  function toggleDropdown() {
    isOpen = !isOpen;
  }

  function selectTheme(themeId: string) {
    setThemeUserChoice(themeId);
    isOpen = false;
  }

  function selectVfoTheme(vfoId: string | null) {
    currentVfoTheme = vfoId;
    setVfoTheme(vfoId);
  }

  function getCurrentThemeName(): string {
    return themes.find((t) => t.id === currentTheme)?.name || 'Default Dark';
  }
</script>

<div class="theme-picker">
  <button
    class="theme-button"
    onclick={toggleDropdown}
    bind:this={buttonElement}
    title="Choose theme"
  >
    <Palette size={14} strokeWidth={1.5} />
  </button>

  {#if isOpen}
    <div class="theme-dropdown" bind:this={dropdownElement}>
      <div class="theme-dropdown-header">Theme</div>
      <div class="theme-list">
        {#each ['dark', 'light', 'special'] as category}
          {@const categoryThemes = themes.filter((t) => t.category === category)}
          {#if categoryThemes.length > 0}
            <div class="theme-category-label">
              {category === 'dark' ? 'Dark' : category === 'light' ? 'Light' : 'Special'}
            </div>
            {#each categoryThemes as theme}
            <button
              class="theme-option"
              class:active={theme.id === currentTheme}
              onclick={() => selectTheme(theme.id)}
            >
              <div class="theme-swatch">
                {#each theme.preview as color}
                  <div class="swatch-dot" style="background: {color}"></div>
                {/each}
              </div>
              <span class="theme-name">{theme.name}</span>
              {#if theme.id === currentTheme}
                <span class="theme-check">✓</span>
              {/if}
            </button>
            {/each}
          {/if}
        {/each}

        <!-- VFO Theme Selector -->
        <div class="vfo-theme-section">
          <div class="vfo-theme-label">VFO Display</div>
          <div class="vfo-theme-buttons">
            <button
              class="vfo-theme-btn"
              class:active={currentVfoTheme === null}
              onclick={() => selectVfoTheme(null)}
              title="Use main theme for VFO"
            >
              Default
            </button>
            {#each vfoThemeOptions as vfo}
              <button
                class="vfo-theme-btn"
                class:active={currentVfoTheme === vfo.id}
                onclick={() => selectVfoTheme(vfo.id)}
                title="Apply {vfo.name} style to VFO only"
              >
                {vfo.name}
              </button>
            {/each}
          </div>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .theme-picker {
    position: relative;
  }

  .theme-button {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 4px 8px;
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .theme-button:hover {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-accent-cyan, #06b6d4);
    color: var(--v2-text-primary, #fff);
  }

  .theme-button:active {
    transform: scale(0.95);
  }

  .theme-dropdown {
    position: absolute;
    top: calc(100% + 4px);
    right: 0;
    width: min(240px, calc(100vw - 32px));
    background: var(--v2-bg-card);
    border: 1px solid var(--v2-border);
    border-radius: 6px;
    box-shadow: var(--v2-shadow-lg);
    z-index: 1000;
    overflow: hidden;
  }

  .theme-dropdown-header {
    padding: 8px 12px;
    font-family: var(--v2-font-mono);
    font-size: var(--v2-font-size-sm);
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v2-text-muted);
    border-bottom: 1px solid var(--v2-border-dark);
    background: var(--v2-bg-darker);
  }

  .theme-list {
    max-height: 400px;
    overflow-y: auto;
  }

  .theme-category-label {
    padding: 6px 12px 4px;
    font-family: var(--v2-font-mono);
    font-size: var(--v2-font-size-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v2-text-muted);
    border-top: 1px solid var(--v2-border-dark);
  }

  .theme-category-label:first-child {
    border-top: none;
  }

  .theme-option {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: background 0.15s ease;
    text-align: left;
  }

  .theme-option:hover {
    background: var(--v2-bg-input);
  }

  .theme-option.active {
    background: var(--v2-bg-input);
    border-left: 2px solid var(--v2-accent-cyan);
  }

  .theme-swatch {
    display: flex;
    gap: 3px;
    flex-shrink: 0;
  }

  .swatch-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 1px solid var(--v2-theme-picker-border);
  }

  .theme-name {
    flex: 1;
    font-family: var(--v2-font-mono);
    font-size: var(--v2-font-size-md);
    color: var(--v2-text-primary);
    white-space: nowrap;
  }

  .theme-check {
    color: var(--v2-accent-cyan);
    font-size: 14px;
    flex-shrink: 0;
  }

  .vfo-theme-section {
    border-top: 1px solid var(--v2-border-dark);
    padding: 10px 12px;
    background: var(--v2-bg-darker);
  }

  .vfo-theme-label {
    font-family: var(--v2-font-mono);
    font-size: var(--v2-font-size-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v2-text-muted);
    margin-bottom: 8px;
  }

  .vfo-theme-buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .vfo-theme-btn {
    padding: 6px 12px;
    background: var(--v2-bg-input);
    border: 1px solid var(--v2-border);
    border-radius: 3px;
    font-family: var(--v2-font-mono);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--v2-text-muted);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .vfo-theme-btn:hover {
    background: var(--v2-bg-card);
    border-color: var(--v2-accent-cyan);
    color: var(--v2-text-primary);
  }

  .vfo-theme-btn.active {
    background: var(--v2-accent-cyan);
    border-color: var(--v2-accent-cyan);
    color: var(--v2-text-white);
  }

  /* Scrollbar styling */
  .theme-list::-webkit-scrollbar {
    width: 8px;
  }

  .theme-list::-webkit-scrollbar-track {
    background: var(--v2-bg-darker);
  }

  .theme-list::-webkit-scrollbar-thumb {
    background: var(--v2-border);
    border-radius: 4px;
  }

  .theme-list::-webkit-scrollbar-thumb:hover {
    background: var(--v2-border-subtle);
  }
</style>
