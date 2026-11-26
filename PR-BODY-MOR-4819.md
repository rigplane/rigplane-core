## MOR-4819 — Keep the web server available when a radio refuses an optional read

Linear: MOR-4819  
Boundary: open-core

The FTX-1 can return `?;` for `MG;` in CW and RTTY while frequency and mode continue to answer. The previous startup gate turned that refusal into a fatal error. The acquisition scheduler now treats the control as unavailable, backs off on its normal polling lane, and restores the control on a valid reply. The regression models CW-U and RTTY-U at startup, then changes the radio to USB and back to CW-U on the same connection.

Existing mechanisms reused are `AcquisitionScheduler.record_read_refusal`, `declared_read_deferred`, `end_refusals`, the public `fieldStatus` projection, and the radio indicator tooltip. The tooltip lists each active failure once per path. The shared scheduler logs one declared failure per path per scheduler instance. The runtime diagnostics retain the active path, command and raw response, and the existing support log contributor includes the warning even after the control recovers. A parser failure that carries no raw wire evidence can only report its available details. Core has no handled Sentry reporter under `docs/architecture/open-core-policy.md` §2. The remaining Sentry integration belongs in the Pro consumer of Core diagnostics.

**New presentation condition**

- Failure it handles. A radio can refuse a read after it previously supplied a value, leaving that older value in the snapshot.
- Why the existing check misses it. The profile availability map describes when a path exists. The refusal ledger describes whether the radio is answering it now.
- Evidence. `test_ftx1_refused_mic_gain_does_not_abort_startup` expects the live field to become unavailable after USB has supplied a valid mic-gain value, while the listener stays up.

### Declared read audit

Source search covered all of `src` for `_safe_read`, `_record_declared_defect`, `_record_declared_failure`, `record_declared_read_failure` and `record_startup_defect`. The shared `startup_critical_path` predicate selects only `freq_mode.freq_hz` and `freq_mode.mode` at every declared receiver and VFO scope. They establish operating frequency and mode. Its TX-state set is empty. The existing PTT/split exemptions still serve unknown under MOR-4456. The optional TX-target read still serves unknown under MOR-4433.

**No non-critical refusal or malformed reply at the audited defect-recording sites can directly abort startup.** Link failures and the startup gate for critical reads can still fail startup. No healthy-radio refusal of a *critical* path was established in the supplied evidence. The confirmed FTX-1 refusals of `MG;` in CW/RTTY and `RA0;` with SUB active (MOR-2581) concern optional paths.

#### Yaesu CAT (`backends/yaesu_cat/observations.py`)

Commands were traced through `yaesu_cat/radio.py` and the CAT command templates in `rigs/ftx1.toml` and `rigs/ft991a.toml`. Rows name adapter reads, so a row applies on models whose capability and availability checks enable the path. `Yes` under coverage means scheduler unavailability, backoff and recovery for a refusal or malformed answer. `Critical abort retained` and `MOR-4456` denote the existing gate behavior.

| Read label | Wire command | Field path(s) | Startup-critical | Coverage |
| --- | --- | --- | --- | --- |
| `main.freq` | `FA;` | `receiver.main.active.freq_mode.freq_hz` | Yes | Critical abort retained |
| `main.mode` | `MD0;` | `receiver.main.active.freq_mode.mode` | Yes | Critical abort retained |
| `sub.freq` | `FB;` | `receiver.sub.active.freq_mode.freq_hz` | Yes | Critical abort retained |
| `sub.mode` | `MD1;` | `receiver.sub.active.freq_mode.mode` | Yes | Critical abort retained |
| `main.filter_width` | `SH0;` (width + default flag) | `receiver.main.active.freq_mode.filter_width`, `receiver.main.active.freq_mode.filter_width_radio_default` | No | Yes |
| `main.filter_width` | `SH0;` | `receiver.main.active.freq_mode.filter_width` | No | Yes |
| `sub.filter_width` | `SH1;` (width + default flag) | `receiver.sub.active.freq_mode.filter_width`, `receiver.sub.active.freq_mode.filter_width_radio_default` | No | Yes |
| `sub.filter_width` | `SH1;` | `receiver.sub.active.freq_mode.filter_width` | No | Yes |
| `main.s_meter` | `SM0;` | `receiver.main.meters.s_meter` | No | Yes |
| `sub.s_meter` | `SM1;` | `receiver.sub.meters.s_meter` | No | Yes |
| `alc` | `RM4;` | `global.meters.alc` | No | Yes |
| `power` | `RM5;` | `global.meters.power` | No | Yes |
| `swr` | `RM6;` | `global.meters.swr` | No | Yes |
| `comp` | `RM3;` | `global.meters.comp` | No | Yes |
| `main.rx_func` | `FR;` | `global.tx_state.dual_watch` | No | Yes |
| `main.af_level` | `AG0;` | `receiver.main.operator_controls.af_level` | No | Yes |
| `main.rf_gain` | `RG0;` | `receiver.main.operator_controls.rf_gain` | No | Yes |
| `main.squelch` | `SQ0;` | `receiver.main.operator_controls.squelch` | No | Yes |
| `sub.af_level` | `AG1;` | `receiver.sub.operator_controls.af_level` | No | Yes |
| `sub.rf_gain` | `RG1;` | `receiver.sub.operator_controls.rf_gain` | No | Yes |
| `sub.squelch` | `SQ1;` | `receiver.sub.operator_controls.squelch` | No | Yes |
| `main.att` | `RA0;` | `receiver.main.operator_controls.att` | No | Yes |
| `main.preamp` | `PA0;` | `receiver.main.operator_controls.preamp` | No | Yes |
| `main.agc` | `GT0;` | `receiver.main.operator_controls.agc` | No | Yes |
| `sub.agc` | `GT1;` | `receiver.sub.operator_controls.agc` | No | Yes |
| `main.if_shift` | `IS0;` | `receiver.main.operator_controls.if_shift` | No | Yes |
| `sub.if_shift` | `IS1;` | `receiver.sub.operator_controls.if_shift` | No | Yes |
| `main.narrow` | `NA0;` | `receiver.main.operator_toggles.narrow` | No | Yes |
| `sub.narrow` | `NA1;` | `receiver.sub.operator_toggles.narrow` | No | Yes |
| `main.nb_level` | `NL0;` | `receiver.main.operator_controls.nb_level`, `receiver.main.operator_toggles.nb` | No | Yes |
| `sub.nb_level` | `NL1;` | `receiver.sub.operator_controls.nb_level`, `receiver.sub.operator_toggles.nb` | No | Yes |
| `main.nr_level` | `RL0;` | `receiver.main.operator_controls.nr_level`, `receiver.main.operator_toggles.nr` | No | Yes |
| `sub.nr_level` | `RL1;` | `receiver.sub.operator_controls.nr_level`, `receiver.sub.operator_toggles.nr` | No | Yes |
| `main.auto_notch` | `BC0;` | `receiver.main.operator_toggles.auto_notch` | No | Yes |
| `sub.auto_notch` | `BC1;` | `receiver.sub.operator_toggles.auto_notch` | No | Yes |
| `main.manual_notch` | `BP00;` | `receiver.main.operator_toggles.manual_notch` | No | Yes |
| `sub.manual_notch` | `BP10;` | `receiver.sub.operator_toggles.manual_notch` | No | Yes |
| `main.manual_notch_freq` | `BP01;` | `receiver.main.operator_controls.manual_notch_freq` | No | Yes |
| `sub.manual_notch_freq` | `BP11;` | `receiver.sub.operator_controls.manual_notch_freq` | No | Yes |
| `main.sql_type` | `CT0;` | `receiver.main.operator_toggles.repeater_tone`, `receiver.main.operator_toggles.repeater_tsql`, `receiver.main.operator_controls.tone_squelch_type` | No | Yes |
| `sub.sql_type` | `CT1;` | `receiver.sub.operator_toggles.repeater_tone`, `receiver.sub.operator_toggles.repeater_tsql`, `receiver.sub.operator_controls.tone_squelch_type` | No | Yes |
| `main.ctcss_tone_index` | `CN00;` | `receiver.main.operator_controls.tone_freq`, `receiver.main.operator_controls.tsql_freq` | No | Yes |
| `sub.ctcss_tone_index` | `CN10;` | `receiver.sub.operator_controls.tone_freq`, `receiver.sub.operator_controls.tsql_freq` | No | Yes |
| `main.repeater_shift` | `OS0;` | `receiver.main.operator_controls.repeater_shift` | No | Yes |
| `sub.repeater_shift` | `OS1;` | `receiver.sub.operator_controls.repeater_shift` | No | Yes |
| `active` | `VS;` | `global.slow_state.active` | No | Yes |
| `cw_spot` | `CS;` | `global.slow_state.cw_spot` | No | Yes |
| `vd` | `RM8;` | `global.meters.vd` | No | Yes |
| `id` | `RM7;` | `global.meters.id` | No | Yes |
| `power_level` | `PC;` | `global.operator_controls.power_level` | No | Yes |
| `mic_gain` | `MG;` | `global.operator_controls.mic_gain` | No | Yes |
| `compressor_on` | `PR0;` | `global.tx_state.compressor_on` | No | Yes |
| `compressor_level` | `PL;` | `global.operator_controls.compressor_level` | No | Yes |
| `vox` | `VX;` | `global.tx_state.vox_on` | No | Yes |
| `split` | `ST;` FTX-1 / `FT;` FT-991A | `global.tx_state.split` | No | MOR-4456 |
| `clarifier` | `CF000;` | `global.tx_state.rit_on`, `global.tx_state.rit_tx` | No | Yes |
| `clarifier_freq` | `CF001;` | `global.operator_controls.rit_freq` | No | Yes |
| `tuner` | `AC;` | `global.operator_controls.tuner_status` | No | Yes |
| `dial_lock` | `LK;` | `global.tx_state.dial_lock` | No | Yes |
| `key_speed` | `KS;` | `global.operator_controls.key_speed` | No | Yes |
| `cw_pitch` | `KP;` | `global.operator_controls.cw_pitch` | No | Yes |
| `break_in` | `BI;` | `global.operator_controls.break_in` | No | Yes |
| `break_in_delay` | `SD;` | `global.operator_controls.break_in_delay` | No | Yes |
| `main.narrow` | `NA0;` (clause priming) | `receiver.main.operator_toggles.narrow` | No | Yes |
| `sub.narrow` | `NA1;` (clause priming) | `receiver.sub.operator_toggles.narrow` | No | Yes |
| `ptt` | `TX;` | `global.tx_state.ptt` | No | MOR-4456. Separate observation read, with no defect record |
| `tx_target.freq` and its bracket reads | `FT;` → `FA;` or `FB;` → `FT;` | `global.tx_state.tx_target` | No | MOR-4433. No `paths` in this call and no defect record |

#### External rigctld client (`backends/rigctld_client/observations.py`)

| Read label | Wire command | Field path | Startup-critical | Coverage |
| --- | --- | --- | --- | --- |
| `freq` | `f` | `receiver.main.active.freq_mode.freq_hz` | Yes | Critical abort retained |
| `mode` | `m` | `receiver.main.active.freq_mode.mode` | Yes | Critical abort retained |
| `mode` (passband in same reply) | `m` | `receiver.main.active.freq_mode.filter_width` | No | Shared critical decode. See below |
| `ptt` | `t` | `global.tx_state.ptt` | No | MOR-4456. The critical helper declines to record it |
| `rf_gain` | `l RF` | `receiver.main.operator_controls.rf_gain` | No | Yes |
| `af_level` | `l AF` | `receiver.main.operator_controls.af_level` | No | Yes |
| `preamp` | `l PREAMP` | `receiver.main.operator_controls.preamp` | No | Yes |
| `att` | `l ATT` | `receiver.main.operator_controls.att` | No | Yes |
| `nb` | `u NB` | `receiver.main.operator_toggles.nb` | No | Yes |
| `nr` | `u NR` | `receiver.main.operator_toggles.nr` | No | Yes |
| `active_slot` | `v` | `receiver.main.vfo.active_slot` | No | Yes, when VFO reporting is supported |

One non-critical **value** can still indirectly abort startup. External rigctld returns mode and passband in one `m` reply. An invalid passband makes `_decode_mode` reject the whole reply, which `_read_critical` records against the critical mode path. There is no independent filter-width read at this site. The existing compound-mode reply contract remains in force.

#### CI-V (Icom serial and LAN, plus CI-V compatible radios)

`web_startup._record_critical_startup_defect` is the only direct caller outside the scheduler. It is invoked only for a still-blocking critical path after its retry threshold. `_startup_path_command` uses `acquisition_query_resolver_for_profile`. The table enumerates the declared tuning paths found in the CI-V profiles, as applicable to the receiver and VFO layout. The frames below give command and payload bytes in hex, without the CI-V envelope.

| Read label | Wire command | Field path | Startup-critical | Coverage |
| --- | --- | --- | --- | --- |
| `startup gate` (`get_selected_freq`) | `25 00` | `receiver.main.active.freq_mode.freq_hz` | Yes | Existing CI-V gate |
| `startup gate` (`get_selected_mode`) | `26 00` | `receiver.main.active.freq_mode.mode` | Yes | Existing CI-V gate |
| `startup gate` (`get_unselected_freq`) | `25 01` | `receiver.main.unselected.freq_mode.freq_hz` | Yes | Existing CI-V gate |
| `startup gate` (`get_unselected_mode`) | `26 01` | `receiver.main.unselected.freq_mode.mode` | Yes | Existing CI-V gate |
| `startup gate` (SUB via `get_unselected_freq`) | `25 01` | `receiver.sub.active.freq_mode.freq_hz` | Yes | Existing CI-V gate |
| `startup gate` (SUB via `get_unselected_mode`) | `26 01` | `receiver.sub.active.freq_mode.mode` | Yes | Existing CI-V gate |
| `civ_rx.scheduler` (any resolvable optional read) | Profile getter and receiver route from `runtime/_state_queries.py` | All matching declared non-critical paths, e.g. `global.operator_controls.tuner_status` or `receiver.main.operator_controls.filter_shape` | No | Existing MOR-4058 CI-V `FA` refusal ledger and recovery. This route never records a startup defect for these fields |

The CI-V startup resolver does not issue independent reads for explicit slot A/B paths on this route. Receiver IDs `0` and `1` alias `main` and `sub` in the CI-V state projection. A profile without a resolvable getter supplies an empty command to the generic gate. The TX-500 Kenwood CAT profile, audio/SDR and discovery providers have no declared-read defect caller in the source search.

### Verification

Test coverage spans the FTX-1 CAT simulator, an external rigctld TCP peer and IC-7610 CI-V refusal coverage. The refused Yaesu operating-mode case pins the intact startup error text. The rigctld RF gain regression now waits for the next observed value following the scheduler backoff. A source search of the three changed test files and their round-one hunks found no new direct wall-clock reads or timing sleeps. The Mac mini run, browser smoke test and physical-radio behavior remain unverified in this edit-only round.
