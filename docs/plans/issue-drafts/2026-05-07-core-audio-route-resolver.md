# Core Issue Draft: Design and implement AudioRoute resolver for WSJT-X DATA policy

## Context

During IC-7610 LAN TX bridge debugging we found that `--bridge` is not a reliable signal for choosing radio DATA policy. A bridge can be used both for direct Icom LAN audio workflows and for USB/serial audio workflows. Those require different packet-mode behavior:

- Direct Icom LAN TX audio: WSJT-X `PKTUSB`/`PKTLSB`/`PKTRTTY` should map to DATA2/LAN on multi-DATA radios such as IC-7610.
- USB/serial audio route, even with a local bridge: packet mode should remain legacy DATA1/USB behavior.

Canonical design doc: `docs/plans/2026-05-07-audio-route-resolver-design.md`.

## Requirements

- Add a core `AudioRoute` model/resolver that derives radio audio route from backend/profile/capabilities, not from `--bridge`.
- Route policies must distinguish at least:
  - direct Icom LAN audio -> `data2_lan` when the profile supports multiple DATA modes;
  - direct Icom LAN single-DATA profile -> legacy fallback;
  - Icom serial/USB audio bridge -> DATA1/USB or legacy DATA1, never DATA2/LAN;
  - unavailable/unknown route -> no automatic DATA source changes.
- Replace direct CLI/backend guard logic with route-derived `RigctldConfig` wiring.
- Keep DATA1 MOD user-owned by default: no automatic DATA1 MOD writes from prewarm, profile apply, or state restore.
- Keep TX codec behavior aligned with negotiated TX codec: direct Icom LAN PCM TX must send raw PCM, not Opus bytes.

## Acceptance Criteria

- Unit tests cover route resolution for direct LAN, serial/USB bridge, single-DATA fallback, and unavailable route.
- Rigctld tests prove packet-mode mapping follows route-derived policy.
- CLI/web startup tests prove route policy is not inferred from `--bridge` alone.
- Docs explain `bridge` vs `audio route` and DATA1/DATA2 ownership.
- Existing WSJT-X compatibility behavior is preserved for non-LAN routes.

## Notes

This is open-core scope: protocol correctness, backend capability modeling, route-derived rigctld policy, and public docs. Product auto-start behavior belongs in rigplane-pro.
