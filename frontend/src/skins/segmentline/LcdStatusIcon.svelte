<script lang="ts">
  export type LcdStatusIconName =
    | 'AGC' | 'ANF' | 'ATT' | 'ATU' | 'IPP' | 'LOCK' | 'NB' | 'NOTCH' | 'NR'
    | 'PRE' | 'PROC' | 'RIT' | 'SPLIT' | 'TUNE' | 'TX' | 'VOX';
  interface Props { name: LcdStatusIconName; size?: number }
  let { name, size = 13 }: Props = $props();
</script>

<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === 'VOX'}
    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" /><path d="M12 19v3" /><path d="M19 4a4 4 0 0 1 0 6" />
  {:else if name === 'PROC'}
    <path d="M3 12h6" /><path d="m6 9 3 3-3 3" /><path d="M21 12h-6" />
    <path d="m18 9-3 3 3 3" /><path d="M12 4v16" />
  {:else if name === 'PRE'}
    <path d="M5 4v16l15-8z" /><path d="M2 12h3" /><path d="M20 12h2" />
  {:else if name === 'ATT'}
    <path d="M3 12h18" /><path d="M7 6v12" /><path d="m12 17 5-5-5-5" /><path d="M17 12H7" />
  {:else if name === 'IPP'}
    <circle cx="12" cy="14" r="6" /><path d="M9 14h6" /><path d="M12 11v6" />
    <path d="M3 4h6v3H3z" /><path d="M15 4h6v3h-6z" />
  {:else if name === 'NB'}
    <path d="M13 2 4 14h8l-1 8 9-12h-8z" /><path d="m2 2 20 20" />
  {:else if name === 'NR'}
    <path d="M3 12c1-3 2 3 3 0s2 3 3 0 2 3 3 0" /><path d="M14 12h7" />
  {:else if name === 'NOTCH'}
    <path d="M3 18 6 6h2l2 8h4l2-8h2l3 12" /><path d="M11 14h2" />
  {:else if name === 'ANF'}
    <path d="M19.07 4.93A10 10 0 0 0 6.99 3.34" /><path d="M4 6h.01" />
    <path d="M2.29 9.62A10 10 0 1 0 21.31 8.35" />
    <path d="M16.24 7.76A6 6 0 1 0 17.43 14" /><path d="M12 18h.01" />
    <path d="M17.99 11.66A6 6 0 0 1 15.77 16.67" />
    <circle cx="12" cy="12" r="2" /><path d="m13.41 10.59 5.66-5.66" />
  {:else if name === 'AGC'}
    <rect x="4" y="4" width="16" height="16" rx="2" /><path d="M12 7v10" />
    <path d="m9 10 3-3 3 3" /><path d="m9 14 3 3 3-3" />
  {:else if name === 'ATU'}
    <path d="M12 4v8" /><path d="m8 8 4-4 4 4" /><circle cx="12" cy="16" r="5" />
    <path d="M12 13v3" /><path d="m9.5 14.5 2.5 1.5" />
  {:else if name === 'TX'}
    <path d="m18 9-6-6-6 6" /><path d="M12 3v14" /><path d="M5 21h14" />
  {:else if name === 'TUNE'}
    <path d="M12 2v4" /><path d="m16.2 7.8 2.9-2.9" /><path d="M18 12h4" />
    <path d="m16.2 16.2 2.9 2.9" /><path d="M12 18v4" /><path d="m4.9 19.1 2.9-2.9" />
    <path d="M2 12h4" /><path d="m4.9 4.9 2.9 2.9" />
  {:else if name === 'LOCK'}
    <rect width="18" height="11" x="3" y="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
  {:else if name === 'SPLIT'}
    <path d="M8 3 4 7l4 4" /><path d="M4 7h16" /><path d="m16 21 4-4-4-4" /><path d="M20 17H4" />
  {:else if name === 'RIT'}
    <path d="M3 12h18" /><path d="M19 8h-4" /><path d="M7 16H3" /><path d="M5 14v4" />
  {/if}
</svg>

<style>svg { flex: 0 0 auto; }</style>
