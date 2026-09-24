import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const output = process.env.PROFILE_OUTPUT;
if (!output || !path.isAbsolute(output)) throw new Error('PROFILE_OUTPUT must be absolute');
const root = path.resolve(import.meta.dirname, '../..');
if (output.startsWith(path.dirname(root) + path.sep)) throw new Error('Output must be outside this worktree');
export default defineConfig({
  root,
  plugins: [svelte(), {
    name: 'synthetic-fixture',
    resolveId(id) { if (id === 'virtual:synthetic-fixture') return '\0synthetic-fixture'; },
    load(id) { if (id === '\0synthetic-fixture') return `export default ${readFileSync(path.join(output, 'canonical.json'), 'utf8')}`; },
  }],
  resolve: { alias: { $lib: path.join(root, 'src/lib') }, conditions: ['svelte', 'browser'] },
  server: { host: '127.0.0.1', proxy: {} },
  preview: { host: '127.0.0.1', proxy: {} },
  build: { target: "esnext", outDir: path.join(output, 'dist'), emptyOutDir: true, sourcemap: true,
    minify: false, rollupOptions: { input: path.join(import.meta.dirname, 'index.html') } },
});
