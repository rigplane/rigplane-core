<script lang="ts">
  interface Props {
    freqHz: number;
    size?: 'large' | 'small';
  }

  let { freqHz, size = 'large' }: Props = $props();

  let formatted = $derived(formatFreq(freqHz));

  function formatFreq(hz: number): { mhz: string; khz: string; hertz: string } {
    if (hz <= 0) return { mhz: '--', khz: '---', hertz: '---' };
    const mhz = Math.floor(hz / 1_000_000);
    const khz = Math.floor((hz % 1_000_000) / 1_000);
    const hertz = Math.floor(hz % 1_000);
    return {
      mhz: mhz.toString(),
      khz: khz.toString().padStart(3, '0'),
      hertz: hertz.toString().padStart(3, '0'),
    };
  }
</script>

<div class="lcd-freq" class:lcd-freq-large={size === 'large'} class:lcd-freq-small={size === 'small'}>
  <!-- Ghost segments (all-8s) for LCD look -->
  <div class="freq-ghost" aria-hidden="true">
    <span class="seg-mhz">{formatted.mhz.replace(/./g, '8')}</span>
    <span class="seg-dot">.</span>
    <span class="seg-khz">888</span>
    <span class="seg-dot">.</span>
    <span class="seg-hz">888</span>
  </div>
  <!-- Active digits -->
  <div class="freq-active">
    <span class="seg-mhz">{formatted.mhz}</span>
    <span class="seg-dot">.</span>
    <span class="seg-khz">{formatted.khz}</span>
    <span class="seg-dot">.</span>
    <span class="seg-hz">{formatted.hertz}</span>
  </div>
</div>

<style>
  .lcd-freq {
    position: relative;
    display: inline-flex;
    user-select: none;
  }

  .freq-ghost, .freq-active {
    display: flex;
    align-items: baseline;
    font-family: 'DSEG7 Classic', monospace;
    font-weight: bold;
  }

  .freq-ghost {
    color: rgba(26, 16, 0, var(--lcd-alpha-ghost, 0.06));
  }

  .freq-active {
    position: absolute;
    inset: 0;
    color: rgba(26, 16, 0, var(--lcd-alpha-active, 1));
  }

  .seg-dot {
    font-family: 'JetBrains Mono', 'Courier New', monospace;
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active, 1) * 0.7));
    font-weight: 700;
    margin: 0 1px;
  }

  .freq-ghost .seg-dot {
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-ghost, 0.06) * 0.7));
  }

  .seg-hz {
    opacity: 0.6;
  }

  /* ── Large (main VFO) ── */
  .lcd-freq-large .seg-mhz,
  .lcd-freq-large .seg-khz {
    font-size: var(--lcd-frequency-major-size, clamp(48px, 8vw, 80px));
    letter-spacing: 3px;
  }

  .lcd-freq-large .seg-hz {
    font-size: var(--lcd-frequency-hz-size, clamp(36px, 6vw, 60px));
    letter-spacing: 2px;
  }

  .lcd-freq-large .seg-dot {
    font-size: var(--lcd-frequency-dot-size, clamp(36px, 5vw, 56px));
    margin: 0 2px;
  }

  /* ── Small (sub VFO) ── */
  .lcd-freq-small .seg-mhz,
  .lcd-freq-small .seg-khz {
    font-size: clamp(20px, 3vw, 32px);
    letter-spacing: 2px;
  }

  .lcd-freq-small .seg-hz {
    font-size: clamp(16px, 2.5vw, 24px);
    letter-spacing: 1px;
  }

  .lcd-freq-small .seg-dot {
    font-size: clamp(16px, 2vw, 24px);
    margin: 0 1px;
  }

  .lcd-freq-small .freq-active {
    color: rgba(26, 16, 0, calc(var(--lcd-alpha-active, 1) * 0.5));
  }
</style>
