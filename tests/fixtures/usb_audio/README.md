# USB audio capture fixtures

These fixtures are redacted outputs of `rigplane audio usb-capture`. The
capture tool records USB topology and serial-port inventory; it does not open
radio devices.

| Fixture | Host and connected radios | Capture provenance |
|---|---|---|
| `windows/w1-gtr7-ic7300.json` | Windows 11 host; IC-7300 (`ic7300`) | Recorded with `rigplane 3.0.0b20 audio usb-capture`. Its single connected session matches the `--no-unplug` capture shape; the original command flags and host hardware model were not recorded. |
| `macos/m1-mini-ic705-ftx1.json` | Apple silicon Mac mini; IC-705 (`ic705`) and FTX-1 (`ftx1`) | Recorded with `rigplane 3.0.0b20 audio usb-capture`, using `--no-unplug` and one `--radio PROFILE=PORT` argument per radio. |

The capture writer redacts serial values before writing the bundle. Serial
fields and serial-derived port names use redaction tokens; location-derived
port names remain available for topology matching.

There is no real Linux recording in this directory. Linux pairing is
**not hardware-verified**. `tests/test_usb_pairing_linux.py` builds synthetic sysfs
device/interface trees, tty and sound-card links, and ALSA/PortAudio records.
`tests/test_web_usb_pairing_integration.py` reuses those inputs through the real
Linux reader and browser audio-start dispatcher, including frozen-app cases.
