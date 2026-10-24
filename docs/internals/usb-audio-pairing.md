# Shared USB audio pairing

The pure Windows rule lives in `src/rigplane/audio/_usb_pairing.py: pair`.
`src/rigplane/audio/_usb_adapt_windows.py: adapt_windows` converts one capture
session into a normalized snapshot without querying devices. Both
`src/rigplane/audio/usb_driver.py: UsbAudioDriver._select_windows_devices` and
`src/rigplane/audio/_usb_resolve.py: resolve_audio_for_serial_port` use this rule.
Driver refusals raise `src/rigplane/audio/_usb_pairing.py: WindowsAudioTopologyError`,
which retains the reason, evidence, and candidates. The driver keeps its
unambiguous-name fallback when raw topology is unavailable.

The stages run in order, stopping at the first refusal:

- **S0 — availability.** An unavailable device tree, serial inventory,
  PortAudio enumeration, or endpoint ownership refuses with `unavailable`.
- **S1 — serial port.** A case-insensitive port match must identify exactly
  one serial reference with a USB device node; otherwise `serial_not_found`.
- **S2/S3 — containment and uniqueness.** The serial device's direct parent H
  must be an ordinary hub, never a root hub or controller. Inspect H's direct
  children and the children of any direct child hub, with no deeper search.
  There must be exactly one serial-owning USB device (the requested device)
  and exactly one USB device owning both capture and render endpoints. A
  capture-only camera does not qualify. Missing parent links, cycles, and
  duplicate node IDs refuse with `topology_incomplete`; absent or unsuitable H
  gives `containment_unproven`. Other refusals are `serial_ambiguous`,
  `no_codec`, or `codec_ambiguous`.
- **S4 — endpoint ownership.** Match PortAudio names to OS endpoint names in
  the corresponding direction. Normalize whitespace around parentheses; retain
  localization, user renames, and numeric prefixes. MME also accepts a listed
  name of exactly 31 characters when its normalized key prefixes the endpoint
  key. Multiple owners of a matching name contest that record, including
  non-USB and virtual devices.
- **S5 — host API.** In PortAudio's host API order, select the first API with
  exactly one owned capture record and one owned render record, and no
  contested record matching the selected codec. A duplex record can serve
  both directions. If none qualifies, return `contested` when any API was
  rejected for a collision, otherwise `endpoint_names`.

Profiles may carry an optional `[usb] audio_codec` VID:PID list, stored as
`src/rigplane/core/usb_identity.py: UsbAudioIdentity`. Only our own recordings
justify entries. A non-empty list checks the chosen codec after containment;
a mismatch refuses with `codec_identity_mismatch`. Absence or an empty list
uses the same rule without this additional check. Radio facts belong in profile
data; the mechanism has no model-specific branches or product-name heuristics.

**Accepted residual risk:** without a codec list, a serial device directly on
a shared external hub can pair with a foreign headset when the radio codec is
absent, that headset is the only capture-and-render device within the boundary,
and no second serial device is present. A recorded codec list rejects a headset
whose VID:PID differs. The graph test
`tests/test_usb_pairing_rules.py: test_s2_residual_risk_shared_hub_foreign_headset_without_identity_pairs`
pins this accepted behavior; the rule does not prove an internal radio boundary.

Every refusal includes `src/rigplane/audio/_usb_pairing.py: AudioCandidate`
entries for the first host API with endpoint-matched records. Each record and
direction has an index, display name, host API, stable ID, and `near_radio`
flag. A unique endpoint gives `<host API>|<endpoint OS ID>`; otherwise the ID is
`<host API>|name:<listed name>`. Nearby USB owners sort first, then PortAudio
index. Unmatched mapper entries are omitted. Candidates support explicit device
selection; they do not change the refusal into a successful pairing.

Ambiguity refusals map to `usb_ambiguous`, name failures to `usb_topology_names`,
and the remaining reasons to `usb_topology_unavailable`. Capture replay tests
are in `tests/test_usb_adapt_windows.py` and `tests/test_usb_pairing_windows.py`;
the graph clause pins and stage anchors are in `tests/test_usb_pairing_rules.py`.
