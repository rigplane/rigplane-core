/**
 * MOR-3280 part 2 — the per-skin reachability matrix (PR B of 2).
 *
 * Each row loads the REAL built app against the in-page fake socket
 * (`skin-reach-fixture.ts`), asserts the skin's OWN root selector is
 * visible (a topology fallback mounts a different skin and must fail
 * here), then interacts with a fixed set of the skin's non-TX controls
 * and asserts the exact outgoing `{type:'cmd', name, params}` frame for
 * each. A skin that renders but is wired to nothing fails on the frame.
 * This is not exhaustive per-skin coverage: each row pins the families
 * its probes name, nothing more. PR A covered desktop-v2, sdr-test,
 * flagship-probe and dual-receiver-cockpit; this PR adds the five LCD
 * variants, the mobile skin and dual-sdr-face — the last as a negative
 * row: that skin defines itself display-only and must emit no command.
 *
 * Selectors below were verified against the built app (inventory run on
 * the i18n preview server): desktop-v2 paints `standard-mode-*`,
 * `filter-width`, `dsp-compact-*` and the compact band row
 * `band-choices-compact`; sdr-test and flagship-probe paint
 * `filter-mode-*`, `dsp-nrActive` and `band-choice-*`.
 */
import { test, expect, type Locator, type Page } from '@playwright/test';
import {
  bootSkinReach, clearSkinReachFrames, skinReachCommands, type SkinReachBoot,
} from './skin-reach-fixture';

/**
 * Transmit command names this matrix must never emit — clicked or not, a
 * frame with one of these names fails the row. `ws-client.ts: pttIntent`
 * treats `ptt_on`, `ptt_off` AND `{name:'ptt', params:{state}}` as PTT;
 * `ptt_on`/`ptt_off` leave through the managed TX controller
 * (`tx-controller/browser-dependencies.ts: sendPtt`). `set_tuner_status`
 * is the ATU path (`panel-commands.ts: makeTxHandlers` sends it for
 * `onAtuToggle`/`onAtuTune`) and `set_vox` arms VOX (`panel-commands.ts:
 * toggleVox`, exposed by `makeTxHandlers`/`makeVoxHandlers` as
 * `onVoxToggle`). `cw_auto_tune` is deliberately NOT listed: the server
 * handler (`web/handlers/control.py: _cw_auto_tune`) taps RX audio FFT
 * and at most queues a `SetFreq` zero-beat shift — it never keys the
 * transmitter. The probed controls are exclusively receive-side: mode,
 * filter width, DSP, band/tune and VFO select; no PTT key, no
 * TxAux/ATU/VOX control, no CW keyer control and no keyboard input.
 */
const TRANSMIT_COMMANDS = new Set(['ptt_on', 'ptt_off', 'ptt', 'set_tuner_status', 'set_vox']);

interface ReachProbe {
  /** The interaction, including its own `toBeEnabled` on the actionable element. */
  run: (page: Page) => Promise<void>;
  frame: { name: string; params: Record<string, unknown> };
}

interface ReachRow {
  skin: string;
  boot: SkinReachBoot;
  /** The skin's OWN root — a topology fallback must not pass this row. */
  root: string;
  /** Further roots that must ALSO be visible: the LCD family shares
   * `.lcd-layout`, so each row additionally pins its discriminating
   * `.lcd-slot[data-lcd-variant=...]` selector. */
  extraRoots?: string[];
  probes: ReachProbe[];
}

/** Mode key press: USB is active in the fixture, LSB is not. */
async function pressModeKey(page: Page, key: Locator): Promise<void> {
  await expect(key).toBeEnabled();
  await key.click();
}

/**
 * The five LCD skins are thin wrappers over `LcdLayout.svelte` (its
 * `variant` prop: cockpit | scope | peer-split | unified-instrument |
 * panadapter-first); every interactive control this family exposes is the
 * legacy sidebar panels `LcdLayout.svelte` mounts (`LeftSidebar.svelte`,
 * `RightSidebar.svelte`). The segmentline glass of peer-split,
 * unified-instrument and panadapter-first is read-only
 * (`PeerSplitLayout.svelte` renders it through `SemanticRadioSurfaces`'
 * `readonlyDisplay` snippet), so these probes touch the shared sidebars
 * only. The LCD TUNE key (`VfoControlPanel.svelte`'s `requestAtuTune`,
 * transmitter ATU) and every TX/PTT/VOX/keyer control are excluded like
 * the other rows' TX paths. `LeftSidebar.svelte`'s default panel order
 * has no 'dsp' entry, so the DSP panel lives in the right column only —
 * hence the `.content-right` scope on the NR probe.
 */
const lcdSidebarProbes: ReachProbe[] = [
  {
    run: page => pressModeKey(page,
      page.locator('[data-panel-id="mode"] [data-mode-panel]')
        .getByRole('button', { name: 'LSB', exact: true })),
    frame: { name: 'set_mode', params: { mode: 'LSB', receiver: 0 } },
  },
  {
    run: async page => {
      const fil2 = page.locator('[data-panel-id="filter"]')
        .getByRole('button', { name: 'FIL2', exact: true });
      await expect(fil2).toBeEnabled();
      await fil2.click();
    },
    frame: { name: 'set_filter', params: { filter: 2, receiver: 0 } },
  },
  {
    run: async page => {
      const nr = page.locator('.content-right [data-panel-id="dsp"]')
        .getByRole('button', { name: 'NR', exact: true });
      await expect(nr).toBeEnabled();
      await nr.click();
    },
    frame: { name: 'set_nr', params: { on: true, receiver: 0 } },
  },
  {
    // 40m carries no bsrCode in the catalog caps, so the legacy band
    // button falls back to the bandless jump (`panel-commands.ts`:
    // `makeBandHandlers().onBandSelect` -> `tuningAccumulator().jump`).
    run: async page => {
      const band = page.locator('[data-panel-id="band"]')
        .getByRole('button', { name: '40m', exact: true });
      await expect(band).toBeEnabled();
      await band.click();
    },
    frame: { name: 'set_freq', params: { freq: 7_100_000, receiver: 0 } },
  },
];

/** One row per LCD variant; all five share `lcdSidebarProbes`. */
const LCD_VARIANTS = [
  { skin: 'lcd-cockpit', variant: 'cockpit' },
  { skin: 'lcd-scope', variant: 'scope' },
  { skin: 'peer-split', variant: 'peer-split' },
  { skin: 'unified-instrument', variant: 'unified-instrument' },
  { skin: 'panadapter-first', variant: 'panadapter-first' },
] as const;

const ROWS: ReachRow[] = [
  {
    skin: 'desktop-v2',
    boot: { layout: 'standard' },
    root: '.desktop-control-face.standard-face',
    probes: [
      {
        run: page => pressModeKey(page,
          page.locator('[data-testid="standard-mode-LSB"]').getByRole('radio')),
        frame: { name: 'set_mode', params: { mode: 'LSB', receiver: 0 } },
      },
      {
        run: async page => {
          const width = page.locator('[data-testid="filter-width"] input');
          await expect(width).toBeEnabled();
          await width.fill('1800');
        },
        frame: { name: 'set_filter_width', params: { width: 1800, receiver: 0 } },
      },
      {
        run: async page => {
          const nr = page.getByTestId('dsp-compact-nr');
          await expect(nr).toBeEnabled();
          await nr.click();
        },
        frame: { name: 'set_nr', params: { on: true, receiver: 0 } },
      },
      {
        // The compact band row renders one HardwareButton per band with no
        // per-band testid (`BandInstrumentHost.svelte: bandChoice(compact)`),
        // so the 40m key is targeted by its accessible name.
        run: async page => {
          const band = page.getByTestId('band-choices-compact').getByRole('button', { name: '40m' });
          await expect(band).toBeEnabled();
          await band.click();
        },
        frame: { name: 'set_freq', params: { freq: 7_100_000, receiver: 0 } },
      },
      {
        // desktop-v2's direct frequency path: the frequency-entry dialog
        // the readout opens.
        run: async page => {
          await page.locator('.receiver-instrument [data-vfo-freq]').first().click();
          // The submit key stays disabled until the draft parses.
          await page.getByTestId('band-entry-input').fill('7.100');
          const set = page.getByTestId('band-entry-set');
          await expect(set).toBeEnabled();
          await set.click();
        },
        frame: { name: 'set_freq', params: { freq: 7_100_000, receiver: 0 } },
      },
    ],
  },
  {
    skin: 'sdr-test',
    boot: { layout: 'sdr-test' },
    root: '.desktop-control-face.sdr-test',
    probes: [
      {
        run: page => pressModeKey(page, page.getByTestId('filter-mode-LSB')),
        frame: { name: 'set_mode', params: { mode: 'LSB', receiver: 0 } },
      },
      {
        run: async page => {
          const nr = page.getByTestId('dsp-nrActive');
          await expect(nr).toBeEnabled();
          await nr.click();
        },
        frame: { name: 'set_nr', params: { on: true, receiver: 0 } },
      },
      {
        // 40m has no bsrCode in the catalog caps, so band select falls back
        // to a plain jump to the band default (`SemanticRadioSurfaces.svelte:
        // selectBand` -> `tuneFrequency(..., 'jump')`).
        run: async page => {
          const band = page.getByTestId('band-choice-40m');
          await expect(band).toBeEnabled();
          await band.click();
        },
        frame: { name: 'set_freq', params: { freq: 7_100_000, receiver: 0 } },
      },
    ],
  },
  {
    skin: 'flagship-probe',
    boot: { qaLayout: 'flagship-probe' },
    root: '[data-testid="flagship-geometry-probe"]',
    probes: [
      {
        run: page => pressModeKey(page, page.getByTestId('filter-mode-LSB')),
        frame: { name: 'set_mode', params: { mode: 'LSB', receiver: 0 } },
      },
      {
        run: async page => {
          const nr = page.getByTestId('dsp-nrActive');
          await expect(nr).toBeEnabled();
          await nr.click();
        },
        frame: { name: 'set_nr', params: { on: true, receiver: 0 } },
      },
      {
        run: async page => {
          const band = page.getByTestId('band-choice-40m');
          await expect(band).toBeEnabled();
          await band.click();
        },
        frame: { name: 'set_freq', params: { freq: 7_100_000, receiver: 0 } },
      },
      {
        run: async page => {
          const select = page.locator('[data-strip-receiver="SUB"] [data-vfo-select]');
          await expect(select).toBeEnabled();
          await select.click();
        },
        frame: { name: 'set_vfo', params: { vfo: 'SUB' } },
      },
    ],
  },
  {
    // `presentation/layouts/dual-receiver-cockpit.ts` declares only
    // primary-vfo/secondary-vfo/global/rx-tx/tx-aux zones mounting the
    // vfo, rxTx and txAux surfaces, so the cockpit has no mode, filter,
    // DSP or band controls. This row probes ONLY the SUB VFO select; the
    // other non-TX controls the vfo surface gives this skin — the global
    // zone's split / dual-watch / VFO operations and the strips'
    // frequency entry — are not probed here.
    skin: 'dual-receiver-cockpit',
    boot: { qaLayout: 'dual-receiver-cockpit' },
    root: '[data-testid="dual-receiver-cockpit"]',
    probes: [
      {
        run: async page => {
          const select = page.locator('[data-strip-receiver="SUB"] [data-vfo-select]');
          await expect(select).toBeEnabled();
          await select.click();
        },
        frame: { name: 'set_vfo', params: { vfo: 'SUB' } },
      },
    ],
  },
  ...LCD_VARIANTS.map(({ skin, variant }): ReachRow => ({
    skin,
    boot: { layout: skin },
    root: '.lcd-layout',
    extraRoots: [`.lcd-slot[data-lcd-variant="${variant}"]`],
    probes: lcdSidebarProbes,
  })),
  {
    // The 390x844 viewport makes `App.svelte`'s `isMobile`
    // (min(innerWidth, innerHeight) < 640) select the mobile skin over any
    // layout preference. Probes stay in the default ESSENTIALS chip and
    // the tuning strip; the PTT FAB, the landscape PTT/unkey buttons and
    // the TX chip are never clicked.
    skin: 'mobile',
    boot: { layout: 'standard', viewport: { width: 390, height: 844 } },
    root: '.m-layout',
    probes: [
      {
        run: page => pressModeKey(page,
          page.locator('.m-essentials').getByRole('button', { name: 'LSB', exact: true })),
        frame: { name: 'set_mode', params: { mode: 'LSB', receiver: 0 } },
      },
      {
        run: async page => {
          const fil2 = page.locator('.m-essentials')
            .getByRole('button', { name: 'FIL2', exact: true });
          await expect(fil2).toBeEnabled();
          await fil2.click();
        },
        frame: { name: 'set_filter', params: { filter: 2, receiver: 0 } },
      },
      {
        // 'Tune up one step' is the en-US aria-label of the tuning
        // strip's step-up key (`en-US.json`'s `core.mobile.tune.upStep`).
        // The step rides the paced accumulator (`panel-commands.ts`:
        // `onFreqChange(..., 'step')` -> `tuningAccumulator().step`), so
        // the frame may land after the click — the row's `expect.poll`
        // waits for it. The target is the fixture's MAIN frequency
        // 14 250 000 plus the initial 1000 Hz step
        // (`MobileRadioLayout.svelte`'s `tuningStep` starts at 1000 and
        // keeps it: USB's `SSB_STEPS` in `mobile-layout-logic.ts`
        // includes 1000).
        run: async page => {
          const up = page.getByRole('button', { name: 'Tune up one step' });
          await expect(up).toBeEnabled();
          await up.click();
        },
        frame: { name: 'set_freq', params: { freq: 14_251_000, receiver: 0 } },
      },
    ],
  },
];

test.describe('MOR-3280 per-skin reachability matrix', () => {
  for (const row of ROWS) {
    test(`${row.skin} wires its painted controls to commands`, async ({ page }) => {
      await bootSkinReach(page, row.boot);
      await expect(page.locator(row.root).first(), `${row.skin} mounts its own root`).toBeVisible();
      for (const extra of row.extraRoots ?? []) {
        await expect(page.locator(extra).first(), `${row.skin} mounts its variant root`).toBeVisible();
      }
      const emitted: string[] = [];
      for (const probe of row.probes) {
        await clearSkinReachFrames(page);
        await probe.run(page);
        await expect.poll(async () => (await skinReachCommands(page))[0] ?? null)
          .toEqual(probe.frame);
        emitted.push(...(await skinReachCommands(page)).map(command => command.name));
      }
      // Per-probe reads stop at the poll match; read the recorder once
      // more after the last probe so a frame emitted late in the row —
      // outside any probe's window — still reaches the guard.
      const trailing = (await skinReachCommands(page)).map(command => command.name);
      const transmitted = [...emitted, ...trailing].filter(name => TRANSMIT_COMMANDS.has(name));
      expect(transmitted, 'no transmit command left the page').toEqual([]);
    });
  }

  // The negative row. `DualSdrFaceSkin.svelte` renders `DualSdrFace`
  // through `SemanticRadioSurfaces`' `readonlyDisplay` snippet and passes
  // no `onPreChange` command callback (its own comment declares the
  // entrypoint display-only), and `DualSdrFace.svelte`'s `preEnabled`
  // gates the one data-dependent key on `onPreChange !== undefined` while
  // every other `[data-control]` button is hard-disabled. This row pins
  // exactly that: no enabled control, and no cmd frame without a click.
  test('dual-sdr-face stays display-only: controls disabled, no command', async ({ page }) => {
    await bootSkinReach(page, { layout: 'dual-sdr-face' });
    await expect(page.locator('[data-testid="dual-sdr-face"]'), 'dual-sdr-face mounts').toBeVisible();
    const controls = page.locator('[data-testid="dual-sdr-face"] [data-control]');
    const count = await controls.count();
    expect(count, 'the face paints its control set').toBeGreaterThan(0);
    for (let index = 0; index < count; index += 1) {
      await expect(controls.nth(index)).toBeDisabled();
    }
    // Idle baseline: skin load may emit frames on some skins, so — the
    // same way the row loop clears between probes — the recorder is
    // emptied after settle and must stay empty with no interaction.
    await clearSkinReachFrames(page);
    await page.waitForTimeout(300);
    expect(await skinReachCommands(page), 'display-only skin emits no command').toEqual([]);
  });
});
