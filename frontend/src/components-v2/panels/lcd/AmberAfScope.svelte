<script lang="ts">
  import { onMount, untrack } from 'svelte';

  interface Props {
    /** FFT pixel data from AudioFftScope (0-160 range) */
    data: Uint8Array | null;
    /** Register a push callback for streaming updates */
    onRegisterPush?: (fn: (data: Uint8Array) => void) => void;
    /** Filter width — raw index from radio (e.g. 0-36 for FTX-1 SSB) or Hz */
    filterWidth?: number;
    /** Max filter width value (index or Hz) for normalization */
    filterWidthMax?: number;
    /** IF shift in Hz */
    ifShift?: number;
    /** Contour level 0=off, >0=active */
    contour?: number;
    /** Contour center frequency offset (0-255) */
    contourFreq?: number;
    /** Manual notch active */
    manualNotch?: boolean;
    /** Manual notch frequency (0-255 raw) */
    notchFreq?: number;
    /** Auto notch active */
    autoNotch?: boolean;
    /** Layout sizing mode: 'compact' = fixed strip height; 'fill' = stretch to container;
     *  'dominant' = fill height + overlays a dimmed running-max trace */
    mode?: 'compact' | 'fill' | 'dominant';
    /** Audio sample rate */
    sampleRate?: number;
    /** Actual Hz width of the received FFT data (defaults to sampleRate) */
    bandwidth?: number;
    /** Compact display mode */
    compact?: boolean;
  }

  let {
    data,
    onRegisterPush,
    filterWidth = 13,
    filterWidthMax = 36,
    ifShift = 0,
    contour = 0,
    contourFreq = 128,
    manualNotch = false,
    notchFreq = 128,
    sampleRate = 48000,
    bandwidth,
    mode = 'compact',
  }: Props = $props();

  // Effective bandwidth: use provided bandwidth, fall back to sampleRate
  let effectiveBandwidth = $derived(bandwidth ?? sampleRate);

  let canvas: HTMLCanvasElement;
  let cssWidth = $state(1);
  let cssHeight = $state(1);
  let rafId = 0;
  let visible = true;
  let latestPixels: Uint8Array | null = null;

  // LCD ink prefix — alpha is scaled by `--lcd-alpha-*` tokens read off
  // the nearest `.lcd-screen` ancestor each frame (plan §2.4).
  const INK_A = 'rgba(26, 16, 0,';
  let alphaActive = 1;
  let alphaGhost = 0.06;

  function refreshAlphas(): void {
    const root = canvas?.closest<HTMLElement>('.lcd-screen') ?? canvas?.parentElement;
    if (!root) return;
    const style = getComputedStyle(root);
    const a = parseFloat(style.getPropertyValue('--lcd-alpha-active'));
    const g = parseFloat(style.getPropertyValue('--lcd-alpha-ghost'));
    if (Number.isFinite(a)) alphaActive = a;
    if (Number.isFinite(g)) alphaGhost = g;
  }

  // FFT bar smoothing — fast attack shows transients, moderate decay avoids flicker
  let smoothed: Float32Array | null = null;
  const ATTACK = 0.55;
  const DECAY = 0.25;

  // Running-max trace for dominant mode — tracks per-bin peak with slow linear decay.
  // Decay rate: ~255/(30fps * 3s) → normalised 1/(30*3) ≈ 0.011 per frame → ~3s fade.
  let runningMax: Float32Array | null = null;
  const MAX_DECAY = 1 / (30 * 3);

  // Trapezoid animation — adaptive lerp toward target filterWidth
  // Big jumps (fast knob turning) → fast animation to keep up
  // Small jumps (fine tuning) → smooth slow animation for polish
  let animatedFilterWidth = $state(untrack(() => filterWidth));
  let prevTargetFilter = untrack(() => filterWidth);
  let adaptiveLerp = 0.12;

  function draw(): void {
    if (!visible) { rafId = 0; return; }
    const pixels = latestPixels ?? data;
    let w = cssWidth;
    let h = cssHeight;
    if ((w <= 1 || h <= 1) && canvas) {
      w = canvas.clientWidth || canvas.parentElement?.clientWidth || 1;
      h = canvas.clientHeight || canvas.parentElement?.clientHeight || 1;
      if (w > 1 && h > 1) {
        cssWidth = w;
        cssHeight = h;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    }
    // Detect how fast the knob is turning
    if (filterWidth !== prevTargetFilter) {
      const jump = Math.abs(filterWidth - prevTargetFilter);
      prevTargetFilter = filterWidth;
      if (jump > 2) {
        // Fast turning — snap instantly, no lag
        animatedFilterWidth = filterWidth;
        adaptiveLerp = 0.5;
      } else {
        // Slow/single step — animate smoothly
        adaptiveLerp = 0.15;
      }
    }

    // Animate trapezoid toward target filter width
    const diff = filterWidth - animatedFilterWidth;
    if (Math.abs(diff) > 0.01) {
      animatedFilterWidth += diff * adaptiveLerp;
    } else {
      animatedFilterWidth = filterWidth;
    }

    if (canvas && w > 1 && h > 1) {
      const ctx = canvas.getContext('2d');
      if (ctx) render(ctx, pixels, w, h);
    }
    rafId = requestAnimationFrame(draw);
  }

  function render(
    ctx: CanvasRenderingContext2D,
    pixels: Uint8Array | null,
    w: number,
    h: number,
  ): void {
    const hasShift = ifShift !== 0;
    const labelH = 40; // always reserve space for two label rows
    const trapTop = labelH;
    const trapH = h - labelH;

    // Clear to transparent (amber LCD shines through)
    ctx.clearRect(0, 0, w, h);

    // Refresh resolved alphas from CSS tokens (cheap — one getComputedStyle).
    refreshAlphas();

    // ── Trapezoid geometry ──
    // The total construct (whiskers + trapezoid) has FIXED outer width.
    // The trapezoid (filter passband) grows/shrinks inside.
    // When filter narrows → trapezoid shrinks, whiskers extend.
    // When filter widens → trapezoid grows, whiskers shrink.

    // Fixed outer endpoints of the whiskers — always centered at w/2
    const totalHalfW = w * 0.42;
    const whiskerLeft = w / 2 - totalHalfW;
    const whiskerRight = w / 2 + totalHalfW;

    // Trapezoid center shifts with IF shift; whiskers stay fixed
    const shiftRef = Math.max(animatedFilterWidth, filterWidthMax * 0.5);
    const cx = w / 2 + (ifShift / shiftRef) * totalHalfW * 0.8;

    // Slope: how much the legs flare outward
    const slopeExtra = trapH * 0.35;

    // Filter width → top edge width (proportional to max)
    const filterRatio = Math.max(0.05, Math.min(1, animatedFilterWidth / Math.max(1, filterWidthMax)));
    const maxTopHalfW = totalHalfW - slopeExtra;
    const topHalfW = Math.max(trapH * 0.1, maxTopHalfW * filterRatio);

    // Trapezoid corners (in trapezoid zone: trapTop → h)
    const tl = cx - topHalfW;
    const tr = cx + topHalfW;
    const bl = cx - topHalfW - slopeExtra;
    const br = cx + topHalfW + slopeExtra;

    // ── Labels: fixed anchor so digits don't jump ──
    // Prefix ("Filter:") right-aligned to anchor, digits left-aligned from anchor
    const monoFont = "bold 12px 'JetBrains Mono', 'Courier New', monospace";
    const segFont = "bold 14px 'DSEG7 Classic', monospace";
    // Anchor: right edge of longest prefix ("Filter: ")
    ctx.font = monoFont;
    const anchorX = w / 2 - ctx.measureText('0000').width / 2; // digits roughly centered

    function drawRow(label: string, value: string, y: number, dimmed = false, ghost = false): void {
      const alpha = ghost ? alphaGhost * 2 : dimmed ? alphaActive * 0.7 : alphaActive;
      const hzAlpha = ghost ? alphaGhost * 1.3 : alphaActive * 0.6;
      ctx.textAlign = 'right';
      ctx.font = monoFont;
      ctx.fillStyle = `${INK_A} ${alpha})`;
      ctx.fillText(label, anchorX, y);
      ctx.textAlign = 'left';
      ctx.font = segFont;
      ctx.fillText(value, anchorX, y);
      const segValW = ctx.measureText(value).width;
      ctx.font = monoFont;
      ctx.fillStyle = `${INK_A} ${hzAlpha})`;
      ctx.fillText('Hz', anchorX + segValW + 2, y);
    }

    // ── Shift label (top row — always visible, dim when inactive) ──
    if (hasShift) {
      const shiftSign = ifShift > 0 ? '+' : '';
      drawRow('Shift: ', `${shiftSign}${ifShift}`, 16, true);
    } else {
      // Ghost LCD segments: show "+0000" in very dim ink
      drawRow('Shift: ', '+0000', 16, false, true);
    }

    // ── Filter label (bottom row) ──
    const filterHz = Math.round(animatedFilterWidth);
    drawRow('Filter: ', String(filterHz), labelH - 6);

    // ── Draw trapezoid + whiskers (thick LCD ink) ──
    ctx.strokeStyle = `${INK_A} ${alphaActive})`;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(whiskerLeft, h);
    ctx.lineTo(bl, h);
    ctx.lineTo(tl, trapTop);
    ctx.lineTo(tr, trapTop);
    ctx.lineTo(br, h);
    ctx.lineTo(whiskerRight, h);
    ctx.stroke();

    // ── Contour (U-shape dip from top of trapezoid) ──
    if (contour > 0) {
      const contourX = tl + (contourFreq / 255) * (tr - tl);
      const depth = (contour / 255) * trapH * 0.4;
      const cWidth = (tr - tl) * 0.25;

      ctx.strokeStyle = `${INK_A} ${alphaActive * 0.6})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(contourX - cWidth, trapTop);
      ctx.quadraticCurveTo(contourX, trapTop + depth * 2, contourX + cWidth, trapTop);
      ctx.stroke();
    }

    // ── Manual notch (sharp V from top of trapezoid) ──
    if (manualNotch) {
      const notchX = tl + (notchFreq / 255) * (tr - tl);
      const depth = trapH * 0.55;
      const nHalfW = (tr - tl) * 0.06;

      ctx.strokeStyle = `${INK_A} ${alphaActive * 0.75})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(notchX - nHalfW, trapTop);
      ctx.lineTo(notchX, trapTop + depth);
      ctx.lineTo(notchX + nHalfW, trapTop);
      ctx.stroke();
    }

    // ── FFT bars INSIDE trapezoid — only passband portion of spectrum ──
    drawFft(ctx, pixels, w, h, trapTop, trapH, cx, topHalfW, slopeExtra, filterHz);
  }

  function drawFft(
    ctx: CanvasRenderingContext2D,
    pixels: Uint8Array | null,
    w: number,
    h: number,
    trapTop: number,
    trapH: number,
    cx: number,
    topHalfW: number,
    slopeExtra: number,
    passbandHz: number,
  ): void {
    if (!pixels || pixels.length === 0) {
      smoothed = null;
      runningMax = null;
      return;
    }

    const maxVal = 160;
    const barW = 2;
    const gap = 1;
    const step = barW + gap;

    // Determine bar range: only inside trapezoid at bottom
    const botLeft = cx - topHalfW - slopeExtra;
    const botRight = cx + topHalfW + slopeExtra;
    const startX = Math.max(0, Math.floor(botLeft));
    const endX = Math.min(w, Math.ceil(botRight));
    const numBars = Math.floor((endX - startX) / step);

    if (numBars <= 0) return;

    if (!smoothed || smoothed.length !== numBars) {
      smoothed = new Float32Array(numBars);
    }

    // Allocate running-max buffer only in dominant mode
    const isDominant = mode === 'dominant';
    if (isDominant && (!runningMax || runningMax.length !== numBars)) {
      runningMax = new Float32Array(numBars);
    }

    // Only show FFT bins within the passband (0 → passbandHz)
    const halfBandwidth = effectiveBandwidth / 2;
    const passbandFrac = Math.min(1, passbandHz / halfBandwidth);

    for (let i = 0; i < numBars; i++) {
      const x = startX + i * step;
      const barCenterX = x + barW / 2;

      // Get raw amplitude — map bar position to passband FFT bins only
      // pixels[] is symmetric: [-Nyquist ... DC ... +Nyquist]
      // DC is at center (pixels.length / 2), positive freqs are right half
      const dcIdx = Math.floor(pixels.length / 2);
      const positiveLen = pixels.length - dcIdx; // DC → Nyquist

      // Bar position 0→1 within trapezoid
      const barFrac = (barCenterX - startX) / (endX - startX);
      // Map to positive-side FFT bin within passband only
      const pixIdx = dcIdx + Math.floor(barFrac * passbandFrac * positiveLen);
      const clamped = Math.max(dcIdx, Math.min(pixels.length - 1, pixIdx));
      const rawAmp = Math.min(pixels[clamped], maxVal) / maxVal;

      // Smooth
      const prev = smoothed[i];
      smoothed[i] = rawAmp > prev
        ? prev + (rawAmp - prev) * ATTACK
        : prev + (rawAmp - prev) * DECAY;

      const amp = smoothed[i];

      // Update running-max: decay first, then take max with current smoothed value
      if (isDominant && runningMax) {
        runningMax[i] = Math.max(Math.max(0, runningMax[i] - MAX_DECAY), amp);
      }

      // Max bar height = clipped by trapezoid at this X
      const distFromCenter = Math.abs(barCenterX - cx);
      let maxBarH: number;
      if (distFromCenter <= topHalfW) {
        maxBarH = trapH;
      } else if (distFromCenter <= topHalfW + slopeExtra) {
        maxBarH = trapH * (1 - (distFromCenter - topHalfW) / slopeExtra);
      } else {
        continue;
      }

      const barH = Math.min(amp * trapH * 0.85, maxBarH);
      if (barH < 1) continue;

      const y = h - barH;
      const snapY = Math.round(y / 2) * 2;
      const snapH = h - snapY;

      const alpha = (0.35 + amp * 0.5) * alphaActive;
      ctx.fillStyle = `${INK_A} ${alpha})`;
      ctx.fillRect(x, snapY, barW, snapH);
    }

    // ── Running-max overlay (dominant mode only) ──
    // Render as a dimmed stroke on top of the FFT bars
    if (isDominant && runningMax) {
      ctx.strokeStyle = `${INK_A} ${alphaGhost * 3})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < numBars; i++) {
        const x = startX + i * step + barW / 2;
        const barCenterX = x;
        const distFromCenter = Math.abs(barCenterX - cx);
        let maxBarH: number;
        if (distFromCenter <= topHalfW) {
          maxBarH = trapH;
        } else if (distFromCenter <= topHalfW + slopeExtra) {
          maxBarH = trapH * (1 - (distFromCenter - topHalfW) / slopeExtra);
        } else {
          if (started) { ctx.stroke(); ctx.beginPath(); started = false; }
          continue;
        }
        const peakH = Math.min(runningMax[i] * trapH * 0.85, maxBarH);
        const peakY = h - peakH;
        if (!started) { ctx.moveTo(x, peakY); started = true; }
        else { ctx.lineTo(x, peakY); }
      }
      if (started) ctx.stroke();
    }
  }

  function onVisibilityChange() {
    visible = !document.hidden;
    if (visible && rafId === 0) rafId = requestAnimationFrame(draw);
  }

  onMount(() => {
    onRegisterPush?.((pixels: Uint8Array) => {
      latestPixels = pixels;
    });

    document.addEventListener('visibilitychange', onVisibilityChange);
    rafId = requestAnimationFrame(draw);

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      cssWidth = Math.max(1, Math.floor(rect.width));
      cssHeight = Math.max(1, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cssWidth * dpr);
      canvas.height = Math.round(cssHeight * dpr);
      canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
    });
    ro.observe(canvas);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      ro.disconnect();
      cancelAnimationFrame(rafId);
      rafId = 0;
    };
  });
</script>

<div class="af-scope">
  <canvas bind:this={canvas}></canvas>
</div>

<style>
  .af-scope {
    width: 100%;
    height: 100%;
    position: relative;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
