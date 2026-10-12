/**
 * MOR-3280 part 2 — the per-skin reachability matrix (PR A of 2).
 *
 * Each row loads the REAL built app against the in-page fake socket
 * (`skin-reach-fixture.ts`), asserts the skin's OWN root selector is
 * visible (a topology fallback mounts a different skin and must fail
 * here), then interacts with one non-TX control per control family the
 * skin legitimately has and asserts the exact outgoing
 * `{type:'cmd', name, params}` frame. A skin that renders but is wired to
 * nothing fails on the frame. This PR covers desktop-v2, sdr-test,
 * flagship-probe and dual-receiver-cockpit; the five LCD skins, mobile and
 * dual-sdr-face belong to PR B.
 *
 * Selectors below were verified against the built app (inventory run on
 * the i18n preview server): desktop-v2 paints `standard-mode-*`,
 * `filter-width` and `dsp-compact-*` and reaches tuning only through the
 * frequency-entry dialog (its band row is `band-choices-compact`, whose
 * HardwareButtons carry no per-band testid); sdr-test and flagship-probe
 * paint `filter-mode-*`, `dsp-nrActive` and `band-choice-*`.
 */
import { test, expect, type Locator, type Page } from '@playwright/test';
import {
  bootSkinReach, clearSkinReachFrames, skinReachCommands, type SkinReachBoot,
} from './skin-reach-fixture';

/**
 * Transmit commands this matrix must never emit — clicked or not, a frame
 * with one of these names fails the row. `ptt_on`/`ptt_off` are the only
 * PTT wire names (`ws-client.ts: pttIntent`); `cw_auto_tune` is the CW
 * keyer's auto-tune, `set_tuner_status` the ATU tune path (values 1 and 2
 * start a tuning cycle) and `set_vox` arms VOX — all dispatched through
 * `panel-commands.ts` (`makeCwLevelHandlers`, `makeTxHandlers`,
 * `makeVoxHandlers`). The probed controls are exclusively receive-side:
 * mode, filter width, DSP, band/tune and VFO select; no PTT key, no
 * TxAux/ATU/VOX control, no CW keyer control and no keyboard input.
 */
const TRANSMIT_COMMANDS = new Set(['ptt_on', 'ptt_off', 'cw_auto_tune', 'set_tuner_status', 'set_vox']);

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
  probes: ReachProbe[];
}

/** Mode key press: USB is active in the fixture, LSB is not. */
async function pressModeKey(page: Page, key: Locator): Promise<void> {
  await expect(key).toBeEnabled();
  await key.click();
}

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
        // desktop-v2 has no per-band buttons (compact band row); its tune
        // family is the frequency-entry dialog the readout opens.
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
    // dual-receiver-cockpit legitimately has no mode/filter/DSP/band
    // surfaces: `presentation/layouts/dual-receiver-cockpit.ts` declares
    // only the primary-vfo, secondary-vfo, global, rx-tx and tx-aux zones,
    // so the SUB VFO select is the whole non-TX matrix for this skin.
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
];

test.describe('MOR-3280 per-skin reachability matrix', () => {
  for (const row of ROWS) {
    test(`${row.skin} wires its painted controls to commands`, async ({ page }) => {
      await bootSkinReach(page, row.boot);
      await expect(page.locator(row.root).first(), `${row.skin} mounts its own root`).toBeVisible();
      const emitted: string[] = [];
      for (const probe of row.probes) {
        await clearSkinReachFrames(page);
        await probe.run(page);
        await expect.poll(async () => (await skinReachCommands(page))[0] ?? null)
          .toEqual(probe.frame);
        emitted.push(...(await skinReachCommands(page)).map(command => command.name));
      }
      const transmitted = emitted.filter(name => TRANSMIT_COMMANDS.has(name));
      expect(transmitted, 'no transmit command left the page').toEqual([]);
    });
  }
});
