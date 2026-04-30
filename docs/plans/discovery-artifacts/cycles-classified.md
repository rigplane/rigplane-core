# Cycle classification (5 SCCs)

## SCC: icom_lan._audio_runtime_mixin ↔ icom_lan._dual_rx_runtime ↔ icom_lan._scope_runtime ↔ icom_lan.radio ↔ icom_lan.radio_initial_state ↔ icom_lan.radio_reconnect ↔ icom_lan.radio_state_snapshot (7 nodes, 12 intra-SCC edges)

- icom_lan._audio_runtime_mixin → icom_lan.radio: L16:type_checking
- icom_lan._dual_rx_runtime → icom_lan.radio: L15:type_checking
- icom_lan._scope_runtime → icom_lan.radio: L16:type_checking
- icom_lan.radio → icom_lan._audio_runtime_mixin: L31:module
- icom_lan.radio → icom_lan._dual_rx_runtime: L35:module
- icom_lan.radio → icom_lan._scope_runtime: L36:module
- icom_lan.radio → icom_lan.radio_initial_state: L27:module
- icom_lan.radio → icom_lan.radio_reconnect: L28:module
- icom_lan.radio → icom_lan.radio_state_snapshot: L29:module
- icom_lan.radio_initial_state → icom_lan.radio: L25:type_checking
- icom_lan.radio_reconnect → icom_lan.radio: L28:type_checking
- icom_lan.radio_state_snapshot → icom_lan.radio: L26:type_checking

**Verdict:** DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)

## SCC: icom_lan._civ_rx ↔ icom_lan._runtime_protocols (2 nodes, 2 intra-SCC edges)

- icom_lan._civ_rx → icom_lan._runtime_protocols: L38:type_checking
- icom_lan._runtime_protocols → icom_lan._civ_rx: L21:type_checking

**Verdict:** DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)

## SCC: icom_lan.profiles ↔ icom_lan.rig_loader (2 nodes, 2 intra-SCC edges)

- icom_lan.profiles → icom_lan.rig_loader: L266:function_local
- icom_lan.rig_loader → icom_lan.profiles: L17:module

**Verdict:** DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)

## SCC: icom_lan.rigctld.handler ↔ icom_lan.rigctld.routing (2 nodes, 2 intra-SCC edges)

- icom_lan.rigctld.handler → icom_lan.rigctld.routing: L40:module
- icom_lan.rigctld.routing → icom_lan.rigctld.handler: L26:type_checking

**Verdict:** DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)

## SCC: icom_lan.web.server ↔ icom_lan.web.web_routing ↔ icom_lan.web.web_startup (3 nodes, 4 intra-SCC edges)

- icom_lan.web.server → icom_lan.web.web_routing: L1172:function_local
- icom_lan.web.server → icom_lan.web.web_startup: L920:function_local, L989:function_local
- icom_lan.web.web_routing → icom_lan.web.server: L26:type_checking, L50:function_local
- icom_lan.web.web_startup → icom_lan.web.server: L25:type_checking

**Verdict:** DEFERRED-ONLY (no runtime cycle survives when only top-level imports are kept)

