# MOR-2551 temporary UI telemetry

Build the instrumented frontend or packaged preview with `VITE_UI_DIAGNOSTICS=1`
in the build process environment. A normal build, with the variable unset, has
the collector off. This is a build flag: adding the variable when launching an
already packaged app cannot enable it. The diagnostic package should be kept
separate from the ordinary release artifact.

Open the app and expand **UI diagnostics** at the lower right. The panel updates
every five seconds; **Copy summary** copies the current values. If clipboard
access is unavailable in WKWebView, select the text shown in the panel. No
Web Inspector, settings change, backend endpoint, or network upload is needed.

All counters are cumulative since this page context loaded. Rates are the
cumulative count divided by elapsed seconds. Control text bytes are UTF-8 wire
bytes; audio and scope bytes are binary `ArrayBuffer.byteLength`. Scope arrival
counts are callbacks from the scope channel, before parse and acceptance checks.
Scope accepted counts are frames stored in `ScopeController`; they do not imply
the frame was chosen for presentation. `radioCommit` counts accepted whole-state
store assignments. `scopePublish` counts host notification cycles, including
expiry or authority changes. Draw timings cover synchronous renderer calls,
including waterfall row push, and exclude browser compositing. Only aggregate
numbers are retained; no frame content, state, URL, token, or event log is kept.

Remove the temporary probes and this file after the comparison is complete.
