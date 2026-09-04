<script lang="ts">
  import type { ScopeFrame } from '../../lib/runtime/adapters/scope-adapter';
  import { tick } from 'svelte';

  interface Props { frame: ScopeFrame | null; }
  let { frame }: Props = $props();
  let canvas: HTMLCanvasElement;
  $effect(() => {
    const next = frame;
    void tick().then(() => {
      if (!canvas || !next) return;
      const context = canvas.getContext('2d');
      if (!context) return;
      const width = canvas.width = Math.max(1, canvas.clientWidth);
      const height = canvas.height = Math.max(1, canvas.clientHeight);
      context.clearRect(0, 0, width, height);
      const pixels = next.pixels;
      if (pixels.length === 0) return;
      context.beginPath();
      for (let x = 0; x < width; x += 1) {
        const value = pixels[Math.min(pixels.length - 1, Math.floor((x / width) * pixels.length))];
        const y = height - 1 - (value / 255) * (height - 1);
        x === 0 ? context.moveTo(x, y) : context.lineTo(x, y);
      }
      context.strokeStyle = '#dcefeb'; context.lineWidth = 1; context.stroke();
    });
  });
</script>

<div class="scope" data-scope-state={frame === null ? 'unknown' : 'frame'}>
  {#if frame === null}<output>— scope unavailable</output>{/if}
  <canvas bind:this={canvas} aria-label="Receiver spectrum" data-supplied-pixels={frame?.pixels.length ?? 0}></canvas>
  {#if frame !== null}<div class="axis">{frame.startFreq} — {frame.endFreq}</div>{/if}
</div>

<style>
  .scope { min-height: 150px; position: relative; background: repeating-linear-gradient(0deg, #05090b 0 18px, #1f3539 19px 20px), repeating-linear-gradient(90deg, transparent 0 46px, #1f3539 47px 48px); border: 2px solid #657277; }
  canvas { display: block; width: 100%; height: 150px; }.axis { color: #c5d0d0; font: 11px ui-monospace, monospace; text-align: center; }
  output { position: absolute; z-index: 1; inset: 0; display: grid; place-items: center; color: #9ba8ab; font: 13px ui-monospace, monospace; background: #05090bd9; }
</style>
