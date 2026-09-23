# MOR-2551 temporary UI telemetry

Build the instrumented frontend or packaged preview with `VITE_UI_DIAGNOSTICS=1`
in the build process environment. A normal build, with the variable unset, has
the collector off. This is a build flag: adding the variable when launching an
already packaged app cannot enable it. The diagnostic package should be kept
separate from the ordinary release artifact.

Open the app and expand **UI diagnostics** at the lower right. The panel updates
every five seconds; **Copy summary** copies the values currently shown. If clipboard
access is unavailable in WKWebView, the panel freezes automatically so the
shown text can be selected. **Pause updates** also freezes it for manual
selection; **Resume updates** refreshes it. **Reset window** clears all counters
and starts a new elapsed-time window without reloading or touching the radio. No
Web Inspector, settings change, backend endpoint, or network upload is needed.

All counters are cumulative since page startup or the last reset. Rates are the
count divided by elapsed seconds in that window. `controlText` counts only the
singleton control channel, not the named scope channels. Its volume is JavaScript
string length in UTF-16 code units, a low-cost size proxy rather than wire bytes;
audio and scope volumes are binary `ArrayBuffer.byteLength`. Scope arrival
counts are callbacks from the scope channel, before parse and acceptance checks.
Scope accepted counts are frames stored in `ScopeController`; they do not imply
the frame was chosen for presentation. `radioCommit` counts accepted whole-state
store assignments. `scopePublish` counts host notification cycles, including
expiry or authority changes. Draw timings cover synchronous renderer calls
and exclude browser compositing. `waterfallFrameWork` covers the frame callback's
option update and row push; separate effect-driven option updates and resize
work are outside that metric. Only aggregate
numbers are retained; no frame content, state, URL, token, or event log is kept.

Remove the temporary probes and this file after the comparison is complete.
