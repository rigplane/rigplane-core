# TCI ecosystem notes

Status: reference note (not a design decision)
Date: 2026-10-29
Linear: MOR-3821
Scope: public open-core `rigplane-core`; background for the TCI server program.
Docs only: this file adds no code.

## How to read this note

This is an English summary of a Russian-language research report dated
2026-10-29. Statements about client and server behaviour are second-hand:
they come from vendor pages, documentation, forums and release notes, not
from packet captures or source reads done for this program. The markers
`unknown` and `not confirmed` are kept as in the report, every row keeps its
source link, and no facts were added. Direct wording from sources is limited
to a few words in quotation marks with attribution.

## 1. What TCI is and who owns it

TCI (Transceiver Control Interface) is an open network protocol by Expert
Electronics ("Expert Group" LLC, Taganrog, Russia; address and contact are
stated in the specification itself). It first appeared in ExpertSDR2 1.2.0
(April 2018) as the company's new standard linking SDR receivers/transceivers
with loggers, digital-mode programs and skimmers. The specification lives on
GitHub under an MIT license.

Transport is full-duplex WebSocket over TCP: the server is the transceiver,
clients are all other programs and devices, on one computer or a local
network. Commands are ASCII text lines of the form `name:arg1,arg2;`. Audio
and IQ are binary frames with a 64-byte header. One channel carries control
(similar to CAT), RX/TX audio, IQ, panorama spots and CW macros together.

Command names are case-insensitive, the characters `:` `,` `;` are reserved,
and an unknown or incorrect command is silently ignored. There are four
command kinds: initialisation (the server sends it on connect, ending with
`ready;`), bidirectional control, single-client commands (audio, IQ, spots)
and notifications (sensors, meters). The server broadcasts state changes to
all clients: updates are batched every 30 ms, and a parameter just changed by
one client is frozen for the others for 200 ms (500 ms for CW speed).

Audio and IQ frames carry a 64-byte header plus up to 16384 data bytes.
Stream types are IQ, RX audio, TX audio, `TX_CHRONO` (a marker by which the
client returns a TX audio chunk) and line out. Sample formats are INT16,
INT24, INT32 and FLOAT32 (float32 is the default); audio rates are 8/12/24/48
kHz with 1 or 2 channels (default 2) and 100-2048 samples per packet; the TX
buffer is 50-500 ms (default 50 ms). IQ rates are 48/96/192/384 kHz.

Spots: a client sends `SPOT:<callsign>,<mode>,<frequency>,<colour>,<text>;`,
spots live 10 minutes, `SPOT_DELETE` and `SPOT_CLEAR` remove them, and the
server reports clicks as `CLICKED_ON_SPOT` / `RX_CLICKED_ON_SPOT`. CW support
covers `CW_MACROS` (text with speed control characters), `CW_MSG`
(prefix + callsign + suffix, the callsign editable on the fly),
`CW_MACROS_STOP`, `CW_TERMINAL` and the `CALLSIGN_SEND` / `CW_MACROS_EMPTY`
notifications. Vendors may add their own commands; the `DEVICE` identifier
says which device answers.

## 2. Specification versions

Version history follows the commit history of the specification repository
and the changelog table inside the PDF itself:

- First repository commit: November 2021. Versions 1.8 (May 2022), 1.9
  (July 2022; new audio-stream setup commands and
  `TX_STREAM_AUDIO_BUFFERING`), 1.9.1 (May 2023), 1.10 / "2.0" (December
  2023 / January 2024; a commit states 2.0 matches 1.10).
- Version 2.1: two commits on 21-22 October 2026. The PDF is dated
  "September 2026" and names ExpertSDR3 1.3.1. New in 2.1: `RX_ENABLE`,
  `ECODER_SWITCH_*`, `HW_SENSORS*` and `CW_MON` commands; the rename of
  `CHANNEL_COUNT` to `CHANNELS_COUNT`; the `count` packet-counter field in
  the stream header; new "Reserved Commands" and "Notes on the Command
  Format" sections.
- Sources: [repository](https://github.com/ExpertSDR3/TCI),
  [commits](https://api.github.com/repos/ExpertSDR3/TCI/commits),
  [PDF](https://raw.githubusercontent.com/ExpertSDR3/TCI/main/TCI%20Protocol%202.1.pdf),
  [license](https://github.com/ExpertSDR3/TCI/blob/main/LICENSE).

Settled against the specification texts 1.7-2.1: `audio_start` takes one
argument (a single receiver number, `AUDIO_START:arg1;`). An earlier draft of
this note repeated a third-party repository's claim of a two-argument form;
that claim is refuted against the PDFs and is kept here only as a record of
the correction. The leaf grammar still parses the two-argument shape, as a
tolerance pin only, not as a claim about any client.
Open item: the report notes the default port is not stated in the
specification text it parsed; third-party sources give 40001 for ExpertSDR
and 50001 for Thetis, AetherSDR and wfview.

## 3. Client programs

Columns: control = rig control over TCI; audio = RX/TX audio over TCI;
spots = panorama spots. The primary source is the vendor page
([eesdr list](https://eesdr.com/en/software-en/software-en)), which describes
capabilities tersely, so many cells are `unknown`; `not confirmed` means the
report found no confirmation.

| Program | control | audio over TCI | spots | Platform | Source |
|---|---|---|---|---|---|
| WSJT-X (official, 3.0.0 and newer) | yes | yes | n/a (not a logger); unknown | not rechecked against packets | [Release Notes](https://wsjt.sourceforge.io/Release_Notes.txt) (3.0.0: TCI supported for rig control and audio input/output; TCI fixes in 3.0.2 and 3.2.0-rc1) |
| WSJT-X Improved (DG2YCB) | yes (first test build announced 2025-01-24) | unknown for this branch separately (base WSJT-X supports it) | unknown | Windows, Linux, macOS | [announcement](https://sourceforge.net/p/wsjt/mailman/message/59122155); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| JTDX | yes | yes (changelog 2.2.157-2.2.159: TCI audio, TX buffering, PTT test with TCI audio) | n/a / unknown | Windows, Linux, macOS | [release note](https://machamradio.com/blog/2022/04/07/jtdx-2-2-159-is-now-available/); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| MSHV (LZ2HV) | yes (in eesdr list; details not confirmed) | unknown | unknown | Windows, Linux | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| JS8Call (original) | not confirmed | not confirmed | n/a | — | no confirmation found |
| JS8Call-improved | in development: pull request #314 open, marked WIP, not merged; CAT "pretty solid" | in test: RX works, TX unstable over the tci-bridge | n/a | Windows, Linux, macOS (build) | [PR](https://github.com/JS8Call-improved/JS8Call-improved/pull/314) |
| fldigi | no (natively); only via TCI-Hamlib Adapter | no | n/a | — | [tciadapter](https://github.com/ftl/tciadapter); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| Log4OM 2 | yes (since 2.16.0.0: CAT, CW keyer) | unknown | yes (TCI SPOT integration, release 2.16.0.0) | Windows | [release note](https://www.log4om.com/2021/08/02/release-note-version-2-16-0-0/); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| QLog | yes (TCI in the control-method list) | unknown | unknown | Linux, Windows | [release note](https://cq.sk/en/qlog-v0-51-0/) |
| MacLoggerDX | yes (since v6.23b47) | not native: audio goes via ExpertSDR VAC | unknown | macOS | [help page](https://dogparksoftware.com/MacLoggerDX%20Help/sunsdr_wsjt-x.html); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| OpsLog (F4BPO) | yes (CAT over TCI for SunSDR; built-in SunSDR console) | unknown | unknown | Windows | [wiki](https://github.com/GregTroar/OpsLog/wiki); [eesdr list](https://eesdr.com/en/software-en/software-en) |
| LogHX (RX4HX) | yes ("full TCI support") | unknown | yes (cluster spots on the panorama) | Windows | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| 5MContest (RM5F) | yes | unknown | unknown | Windows | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| SWISSLOG (HB9BJS) | yes ("native ExpertSDR support") | unknown | unknown | Windows | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| RUMlog (DL2RUM) | yes | unknown | unknown | macOS | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| SDC (UT4LW) | yes (exchange with ExpertSDR2 moved to TCI since 9.00) | yes (IQ stream over TCI for the skimmer; a 19.06 report complains the SDC+TCI skimmer "does not work") | yes (spots of its own CW skimmer on the ExpertSDR panorama) | Windows, macOS | [release note](https://pileupdx.com/2018/04/29/expertsdr2-1-2-released-big-update/); [forum](https://groups.google.com/g/sdc-ut4lw/c/6rBiHMxAhRw) |
| TCISkimmer Lite (VK6NX) | yes (full TCI 2.0) | IQ yes | yes (skimmer) | Windows, macOS, Linux | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| CW Skimmer / Skimmer Server (Afreet) | not confirmed (product page does not mention TCI; ExpertSDR users reach it via SDC/RadioSync/virtual cable) | not confirmed | not confirmed | Windows | [product page](https://dxatlas.com/CwSkimmer/) |

| N1MM+ | not confirmed natively. Secondary reports: PTT from E-Coder2 travels over TCI and is used with N1MM; the third-party VSELink forwards N1MM+ spots to ExpertSDR2 over TCI | not confirmed | not N1MM+ itself, but VSELink | Windows | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| DXLog.net | not confirmed (nothing found in search) | not confirmed | not confirmed | Windows | — |
| Logger32 | not confirmed | not confirmed | not confirmed | Windows | — |
| DXKeeper / Commander (DXLab) | not confirmed (vendor site lists other radio brands) | not confirmed | not confirmed | Windows | [vendor site](https://dxlabsuite.com) |
| HamRS | not confirmed | not confirmed | not confirmed | — | — |
| Win4Icom / Win4Yaesu / Win4K3 | not confirmed (nothing found in search) | not confirmed | not confirmed | Windows | — |
| wfview | a TCI server, not a client (see section 4) | — | — | Linux, macOS, Windows | [manual](https://wfview.org/wfview-user-manual/settings/) |
| SparkSDR | not confirmed (neither client nor server confirmed) | not confirmed | not confirmed | Windows, Linux, macOS (common knowledge, not rechecked) | — |
| HamDeck (DL3NEY) | yes (control via Stream Deck) | n/a | n/a | unknown | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| Cluster-TCI-Bridge (OE3IDE) | n/a | n/a | yes (cluster spots on the panorama, including on Thetis) | unknown | [forum](https://community.apache-labs.com/viewtopic.php?p=31538) |
| VSELink (M0VSE) | n/a | n/a | yes (N1MM+ spots to ExpertSDR2) | unknown | [eesdr list](https://eesdr.com/en/software-en/software-en) |
| TCI-Hamlib Adapter (DL3NEY) | yes (for Hamlib clients: WSJT-X, fldigi, CQRLog) | no | no | cross-platform (Go) | [repository](https://github.com/ftl/tciadapter) |
| CloudlogTCI | not confirmed details (a TCI bridge for Cloudlog/Wavelog) | unknown | unknown | unknown | [repository](https://github.com/anthonydiiorio/CloudlogTCI) |
| FreeData | forum headline says "now supports TCI"; page content did not open | unknown | unknown | unknown | [forum thread](https://eesdr.com/en/forum-en/connection-to-third-party-software-esdr3/10362-freedata-multi-platform-application-for-sending-files-messages-now-supports-tci) |
| Rigflow | a TCI client for digital modes (per the pe5jw review) | yes (per review) | unknown | unknown | [review](https://github.com/pe5jw/tci-protocol-overview) |

Report conclusion: firmly confirmed with control and audio are official
WSJT-X and JTDX (and, per documentation, MSHV and WSJT-X Improved). Loggers
are confirmed for control and spots (Log4OM, LogHX) and for control (QLog,
MacLoggerDX, OpsLog, SWISSLOG, 5MContest, RUMlog). The other big names
(N1MM+, DXLog.net, Logger32, DXLab, HamRS, Win4Icom/Yaesu, CW Skimmer,
SparkSDR, fldigi, original JS8Call) show no native TCI confirmation.

## 4. Servers and bridges

| Server | What it is | What is confirmed | Source |
|---|---|---|---|
| ExpertSDR2 / ExpertSDR3 (Expert Electronics) | reference implementation | Hardware: MB1, SunSDR2 DX/PRO/QRP, SunSDR2, SunSDR; receivers ColibriDDC, ColibriNANO. Latest ExpertSDR3: 1.3.2 of 2026-09-08. The RadioSync utility links non-TCI programs (CAT, VAC, IQ) to a TCI server | [product page](https://eesdr.com/en/software-en/expertsdr3-en) |
| Thetis (ramdor; mi0bot branch for Hermes-Lite 2; ANAN) | Windows program for OpenHPSDR | TCI server present; an April 2026 note mentions "TCI audio/IQ streaming"; WS and WSS are planned. Its TCI version is not stated. Setup page: CAT Control / User Interface. Default port 50001 (per an Apache Labs forum thread). Pretends to be `ExpertSDR2`; extra features open through Kenwood-like `ZZ` commands | [repository](https://github.com/ramdor/Thetis); [review](https://github.com/pe5jw/tci-protocol-overview); [forum](https://community.apache-labs.com/viewtopic.php?p=26010) |
| AetherSDR | open FlexRadio client (GPL-3.0; Linux/macOS/Windows) with a built-in TCI v2.0 server: CAT, audio, IQ, CW, spots | the only found way to get TCI from Flex. The SDC author wrote in April 2026 that he sees no signs of IQ-over-TCI work in AetherSDR; audio is received | [repository](https://github.com/ten9876/AetherSDR); [forum](https://groups.google.com/g/sdc-ut4lw/c/6rBiHMxAhRw) |
| wfview (Icom, Kenwood) | open program for Icom/Kenwood: audio plus control for other applications | see bridges below | [manual](https://wfview.org/wfview-user-manual/settings/) |
| Other servers per the pe5jw review | deskHPSDR, Lyra-SDR-cpp, SDROxide (client and server), NereusSDR (TCI 2.0 server since v0.5.0 of 2026-05-13), Kainos (Thetis-based), Bolt SDR, Zeus (archived 2026-07-17; forks AnanCore, station-engine) | the review itself marks many cells untested; treat as incomplete | [review](https://github.com/pe5jw/tci-protocol-overview) |
| Apache Labs ANAN | own TCI in firmware: not confirmed; works through Thetis | — | forum thread above |
| FlexRadio SmartSDR | own TCI server: not confirmed (Flex has its own SmartSDR API); only through AetherSDR | — | [forum](https://community.flexradio.com/discussion/8027404/anybody-using-the-tcip-rotocol-interface-in-log4om-w-their-flex) |

Note: SDC is sometimes called a TCI server for Flex (as in the TLF support
request), but the report found no official confirmation. Documented is an
SDC tab with the SmartSDR API for a direct link without TCI. Status of "SDC
as a TCI server": not confirmed.

Bridges that make an ordinary CAT transceiver look like a TCI server (the
closest analogues of the RigPlane program; all small, none an established
standard):

1. **wfview** (Icom, Kenwood; open). Built-in TCI server: a settings field
   "TCI Server Port" (0 disables it). The manual calls it "advanced and
   rather experimental". Audio and control travel in one connection. A
   developer wrote in June 2026 that TCI in wfview v2 is weak and fully
   rewritten for v3; the roadmap lists "Enhanced TCI support". Forum reports:
   in v2.22/2.23 WSJT-X and JTDX did not connect ("TCI SDR is not switched
   on") while QLog and Cluster-TCI-Bridge worked; in v3.04 on Windows a user
   reported TCI working with a remote IC-7300 (mainly FT modes). Sources:
   [manual](https://wfview.org/wfview-user-manual/settings/),
   [roadmap](https://wfview.org/roadmap/),
   [forum](https://forum.wfview.org/t/tci-issue-with-wsjt-x-and-jtdx/5019).
2. **tci-bridge (rruchte)**. Standalone Qt6/C++ program: a TCI server
   (protocol subset), control through `rigctld` (Hamlib), audio through Qt
   Multimedia. Built for JS8Call with ordinary radios. TX is off by default,
   with a TX watchdog timer; network jitter is smoothed by a buffer. No
   license stated on the page; 7 commits, zero stars. A JS8Call-improved test:
   TX with an IC-7300 on Mac did not work, RX worked. Source:
   [repository](https://github.com/rruchte/tci-bridge) (a vu3fgj fork gave
   404 at check time).
3. **k3-tci-bridge (motoham88)**. An Elecraft K3 as a TCI server on
   Raspberry Pi, port 50001: bidirectional 48 kHz audio uncompressed
   (about 3 Mbps each way), CW, web interface. MIT. Verified with WSJT-X
   3.0.1 (control and audio). No IQ or panorama. Source:
   [repository](https://github.com/motoham88/k3-tci-bridge).
4. **TCI-X and kadrad-hub (g0kad)**. TCI-X is an open TCI extension
   ("superset"): it adds what ordinary CAT transceivers miss (memory,
   attenuator and preamplifier, named filters, VOX, compressor, extra
   meters, a radio capability manifest). A client enables it with
   `tcix:1.0;`; ordinary TCI clients and servers stay compatible. Draft 0.2
   of 2026-10-27 (two days before the report), MIT. The reference server
   kadrad-hub drives Icom, Elecraft, Yaesu, QRP Labs and Flex directly and
   others through `rigctld`; Expert Electronics neither released nor
   endorsed the extension. Source: [draft](https://github.com/g0kad/TCI-X).
   The kadrad-hub repository itself was not visible in the author profile at
   check time.
5. **Reverse direction** (a TCI radio reached from Hamlib) is adjacent, not
   competing: [TCI-Hamlib Adapter](https://github.com/ftl/tciadapter), the
   Hamlib `rigs/tci` driver
   ([sources](https://github.com/Hamlib/Hamlib/tree/master/rigs/tci)) and
   [hamlib-tci-sidecar](https://github.com/jfrancis42/hamlib-tci-sidecar)
   (archived 2026-06-22; needs a Hamlib patch). It follows that Hamlib can
   act as a TCI client, so a RigPlane TCI server can be checked with the
   Hamlib driver.

A compatibility reference for testing against:
[review](https://github.com/pe5jw/tci-protocol-overview) compares real server
behaviours (Thetis, deskHPSDR, Zeus, NereusSDR and others): different
`audio_start` arguments, different ports, mode names (`CWL`/`CWU` vs `CW`,
`NFM` vs `FM`), who receives PTT events, IQ exclusivity. The author marks
many cells untested.

## 5. Adoption (with caveats)

Signs in favour:

- The vendor lists about 20 programs and devices (digital modes, skimmers,
  loggers, amplifiers, ESP32 controllers).
- Official WSJT-X added TCI in 3.0.0 (April 2026) and fixes it in 3.0.2 and
  3.2.0-rc1; the report calls this the strongest signal.
- Server count grows in 2026: NereusSDR, Zeus, deskHPSDR, SDROxide,
  AetherSDR, Thetis (audio/IQ over TCI), wfview v3; private projects such as
  k3-tci-bridge and TCI-X.
- Community: SunSDR owners, Thetis/ANAN/Hermes-Lite users, Flex-on-Linux
  users (through AetherSDR), contesters (Log4OM, LogHX, SWISSLOG), remote
  stations.

Signs for caution:

- An Apache Labs forum voice says TCI stays mainly a niche of SunSDR fans
  (from a search snippet; the page itself would not open; message date
  unknown): [thread](https://community.apache-labs.com/viewtopic.php?p=18953).
- The "TCI client for TLF" request was closed on 2026-10-14 after half a year
  idle (the maintainer needs a C library):
  [issue](https://github.com/Tlf/tlf/issues/508).
- Big programs (N1MM+, DXLab, Logger32, DXLog.net, CW Skimmer) show no
  native support.
- For wfview, TCI stays "experimental", and one developer called TCI a
  "moving target" (forum).

Report assessment (opinion, not a measurement): a real, working, growing but
not universal ecosystem, centred on digital modes, loggers and
SunSDR/Thetis. There are no user counts.

## 6. What RigPlane users gain, risks and limits

Concrete gains:

- One network connection instead of a "rigctld + virtual audio cable + audio
  setup" chain. Official WSJT-X, JTDX, MSHV, Log4OM, QLog, MacLoggerDX and
  others could control the radio and exchange audio without separate audio
  devices. TCI audio is what rigctld cannot carry at all (as the TLF request
  author notes).
- Network operation: client and RigPlane on different computers (remote
  station).
- Several clients at once with server-driven state sync; the specification
  describes this as the normal case.
- Spots: clients (Log4OM, LogHX, Cluster-TCI-Bridge) can send `SPOT`, which a
  RigPlane server receives; click feedback (`CLICKED_ON_SPOT`) can go back
  to the client.
- CW macros from loggers (`CW_MACROS`, `CW_MSG`) for contests.
- Compatibility with rigctld clients stays (the rigctld server already
  exists).

Risks and limits:

1. *Specification churn.* 1.8, 1.9, 2.0, 2.1 in three and a half years;
   2.1 released a week before the report. The document calls itself a "living
   document". The vendor states old clients stay frame-compatible from 1.8
   to 2.1, but the command set keeps growing.
2. *Single-vendor control.* The protocol is MIT, but one company develops it
   for ExpertSDR3; there is no independent committee. Third-party extensions
   (TCI-X) explicitly defer to the base specification. The vendor company is
   in Russia; if compliance matters for RigPlane, check it separately (not
   researched).
3. *Implementation divergence.* The pe5jw review shows: different
   `audio_start` argument counts, mode names, ports (40001 vs 50001), who
   gets PTT events, IQ exclusivity. Verify against real clients (WSJT-X 3.x,
   JTDX, Log4OM, QLog, MacLoggerDX).
4. *Specification details to implement exactly:* the mandatory init sequence
   and `READY;`; the 200 ms freeze and 30 ms batching; empty arguments are
   forbidden; an unparsable write argument degrades the write into a read;
   TX audio is keyed by `TX_CHRONO` (the server sends a marker, the client
   answers with an audio chunk of the stated length, or zeros); recommended
   packet length is not shorter than 10 ms.
5. *Audio format.* 64-byte header, little-endian per a third-party Go
   library (not found in the 2.1 specification itself), float32 default, 1
   or 2 channels, 8/12/24/48 kHz, TX buffer 50-500 ms. USB codecs of
   Icom/Yaesu rigs differ in rate and format, so resampling plus gap-free
   packet buffering is needed (network jitter and audio stutter are a common
   complaint: JTDX on Linux, tci-bridge on Mac).
6. *IQ stream.* Ordinary CAT transceivers have no IQ (except IQ-output
   models). A server may omit it, but what clients do on refusal is not
   confirmed.
7. *"Audio to one client".* The hamlib-tci-sidecar author states ExpertSDR3
   streams audio/IQ to one client only (the PTT holder); the report did not
   find this limit in the 2.1 specification. It is a third-party statement.
   The RigPlane server needs an upfront policy (for example, audio to all
   clients, TX to one).
8. *Security.* The read specification sections show no authentication; it is
   a plain WebSocket. Conclusion from the specification text, not a quote:
   listen on 127.0.0.1 by default with an explicitly configurable port, or
   warn when binding to the network.
9. *Licence is clean:* MIT, implementable from scratch with no agreements.
10. *Precedents show audio, not the protocol, is the hard part:* wfview v2
    TCI was weak and rewritten; tci-bridge TX is unstable.

Still not confirmed: the exact default port in the specification; the byte
order in the specification; native TCI support in N1MM+, DXLog.net,
Logger32, DXLab, HamRS, Win4Icom/Yaesu, CW Skimmer, SparkSDR, fldigi and
JS8Call; the contents of kadrad-hub; the MacLoggerDX article contents on
audio and spots; the dates of the Apache Labs forum messages.

## 7. Delta for the design

1. Official WSJT-X 3.0.0 and newer is a TCI client (control plus audio):
   add it to the client list in design section 5.2 and, later, to the
   recorded-transcript conformance set.
2. The design already follows the 2.1 PDF (header `count`, `channels_count`
   naming). The 2.1-only command names (`RX_ENABLE`, `ECODER_SWITCH_*`,
   `HW_SENSORS*`, `CW_MON`) and the renamed `CHANNELS_COUNT` must parse as
   ordinary names in the leaf grammar, with no special casing (the adapter
   ignores what it does not implement).
3. Client quirks to pin by tests: the default port and the mode names. The
   `audio_start` arity is settled (one argument, section 2); the leaf keeps
   the two-argument shape only as a tolerance pin.
