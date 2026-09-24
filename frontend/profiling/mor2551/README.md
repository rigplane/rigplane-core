# Offline state replay profiler

This diagnostic entry imports production `src/main.ts` in `mounted` mode and
production transport/stores in `store-only` mode. It is not part of the normal
Vite entry. `run.mjs` requires the full state and two deltas to commit, the
mounted desktop shell to render, and VIEW OFF before profiling.

From the repository root, prepare an absolute artifact directory outside every
worktree and set `PROFILE_OUTPUT` to it. Then run:

```sh
uv sync --all-extras
uv run python frontend/profiling/mor2551/generate-fixture.py > "$PROFILE_OUTPUT/canonical.json"
cd frontend
npm ci
npx vite build --config profiling/mor2551/vite.config.ts
node profiling/mor2551/run.mjs --preflight
node profiling/mor2551/run.mjs
node profiling/mor2551/summarize.mjs
node profiling/mor2551/run.mjs --webkit
npx svelte-check --tsconfig profiling/mor2551/tsconfig.json
```

The fixture derives canonical status paths from the backend's two-receiver
projection and combines its default payload with the existing browser catalog
and visual-test IC-7610 fixtures. Missing/null fields remain unobserved. Frame
sizes and status counts are reported rather than padded. Freshness and delivery
counters advance, RF values remain fixed, PTT stays false. The bounded serialized
stream delivers 11 states/s (one full per 110 states) and 14 inert events/s via
separate timer tasks; browser throttling/jitter is visible in recorded arrivals.

The runner serves built files on loopback with no API proxy. Browser contexts
block service workers; fake WebSocket/fetch implementations sink application
requests, and Playwright blocks unexpected network requests and real sockets.
The precise startup PUT `/api/local/v1/rc28/tuning-step` receives a synthetic 404;
its count and body hash are recorded. Any other write, or that PUT during replay,
fails. This differs from an installed companion that implements the endpoint.
The empty local-extension manifest and band-plan fixtures are also synthetic.

Each arm uses a fresh context, five seconds of warmup, and twenty seconds of
capture, in mounted/store/store/mounted order. Chromium CDP samples at 1 ms;
WebKit runs the same cadence and acceptance checks without CDP profiling.
`hotspots.json` resolves function locations through build source maps; anonymous
and unmapped functions retain explicit labels. Sampled time is a statistical
estimate, not process CPU. Synthetic Chromium results cannot establish an
installed WebKit CPU cause or validate a product optimization.
