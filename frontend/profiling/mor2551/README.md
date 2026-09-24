# Offline state replay profiler

This development-only entry imports production `src/main.ts` in `mounted` mode and
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
# Optional; requires the Playwright WebKit browser:
node profiling/mor2551/run.mjs --webkit
npx svelte-check --tsconfig profiling/mor2551/tsconfig.json
```

The fixture derives canonical status paths from the backend's two-receiver
projection and combines its default payload with the existing browser catalog
and visual-test IC-7610 fixtures. Missing/null fields remain unobserved. Frame
sizes and status counts are reported rather than padded. Semantic and freshness revisions remain constant in the steady-fresh scenario;
observation/delivery counters and timestamps advance. RF values remain fixed and
PTT stays false. assertSteadyFreshFixture checks all 400 serialized frames before
production imports. The bounded serialized
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
capture, in mounted/store/store/mounted order. The stream pauses after warmup,
resumes after Profiler.start, and stops before Profiler.stop. All reported profile
window frames therefore occur within the active profile, excluding save overhead. Chromium CDP samples at 1 ms;
WebKit runs the same cadence and acceptance checks without CDP profiling.
`hotspots.json` resolves function locations through build source maps; anonymous
and unmapped functions retain explicit labels. Sampled time is a statistical
estimate, not process CPU. Synthetic Chromium results cannot establish an
installed WebKit CPU cause or validate a product optimization.

## Regression gate

After `uv sync --all-extras`, `npm ci` in `frontend`, and Playwright Chromium
provisioning (`npx playwright install chromium`), run from `frontend`:

```sh
export PROFILE_OUTPUT="$(mktemp -d)"
npm run test:replay
# Optional four-arm CPU capture and source-map summary, using that same build:
npm run profile:replay
```

`test:replay` generates the canonical fixture, builds the dedicated entry, checks
full + two delta acceptance, then delivers another 118 states as separate browser
tasks in each mode. `run.mjs` asserts the exact accepted sequence 1–121, including
the full frame at 110, PTT false, mounted VIEW OFF, and no forbidden I/O or commands.
This fast replay checks acceptance, not real-time cadence or performance.
Startup PUT sinks remain recorded in each `*-preflight.json` / `*-replay.json`.

The ordinary `npm run check` includes the profiler typecheck. The existing quick
Vitest run includes `radio-view-model-cache.isolated.test.ts` for snapshot and
capability invalidation plus TX/audio/scope bypass. Browser replay is an explicit
command: quick currently provisions Chromium after Vitest, and Python dependencies
only for core paths. It does not automatically execute this command. Running it
in CI requires Python dependencies and Chromium first; CDP is not used by this
gate. No sampled-CPU threshold is enforced on shared runners. Profile-window
frame counts remain measured arrivals and can differ at a timer boundary; inspect
them before comparing profiles. Artifacts stay in the chosen external directory.
