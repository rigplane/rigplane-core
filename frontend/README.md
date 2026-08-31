# RigPlane Frontend

Svelte 5 + TypeScript + Vite UI for the RigPlane multi-vendor radio control
platform (Icom, Yaesu, Discovery, Xiegu) over LAN/USB.

## Quick Start

```bash
cd frontend
pnpm install
pnpm dev        # Vite dev server at http://localhost:5173 with HMR
                # Proxies /api and /ws to backend at http://localhost:8080
```

## Build

```bash
pnpm build      # → dist/ (optimized bundle)
pnpm preview    # Preview the production build locally
```

## Type Check & Tests

```bash
pnpm check      # svelte-check + tsc
pnpm test       # vitest run
pnpm test:e2e   # Playwright end-to-end tests
```

## Component Architecture

All interactive controls use `HardwareButton`. All read-only status displays use `StatusIndicator`.

```svelte
<script>
  import { HardwareButton, StatusIndicator } from '$lib/Button';
</script>

<!-- Toggle button with edge-left indicator -->
<HardwareButton active={nrEnabled} indicator="edge-left" color="amber" onclick={toggleNr}>
  NR
</HardwareButton>

<!-- Read-only status badge -->
<StatusIndicator label={mode} active color="cyan" />
```

Component APIs live with the code in `src/lib/Button/`: `index.ts` is the entry
point and re-exports the components, with prop types in `types.ts`.

The `--v2-*` CSS design tokens live in `src/components-v2/theme/`:
`tokens.css` holds the base set, and the per-theme files under `themes/`
and `vfo-themes/` override them and define a few badge-color tokens of
their own.

Layout and skin composition are covered by the frontend ADR,
[`docs/plans/2026-04-12-target-frontend-architecture.md`](../docs/plans/2026-04-12-target-frontend-architecture.md).

## Directory Structure

```
src/
├── App.svelte                      # Root component
├── app.css                         # Global styles
├── styles/
│   └── tokens.css                  # Legacy tokens (--bg, --accent, …)
├── lib/
│   ├── Button/                     # Button component library
│   │   ├── HardwareButton.svelte   # Primary interactive control
│   │   ├── StatusIndicator.svelte  # Read-only status badge
│   │   ├── ControlButton.svelte    # Internal base primitive
│   │   └── types.ts                # Prop type definitions
│   ├── stores/                     # Svelte 5 $state stores
│   ├── transport/                  # WebSocket/HTTP client
│   └── utils/                      # Frequency formatting, BCD, …
├── components-v2/                  # v2 design system (active)
│   ├── controls/
│   │   ├── control-button.css      # Button styles
│   │   ├── button-tokens.css       # Button design tokens
│   │   └── value-control/          # ValueControl + renderers
│   ├── layout/
│   │   ├── RadioLayout.svelte      # Main grid layout
│   │   ├── LeftSidebar.svelte      # Collapsible control panels
│   │   └── VfoHeader.svelte        # VFO header with badges
│   ├── vfo/
│   │   └── VfoPanel.svelte         # Individual VFO receiver display
│   ├── panels/                     # DspPanel, TxPanel, CwPanel, …
│   ├── theme/
│   │   ├── tokens.css              # All --v2-* design tokens
│   │   └── themes/                 # 20+ theme overrides
│   └── wiring/
│       ├── state-adapter.ts        # Radio state → component props
│       └── command-bus.ts          # User actions → radio commands
└── components/                     # Legacy v1 components
```

## Architecture

### Key Principles

- **Server state is the single source of truth** — UI never treats DOM state as truth
- **Rendered UI = server_state + pending_actions + local_ui_state**
- **Commands** are sent via WebSocket and reconciled with server state on next poll
- **Components are pure** — props in, events out; no component reads from WebSocket directly

### Button System (v2)

After the UI migration, all controls use a unified button system:

| Component | Role |
|-----------|------|
| `HardwareButton` | All interactive toggles and selectors |
| `StatusIndicator` | All read-only status badges |
| `ControlButton` | Internal base (do not use directly) |
| `ValueControl` | Sliders and knobs (hbar/bipolar/knob/discrete) |

### Color Scheme

| Color  | Semantic use |
|--------|-------------|
| Cyan   | MAIN receiver, default controls |
| Green  | ATT, PRE, DIGI-SEL, SQL |
| Amber  | DSP (NR, NB, NOTCH, ANF), RIT, XIT |
| Orange | SUB receiver accent, NB badge |
| Red    | TX active, danger |
