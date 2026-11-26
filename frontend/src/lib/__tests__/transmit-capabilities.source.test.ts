import { describe, expect, it } from 'vitest';
import { parse } from 'svelte/compiler';
import { CAPABILITY_ATTRIBUTE, CAPABILITY_DATA_KEY, TRANSMIT_CAPABILITIES } from '../transmit-capabilities';

const sources = import.meta.glob('../../**/*.{svelte,ts}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const product = Object.entries(sources).filter(([path]) =>
  !/\/__tests__\/|\.(test|spec)\./u.test(path) && !path.endsWith('/transmit-capabilities.ts'));

// Pin the existing hosts as well as discovering new ones: removing a file's
// last marker/import must not remove that file from completeness enforcement.
const knownHosts = new Set([
  "../../AppGlobalHost.svelte",
  "../../components-v2/controls/ManagedTotControl.svelte",
  "../../components-v2/controls/PttFab.svelte",
  "../../components-v2/controls/TxMicProcessingControl.svelte",
  "../../components-v2/dialogs/ConfirmDialog.svelte",
  "../../components-v2/layout/MobileRadioLayout.svelte",
  "../../components-v2/layout/RadioLayout.svelte",
  "../../components-v2/layout/StatusBar.svelte",
  "../../components-v2/panels/AntennaPanel.svelte",
  "../../components-v2/panels/CwPanel.svelte",
  "../../components-v2/panels/EssentialsPanel.svelte",
  "../../components-v2/panels/ModInputTxWarning.svelte",
  "../../components-v2/panels/ModePanel.svelte",
  "../../components-v2/panels/RitXitPanel.svelte",
  "../../components-v2/panels/TxPanel.svelte",
  "../../components-v2/panels/VoxPanel.svelte",
  "../../components-v2/panels/lcd/VfoControlPanel.svelte",
  "../../primitives/control-instruments/ControlInstrumentRendererHost.svelte",
  "../../semantic/AntennaInstrumentHost.svelte",
  "../../semantic/CwKeyerInstrumentHost.svelte",
  "../../semantic/CwKeyerSurface.svelte",
  "../../semantic/FilterInstrumentHost.svelte",
  "../../semantic/RepeaterSurface.svelte",
  "../../semantic/RitXitScanInstrumentHost.svelte",
  "../../semantic/RitXitScanSurface.svelte",
  "../../semantic/RxAudioInstrumentHost.svelte",
  "../../semantic/RxTxSurface.svelte",
  "../../semantic/TxAuxFiniteHost.svelte",
  "../../semantic/TxAuxScalarHost.svelte",
  "../../semantic/VfoOperationGroup.svelte",
]);

type Node = { type?: string; name?: string; start?: number; end?: number; attributes?: Node[]; [key: string]: unknown };
const isNode = (value: unknown): value is Node => !!value && typeof value === 'object' && 'type' in value;
const text = (node: Node, source: string) => source.slice(node.start, node.end).replace(/\s+/gu, ' ').trim();

// NOT_TX is a reviewed inventory of exact unmarked button sites in files that
// also host TX controls. Values are reasons, not blanket file exemptions.
// Duplicate sites are counted: adding an identical button still needs review.
const notTx = (reason: string, count = 1) => ({ count, reason });
const NOT_TX: Record<string, Record<string, { count: number; reason: string }>> = {
  "../../components-v2/dialogs/ConfirmDialog.svelte": {
    "button data-testid=\"confirm-dialog-cancel\"": notTx("Dismisses UI; sends no TX command."),
    "button data-testid=\"confirm-dialog-close\"": notTx("Dismisses UI; sends no TX command."),
  },
  "../../components-v2/layout/MobileRadioLayout.svelte": {
    "HardwareButton onclick={() => { modeHandlers.onModeChange(m); modeModalOpen = false; }}": notTx("Ordinary operating-mode selection; outside the TX-operation marker contract."),
    "button class=\"m-ls-mode-btn\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"m-ls-step-btn\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"m-ls-tune-btn\"": notTx("Ordinary VFO frequency/step adjustment; outside the TX-operation marker contract.", 2),
    "button class=\"m-ls-unkey\"": notTx("Emergency receive/release must remain reachable."),
    "button class=\"m-receiver-pill\"": notTx("Selects the receiver in the UI.", 2),
    "button class=\"m-settings-btn\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"m-step-option\"": notTx("Ordinary VFO frequency/step adjustment; outside the TX-operation marker contract.", 2),
    "button class=\"m-tune-btn m-tune-fast\"": notTx("Ordinary VFO frequency/step adjustment; outside the TX-operation marker contract.", 2),
    "button class=\"m-tune-btn\"": notTx("Ordinary VFO frequency/step adjustment; outside the TX-operation marker contract.", 2),
    "button class=\"m-tune-step\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"m-tx-settings-btn\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
  },
  "../../components-v2/layout/RadioLayout.svelte": {
    "button class=\"settings-close\"": notTx("Dismisses UI; sends no TX command."),
    "button class=\"standard-tx-disclosure\"": notTx("Opens an editor; the operation controls inside carry their own markers.", 2),
    "button class=\"standard-tx-settings-trigger\"": notTx("Opens an editor; the operation controls inside carry their own markers.", 3),
  },
  "../../components-v2/layout/StatusBar.svelte": {
    "button class=\"control-btn report-btn\"": notTx("Opens the report dialog."),
    "button class=\"control-btn settings-btn\"": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"control-btn\"": notTx("Connects or disconnects the transport; outside the TX-operation marker contract."),
    "button class=\"now-playing\"": notTx("Local receive-audio player UI."),
    "button class=\"np-close\"": notTx("Dismisses UI; sends no TX command."),
  },
  "../../components-v2/panels/AntennaPanel.svelte": {
    "HardwareButton onclick={onToggleRxAnt}": notTx("Receive-only antenna selection."),
  },
  "../../components-v2/panels/CwPanel.svelte": {
    "HardwareButton onclick={() => onApfChange(apfMode > 0 ? 0 : 1)}": notTx("Receive audio peak filtering."),
    "HardwareButton onclick={() => onAutoTune()}": notTx("RX CW frequency correction; cannot key TX."),
    "HardwareButton onclick={() => onTwinPeakToggle()}": notTx("Receive audio peak filtering."),
  },
  "../../components-v2/panels/EssentialsPanel.svelte": {
    "HardwareButton onclick={() => onFilterChange(idx + 1)}": notTx("Receive filter selection."),
    "HardwareButton onclick={() => onModeChange(m)}": notTx("Ordinary operating-mode selection; outside the TX-operation marker contract."),
    "HardwareButton onclick={() => onMonitorModeChange(opt)}": notTx("Receive/local listening routing; does not select the TX microphone input."),
    "HardwareButton onclick={() => onNbToggle(!dsp.nbActive)}": notTx("Receive DSP noise/notch filtering."),
    "HardwareButton onclick={() => onNotchModeChange(dsp.notchMode !== 'off' ? 'off' : 'auto')}": notTx("Receive DSP noise/notch filtering."),
    "HardwareButton onclick={() => onNrModeChange(dsp.nrMode > 0 ? 0 : 1)}": notTx("Receive DSP noise/notch filtering."),
    "HardwareButton onclick={onFilterMore}": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "HardwareButton onclick={onModeMore}": notTx("Opens an editor; the operation controls inside carry their own markers."),
  },
  "../../components-v2/panels/ModInputTxWarning.svelte": {
    "button data-testid=\"mod-input-dismiss\"": notTx("Dismisses UI; sends no TX command."),
  },
  "../../components-v2/panels/ModePanel.svelte": {
    "HardwareButton onclick={() => onModeChange(mode)}": notTx("Ordinary operating-mode selection; outside the TX-operation marker contract."),
  },
  "../../components-v2/panels/TxPanel.svelte": {
    "HardwareButton onclick={() => { if (!lpSuppressClick) openSettings(); lpSuppressClick = false; }}": notTx("Opens an editor; the operation controls inside carry their own markers."),
    "button class=\"modal-backdrop\"": notTx("Dismisses UI; sends no TX command."),
    "button class=\"modal-close\"": notTx("Dismisses UI; sends no TX command."),
  },
  "../../components-v2/panels/lcd/VfoControlPanel.svelte": {
    "button class=\"lcd-btn\"": notTx("Receive dual-watch selection."),
  },
  "../../semantic/AntennaInstrumentHost.svelte": {
    "button data-testid=\"antenna-rx-toggle\"": notTx("Receive-only antenna selection."),
  },
  "../../semantic/CwKeyerSurface.svelte": {
    "button class=\"cw-keyer-toggle cw-extra-toggle\"": notTx("Discloses additional controls; sends no command."),
    "button data-testid=\"cw-keyer-apf-on\"": notTx("Receive audio peak filtering."),
    "button data-testid=\"cw-keyer-auto-tune\"": notTx("RX CW frequency correction; cannot key TX."),
    "button data-testid=\"cw-keyer-twin-peak-toggle\"": notTx("Receive audio peak filtering."),
    "button data-testid={`cw-keyer-apf-${label}`}": notTx("Receive audio peak filtering."),
  },
  "../../semantic/FilterInstrumentHost.svelte": {
    "HardwareButton onclick={() => modeBehavior.invoke(choice)}": notTx("Ordinary operating-mode selection; outside the TX-operation marker contract."),
    "button data-testid={`filter-mode-${choice}`}": notTx("Ordinary operating-mode selection; outside the TX-operation marker contract."),
    "button data-testid={`filter-select-${index + 1}`}": notTx("Receive filter selection."),
    "button data-testid={`filter-shape-${value}`}": notTx("Receive filter selection."),
  },
  "../../semantic/RitXitScanSurface.svelte": {
    "button data-testid=\"scan-toggle\"": notTx("Receive scanning controls."),
    "button data-testid={`scan-resume-0x${hex(value)}`}": notTx("Receive scanning controls."),
    "button data-testid={`scan-span-0x${hex(value)}`}": notTx("Receive scanning controls."),
    "button data-testid={`scan-type-0x${hex(value)}`}": notTx("Receive scanning controls."),
  },
  "../../semantic/RxAudioInstrumentHost.svelte": {
    "button data-testid=\"rx-audio-split-toggle\"": notTx("Receive/local listening routing; does not select the TX microphone input."),
    "button data-testid={`rx-audio-focus-${focus}`}": notTx("Receive/local listening routing; does not select the TX microphone input."),
    "button data-testid={`rx-audio-monitor-${mode}`}": notTx("Receive/local listening routing; does not select the TX microphone input."),
    "button data-testid={`rx-audio-split-${label}`}": notTx("Receive/local listening routing; does not select the TX microphone input."),
  },
  "../../semantic/RxTxSurface.svelte": {
    "button data-testid=\"rx-tx-unkey\"": notTx("Emergency receive/release must remain reachable."),
  },
  "../../semantic/VfoOperationGroup.svelte": {
    "HardwareButton onclick={() => emit({ kind: 'toggle-dial-lock' })}": notTx("Locks the tuning dial; does not key TX."),
    "HardwareButton onclick={() => emit({ kind: 'toggle-dual-watch' })}": notTx("Receive dual-watch selection."),
    "button class=\"fact-toggle\"": notTx("Selects the active receiver; does not swap/copy the transmit VFO."),
  },
};

function unmarkedButtons(source: string): string[] {
  const buttons: string[] = [];
  function visit(value: unknown, marked = false): void {
    if (Array.isArray(value)) { value.forEach((child) => visit(child, marked)); return; }
    if (!isNode(value)) return;
    const attrs = value.attributes ?? [];
    const covered = marked || attrs.some((attr) => {
      const code = text(attr, source);
      return code.includes(CAPABILITY_ATTRIBUTE) // rejects hardcoded markers in the scan below
        || code.includes('CAPABILITY_ATTRIBUTE') || code.includes('CAPABILITY_DATA_KEY')
        || (attr.name === 'capability' && /TRANSMIT_CAPABILITIES|capability/u.test(code));
    });
    if ((value.type === 'RegularElement' || value.type === 'Component')
      && (value.name === 'button' || value.name?.endsWith('Button')) && !covered) {
      const attr = attrs.find((a) => a.name === 'data-testid')
        ?? attrs.find((a) => a.name === 'class')
        ?? attrs.find((a) => a.name === 'onclick');
      buttons.push(`${value.name} ${attr ? text(attr, source) : '(no identity)'}`);
    }
    for (const [key, child] of Object.entries(value)) {
      if (!['attributes', 'css', 'instance', 'module'].includes(key)) visit(child, covered);
    }
  }
  visit(parse(source, { modern: true }).fragment);
  return buttons;
}

describe('transmit capability source ownership', () => {
  it('keeps ID and attribute literals in the contract, with imports at consumers', () => {
    const ids = Object.values(TRANSMIT_CAPABILITIES).map(({ id }) => id);
    const literals = [...ids, CAPABILITY_ATTRIBUTE, CAPABILITY_DATA_KEY];
    const violations: string[] = [];
    for (const [path, source] of product) {
      for (const literal of literals) {
        const escaped = literal.replace(/[.*+?^${}()|[\]\\]/gu, '\\$&');
        if (new RegExp(`(['"\\x60])${escaped}\\1`, 'u').test(source)
          || (literal === CAPABILITY_ATTRIBUTE && source.includes(literal))) {
          violations.push(`${path}: literal ${literal}`);
        }
      }
      if (/\bCAPABILITY_(ATTRIBUTE|DATA_KEY)\b/u.test(source)) {
        expect(source, path).toMatch(/import\s+[\s\S]*?\bCAPABILITY_(?:ATTRIBUTE|DATA_KEY)\b[\s\S]*?from\s+['"][^'"]*transmit-capabilities['"]/u);
      }
    }
    expect(violations).toEqual([]);
  });

  it('requires a marker or a documented NOT_TX entry for every button in a marked host', () => {
    for (const path of knownHosts) expect(sources[path], path).toBeDefined();
    const actual: Record<string, Record<string, number>> = {};
    for (const [path, source] of product) {
      if (!path.endsWith('.svelte') || (!knownHosts.has(path)
        && !/\bCAPABILITY_(ATTRIBUTE|DATA_KEY)\b/u.test(source))) continue;
      const buttons = unmarkedButtons(source);
      if (buttons.length) actual[path] = Object.fromEntries([...new Set(buttons)].sort()
        .map((button) => [button, buttons.filter((other) => other === button).length]));
    }
    const expected = Object.fromEntries(Object.entries(NOT_TX).map(([path, buttons]) => [path,
      Object.fromEntries(Object.entries(buttons).map(([button, entry]) => {
        expect(entry.reason.trim().length, `${path}: ${button}`).toBeGreaterThan(0);
        return [button, entry.count];
      })),
    ]));
    expect(actual).toEqual(expected);
  });
});
