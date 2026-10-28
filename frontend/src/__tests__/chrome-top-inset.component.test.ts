/**
 * MOR-3845 — the `--rp-chrome-top` page top inset contract.
 *
 * An embedding shell (the unified title bar) reserves the strip above the
 * page by setting `--rp-chrome-top` on <html>. Core must then (a) start the
 * root layout below that strip and (b) never let a fixed surface anchored to
 * the viewport top cover it. This is the same pattern as MOR-1240's
 * `--rp-status-bar-bottom`, and jsdom does no layout, so what is assertable
 * here is the CSS contract: the default is 0px (nothing changes with the
 * property unset), the root layout consumes the property, and every
 * fixed-position surface anchored at the viewport top references it.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), 'utf8');

const CHROME_VAR = /var\(--rp-chrome-top,\s*0px\)/;

/** All `selector { body }` pairs of every <style> block in the source. */
function cssRules(source: string): { selector: string; body: string }[] {
  const styleBlocks = [...source.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
  const css = (styleBlocks.length > 0 ? styleBlocks.join('\n') : source)
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const rules: { selector: string; body: string }[] = [];
  for (const match of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    rules.push({ selector: match[1].trim(), body: match[2] });
  }
  return rules;
}

describe('MOR-3845 — root layout honours --rp-chrome-top', () => {
  it('declares the 0px default and pads the app root below the chrome strip', () => {
    const css = read('../app.css');
    expect(css).toMatch(/--rp-chrome-top:\s*0px/);
    const appRule = cssRules(css).find(({ selector }) => selector === '#app');
    expect(appRule, 'app.css must style #app').toBeDefined();
    expect(appRule!.body).toMatch(/padding-top:\s*var\(--rp-chrome-top,\s*0px\)/);
  });

  it('changes nothing visually with the property unset (0px fallback everywhere)', () => {
    const sources = ['../app.css', '../App.svelte', '../AppGlobalHost.svelte', '../components/shared/Toast.svelte'].map(read);
    for (const source of sources) {
      const uses = source.match(/var\(--rp-chrome-top,\s*0px\)/g) ?? [];
      expect(uses.length, 'every consumer carries the 0px fallback').toBeGreaterThan(0);
    }
  });
});

describe('MOR-3845 — fixed top-anchored surfaces respect --rp-chrome-top', () => {
  // CSS-class surfaces: the rule that styles the class must consume the
  // chrome inset. Full-screen backdrops/overlays (inset: 0), top-edge
  // badges, popovers and toasts. Bottom-anchored and JS-positioned fixed
  // elements are outside the contract and stay untouched.
  const CLASS_SURFACES: readonly { file: string; cssClass: string }[] = [
    { file: '../App.svelte', cssClass: '.error-overlay' },
    { file: '../AppGlobalHost.svelte', cssClass: '.global-tx' },
    { file: '../AppGlobalHost.svelte', cssClass: '.global-tx-fault' },
    { file: '../components/shared/Toast.svelte', cssClass: '.toast-container' },
    { file: '../components-v2/dialogs/ConfirmDialog.svelte', cssClass: '.modal-backdrop' },
    { file: '../components-v2/dialogs/SendReportDialog.svelte', cssClass: '.modal-backdrop' },
    { file: '../components-v2/panels/FilterPanel.svelte', cssClass: '.modal-backdrop' },
    { file: '../components-v2/panels/TxPanel.svelte', cssClass: '.modal-backdrop' },
    { file: '../components-v2/panels/DspPanel.svelte', cssClass: '.menu-backdrop' },
    { file: '../components-v2/controls/AttenuatorControl.svelte', cssClass: '.menu-backdrop' },
    { file: '../components-v2/controls/BottomSheet.svelte', cssClass: '.m-sheet-backdrop' },
    { file: '../components-v2/controls/ManagedTotStatusControl.svelte', cssClass: '.managed-tot-popover' },
    { file: '../components-v2/layout/KeyboardHandler.svelte', cssClass: '.keyboard-help-overlay' },
    { file: '../components-v2/layout/KeyboardHandler.svelte', cssClass: '.keyboard-leader-pill' },
    { file: '../components-v2/layout/StatusBar.svelte', cssClass: '.np-backdrop' },
    { file: '../components-v2/layout/StatusBar.svelte', cssClass: '.np-detail' },
    { file: '../components-v2/layout/MobileRadioLayout.svelte', cssClass: '.m-step-picker-backdrop' },
    { file: '../components-v2/layout/RadioLayout.svelte', cssClass: '.settings-backdrop' },
    { file: '../components/spectrum/BandPlanOverlay.svelte', cssClass: '.popup-backdrop' },
    { file: '../components/spectrum/EiBiBrowser.svelte', cssClass: '.eibi-backdrop' },
    { file: '../components/spectrum/ScopeMorePanel.svelte', cssClass: '.scope-more-backdrop' },
    { file: '../components/spectrum/ScopeSettingsPopover.svelte', cssClass: '.popover-backdrop' },
    { file: '../components/spectrum/SpectrumToolbar.svelte', cssClass: '.layer-dropdown-backdrop' },
    { file: '../components/spectrum/SpectrumToolbar.svelte', cssClass: '.popover-backdrop' },
    { file: '../components/spectrum/SpectrumPanel.svelte', cssClass: '.spectrum-panel.fullscreen' },
    { file: '../semantic/FrequencyEntryDialog.svelte', cssClass: '.frequency-entry-backdrop' },
    { file: '../lib/local-extensions/LocalExtensionsHost.svelte', cssClass: '.local-extension-host' },
  ];

  it.each(CLASS_SURFACES)('$file — $cssClass offsets below the chrome strip', ({ file, cssClass }) => {
    const rules = cssRules(read(file));
    // Match the selector token exactly (`.global-tx` must not be satisfied
    // by `.global-tx-fault`, `.managed-tot-popover` not by its backdrop),
    // and accept ANY rule carrying that token: a class may be styled by a
    // shared block plus its own top-edge rule.
    const honoured = rules.some(
      ({ selector, body }) =>
        selector.split(',').some((token) => token.trim() === cssClass)
        && CHROME_VAR.test(body),
    );
    expect(honoured, `${file}: ${cssClass} must reference --rp-chrome-top`).toBe(true);
  });

  it('starts the powered-off overlay below the bar edge or the chrome strip, whichever is lower', () => {
    // Inline style (MOR-1240 pattern): the max() keeps the overlay below a
    // mounted status bar AND below the reserved chrome strip.
    expect(read('../AppGlobalHost.svelte'))
      .toContain('max(var(--rp-status-bar-bottom, 0px), var(--rp-chrome-top, 0px))');
  });

  it('shrinks the fixed landscape shell instead of sliding it under the bar', () => {
    const rules = cssRules(read('../components-v2/layout/MobileRadioLayout.svelte'));
    const rule = rules.find(({ selector }) => selector.includes('.m-landscape'));
    expect(rule, 'MobileRadioLayout must style .m-landscape').toBeDefined();
    expect(rule!.body).toMatch(/top:\s*var\(--rp-chrome-top,\s*0px\)/);
    expect(rule!.body).toMatch(/height:\s*calc\(100dvh - var\(--rp-chrome-top,\s*0px\)\)/);
  });
});
