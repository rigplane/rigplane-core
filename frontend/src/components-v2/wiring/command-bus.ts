/**
 * Command Bus — maps v2 UI callbacks → sendCommand() WebSocket calls.
 *
 * Each `makeXxxHandlers()` returns an object of callback functions
 * matching the corresponding v2 component's event props.
 *
 * Optimistic state updates happen inside ws-client.ts `_applyOptimistic()`.
 *
 * Epic #289, Phase 2.
 */

import { sendCommand } from '$lib/transport/ws-client';
import { getRadioState, patchRadioState } from '$lib/stores/radio.svelte';
import { adjustTuningStep } from '$lib/stores/tuning.svelte';
import { dispatchKeyboardRadioAction } from '$lib/runtime/commands/panel-commands';
export {
  makeAgcHandlers,
  makeAudioRoutingHandlers,
  makeBandHandlers,
  makeCwPanelHandlers,
  makeDspHandlers,
  makeFilterHandlers,
  makeModeHandlers,
  makePresetHandlers,
  makeRfFrontEndHandlers,
  makeRitXitHandlers,
  makeRxAudioHandlers,
  makeScanHandlers,
  makeTxHandlers,
  makeAntennaHandlers,
  makeVfoHandlers,
  makeVoxHandlers,
} from '$lib/runtime/commands/panel-commands';
import type { KeyboardActionConfig } from '../layout/keyboard-map';
import { clampRef, clampSpan } from '../../components/spectrum/spectrum-toolbar-logic';

/* ── Helpers ─────────────────────────────────────────────────── */

function cmd(name: string, params: Record<string, unknown> = {}): void {
  sendCommand(name, params);
}

/* ── System Handlers ─────────────────────────────────────────── */

export function makeSystemHandlers() {
  return {
    onDialLock: (on: boolean) => cmd('set_dial_lock', { on }),
    onPowerOff: () => cmd('set_powerstat', { on: false }),
    onSpeak: () => cmd('speak', { mode: 0 }),
  };
}

/**
 * MOR-1311 (vocabulary slice 11B). The shipped scope-toolbar/popover command
 * vocabulary, composed unmodified — every name and parameter below is
 * `SpectrumToolbar.svelte`'s/`ScopeSettingsPopover.svelte`'s own `sendCommand`
 * call, reproduced 1:1 rather than re-derived.
 */
export function makeScopeControlsHandlers() {
  return {
    onModeChange: (mode: number) => cmd('set_scope_mode', { mode }),
    onEdgeChange: (edge: number) => cmd('set_scope_edge', { edge }),
    onSpanChange: (span: number) => cmd('set_scope_span', { span }),
    onSpeedChange: (speed: number) => cmd('set_scope_speed', { speed }),
    onHoldChange: (on: boolean) => cmd('set_scope_hold', { on }),
    onRefChange: (ref: number) => cmd('set_scope_ref', { ref }),
    onDualChange: (dual: boolean) => cmd('set_scope_dual', { dual }),
    onReceiverChange: (receiver: number) => cmd('switch_scope_receiver', { receiver }),
    onDuringTxChange: (on: boolean) => cmd('set_scope_during_tx', { on }),
    onCenterTypeChange: (center_type: number) => cmd('set_scope_center_type', { center_type }),
    onVbwChange: (narrow: boolean) => cmd('set_scope_vbw', { narrow }),
    onRbwChange: (rbw: number) => cmd('set_scope_rbw', { rbw }),
  };
}

export function makeKeyboardHandlers() {
  return {
    dispatch(action: KeyboardActionConfig): void {
      if (dispatchKeyboardRadioAction(action)) return;
      switch (action.action) {
        case 'adjust_tuning_step': {
          adjustTuningStep(action.params?.direction === 'down' ? 'down' : 'up');
          return;
        }
        case 'open_filter_settings': {
          window.dispatchEvent(new CustomEvent('rigplane:open-filter-settings'));
          return;
        }
        case 'toggle_dial_lock': {
          const on = !(getRadioState()?.dialLock ?? false);
          patchRadioState({ dialLock: on });
          cmd('set_dial_lock', { on });
          return;
        }
        case 'focus_target': {
          const target = action.params?.target;
          if (typeof target === 'string') {
            const selectors: Record<string, string> = {
              af: '[data-panel="rf-frontend"] [data-control="af-gain"]',
              rf:
                '[data-panel="rf-frontend"] [data-control="rf-sql-dual"], [data-panel="rf-frontend"] [data-control="rf-gain"]',
              filter: '[data-panel="filter"]',
              squelch:
                '[data-panel="rf-frontend"] [data-control="rf-sql-dual"], [data-panel="rf-frontend"] [data-control="squelch"]',
              mode: '[data-panel="mode"]',
              pbt: '[data-panel="filter"] [data-control="pbt-inner"]',
              waterfall: '[data-waterfall]',
              vfo: '[data-vfo="main"] .freq-display',
            };
            const el = document.querySelector(selectors[target] ?? `[data-panel="${target}"]`);
            if (el instanceof HTMLElement) {
              el.focus();
              el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
          }
          return;
        }
        case 'scope_span_step': {
          const scope = getRadioState()?.scopeControls;
          const current = scope?.span ?? 3;
          const delta = action.params?.direction === 'down' ? -1 : 1;
          const span = clampSpan(current, delta);
          cmd('set_scope_span', { span });
          return;
        }
        case 'scope_ref_step': {
          const scope = getRadioState()?.scopeControls;
          const current = scope?.refDb ?? 0;
          const delta = action.params?.direction === 'down' ? -5 : 5;
          const ref = clampRef(current, delta);
          cmd('set_scope_ref', { ref });
          return;
        }
        case 'scope_toggle_hold': {
          const scope = getRadioState()?.scopeControls;
          const on = !(scope?.hold ?? false);
          cmd('set_scope_hold', { on });
          return;
        }
        case 'scope_toggle_dual': {
          const scope = getRadioState()?.scopeControls;
          const dual = !(scope?.dual ?? false);
          cmd('set_scope_dual', { dual });
          return;
        }
        case 'scope_toggle_fst': {
          const scope = getRadioState()?.scopeControls;
          const currentSpeed = scope?.speed ?? 1;
          // Toggle FST (speed=0) vs MID (speed=1).
          const speed = currentSpeed === 0 ? 1 : 0;
          cmd('set_scope_speed', { speed });
          return;
        }
        default:
          console.warn('[keyboard] unhandled action', action.action, action.params);
      }
    },
  };
}
