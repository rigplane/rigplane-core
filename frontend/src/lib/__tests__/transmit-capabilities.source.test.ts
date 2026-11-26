import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'svelte/compiler';
import { CAPABILITY_ATTRIBUTE, CAPABILITY_DATA_KEY, TRANSMIT_CAPABILITIES, MARKED_CONTROL_HANDLERS, NON_DOM_TX_PATHS } from '../transmit-capabilities';

const testDirectory = dirname(fileURLToPath(import.meta.url));
const sourceDirectory = resolve(testDirectory, '../..');
const defaultKeymap = readFileSync(resolve(testDirectory, '../../../../rigs/_keyboard-default.toml'), 'utf8');

const sources = Object.fromEntries(Object.entries(import.meta.glob<string>(
  '../../**/*.{svelte,ts}', { query: '?raw', import: 'default', eager: true },
)).map(([path, source]) => [`../../${relative(sourceDirectory, resolve(testDirectory, path))}`, source]));
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
  "../../semantic/VfoOperationSeatHost.svelte",
]);

type Node = { type?: string; name?: string; start?: number; end?: number; attributes?: Node[]; [key: string]: unknown };
const isNode = (value: unknown): value is Node => !!value && typeof value === 'object' && 'type' in value;
const text = (node: Node, source: string) => source.slice(node.start, node.end).replace(/\s+/gu, ' ').trim();

// NOT_TX is a reviewed inventory of exact unmarked control sites in files that
// also host TX controls. Values are reasons, not blanket file exemptions.
// Duplicate sites are counted: adding an identical button still needs review.
const notTx = (reason: string, count = 1) => ({ count, reason });
const NOT_TX: Record<string, Record<string, { count: number; reason: string }>> = {
  "../../components-v2/controls/ManagedTotControl.svelte": {
    "input data-testid=\"managed-tot-draft\"": notTx("Edits a local draft; only Save applies the transmit timeout."),
  },
  "../../components-v2/dialogs/ConfirmDialog.svelte": {
    "button data-testid=\"confirm-dialog-cancel\"": notTx("Dismisses UI; sends no TX command."),
    "button data-testid=\"confirm-dialog-close\"": notTx("Dismisses UI; sends no TX command."),
  },
  "../../components-v2/layout/MobileRadioLayout.svelte": {
    "div class=\"m-step-picker-backdrop\"": notTx("Dismisses the receive tuning-step editor.", 2),
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
    "button class=\"m-tx-info\"": notTx("Opens the RF-power editor; the ValueControl inside carries the operation marker."),
  },
  "../../components-v2/layout/RadioLayout.svelte": {
    "button class=\"settings-close\"": notTx("Dismisses UI; sends no TX command."),
    "button class=\"standard-tx-disclosure\"": notTx("Opens an editor; the operation controls inside carry their own markers.", 2),
    "button class=\"standard-tx-settings-trigger\"": notTx("Opens an editor; the operation controls inside carry their own markers.", 3),
  },
  "../../components-v2/layout/StatusBar.svelte": {
    "select class=\"skin-select\"": notTx("Selects the local presentation; sends no radio command."),
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
    "ValueControl binding={cwPitchBinding}": notTx("Receive CW audio pitch; does not change keying."),
    "HardwareButton onclick={() => onApfChange(apfMode > 0 ? 0 : 1)}": notTx("Receive audio peak filtering."),
    "HardwareButton onclick={() => onAutoTune()}": notTx("RX CW frequency correction; cannot key TX."),
    "HardwareButton onclick={() => onTwinPeakToggle()}": notTx("Receive audio peak filtering."),
  },
  "../../components-v2/panels/EssentialsPanel.svelte": {
    "ValueControl binding={afLevelBinding}": notTx("Receive AF volume."),
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
  "../../components-v2/wiring/SemanticRadioSurfaces.svelte": {
    "button data-testid=\"memory-open\"": notTx("Opens the memory browser; sends no radio command."),
  },
  "../../semantic/AntennaInstrumentHost.svelte": {
    "ControlInstrumentRendererHost seat={rxAntSeat}": notTx("Receive-only antenna selection."),
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
    "ControlInstrumentRendererHost seat={focusSeat}": notTx("Selects the receive/local listening focus."),
    "ControlInstrumentRendererHost seat={monitorSeat}": notTx("Selects receive/local listening routing, not the TX microphone input."),
    "ControlInstrumentRendererHost seat={splitSeat}": notTx("Selects receive/local stereo routing."),
    "ControlInstrumentRendererHost seat={splitToggleSeat}": notTx("Selects receive/local stereo routing."),
    "ValueControl binding={channelGainBindings[channel]}": notTx("Receive audio channel gain."),
    "ValueControl binding={receiver === undefined ? afLevelBinding : receiverAfBindings[receiver]}": notTx("Receive AF volume."),
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
  "../../semantic/VfoOperationSeatHost.svelte": {
    "ControlInstrumentRendererHost seat={dualWatchSeat}": notTx("Receive dual-watch selection."),
    "ControlInstrumentRendererHost seat={receiverSeat}": notTx("Selects the active receiver; does not swap/copy the transmit VFO."),
  },
  "../../semantic/VfoSurface.svelte": {
    "HardwareButton onclick={() => selectVfo(vfo)}": notTx("Selects the active receiver; does not swap/copy the transmit VFO."),
    "button class=\"vfo-select\"": notTx("Selects the active receiver; does not swap/copy the transmit VFO.", 3),
  },
};

// Reviewed hosts outside the operation contract, with exact control counts.
// New files and added controls must be classified even behind opaque callbacks.
// A recognized operation or a marker moves a host into the per-control audit.
const NON_OPERATION_HOSTS: Record<string, { count: number; reason: string }> = {
  "../../components/shared/Toast.svelte": notTx("Dismisses notifications.", 1),
  "../../components/spectrum/BandPlanOverlay.svelte": notTx("Receive spectrum display, source and view settings.", 3),
  "../../components/spectrum/DxOverlay.svelte": notTx("Receive spectrum display, source and view settings.", 1),
  "../../components/spectrum/EiBiBrowser.svelte": notTx("Receive spectrum display, source and view settings.", 13),
  "../../components/spectrum/ScopeFlatKey.svelte": notTx("Receive spectrum display, source and view settings.", 1),
  "../../components/spectrum/ScopeSettingsPopover.svelte": notTx("Receive spectrum display, source and view settings.", 8),
  "../../components/spectrum/SpectrumPanel.svelte": notTx("Receive spectrum display, source and view settings.", 9),
  "../../components/spectrum/SpectrumToolbar.svelte": notTx("Receive spectrum display, source and view settings.", 64),
  "../../components/spectrum/SpectrumZoomOverlay.svelte": notTx("Receive spectrum display, source and view settings.", 4),
  "../../components/spectrum/StackedTraceCanvas.svelte": notTx("Receive spectrum display, source and view settings.", 3),
  "../../components-v2/controls/AttenuatorControl.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 4),
  "../../components-v2/controls/BandSelector.svelte": notTx("Ordinary receive frequency/band selection.", 6),
  "../../components-v2/controls/CollapsiblePanel.svelte": notTx("Discloses local UI.", 2),
  "../../components-v2/controls/ControlButtonDemo.svelte": notTx("Local control demonstration; no live radio command.", 82),
  "../../components-v2/controls/InstallPrompt.svelte": notTx("Installs/dismisses the local app prompt.", 2),
  "../../components-v2/controls/LanguageSelector.svelte": notTx("Selects the local UI locale.", 1),
  "../../components-v2/controls/ManagedTotStatusControl.svelte": notTx("Opens the timeout editor; Save owns the operation marker.", 2),
  "../../components-v2/controls/SegmentedButton.svelte": notTx("Generic renderer; operation markers belong to its callers.", 1),
  "../../components-v2/controls/StatusBadge.svelte": notTx("Generic renderer; operation markers belong to its callers.", 1),
  "../../components-v2/controls/ThemePicker.svelte": notTx("Selects the local UI theme.", 4),
  "../../components-v2/controls/ValueControlLab.svelte": notTx("Local scalar demonstration; no live radio command.", 6),
  "../../components-v2/controls/WorkspaceImportExport.svelte": notTx("Imports/exports local workspace preferences.", 3),
  "../../components-v2/controls/WorkspaceSettingsPanel.svelte": notTx("Edits local workspace preferences.", 7),
  "../../components-v2/controls/value-control/BipolarRenderer.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/controls/value-control/DiscreteRenderer.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/controls/value-control/DualParamRenderer.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/controls/value-control/HBarRenderer.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/controls/value-control/KnobRenderer.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/controls/value-control/skins/ProfessionalKnob.svelte": notTx("Generic scalar renderer; the operation marker belongs to its caller.", 1),
  "../../components-v2/dialogs/SendReportDialog.svelte": notTx("Edits/submits the report UI; no radio operation.", 12),
  "../../components-v2/layout/KeyboardHandler.svelte": notTx("Opens/closes the shortcut help.", 2),
  "../../components-v2/layout/LeftSidebar.svelte": notTx("Opens sidebar settings.", 1),
  "../../components-v2/layout/MobileNav.svelte": notTx("Selects local navigation.", 1),
  "../../components-v2/layout/mobile-chip-bar.svelte": notTx("Opens mobile editors.", 1),
  "../../components-v2/panels/AgcPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 1),
  "../../components-v2/panels/AudioRoutingControl.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 4),
  "../../components-v2/panels/DockMeterPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 3),
  "../../components-v2/panels/DspPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 18),
  "../../components-v2/panels/FilterPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 15),
  "../../components-v2/panels/MemoryPanel.svelte": notTx("Memory browser and ordinary frequency/mode recall.", 12),
  "../../components-v2/panels/RfFrontEnd.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 5),
  "../../components-v2/panels/RxAudioPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 2),
  "../../components-v2/panels/ScanPanel.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 4),
  "../../components-v2/panels/lcd/AmberCockpit.svelte": notTx("Selects the receive meter source.", 1),
  "../../components-v2/panels/lcd/AmberMemoryStrip.svelte": notTx("Memory browser and ordinary frequency/mode recall.", 1),
  "../../components-v2/panels/lcd/LcdContrastControl.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 1),
  "../../components-v2/panels/lcd/LcdDisplayModeControl.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 1),
  "../../components-v2/vfo/ActiveReceiverToggle.svelte": notTx("Selects the active receiver.", 2),
  "../../components-v2/vfo/VfoPanel.svelte": notTx("Receiver/slot selection and ordinary frequency/mode editing.", 2),
  "../../lib/Button/ControlButton.svelte": notTx("Generic button renderer; operation markers are forwarded by its callers.", 1),
  "../../lib/Button/DotButton.svelte": notTx("Generic button renderer; operation markers are forwarded by its callers.", 1),
  "../../lib/Button/FillButton.svelte": notTx("Generic button renderer; operation markers are forwarded by its callers.", 1),
  "../../lib/Button/HardwareButton.svelte": notTx("Generic button renderer; operation markers are forwarded by its callers.", 1),
  "../../lib/Button/HardwarePlainButton.svelte": notTx("Generic button renderer; operation markers are forwarded by its callers.", 1),
  "../../lib/local-extensions/LocalExtensionsHost.svelte": notTx("Extension permission/editor UI; radio-power confirmation delegates to the marked ConfirmDialog.", 6),
  "../../semantic/BandFrequencyEntry.svelte": notTx("Ordinary receive frequency entry.", 2),
  "../../semantic/BandInstrumentHost.svelte": notTx("Ordinary receive frequency/band selection.", 4),
  "../../semantic/DspInstrumentHost.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 13),
  "../../semantic/DspScalarHost.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 2),
  "../../semantic/DspSurface.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 3),
  "../../semantic/FilterSurface.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 4),
  "../../semantic/FrequencyEntryDialog.svelte": notTx("Ordinary receive frequency entry.", 1),
  "../../semantic/MemorySurface.svelte": notTx("Memory browser and ordinary frequency/mode recall.", 12),
  "../../semantic/RadioMemoryList.svelte": notTx("Memory browser and ordinary frequency/mode recall.", 4),
  "../../semantic/RfFrontEndInstrumentHost.svelte": notTx("Receive DSP, filter, gain, audio or display settings; no TX operation.", 7),
  "../../semantic/ScopeControlsSurface.svelte": notTx("Receive spectrum display, source and view settings.", 27),
  "../../skins/dual-sdr-face/DualSdrFace.svelte": notTx("Read-only TX placeholders and receive preamp; production supplies no command callback.", 4),
};

// Find operation hosts from command/callback use, independently of markers.
// Generic leaf renderers inherit the composite root's marker at their callsite.
const TX_OPERATION = /\b(?:onRequestKey|onPttDown|onPttToggle|onTransmit|transmitOn|pttOn|onRfPowerChange|setRfPower|set_rf_power|onAtuTune|onVoxToggle|onBreakInChange|onBreakInMode|onDataModeChange|onSplitToggle|onXitToggle|onRitToggle|onSelectPort|onReversePaddleToggle|TxAuxLevelField|TxAuxToggleField|set_break_in|set_vox|set_tx_antenna|set_repeater_tone)\b/u;

function unmarkedControls(source: string): string[] {
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
      && (['button', 'input', 'select', 'ValueControl', 'ControlInstrumentRendererHost'].includes(value.name ?? '')
        || value.name?.endsWith('Button')
        || attrs.some((attr) => attr.name === 'role' && /^role=["'](?:button|slider|spinbutton)["']$/u.test(text(attr, source)))) && !covered) {
      const attr = attrs.find((a) => a.name === 'data-testid')
        ?? attrs.find((a) => a.name === 'class')
        ?? attrs.find((a) => ['onclick', 'onchange', 'oninput', 'binding', 'seat'].includes(a.name ?? ''));
      buttons.push(`${value.name} ${attr ? text(attr, source) : '(no identity)'}`);
    }
    for (const [key, child] of Object.entries(value)) {
      if (!['attributes', 'css', 'instance', 'module'].includes(key)) visit(child, covered);
    }
  }
  visit(parse(source, { modern: true }).fragment);
  return buttons;
}

function unmarkedControlsBySource(entries: ReadonlyArray<readonly [string, string]>) {
  const actual: Record<string, Record<string, number>> = {};
  for (const [path, source] of entries) {
    if (!path.endsWith('.svelte')) continue;
    const controls = unmarkedControls(source);
    const reviewed = NON_OPERATION_HOSTS[path];
    const operationHost = knownHosts.has(path) || TX_OPERATION.test(source)
      || /\bCAPABILITY_(ATTRIBUTE|DATA_KEY)\b/u.test(source);
    if (reviewed && !operationHost && controls.length === reviewed.count) continue;
    if (controls.length || reviewed) actual[path] = Object.fromEntries([...new Set(controls)].sort()
      .map((control) => [control, controls.filter((other) => other === control).length]));
  }
  return actual;
}

function undocumentedKeyboardActions(inventory: Readonly<Record<string, unknown>>): string[] {
  const commands = sources['../../lib/runtime/commands/panel-commands.ts'];
  const keyboard = commands.slice(commands.indexOf('export function dispatchKeyboardRadioAction'));
  const cases = [...keyboard.matchAll(/case '([^']+)':([\s\S]*?)(?=\n\s*case '|\n\s*default:|$)/gu)];
  const bound = [...defaultKeymap.matchAll(/^action\s*=\s*"([^"]+)"/gmu)].map((match) => match[1]);
  const affected = cases.filter(([, , body]) => Object.keys(MARKED_CONTROL_HANDLERS)
    .some((handler) => body.includes(handler))).map(([, action]) => action);
  return [...new Set(bound.filter((action) => affected.includes(action)))]
    .filter((action) => !(action in inventory));
}

describe('transmit capability source ownership', () => {
  it('rejects a newly added TX component without any marker or contract import', () => {
    const path = '../../semantic/NewTransmitControl.svelte';
    const source = '<script>let { perform } = $props();</script><button onclick={perform}>TX</button>';
    expect(unmarkedControlsBySource([[path, source]])).toEqual({
      [path]: { 'button onclick={perform}': 1 },
    });
  });

  it.each(['input', 'select', 'ValueControl'])('discovers an unmarked TX %s in a new component', (control) => {
    const path = '../../semantic/NewRfPowerControl.svelte';
    const source = `<script>let { onRfPowerChange } = $props();</script><${control} onchange={onRfPowerChange} />`;
    expect(unmarkedControlsBySource([[path, source]])).toEqual({
      [path]: { [`${control} onchange={onRfPowerChange}`]: 1 },
    });
  });

  it('discovers an unmarked custom scalar in a new component', () => {
    const path = '../../semantic/NewScalar.svelte';
    const source = '<script>let { perform } = $props();</script><div role="slider" onclick={perform}></div>';
    expect(unmarkedControlsBySource([[path, source]])).toEqual({
      [path]: { 'div onclick={perform}': 1 },
    });
  });

  it('rejects an extra control added to a reviewed receive-only host', () => {
    const path = '../../components-v2/controls/LanguageSelector.svelte';
    const source = sources[path] + '<button onclick={() => {}}>TX</button>';
    expect(unmarkedControlsBySource([[path, source]])[path]).toBeDefined();
  });

  it('rejects removal of the semantic RF-power composite marker', () => {
    const path = '../../semantic/TxAuxScalarHost.svelte';
    const source = sources[path];
    const withoutMarker = source.replace(/^.*\{\s*\[CAPABILITY_ATTRIBUTE\].*\n/gmu, '');
    expect(withoutMarker).not.toBe(source);
    expect(unmarkedControlsBySource([[path, source]])).toEqual({});
    expect(unmarkedControlsBySource([[path, withoutMarker]])[path]).toEqual({
      'ValueControl binding={bindings[field]}': 2,
    });
  });

  it('rejects removal of the DATA-mode keyboard inventory entry', () => {
    const { cycle_data_mode: removed, ...withoutDataMode } = NON_DOM_TX_PATHS;
    expect(removed.triggers).toContainEqual({
      file: 'lib/runtime/commands/panel-commands.ts', symbol: "case 'cycle_data_mode':",
    });
    expect(undocumentedKeyboardActions(withoutDataMode)).toContain('cycle_data_mode');
    expect(undocumentedKeyboardActions(NON_DOM_TX_PATHS)).toEqual([]);
  });

  it('documents every default keyboard action that calls a marked-control handler', () => {
    // Resolve action aliases from the real switch, rather than maintaining a
    // second action allow-list in this test. The contract owns the shared
    // handlers used by marked controls and their capability mapping.
    const commands = sources['../../lib/runtime/commands/panel-commands.ts'];
    expect(commands).toBe(readFileSync(resolve(testDirectory,
      '../../lib/runtime/commands/panel-commands.ts'), 'utf8'));
    const keyboard = commands.slice(commands.indexOf('export function dispatchKeyboardRadioAction'));
    const cases = [...keyboard.matchAll(/case '([^']+)':([\s\S]*?)(?=\n\s*case '|\n\s*default:|$)/gu)];
    const bound = [...defaultKeymap.matchAll(/^action\s*=\s*"([^"]+)"/gmu)].map((match) => match[1]);
    const handlers = Object.keys(MARKED_CONTROL_HANDLERS);
    expect(cases.length).toBeGreaterThan(0);
    expect(bound.length).toBeGreaterThan(0);
    const affected = cases.filter(([, , body]) => handlers.some((handler) => body.includes(handler)))
      .map(([, action]) => action);
    expect(affected).toContain('toggle_split');
    expect(affected).toContain('cycle_data_mode');
    const missing = undocumentedKeyboardActions(NON_DOM_TX_PATHS);
    expect(missing, `Undocumented non-DOM TX actions: ${missing.join(', ')}`).toEqual([]);
    for (const [handler, capability] of Object.entries(MARKED_CONTROL_HANDLERS)) {
      expect(keyboard, handler).toContain(handler);
      expect(Object.values(TRANSMIT_CAPABILITIES).map(({ id }) => id)).toContain(capability);
    }
    for (const [action, entry] of Object.entries(NON_DOM_TX_PATHS)) {
      expect(entry.reason.trim(), action).not.toBe('');
      expect(entry.triggers.length, action).toBeGreaterThan(0);
      for (const trigger of entry.triggers) {
        const source = sources[`../../${trigger.file}`];
        expect(source, trigger.file).toBeDefined();
        expect(source, `${action}: ${trigger.file}`).toContain(trigger.symbol);
      }
    }
  });

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

  it('requires a marker or a documented NOT_TX entry for every operation host control', () => {
    for (const path of knownHosts) expect(sources[path], path).toBeDefined();
    for (const [path, entry] of Object.entries(NON_OPERATION_HOSTS)) {
      expect(sources[path], path).toBe(readFileSync(
        resolve(testDirectory, path), 'utf8'));
      expect(entry.reason.trim(), path).not.toBe('');
    }
    const actual = unmarkedControlsBySource(product);
    const expected = Object.fromEntries(Object.entries(NOT_TX).map(([path, buttons]) => [path,
      Object.fromEntries(Object.entries(buttons).map(([button, entry]) => {
        expect(entry.reason.trim().length, `${path}: ${button}`).toBeGreaterThan(0);
        return [button, entry.count];
      })),
    ]));
    expect(actual).toEqual(expected);
  });
});
