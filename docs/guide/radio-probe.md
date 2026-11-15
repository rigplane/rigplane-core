---
description: Record how your radio answers into one file you can send for support — read-only, nothing uploaded.
---

# Radio Probe

## Send us what your radio answers

If we ask you to "run the radio probe", this is all it is: a small program asks your radio the same questions RigPlane would ask it, and writes the answers into one file. You then send us that file.

**It only listens. It does not change anything.** It never changes a setting on your radio, never transmits, never starts the antenna tuner and never writes to the radio's memory. It sends nothing over the internet: the file stays on your computer until you decide to email it. Before it saves, it shows you what is in the file and asks you.

It takes about a minute.

### Windows (RigPlane Pro installed with the normal installer)

1. **Close RigPlane Pro.** Open its menu and choose **Exit**. Then look at the bottom right of the screen, near the clock (click the small arrow if some icons are hidden): if the RigPlane Pro icon is still there, open its menu and quit it too. The radio's cable can only be used by one program at a time.
2. Turn your radio on and connect it to the computer with the USB cable you normally use. Do not press buttons or turn knobs on the radio while the program runs.
3. Press the **Windows key**, type `PowerShell`, press **Enter**. A window with a blinking cursor opens.
4. Copy this one line, paste it into that window (a right-click pastes), and press **Enter**:

```
& "C:\Program Files\RigPlane Pro\rigplane-pro\rigplane-pro.exe" radio-probe
```

   This is where the Windows installer puts RigPlane Pro by default. **If you chose a different folder when you installed it:** right-click the RigPlane Pro icon in the Start menu, choose **Open file location**, and use that folder instead of `C:\Program Files\RigPlane Pro` at the start of the line (keep the `\rigplane-pro\rigplane-pro.exe` part). This line needs a RigPlane Pro version that includes the `radio-probe` command.

5. The program looks for your radio and tells you what it found, for example:

```
Found: FT-991A on COM5
Is this your radio? [Y/n]
```

   Press **Enter** for yes. (If it finds several radios, it numbers them: type the number of yours. If it finds none, it tells you what to check: the radio is on, the cable is plugged in, the USB driver is installed, RigPlane Pro is closed.)

6. It asks your radio about 40 to 150 questions, one at a time, and shows a counter. Wait until it stops.
7. Before saving, it shows what will be in the file:
   - the questions and the exact replies of your radio,
   - what the radio says it is (its model code) and the RigPlane version,
   - your radio's current settings (frequency, mode, power and similar): these show what you use the radio for.

   The program adds no name, computer name, internet address or file from your computer. Text stored in the radio's memory is included only if we asked you to add an extra option to the line; with the line above it is not.

   Press **Enter** to save, or `n` to throw everything away. Nothing is written unless you say yes.
8. The file is saved in your **Documents** folder, named like `FT-991A-radio-probe-2026-11-14.json`; the program prints the full location and opens the folder. Attach the file to an email to the person who asked you. If you want to look inside first, open it with Notepad: it is plain text.

### If you installed RigPlane (Core) yourself, on Windows, macOS or Linux

Close any program that uses the radio, turn the radio on, open a terminal (Windows: Windows key, `PowerShell`; macOS: Command + Space, `Terminal`; Linux: Ctrl + Alt + T) and type:

```
rigplane probe
```

Then follow steps 5 to 8 above.

### If something goes wrong

- **"No radio found":** check the radio is on, the USB cable is firmly connected and that RigPlane Pro is closed. If it still finds nothing, tell us your radio model and send us what the window says.
- **The counter stops for a long time:** press **Ctrl + C**. Nothing is saved and nothing on the radio was changed. Tell us what the window said.
- **You are worried about something in the file:** do not send it. Tell us and we will look at it together.

## Reference

### Running it

With no arguments the probe scans USB serial ports for your radio, asks
which one is yours, asks its questions, and saves the file to your
Documents folder:

```bash
rigplane probe
```

Name the model to probe only that radio, or name both the model and the
port to skip the scan:

```bash
rigplane probe FT-991A
rigplane probe FT-991A --port /dev/ttyUSB0
```

Find the profile name in `rigplane models` - it is the model column
(`FT-991A`, `IC-7300`, `FTX-1`, ...). Full form with every option:

```bash
rigplane probe [<profile>] [--port DEVICE] [--baud N] [--address 0xNN] \
    [--out FILE] [--include-private] [--label TEXT] [--yes]
```

| Option | Meaning |
| --- | --- |
| `--baud N` | Serial speed. Defaults to the speed the radio answered the scan at, or the profile's own default with an explicit `--port`. |
| `--address 0xNN` | CI-V radio address. Defaults to the address the radio answered the scan at, or the profile's address with an explicit `--port`. |
| `--out FILE` | Where to write. Defaults to `<model>-radio-probe-<YYYY-MM-DD>.json` in your Documents folder; a `-2`, `-3` suffix is added when the name exists, and the home folder (then the current directory) is used when Documents is missing or read-only. |
| `--include-private` | Also read stored memory contents. Off by default. |
| `--label TEXT` | A free-text note stored in the file, for example which state the radio was in. |
| `--yes` | Answer every prompt with yes. Needed when there is no terminal to ask in. |

With an explicit `--port` you must also name the profile: a bare
`--port` stops with exit code 2 and tells you to add the model or drop
the flag. The probe asks which radio is yours after a scan, asks once
before it starts, and asks once before it saves. Answer `n` to any of
them and it stops without writing anything. When stdin is not a
terminal (a script, a pipe), one radio is taken only with `--yes`, and
several radios stop with exit code 2 instead of guessing.

Exit codes: `0` done (quitting before saving is also `0`), `2` bad
usage (unknown profile, unsupported protocol, `--port` without a
profile, no terminal to choose in), `3` no radio found, the port could
not be opened, or nothing answered at all.

### What lands in the file

- The questions asked and the radio's exact replies, one row per read.
  Each row says what the profile declares (`expected`: `present` for a
  listed read, `absent` for a declared-absent read the probe asked
  anyway - those rows name the lending profiles in `note`).
- The reads that were deliberately not sent, each with a reason
  (`skipped`: `unsafe_family`, `private`, `response_code`,
  `no_unambiguous_bytes`, or `run_aborted` when the run stopped
  before attempting them). On the screen the same reasons appear as
  plain sentences, and read names appear as human words (`auto notch`);
  the file keeps the exact enum and the profile identifier
  (`get_auto_notch`).
- How the run ended (`termination`: `completed`, `silence_stop`, or
  `transport_error`, with the reason in plain words).
- What the radio says it is (its identity answer) and whether it
  matches the profile - a mismatch never stops the run.
- Your radio's settings at that moment: frequency, mode, power, and
  the other values the profile reads.
- A before/after snapshot of those settings, and anything that changed
  during the run.

### What is asked, and what is skipped

A read asks a question; it never changes anything. So reads the
profile marks absent are asked too: "absent" is our belief from a
manual, and the point of the tool is to test beliefs against the real
radio. An answer, a refusal, and silence are all information.

A declared-absent CI-V read borrows its bytes from the other shipped
profiles of the same protocol: the same-name command of every sibling
profile, used only when all of them agree on the bytes. Nobody
invents bytes. Borrowed bytes pass through the same filters as native
ones (refused families, stored memory contents without
`--include-private`, the `0xFA`/`0xFB` reply codes), plus one extra
safeguard: a borrowed read is sent only when its command byte is on a
positive read-safe list (a frame with no data is an action, not a
query, for some families such as VFO select, scan, tuner, or speech)
and every lending profile declares the entry as a `get_*` read.
Anything else is recorded as skipped with the reason why - including
native reads the profile lists but the probe never sends (such as the
power and tuner families, or stored memory contents without the flag).
Declared-absent CAT reads (none exist today) are skipped
`no_unambiguous_bytes`: borrowing across CAT profiles is out of scope.

What the tool itself never adds to the file: your name, computer
name, internet address, any file from your computer, the port path,
or log files. Two things you control can still identify you: the
`--label` text is saved exactly as you typed it, and stored memory
contents (only with `--include-private`) can hold callsigns and
stored text. Stored memory contents stay out unless you pass
`--include-private` - look through the file before you send it
either way.

### If the radio stays silent

After five unanswered questions in a row the probe stops and says so -
usually the radio is off or the port and speed are wrong. The rows
collected so far are still offered for saving, and the summary says
in plain words that the run stopped early. Reads never attempted are
listed under `skipped` with the reason `run_aborted`, so every
declared read still appears exactly once - asked or skipped.

### File contract and screen text

- File format: [Probe File Contract](../contracts/probe-file-v1.md).
- Screen wording lives in `probe/render.py` (`start_screen`,
  `pre_save_summary`, `closing_line`, and the discovery helpers) and is
  pinned by exact-string tests; every customer-visible string is plain
  ASCII. Discovery reuses the serial machinery of `rigplane discover
  --serial-only` (`backends/discovery.py`, `discover_serial_radios`);
  the run logic lives in `probe/runner.py` (`run_probe`).
