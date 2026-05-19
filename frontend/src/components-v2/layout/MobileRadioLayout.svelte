<script lang="ts">
  import { runtime } from '$lib/runtime';
  import { t } from '$lib/i18n';
  import { hasTx, hasDualReceiver, hasAnyScope, hasSpectrum, receiverLabel } from '$lib/stores/capabilities.svelte';
  import { HardwareButton } from '$lib/Button';
  import SpectrumPanel from '../../components/spectrum/SpectrumPanel.svelte';
  import AmberLcdDisplay from '../panels/lcd/AmberLcdDisplay.svelte';
  import FrequencyDisplay from '../display/FrequencyDisplay.svelte';
  import LinearSMeter from '../meters/LinearSMeter.svelte';
  import CollapsiblePanel from '../controls/CollapsiblePanel.svelte';
  import BottomSheet from '../controls/BottomSheet.svelte';
  import BandSelector from '../controls/BandSelector.svelte';
  import FilterPanel from '../panels/FilterPanel.svelte';
  import RxAudioPanel from '../panels/RxAudioPanel.svelte';
  import TxPanel from '../panels/TxPanel.svelte';
  import DspPanel from '../panels/DspPanel.svelte';
  import AgcPanel from '../panels/AgcPanel.svelte';
  import RfFrontEnd from '../panels/RfFrontEnd.svelte';
  import RitXitPanel from '../panels/RitXitPanel.svelte';
  import AntennaPanel from '../panels/AntennaPanel.svelte';
  import ScanPanel from '../panels/ScanPanel.svelte';
  import CwPanel from '../panels/CwPanel.svelte';
  import DockMeterPanel from '../panels/DockMeterPanel.svelte';
  import KeyboardHandler from './KeyboardHandler.svelte';
  import MobileChipBar from './mobile-chip-bar.svelte';
  import EssentialsPanel from '../panels/EssentialsPanel.svelte';
  import PttFab from '../controls/PttFab.svelte';
  import { ValueControl, rawToPercentDisplay } from '../controls/value-control';
  import {
    Settings, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight,
    Sliders, Radio as RadioIcon,
  } from 'lucide-svelte';
  import {
    resolveVfoLayoutProfile,
    vfoLayoutStyleVars,
  } from './vfo-layout-tokens';
  import { getTxPermit } from '$lib/utils/tx-permit';
  import { getStepsForMode, formatStep, formatSValue, formatDbm, formatPower } from './mobile-layout-logic';
  import {
    toVfoProps, toVfoOpsProps, toMeterProps,
    toRfFrontEndProps, toModeProps, toFilterProps, toAgcProps, toRitXitProps,
    toBandSelectorProps, toRxAudioProps, toDspProps, toTxProps, toCwProps, toAntennaProps, toScanProps,
  } from '../wiring/state-adapter';
  import {
    makeVfoHandlers, makeMeterHandlers, makeKeyboardHandlers,
    makeRfFrontEndHandlers, makeModeHandlers, makeFilterHandlers,
    makeAgcHandlers, makeRitXitHandlers, makeBandHandlers, makePresetHandlers,
    makeRxAudioHandlers, makeDspHandlers, makeTxHandlers, makeCwPanelHandlers,
    makeSystemHandlers, makeAntennaHandlers, makeScanHandlers,
  } from '../wiring/command-bus';
  import { getKeyboardConfig } from '$lib/stores/capabilities.svelte';
  import { getTxAudioControl } from '$lib/runtime/adapters/tx-adapter';
  const txAudio = getTxAudioControl();
  import { onMount, onDestroy, untrack } from 'svelte';
  import Toast from '../../components/shared/Toast.svelte';

  // ── State — via runtime ──
  let radioState = $derived(runtime.state);
  let caps = $derived(runtime.caps);
  let keyboardConfig = $derived(getKeyboardConfig());
  let audioState = $derived(runtime.audio);
  let txCapable = $derived(hasTx());

  // ── VFO props ──
  let mainVfo = $derived(toVfoProps(radioState, 'main'));
  let subVfo = $derived(toVfoProps(radioState, 'sub'));
  let vfoOps = $derived(toVfoOpsProps(radioState, caps));
  let meter = $derived(toMeterProps(radioState));
  let mode = $derived(toModeProps(radioState, caps));
  let filter = $derived(toFilterProps(radioState, caps));
  let band = $derived(toBandSelectorProps(radioState));
  let rxAudio = $derived(toRxAudioProps(radioState, caps, audioState));
  let tx = $derived(toTxProps(radioState, caps));
  let rfFrontEnd = $derived(toRfFrontEndProps(radioState, caps));
  let agc = $derived(toAgcProps(radioState, caps));
  let ritXit = $derived(toRitXitProps(radioState, caps));
  let dsp = $derived(toDspProps(radioState, caps));
  let cw = $derived(toCwProps(radioState, caps));
  let antenna = $derived(toAntennaProps(radioState, caps));
  let scan = $derived(toScanProps(radioState));

  // ── Handlers ──
  const vfoHandlers = makeVfoHandlers();
  const meterHandlers = makeMeterHandlers();
  const keyboardHandlers = makeKeyboardHandlers();
  const modeHandlers = makeModeHandlers();
  const filterHandlers = makeFilterHandlers();
  const bandHandlers = makeBandHandlers();
  const presetHandlers = makePresetHandlers();
  const rxAudioHandlers = makeRxAudioHandlers();
  const txHandlers = makeTxHandlers();
  const rfHandlers = makeRfFrontEndHandlers();
  const agcHandlers = makeAgcHandlers();
  const ritXitHandlers = makeRitXitHandlers();
  const dspHandlers = makeDspHandlers();
  const cwHandlers = makeCwPanelHandlers();
  const antennaHandlers = makeAntennaHandlers();
  const scanHandlers = makeScanHandlers();
  const systemHandlers = makeSystemHandlers();

  // ── VFO layout ──
  let receiverDeckElement = $state<HTMLElement | null>(null);
  let receiverDeckWidth = $state<number | null>(null);
  let vfoFreqElement = $state<HTMLElement | null>(null);
  let activeReceiver = $derived((radioState?.active ?? 'MAIN') as 'MAIN' | 'SUB');

  function selectReceiver(target: 'MAIN' | 'SUB') {
    if (target === 'MAIN') {
      vfoHandlers.onMainVfoClick?.();
    } else {
      vfoHandlers.onSubVfoClick?.();
    }
    // Scroll the VFO display into view so the selection is visible to the user.
    if (vfoFreqElement && typeof vfoFreqElement.scrollIntoView === 'function') {
      vfoFreqElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
  let vfoLayoutProfile = $derived(resolveVfoLayoutProfile(receiverDeckWidth));
  let receiverDeckStyle = $derived(vfoLayoutStyleVars(vfoLayoutProfile, {
    width: receiverDeckWidth,
    overrides: {},
  }));

  // ── Modals ──
  // Renamed from settingsOpen for #841 — this sheet now holds only
  // "SETUP" content (keyboard config, antenna naming, CW defaults,
  // diagnostics). Operating controls live in chips.
  let setupOpen = $state(false);
  let modeModalOpen = $state(false);
  let filterModalOpen = $state(false);
  let txSettingsOpen = $state(false);
  let powerModalOpen = $state(false);

  // ── Chip-scroll IA (#839) ──
  let activeChipId = $state('essentials');
  // Conditional chips are gated on capabilities so radios without the feature
  // don't see a dead chip (e.g. RIT-capable rigs get the dedicated RIT chip
  // per #842, TX-capable rigs get TX). Labels resolve through the i18n
  // catalog (RP-ML-005); glossary-stable tokens (BAND/DSP/RF/RIT/XIT/TX)
  // are kept verbatim inside the catalog VALUE — enforced by RP-ML-013A.
  const mobileChips = $derived([
    { id: 'essentials', label: t('core.mobile.chip.essentials') },
    { id: 'band', label: t('core.mobile.chip.band') },
    { id: 'scan', label: t('core.mobile.chip.scan') },
    { id: 'rf', label: t('core.mobile.chip.rf') },
    // DSP chip carries the level/threshold controls that ESSENTIALS exposes
    // only as on/off toggles (codex P2 on PR #925 — removing the SETUP
    // DSP panel left no mobile path to tune NR level, NB level, notch freq).
    { id: 'dsp', label: t('core.mobile.chip.dsp') },
    ...(ritXit.hasRit || ritXit.hasXit ? [{ id: 'rit', label: t('core.mobile.chip.ritXit') }] : []),
    ...(txCapable ? [{ id: 'tx', label: t('core.mobile.chip.tx') }] : []),
  ]);

  // Reset to ESSENTIALS if the active chip disappears from the list
  // (e.g., capability refresh after reconnecting to a non-TX radio).
  $effect(() => {
    if (!mobileChips.some((c) => c.id === activeChipId)) {
      activeChipId = 'essentials';
    }
  });

  // ── Tuning strip ──
  let availableSteps = $derived(getStepsForMode(mode.currentMode));
  let tuningStep = $state(1000); // Hz
  let stepPickerOpen = $state(false);

  // Reset step when mode changes and current step is not in new mode's list
  $effect(() => {
    const steps = getStepsForMode(mode.currentMode);
    if (!steps.includes(tuningStep)) {
      tuningStep = steps[Math.floor(steps.length / 2)] ?? 1000;
    }
  });

  function tuneBy(delta: number) {
    const freq = mainVfo.freq + delta * tuningStep;
    vfoHandlers.onMainFreqChange(freq);
  }

  function selectStep(hz: number) {
    tuningStep = hz;
    stepPickerOpen = false;
  }

  // ── Quick modes (SSB operation essentials) ──
  // Quick modes — show first matching from profile (covers both CW and CW-U).
  const QUICK_MODE_CANDIDATES = ['LSB', 'USB', 'CW', 'CW-U', 'AM'];
  const QUICK_MODES = $derived(QUICK_MODE_CANDIDATES.filter((m) => mode.modes.includes(m)).slice(0, 4));

  // ── Landscape detection ──
  let isLandscape = $state(false);
  function checkOrientation() {
    const wasLandscape = isLandscape;
    isLandscape = window.innerWidth > window.innerHeight && window.innerHeight < 500;
    // Try to enter fullscreen on landscape (hides Safari chrome)
    if (isLandscape && !wasLandscape) {
      requestFullscreen();
      // iOS Safari: scroll trick to minimize chrome
      setTimeout(() => window.scrollTo(0, 1), 50);
    } else if (!isLandscape && wasLandscape) {
      exitFullscreen();
    }
  }

  function requestFullscreen() {
    const el = document.documentElement;
    // Standard Fullscreen API (not supported in iOS Safari, but works on Android Chrome)
    if (el.requestFullscreen) {
      el.requestFullscreen().catch(() => {});
    } else if ((el as any).webkitRequestFullscreen) {
      (el as any).webkitRequestFullscreen();
    }
  }

  function exitFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if ((document as any).webkitFullscreenElement) {
      (document as any).webkitExitFullscreen();
    }
  }

  // ── Screen Wake Lock ──
  let wakeLock: WakeLockSentinel | null = null;
  let wakeLockRequested = false;

  async function requestWakeLock() {
    if (wakeLock) return; // already held
    try {
      if ('wakeLock' in navigator) {
        wakeLock = await navigator.wakeLock.request('screen');
        wakeLock.addEventListener('release', () => { wakeLock = null; });
        console.log('[WakeLock] acquired');
      }
    } catch (e) {
      console.warn('[WakeLock] failed:', e);
    }
  }

  // iOS Safari needs user gesture for Wake Lock — request on first touch
  function ensureWakeLock() {
    if (!wakeLockRequested) {
      wakeLockRequested = true;
      requestWakeLock();
    }
  }

  onMount(() => {
    // Orientation
    checkOrientation();
    window.addEventListener('resize', checkOrientation);

    // Try immediately (works on Chrome/Android)
    requestWakeLock();
    // Re-acquire on visibility change
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        requestWakeLock();
      }
    };
    document.addEventListener('visibilitychange', handleVisibility);
    // Fallback: acquire on first user interaction (iOS)
    const handleInteraction = () => {
      ensureWakeLock();
    };
    document.addEventListener('touchstart', handleInteraction, { once: true });
    document.addEventListener('click', handleInteraction, { once: true });

    // Ultimate fallback: invisible video loop keeps screen on (iOS ≤16.3 or any browser w/o Wake Lock)
    let noSleepVideo: HTMLVideoElement | null = null;
    if (!('wakeLock' in navigator)) {
      noSleepVideo = document.createElement('video');
      noSleepVideo.setAttribute('playsinline', '');
      noSleepVideo.setAttribute('muted', '');
      noSleepVideo.muted = true;
      noSleepVideo.loop = true;
      noSleepVideo.style.position = 'fixed';
      noSleepVideo.style.top = '-1px';
      noSleepVideo.style.left = '-1px';
      noSleepVideo.style.width = '1px';
      noSleepVideo.style.height = '1px';
      noSleepVideo.style.opacity = '0.01';
      // Minimal silent mp4 (1s, 1x1px)
      noSleepVideo.src = 'data:video/mp4;base64,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';
      document.body.appendChild(noSleepVideo);
      const playOnTouch = () => {
        noSleepVideo?.play().catch(() => {});
        document.removeEventListener('touchstart', playOnTouch);
      };
      document.addEventListener('touchstart', playOnTouch, { once: true });
    }

    return () => {
      window.removeEventListener('resize', checkOrientation);
      document.removeEventListener('visibilitychange', handleVisibility);
      document.removeEventListener('touchstart', handleInteraction);
      document.removeEventListener('click', handleInteraction);
      wakeLock?.release();
      if (noSleepVideo) {
        noSleepVideo.pause();
        noSleepVideo.remove();
      }
    };
  });

  // ── PTT ──
  // Modes: 'idle' | 'held' (touch held down) | 'latched' (double-tap locked)
  let pttMode = $state<'idle' | 'held' | 'latched'>('idle');
  let pttActive = $derived(pttMode !== 'idle');

  // ── TX color (depends on mainVfo, tx, pttActive — declared above) ──
  let txPermit = $derived(getTxPermit(mainVfo.freq, caps?.txBands));
  let txIndicatorColor = $derived(
    (tx.txActive || pttActive) ? 'var(--v2-accent-red, #ef4444)' :
    txPermit === 'allowed' ? 'var(--v2-accent-green, #4ade80)' :
    'var(--v2-text-dim, #555)'
  );
  let lastPttDown = 0;
  const DOUBLE_TAP_MS = 350;
  const PTT_SAFETY_TIMEOUT_MS = 3 * 60 * 1000; // 3 minutes
  let pttSafetyTimer: ReturnType<typeof setTimeout> | null = null;

  function clearPttSafety() {
    if (pttSafetyTimer) {
      clearTimeout(pttSafetyTimer);
      pttSafetyTimer = null;
    }
  }

  function startPttSafety() {
    clearPttSafety();
    pttSafetyTimer = setTimeout(() => {
      // Safety: force PTT off after timeout
      pttMode = 'idle';
      disengageTx();
    }, PTT_SAFETY_TIMEOUT_MS);
  }

  async function engageTx() {
    systemHandlers.onPttOn();
    await txAudio.startTx();
  }

  function disengageTx() {
    systemHandlers.onPttOff();
    txAudio.stopTx();
  }

  function pttDown() {
    const now = Date.now();
    if (pttMode === 'latched') {
      // Tap while latched → unlock, go idle
      pttMode = 'idle';
      disengageTx();
      clearPttSafety();
      return;
    }
    if (now - lastPttDown < DOUBLE_TAP_MS && pttMode === 'held') {
      // Double-tap → latch
      pttMode = 'latched';
      startPttSafety();
      lastPttDown = 0;
      return;
    }
    // Normal press → held
    lastPttDown = now;
    pttMode = 'held';
    engageTx();
    startPttSafety();
  }

  function pttUp() {
    if (pttMode === 'held') {
      // Release after single tap → off
      // But give a moment for double-tap detection
      setTimeout(() => {
        if (pttMode === 'held') {
          pttMode = 'idle';
          disengageTx();
          clearPttSafety();
        }
      }, DOUBLE_TAP_MS);
    }
    // If latched, don't turn off on release
  }

  // Orientation-change safety net (codex P1 on PR #931 / #934 regression).
  // PttFab is conditionally mounted on `!isLandscape`; rotation removes
  // the component so its `pointerup` never fires. Without this guard,
  // an in-progress press would leave `pttMode === 'held'` and TX keyed
  // until the 3-minute safety timer.
  //
  // Must fire ONLY on the transition `portrait → landscape`, not on
  // every `pttMode` change — otherwise landscape hold-to-talk engages
  // and is instantly released by this same effect (codex P1 on PR #934).
  //
  // Plain `let` for prevIsLandscape so it doesn't become a reactive
  // dep; `untrack` on `pttMode` so only orientation changes re-run the
  // effect body.
  let prevIsLandscape = false;
  $effect(() => {
    const nowLandscape = isLandscape;
    const enteredLandscape = !prevIsLandscape && nowLandscape;
    prevIsLandscape = nowLandscape;
    if (enteredLandscape && untrack(() => pttMode) === 'held') {
      pttMode = 'idle';
      disengageTx();
      clearPttSafety();
    }
  });

  // ── Landscape PTT guards (#843 parity with FAB) ──
  // Adds pointermove-8px cancel + haptic + TX-permit dim-state so the
  // landscape strip mirrors the guarded FAB (#840). Skips the 50ms hold
  // delay — in landscape the thumb is already poised on PTT, so an
  // intentional press should engage immediately once TX permit allows.
  let lsPttStartX = 0;
  let lsPttStartY = 0;
  let lsPttEngaged = false;
  const LS_PTT_MOVE_CANCEL_PX = 8;

  function lsPttPointerDown(event: PointerEvent) {
    if (pttMode === 'latched') {
      // Tap-to-unlatch — delegate to shared state machine.
      pttDown();
      return;
    }
    if (txPermit === 'denied' && pttMode === 'idle') {
      // Refuse the first press on out-of-band frequency. Second press
      // within ~2s bypasses (user insists). Reuse the FAB convention.
      const now = Date.now();
      if (!lsLastDeniedPressAt || now - lsLastDeniedPressAt > 2000) {
        lsLastDeniedPressAt = now;
        return;
      }
    }
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    lsPttStartX = event.clientX;
    lsPttStartY = event.clientY;
    lsPttEngaged = true;
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
      try { navigator.vibrate(10); } catch { /* noop */ }
    }
    pttDown();
  }

  function lsPttPointerMove(event: PointerEvent) {
    if (!lsPttEngaged) return;
    const dx = event.clientX - lsPttStartX;
    const dy = event.clientY - lsPttStartY;
    if (dx * dx + dy * dy > LS_PTT_MOVE_CANCEL_PX * LS_PTT_MOVE_CANCEL_PX) {
      lsPttEngaged = false;
      pttUp();
    }
  }

  function lsPttPointerUp() {
    if (!lsPttEngaged) return;
    lsPttEngaged = false;
    pttUp();
  }

  let lsLastDeniedPressAt = 0;

  // ── ATU (long-press = tune) ──
  let atuTimer: ReturnType<typeof setTimeout> | null = null;
  let atuDidLongPress = false;

  function atuTouchStart() {
    atuDidLongPress = false;
    atuTimer = setTimeout(() => {
      atuDidLongPress = true;
      txHandlers.onAtuTune(); // Start antenna tune
    }, 600);
  }

  function atuTouchEnd() {
    if (atuTimer) {
      clearTimeout(atuTimer);
      atuTimer = null;
    }
    if (!atuDidLongPress) {
      txHandlers.onAtuToggle(); // Short press = toggle on/off
    }
  }

  // ── ATU status ──
  let atuStatus = $derived(tx.atuActive ? (tx.atuTuning ? 'tuning' : 'on') : 'off');
</script>

{#if isLandscape}
<!-- ═══ LANDSCAPE: fullscreen spectrum + VFO overlay ═══ -->
<div class="m-landscape">
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />
  {#if hasSpectrum()}
    <div class="m-ls-spectrum">
      <SpectrumPanel />
    </div>
  {:else}
    <div class="m-ls-spectrum">
      <AmberLcdDisplay />
    </div>
  {/if}
  <div class="m-ls-overlay">
    <div class="m-ls-vfo">
      <span class="m-tx-indicator" style="background: {txIndicatorColor}"></span>
      <FrequencyDisplay freq={mainVfo.freq} compact active />
    </div>
    <div class="m-ls-quick-modes">
      {#each QUICK_MODES as m}
        <button
          class="m-ls-mode-btn"
          class:m-ls-mode-active={mainVfo.mode === m}
          onclick={() => modeHandlers.onModeChange(m)}
        >{m}</button>
      {/each}
    </div>
    <div class="m-ls-meter">
      <span class="m-ls-smeter">{formatSValue(meter.signal)}</span>
      <span class="m-ls-dbm">{formatDbm(meter.signal)}</span>
    </div>
    <div class="m-ls-controls">
      <button class="m-ls-step-btn" onclick={() => (stepPickerOpen = !stepPickerOpen)}>
        {formatStep(tuningStep)}
      </button>
      <button class="m-ls-tune-btn" onclick={() => tuneBy(-1)}>
        <ChevronLeft size={20} />
      </button>
      <button class="m-ls-tune-btn" onclick={() => tuneBy(1)}>
        <ChevronRight size={20} />
      </button>
      <span class="m-ls-filter">{mainVfo.filter}</span>
      {#if txCapable}
        <button
          class="m-ls-ptt"
          class:m-ptt-held={pttMode === 'held'}
          class:m-ptt-latched={pttMode === 'latched'}
          class:m-ls-ptt-dim={txPermit === 'denied' && pttMode === 'idle'}
          onpointerdown={lsPttPointerDown}
          onpointermove={lsPttPointerMove}
          onpointerup={lsPttPointerUp}
          onpointercancel={lsPttPointerUp}
          onlostpointercapture={lsPttPointerUp}
          oncontextmenu={(e) => e.preventDefault()}
          title={txPermit === 'denied' ? t('core.mobile.tx.notAllowedFreq') : t('core.mobile.tx.pushToTalk')}
        >
          {pttMode === 'latched' ? 'TX🔒' : pttMode === 'held' ? 'TX' : 'PTT'}
        </button>
      {/if}
    </div>
  </div>
  {#if stepPickerOpen}
    <div
      class="m-step-picker-backdrop"
      role="button"
      tabindex="0"
      aria-label={t('core.mobile.closeStepPicker')}
      onclick={() => (stepPickerOpen = false)}
      onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); stepPickerOpen = false; } }}
    ></div>
    <div class="m-ls-step-picker">
      {#each availableSteps as step}
        <button
          class="m-step-option"
          class:m-step-active={step === tuningStep}
          onclick={() => { tuningStep = step; stepPickerOpen = false; }}
        >{formatStep(step)}</button>
      {/each}
    </div>
  {/if}
</div>
{:else}
<!-- ═══ PORTRAIT: normal mobile layout ═══ -->
<div class="m-layout">
  <KeyboardHandler config={keyboardConfig} onAction={keyboardHandlers.dispatch} />

  <!-- ═══ STICKY VFO HEADER ═══ -->
  <header class="m-vfo-bar" bind:this={receiverDeckElement} style={receiverDeckStyle}>
    {#if hasDualReceiver()}
      <div class="m-receiver-selector" role="group" aria-label={t('core.mobile.receiverSelector.label')}>
        <button
          type="button"
          class="m-receiver-pill"
          class:m-receiver-pill-active={activeReceiver === 'MAIN'}
          aria-pressed={activeReceiver === 'MAIN'}
          onclick={() => selectReceiver('MAIN')}
        >
          {receiverLabel('MAIN')}
        </button>
        <button
          type="button"
          class="m-receiver-pill"
          class:m-receiver-pill-active={activeReceiver === 'SUB'}
          aria-pressed={activeReceiver === 'SUB'}
          onclick={() => selectReceiver('SUB')}
        >
          {receiverLabel('SUB')}
        </button>
      </div>
    {/if}
    <div class="m-vfo-row">
      <span class="m-tx-indicator" style="background: {txIndicatorColor}" title={txPermit === 'allowed' ? t('core.mobile.tx.allowed') : t('core.mobile.tx.notAllowedBand')}></span>
      <div class="m-vfo-freq" bind:this={vfoFreqElement}>
        <FrequencyDisplay freq={mainVfo.freq} compact active />
      </div>
      <button class="m-settings-btn" onclick={() => (setupOpen = true)} aria-label={t('core.mobile.setupButton')}>
        <Settings size={16} />
        <span>{t('core.mobile.sheet.setup')}</span>
      </button>
    </div>
    <div class="m-vfo-meta">
      <span class="m-vfo-mode">{mainVfo.mode}</span>
      <span class="m-vfo-filter">{mainVfo.filter}</span>
      {#if hasDualReceiver() && subVfo.freq > 0}
        <span class="m-vfo-sub">{(subVfo.freq / 1_000_000).toFixed(3)}</span>
      {/if}
      {#if ritXit.ritActive}
        <span class="m-vfo-rit" title="RIT offset">
          RIT {ritXit.ritOffset >= 0 ? '+' : ''}{ritXit.ritOffset}
        </span>
      {:else if ritXit.xitActive}
        <span class="m-vfo-rit" title="XIT offset">
          XIT {ritXit.xitOffset >= 0 ? '+' : ''}{ritXit.xitOffset}
        </span>
      {/if}
    </div>
  </header>

  <!-- ═══ S-METER BAR ═══ -->
  <div class="m-smeter-bar">
    <LinearSMeter value={mainVfo.sValue} compact label="" />
  </div>

  <!-- ═══ SCROLLABLE CONTENT ═══ -->
  <main class="m-content">

    <!-- Spectrum / Waterfall / LCD -->
    {#if hasSpectrum()}
      <section class="m-spectrum">
        <SpectrumPanel />
      </section>
    {:else}
      <section class="m-spectrum">
        <AmberLcdDisplay />
      </section>
    {/if}

    <!-- Chip-scroll IA nav (#839) -->
    <MobileChipBar
      chips={mobileChips}
      activeId={activeChipId}
      onSelect={(id) => (activeChipId = id)}
    />

    <!-- Active-chip content area (single panel mounted at a time) -->
    {#if activeChipId === 'essentials'}
      <section class="m-section" id="m-chip-panel-essentials" role="tabpanel">
        <EssentialsPanel
          vfoOps={vfoOps}
          mode={mode}
          filter={filter}
          rxAudio={rxAudio}
          dsp={dsp}
          quickModes={QUICK_MODES}
          onSplitToggle={vfoHandlers.onSplitToggle}
          onSwap={vfoHandlers.onSwap}
          onEqual={vfoHandlers.onEqual}
          onModeChange={modeHandlers.onModeChange}
          onModeMore={() => (modeModalOpen = true)}
          onFilterChange={(n) => filterHandlers.onFilterChange?.(n)}
          onFilterMore={() => (filterModalOpen = true)}
          onMonitorModeChange={rxAudioHandlers.onMonitorModeChange}
          onAfLevelChange={rxAudioHandlers.onAfLevelChange}
          onNbToggle={dspHandlers.onNbToggle}
          onNrModeChange={dspHandlers.onNrModeChange}
          onNotchModeChange={dspHandlers.onNotchModeChange}
        />
      </section>
    {:else if activeChipId === 'band'}
      <section class="m-section" id="m-chip-panel-band" role="tabpanel">
        <CollapsiblePanel title="BAND" panelId="m-band" collapsible={false}>
          <BandSelector />
        </CollapsiblePanel>
      </section>
    {:else if activeChipId === 'scan'}
      <section class="m-section" id="m-chip-panel-scan" role="tabpanel">
        <CollapsiblePanel title="SCAN" panelId="m-scan" collapsible={false}>
          <ScanPanel />
        </CollapsiblePanel>
      </section>
    {:else if activeChipId === 'rf'}
      <section class="m-section" id="m-chip-panel-rf" role="tabpanel">
        <CollapsiblePanel title="RF" panelId="m-rf-quick" collapsible={false}>
          <RfFrontEnd />
        </CollapsiblePanel>
      </section>
    {:else if activeChipId === 'dsp'}
      <section class="m-section" id="m-chip-panel-dsp" role="tabpanel">
        <CollapsiblePanel title="DSP" panelId="m-dsp-chip" collapsible={false}>
          <DspPanel />
        </CollapsiblePanel>
      </section>
    {:else if activeChipId === 'rit'}
      <section class="m-section" id="m-chip-panel-rit" role="tabpanel">
        <CollapsiblePanel title="RIT / XIT" panelId="m-rit-chip" collapsible={false}>
          <RitXitPanel />
        </CollapsiblePanel>
      </section>
    {:else if activeChipId === 'tx' && txCapable}
      <section class="m-section" id="m-chip-panel-tx" role="tabpanel">
        <CollapsiblePanel title="TX" panelId="m-tx" collapsible={false}>
          <div class="m-tx-compact">
            <!-- Power readout (tap → power modal) -->
            <button type="button" class="m-tx-info" onclick={() => (powerModalOpen = true)}>
              <span class="m-tx-power-value">{formatPower(tx.rfPower)}</span>
              {#if tx.txActive || pttActive}
                <span class="m-tx-swr-value">SWR {meter.swr > 0 ? (meter.swr / 10).toFixed(1) : '—'}</span>
              {/if}
            </button>

            <!-- ATU -->
            <button
              class="m-atu-btn"
              class:m-atu-on={atuStatus === 'on'}
              class:m-atu-tuning={atuStatus === 'tuning'}
              ontouchstart={atuTouchStart}
              ontouchend={atuTouchEnd}
              ontouchcancel={atuTouchEnd}
              onmousedown={atuTouchStart}
              onmouseup={atuTouchEnd}
            >
              ATU
            </button>

            <!-- TX settings -->
            <button class="m-tx-settings-btn" onclick={() => (txSettingsOpen = true)}>
              <Sliders size={14} />
            </button>

            <!-- Inline PTT button removed (#840) — PttFab at bottom-right
                 is the persistent, guarded TX affordance. -->
          </div>
          {#if tx.txActive || pttActive}
            <div class="m-tx-meter">
              <DockMeterPanel
                sValue={mainVfo.sValue}
                rfPower={meter.rfPower ?? 0}
                swr={meter.swr}
                alc={meter.alc ?? 0}
                txActive={true}
                meterSource="po"
                onMeterSourceChange={() => {}}
              />
            </div>
          {/if}
        </CollapsiblePanel>
      </section>
    {/if}

    <!-- Spacer for tuning strip -->
    <div class="m-bottom-spacer"></div>
  </main>

  <!-- ═══ TUNING STRIP (FIXED BOTTOM) ═══ -->
  <nav class="m-tuning-strip">
    <button class="m-tune-btn m-tune-fast" onclick={() => tuneBy(-10)}>
      <ChevronsLeft size={18} />
    </button>
    <button class="m-tune-btn" onclick={() => tuneBy(-1)}>
      <ChevronLeft size={22} />
    </button>
    <div class="m-tune-step-wrapper">
      <button class="m-tune-step" onclick={() => (stepPickerOpen = !stepPickerOpen)}>
        {formatStep(tuningStep)}
      </button>
      {#if stepPickerOpen}
        <div
          class="m-step-picker-backdrop"
          role="button"
          tabindex="0"
          aria-label={t('core.mobile.closeStepPicker')}
          onclick={() => (stepPickerOpen = false)}
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); stepPickerOpen = false; } }}
        ></div>
        <div class="m-step-picker">
          {#each availableSteps as s}
            <button
              class="m-step-option"
              class:m-step-active={s === tuningStep}
              onclick={() => selectStep(s)}
            >
              {formatStep(s)}
            </button>
          {/each}
        </div>
      {/if}
    </div>
    <button class="m-tune-btn" onclick={() => tuneBy(1)}>
      <ChevronRight size={22} />
    </button>
    <button class="m-tune-btn m-tune-fast" onclick={() => tuneBy(10)}>
      <ChevronsRight size={18} />
    </button>
  </nav>

  <!-- ═══ SETUP BOTTOM SHEET (#841 rename, SETUP-only content) ═══
       Per docs/plans/2026-04-18-mobile-ia.md §6, rare-config home.
       BAND / DSP / RF / MODE / FILTER / ESSENTIALS live in chips.
       Remaining here: AGC, RIT/XIT (until #842 chip lands), ANTENNA, CW. -->
  <BottomSheet bind:open={setupOpen} title={t('core.mobile.sheet.setup')}>
          <!-- VFO/BAND, DSP panels removed (#841) — BAND lives in the "band" chip,
               VFO ops (SPLIT/A↔B/A=B) live in ESSENTIALS, DSP levels+toggles live
               in ESSENTIALS. -->

          <CollapsiblePanel title="AGC" panelId="m-agc">
            <AgcPanel />
          </CollapsiblePanel>

          <!-- RF FRONT END panel removed (#841) — lives in the "rf" chip. -->

          <CollapsiblePanel title="RIT / XIT" panelId="m-rit">
            <RitXitPanel />
          </CollapsiblePanel>

          {#if antenna.antennaCount > 1}
            <CollapsiblePanel title="ANTENNA" panelId="m-antenna">
              <AntennaPanel />
            </CollapsiblePanel>
          {/if}

          <CollapsiblePanel
            title="CW"
            panelId="m-cw"
            autoCollapseWhen={mode.currentMode !== 'CW' && mode.currentMode !== 'CW-R'}
          >
            <CwPanel />
          </CollapsiblePanel>
  </BottomSheet>

  <!-- ═══ MODE MODAL ═══ -->
  <BottomSheet bind:open={modeModalOpen} title={t('core.mobile.sheet.allModes')} compact>
          <div class="m-mode-grid">
            {#each mode.modes as m}
              <HardwareButton
                active={mode.currentMode === m}
                indicator="edge-left"
                color="cyan"
                onclick={() => { modeHandlers.onModeChange(m); modeModalOpen = false; }}
              >
                {m}
              </HardwareButton>
            {/each}
          </div>
          {#if mode.hasDataMode}
            <div class="m-sheet-subtitle">{t('core.mobile.sheet.dataMode')}</div>
            <div class="m-mode-grid">
              {#each Array.from({ length: Math.max(0, (mode.dataModeCount ?? 0)) + 1 }, (_, i) => i) as d}
                <HardwareButton
                  active={mode.dataMode === d}
                  indicator="edge-left"
                  color="cyan"
                  onclick={() => { modeHandlers.onDataModeChange(d); }}
                >
                  {d === 0 ? 'OFF' : `D${d}`}
                </HardwareButton>
              {/each}
            </div>
          {/if}
  </BottomSheet>

  <!-- ═══ FILTER MODAL ═══ -->
  <BottomSheet bind:open={filterModalOpen} title={t('core.mobile.sheet.filterSettings')}>
          <FilterPanel />
  </BottomSheet>

  <!-- ═══ POWER MODAL ═══ -->
  <BottomSheet bind:open={powerModalOpen} title={t('core.mobile.sheet.rfPower')} compact contentStyle="padding: 12px;">
          <ValueControl
            label="RF Power"
            value={tx.rfPower}
            min={0}
            max={255}
            step={1}
            renderer="hbar"
            displayFn={rawToPercentDisplay}
            accentColor="var(--v2-accent-red)"
            onChange={txHandlers.onRfPowerChange}
            variant="hardware-illuminated"
          />
  </BottomSheet>

  <!-- ═══ TX SETTINGS MODAL ═══ -->
  <BottomSheet bind:open={txSettingsOpen} title={t('core.mobile.sheet.txSettings')}>
          <TxPanel />
  </BottomSheet>
</div>
{/if}

<!-- Toast notifications — rendered in fixed position overlay -->
<Toast />

{#if txCapable && !isLandscape}
  <!-- Guarded sticky PTT FAB (#840) — persistent 1-tap TX in portrait only.
       Landscape has its own guarded `.m-ls-ptt` button (#843); mounting
       FAB there would give two simultaneous TX controls (codex P1 on
       PR #928). Layered guards live in the FAB component. State machine
       stays in the parent (pttMode + 3-min safety timer + double-tap). -->
  <PttFab
    mode={pttMode}
    txPermit={txPermit}
    onDown={pttDown}
    onUp={pttUp}
  />
{/if}

<style>
  /* ── Landscape layout ── */
  .m-landscape {
    position: fixed;
    top: 0;
    left: 0;
    width: 100dvw;
    height: 100dvh;
    background: #000;
    z-index: 50;
    display: flex;
    flex-direction: column;
  }

  .m-ls-spectrum {
    flex: 1;
    min-height: 0;
    position: relative;
  }

  .m-ls-spectrum > :global(.spectrum-panel) {
    height: 100% !important;
    border: none !important;
    border-radius: 0 !important;
  }

  .m-ls-overlay {
    position: absolute;
    top: env(safe-area-inset-top, 0px);
    left: env(safe-area-inset-left, 0px);
    right: env(safe-area-inset-right, 0px);
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 4px 8px;
    background: rgba(0, 0, 0, 0.65);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 60;
    pointer-events: auto;
  }

  .m-ls-vfo {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex: 1;
    min-width: 0;
  }

  .m-ls-vfo > :global(:first-child) {
    flex-shrink: 0;
  }

  .m-ls-quick-modes {
    display: flex;
    gap: 2px;
  }

  .m-ls-mode-btn {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    padding: 3px 8px;
    min-height: 44px;
    border-radius: 3px;
    border: 1px solid rgba(0, 212, 255, 0.3);
    background: transparent;
    color: rgba(0, 212, 255, 0.6);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: all 0.15s;
    letter-spacing: 0.06em;
  }

  .m-ls-mode-active {
    background: rgba(0, 212, 255, 0.25);
    color: #00d4ff;
    border-color: #00d4ff;
  }

  .m-ls-filter {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    padding: 3px 6px;
    border-radius: 3px;
    background: rgba(156, 163, 175, 0.15);
    color: #9ca3af;
    letter-spacing: 0.06em;
  }

  .m-ls-meter {
    display: flex;
    align-items: baseline;
    gap: 6px;
  }

  .m-ls-smeter {
    font-family: 'Roboto Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    color: #4ade80;
  }

  .m-ls-dbm {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    color: #9ca3af;
  }

  .m-ls-controls {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .m-ls-step-btn {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    color: #e0e0e0;
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    padding: 4px 8px;
    cursor: pointer;
    min-height: 44px;
    -webkit-tap-highlight-color: transparent;
  }

  .m-ls-tune-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    min-height: 44px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    background: rgba(255, 255, 255, 0.08);
    color: #e0e0e0;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .m-ls-tune-btn:active {
    background: rgba(255, 255, 255, 0.2);
  }

  .m-ls-ptt {
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    min-width: 52px;
    min-height: 44px;
    padding: 4px 10px;
    border-radius: 4px;
    border: 2px solid var(--v2-accent-red, #ef4444);
    background: rgba(239, 68, 68, 0.15);
    color: var(--v2-accent-red, #ef4444);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, color 0.15s;
  }

  .m-ls-ptt.m-ptt-held {
    background: var(--v2-accent-red, #ef4444);
    color: #fff;
    box-shadow: 0 0 16px rgba(239, 68, 68, 0.5);
  }

  /* TX-permit denied (#843): dim border + text so the operator sees at a
     glance that the current frequency is out-of-band before pressing. */
  .m-ls-ptt.m-ls-ptt-dim {
    border-color: rgba(239, 68, 68, 0.35);
    background: rgba(239, 68, 68, 0.04);
    color: rgba(239, 68, 68, 0.5);
  }

  .m-ls-ptt.m-ptt-latched {
    background: #dc2626;
    color: #fff;
    box-shadow: 0 0 20px rgba(220, 38, 38, 0.6);
    animation: ptt-latch-pulse 1s ease-in-out infinite;
  }

  .m-ls-step-picker {
    position: fixed;
    bottom: 50%;
    right: env(safe-area-inset-right, 8px);
    z-index: 1000;
    display: flex;
    flex-direction: column;
    gap: 2px;
    background: rgba(10, 10, 15, 0.95);
    border: 1px solid rgba(42, 42, 62, 0.8);
    border-radius: 6px;
    padding: 4px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.6);
  }

  /* ── Base layout ── */
  .m-layout {
    display: flex;
    flex-direction: column;
    height: 100vh;
    height: 100dvh;
    background: linear-gradient(180deg, var(--v2-bg-gradient-start) 0%, var(--v2-bg-darkest) 100%);
    overflow: hidden;
    padding-top: env(safe-area-inset-top, 0px);
  }

  /* ── Sticky VFO header ── */
  .m-vfo-bar {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 10px 4px;
    background: var(--v2-bg-card, #111);
    border-bottom: 1px solid var(--v2-border-panel, #333);
    z-index: 10;
  }

  .m-tx-indicator {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    transition: background 0.2s, box-shadow 0.2s;
  }

  .m-receiver-selector {
    display: flex;
    gap: 4px;
    padding: 2px 0 4px;
  }

  .m-receiver-pill {
    flex: 1 1 0;
    min-height: 32px;
    padding: 6px 10px;
    border-radius: 4px;
    border: 1px solid var(--v2-border-darker, #333);
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-secondary, #aaa);
    font-family: 'Roboto Mono', monospace;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.15s, border-color 0.15s, color 0.15s;
  }

  .m-receiver-pill:focus-visible {
    outline: 2px solid var(--v2-accent-cyan, #22d3ee);
    outline-offset: 2px;
  }

  .m-receiver-pill-active {
    background: var(--v2-accent-cyan, #22d3ee);
    border-color: var(--v2-accent-cyan, #22d3ee);
    color: var(--v2-bg-card, #111);
  }

  .m-vfo-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .m-vfo-freq {
    line-height: 1;
    flex: 1;
  }

  .m-vfo-freq :global(.freq) {
    font-size: 28px;
  }

  .m-settings-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 32px;
    padding: 0 10px;
    border-radius: 4px;
    border: 1px solid var(--v2-border-darker, #333);
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-secondary, #aaa);
    font-family: 'Roboto Mono', monospace;
    font-weight: 700;
    font-size: 9px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    flex-shrink: 0;
  }

  .m-settings-btn:active {
    background: var(--v2-bg-card, #222);
  }

  .m-vfo-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--v2-text-muted, #888);
  }

  .m-vfo-mode {
    color: var(--v2-accent-cyan, #22d3ee);
    padding: 2px 8px;
    border: 1px solid var(--v2-accent-cyan, #22d3ee);
    border-radius: 3px;
    font-size: 11px;
  }

  .m-vfo-filter {
    color: var(--v2-text-secondary, #aaa);
    font-size: 11px;
  }

  .m-vfo-sub {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    color: var(--v2-text-dim, #666);
    margin-left: auto;
    letter-spacing: 0.02em;
  }

  .m-vfo-sub::before {
    content: 'SUB ';
    font-size: 8px;
    font-weight: 700;
    color: var(--v2-text-dim, #555);
    letter-spacing: 0.08em;
  }

  /* RIT/XIT offset badge in sticky header meta row (#842). Only renders
     when RIT or XIT is active so the header stays quiet most of the time. */
  .m-vfo-rit {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    color: var(--v2-accent-yellow, #facc15);
    letter-spacing: 0.02em;
    padding: 0 4px;
    border: 1px solid rgba(250, 204, 21, 0.35);
    border-radius: 3px;
  }

  /* ── S-meter bar (full width, below VFO) ── */
  .m-tx-meter {
    padding: 4px 8px 0;
    border-top: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .m-smeter-bar {
    flex-shrink: 0;
    padding: 2px 4px;
    background: var(--v2-bg-darker, #0a0a14);
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  /* ── Scrollable content ── */
  .m-content {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .m-content::-webkit-scrollbar {
    display: none;
  }

  /* ── Spectrum ── */
  .m-spectrum {
    height: 220px;
    min-height: 180px;
    border-bottom: 1px solid var(--v2-border-darker, #222);
  }

  .m-spectrum :global(.spectrum-panel) {
    height: 100%;
    border: none;
    border-radius: 0;
    box-shadow: none;
  }

  /* ── Sections ── */
  .m-section {
    display: flex;
    flex-direction: column;
  }

  .m-section :global(.collapsible-panel) {
    border-radius: 0;
    border-left: none;
    border-right: none;
  }

  /* ── TX compact section ── */
  .m-tx-compact {
    display: flex;
    align-items: stretch;
    gap: 4px;
    padding: 8px;
    min-height: 44px;
  }

  

  .m-ptt-held {
    background: var(--v2-accent-red, #ef4444);
    border-color: var(--v2-accent-red, #ef4444);
    color: #fff;
    box-shadow: 0 0 20px rgba(239, 68, 68, 0.5);
  }

  .m-ptt-latched {
    background: #dc2626;
    border-color: #dc2626;
    color: #fff;
    box-shadow: 0 0 24px rgba(220, 38, 38, 0.6);
    animation: ptt-latch-pulse 1s ease-in-out infinite;
  }

  @keyframes ptt-latch-pulse {
    0%, 100% { box-shadow: 0 0 24px rgba(220, 38, 38, 0.6); }
    50% { box-shadow: 0 0 32px rgba(220, 38, 38, 0.8); }
  }

  .m-tx-info {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-width: 44px;
    min-height: 44px;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid var(--v2-border-darker, #333);
    background: var(--v2-bg-input, #1a1a2e);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .m-tx-info:active {
    background: var(--v2-bg-card, #222);
  }

  .m-tx-power-value {
    font-family: 'Roboto Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    color: var(--v2-text-primary, #ddd);
    letter-spacing: 0.02em;
    white-space: nowrap;
  }

  .m-tx-swr-value {
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    color: var(--v2-text-dim, #888);
    white-space: nowrap;
  }

  .m-atu-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    min-width: 44px;
    min-height: 44px;
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--v2-text-dim, #444);
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-muted, #888);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: all 0.15s;
  }

  .m-atu-on {
    border-color: var(--v2-accent-green, #4ade80);
    color: var(--v2-accent-green, #4ade80);
    background: rgba(74, 222, 128, 0.1);
  }

  .m-atu-tuning {
    border-color: var(--v2-accent-yellow, #facc15);
    color: var(--v2-accent-yellow, #facc15);
    background: rgba(250, 204, 21, 0.1);
    animation: atu-pulse 0.6s ease-in-out infinite;
  }

  @keyframes atu-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  @media (prefers-reduced-motion: reduce) {
    .m-ptt-latched,
    .m-ls-ptt.m-ptt-latched {
      animation: none;
    }
    .m-atu-tuning {
      animation: none;
    }
  }

  .m-tx-settings-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    min-width: 44px;
    min-height: 44px;
    padding: 4px 8px;
    border-radius: 4px;
    border: 1px solid var(--v2-border-darker, #333);
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-muted, #888);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.06em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }

  .m-tx-settings-btn:active {
    background: var(--v2-bg-card, #222);
  }

  /* ── Bottom spacer ── */
  .m-bottom-spacer {
    height: calc(52px + env(safe-area-inset-bottom, 0px));
    flex-shrink: 0;
  }

  /* ── Tuning strip ── */
  .m-tuning-strip {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    align-items: stretch;
    height: 52px;
    padding-bottom: env(safe-area-inset-bottom, 0px);
    background: var(--v2-bg-card, #111);
    border-top: 1px solid var(--v2-border-panel, #333);
    z-index: 100;
    gap: 1px;
  }

  .m-tune-btn {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--v2-bg-input, #1a1a2e);
    border: none;
    color: var(--v2-text-primary, #ddd);
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    transition: background 0.1s;
    min-height: 44px;
  }

  .m-tune-btn:active {
    background: var(--v2-accent-cyan, #22d3ee);
    color: var(--v2-bg-darkest, #000);
  }

  .m-tune-fast {
    flex: 0.7;
    color: var(--v2-text-muted, #888);
  }

  .m-tune-fast:active {
    background: var(--v2-accent-cyan, #22d3ee);
    color: var(--v2-bg-darkest, #000);
  }

  .m-tune-step {
    flex: 1.2;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--v2-bg-darker, #0a0a14);
    border: 1px solid var(--v2-border-panel, #333);
    border-top: none;
    border-bottom: none;
    color: var(--v2-accent-cyan, #22d3ee);
    font-family: 'Roboto Mono', monospace;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    min-height: 44px;
  }

  .m-tune-step:active {
    background: var(--v2-bg-input, #1a1a2e);
  }

  .m-tune-step-wrapper {
    position: relative;
    flex: 1.2;
    display: flex;
  }

  .m-tune-step-wrapper .m-tune-step {
    flex: 1;
  }

  .m-step-picker-backdrop {
    position: fixed;
    inset: 0;
    z-index: 149;
  }

  .m-step-picker {
    position: absolute;
    bottom: 100%;
    left: 50%;
    transform: translateX(-50%);
    margin-bottom: 4px;
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid var(--v2-border-panel, #333);
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 -8px 24px rgba(0, 0, 0, 0.5);
    z-index: 150;
    min-width: 100px;
  }

  .m-step-option {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 10px 16px;
    background: var(--v2-bg-card, #111);
    border: none;
    color: var(--v2-text-primary, #ddd);
    font-family: 'Roboto Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
    min-height: 44px;
  }

  .m-step-option:active {
    background: var(--v2-bg-input, #1a1a2e);
  }

  .m-step-active {
    color: var(--v2-accent-cyan, #22d3ee);
    background: rgba(34, 211, 238, 0.1);
    font-weight: 700;
  }

  /* ── Bottom sheet content overrides ── */
  .m-sheet-subtitle {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--v2-text-dim, #555);
    padding: 10px 10px 4px;
    text-transform: uppercase;
  }

  :global(.m-sheet-content .collapsible-panel) {
    border-radius: 0;
    border-left: none;
    border-right: none;
  }

  /* ── Mode grid (in modal) ── */
  .m-mode-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 4px;
    padding: 8px 10px;
  }

  .m-mode-grid > :global(button) {
    min-height: 44px;
  }
</style>
