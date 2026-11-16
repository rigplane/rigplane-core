import path from 'path';
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

/**
 * Vite config for the R-A1 test-only boot entry (remote-entry.html +
 * boot-entry.ts): the same plugin, alias and build target as the real UI
 * build (vite.config.ts), with the entry's own HTML as the input and
 * frontend/dist/remote-entry as the output — inside the station's
 * static_dir, so the station serves the page and its assets itself on its
 * own origin (a secure context, like every row of this harness). `npm run
 * build` wipes `dist/`, so this build always runs AFTER it, from the
 * Playwright globalSetup (build-entry.setup.ts).
 */
export default defineConfig({
  // The entry's own directory is the root: the page is emitted as
  // dist/remote-entry/index.html (not nested by its source path), and no
  // public/ dir is copied — the station serves everything the page needs.
  root: __dirname,
  plugins: [svelte()],
  resolve: {
    alias: {
      '$lib': path.resolve(__dirname, '../../../src/lib'),
    },
    conditions: ['svelte', 'browser'],
  },
  base: '/remote-entry/',
  build: {
    // Absolute: vite resolves a relative outDir against its root, which is
    // this config's directory — the station serves frontend/dist, so the
    // entry must land there whatever the root is.
    outDir: path.resolve(__dirname, '../../../dist/remote-entry'),
    emptyOutDir: true,
    target: ['es2020', 'safari14'],
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html'),
    },
  },
});
