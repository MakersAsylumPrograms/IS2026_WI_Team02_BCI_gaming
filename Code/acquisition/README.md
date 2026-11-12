# EOG + joystick input for the dashboard

**BioAmp EXG Pill v3 (GPIO 5) + joystick (GPIO 1/2/6) → ESP32-S3 → USB-to-UART (COM7) → official Chords Python → dashboard**

The eyes give **one command, CYCLE**: look to **either side** (left or right, it does not
matter) and **hold** for about a second. Each CYCLE moves the patient screen's highlight one
option, always in the same direction. Looking back to the centre is not a command, and blinks
never are. The **joystick** corrects and confirms: left/up → PREVIOUS, right/down → NEXT,
press → SELECT. Both run in one process and reach the dashboard as the device's `eog` and
`joystick` input.

## 1. Safety first

- Laptop **on battery**, charger unplugged, and no mains-powered peripherals (monitor,
  USB hub with its own supply) connected while electrodes are on a person.
- Power the Pill from the ESP32's **3V3** pin only. Before the first electrode session,
  check the exact Pill v3 variant's supply and output limits in its guide, and confirm
  there are no undocumented wiring or jumper changes.
- Educational prototype; not a medical device.

## 2. Wiring and electrodes

| Module pin | ESP32-S3-DevKitC-1 (N16R8) |
| --- | --- |
| Pill VCC / GND | 3V3 / GND |
| Pill OUT | GPIO **5** |
| Joystick GND | GND |
| Joystick +5V | 5V (team choice; 3V3 also works) |
| Joystick VRx | GPIO **1** |
| Joystick VRy | GPIO **2** |
| Joystick SW | GPIO **6** (internal pull-up; pressed = LOW) |

**GPIO 1 sits right next to the TX and RX pins** (header order from the GND corner: GND, TX, RX,
1, 2 ...). Use the printed labels. A joystick wire on TX/RX makes the board stop answering
(`no ESP32-S3 Chords firmware answered WHORU`). Check before a session: `view` should show a raw
EOG baseline of about **1,800–2,000** counts, a firm look should move it by about 1,000, and
`joystick` should print a centre around 3,100 (5V) or 2,000 (3V3) that moves toward 0 / 4,095 as
you push. Both axes stuck at exactly 0 means the joystick has no power.

On 5V the joystick's X/Y outputs go above the ESP32-S3's ~3.6 V pin limit at full deflection; a
10 kΩ resistor in series in each of VRx and VRy limits the current. The ADC saturates at ~3.1 V,
which the joystick code allows for.

Horizontal EOG with three electrodes, following the Pill v3 EOG guide: IN+ and IN− at the outer
corners of the two eyes, reference (REF) where that guide places it. Use fresh gel electrodes for
each session. Pass the IN+ side with `--in-plus left|right`; the CYCLE detector ignores direction,
so either orientation works.

Serial acquisition loads the unchanged official `chords_serial.py` from the pinned installed
distribution directly, which avoids the upstream package initializer (GUI/LSL imports). No native
LSL installation is needed.

## 3. Flash the firmware

Open `Code/firmware/ESP32-S3/ESP32-S3.ino` in the Arduino IDE. Tested with **esp32 by
Espressif 3.3.12**:

| Tools setting | Value |
| --- | --- |
| Board | ESP32S3 Dev Module |
| USB CDC On Boot | **Disabled** |
| USB Mode | Hardware CDC and JTAG |
| Upload Mode | UART0 / Hardware CDC |
| Flash Size | 16MB (128Mb) |
| PSRAM | OPI PSRAM |
| Port | COM7 (the board's **UART** USB connector) |

Same build from the command line:
`arduino-cli compile --fqbn "esp32:esp32:esp32s3:CDCOnBoot=default,USBMode=hwcdc,UploadMode=default,FlashSize=16M,PSRAM=opi" Code/firmware/ESP32-S3`

The firmware starts **stopped** and only streams after `START`. **Close the Arduino Serial
Monitor and Plotter** before Python opens COM7.

## 4. Set up Python (once)

```powershell
& ./Code/acquisition/setup.ps1
cd Code/acquisition
```

<<<<<<< HEAD
This creates a separate Python 3.11 `.venv` and installs official `chordspy` 0.2.2 (hash-pinned).
All commands below run from `Code/acquisition` as `.venv\Scripts\python.exe -m eog_acquisition <command>`
(add `--port COMx` if not COM7).
=======
This creates a separate Python 3.11–3.13 `.venv` (using the first installed compatible version)
and installs official `chordspy` 0.2.2 (hash-pinned). If none is installed, run `py install 3.11`,
then rerun setup.
It is separate from the dashboard's environment. All commands below run from `Code/acquisition`:
`.venv\Scripts\python.exe -m eog_acquisition <command>` (add `--port COMx` if not COM7).
>>>>>>> d257b1c3ef4240ca849052b9a83073c45b452250

## 5. Use it with the dashboard

1. Flash `Code/firmware/ESP32-S3` (it sends the EOG on channel 1 and the joystick on channels 2–4).
2. Start the dashboard backend (`Code/backend/start.ps1`) and sign in on the **patient** screen.
3. Electrodes on, joystick untouched, then:

```powershell
.venv\Scripts\python.exe -m eog_acquisition run --dashboard --plot
```

- **Eyes:** after a 3 s warm-up, each look to either side, held until the plot flashes **CYCLE**,
  moves the highlight one option (`PREVIOUS`, i.e. to the left; `--command NEXT` for the other way).
- **Joystick:** its centre is measured in the first half second while it is untouched. Then every
  push moves the highlight (left/up → PREVIOUS, right/down → NEXT) and a press selects. One push
  is one command, even if held. If left/right come out the wrong way round, add `--invert-joystick`.
  `--no-joystick` ignores it.

`run` checks that the backend is reachable first and keeps the device shown as online. Commands
that cannot be delivered within 1.6 s are dropped, never delivered late. Without `--dashboard`,
commands are only printed and logged to `data\events\`.

Test the joystick on its own (prints the raw X/Y/button values and every push):
`.venv\Scripts\python.exe -m eog_acquisition joystick` (add `--dashboard` to send them too).

The plot's lower graph is the **recovered gaze**: a look is a step that has to leave the dashed
band and stay out for the hold time.

`run` uses the team model `models/cycle-default.json` unless `--model` is given.

## 6. Calibrate for a person or electrode placement

```powershell
.venv\Scripts\python.exe -m eog_acquisition session --set calibration --in-plus left --plot --auto-calibrate
```

About 9 minutes: 30 s looking at the centre, 20 trials each of LEFT, RIGHT, BLINK and
DOUBLE BLINK in random order, then 2 minutes of normal screen use. The terminal shows only the
current command as one big word, and every command beeps. Do each command **as soon as it
appears**. For LEFT/RIGHT, move only the eyes to the mark and **hold** until CENTRE.

Calibration (also `calibrate <recording> [<recording> ...]`):

- **Pill time constant:** the value that makes held looks flattest after inverting the Pill's
  high-pass filter.
- **Threshold and hold time:** the most **precise** pair (every CYCLE outside a LEFT/RIGHT trial
  counts as false, including during normal use) that still catches at least 80% of deliberate
  looks. Use `--min-recall 0.9` to trade precision for sensitivity.

The model is saved to `data\models\cycle-<time>.json` with its calibration report. Check it on
any other recording with `evaluate <recording> --model <model>`, or watch it replay with
`replay <recording> --model <model>`. Recordings are only read, never modified.

To make a new model the team default, save it as `models/cycle-default.json` and commit it.

## 7. How it works and how well

The Pill's output is the eye signal after a first-order high-pass (time constant ~0.1–0.15 s). A
held look shows up only as a spike that dies away in ~0.1 s, so a held look, a glance and a blink
all look alike. `cycle.py` **inverts that filter** (`GazeRecovery`), which brings back the gaze
level. A held look to either side becomes a plateau and a blink a short pulse. `CycleDetector`
then emits one CYCLE when the recovered gaze moves more than `threshold` from where it was and
stays there for `hold` seconds. After that it waits for the return to centre and settles before
it listens again. Nothing counts during the 3 s warm-up, across a data gap, or while the ADC is
saturated (hard blinks, a loose electrode). After saturation the inversion restarts cleanly.

Team model (`models/cycle-default.json`): calibrated on `calibration-20261112-125337`: Pill
time constant 0.15 s, threshold 1,932 counts, hold 1.1 s.

| Recording (2026-11-11/12) | Result |
| --- | --- |
| Calibration session, held looks (fitted on it) | 32 of 40 looks; protocol precision 0.91 (3 false CYCLEs in 6.5 min, 1 after a blink cue); 3.5 CYCLEs/min during free screen use |
| 2026-11-11 signal checks run 3 and run 4 (other day, other electrode orientation) | 5 of 6 and 6 of 6 looks, no extra CYCLEs |
| Session with quick look-and-back (no hold) | 4 of 40 (no hold, no CYCLE), 0 false |
| Broken wiring, no electrodes (5 min), electrode off | 0 CYCLEs |

**Limit:** on one horizontal channel, a natural look that is held just as long and is just as
large is the same signal as a deliberate one. That is why normal screen use still produces a few
CYCLEs a minute, and why the joystick will confirm selections.

## 8. Commands

| Command | Purpose |
| --- | --- |
| `check` | firmware identity, start/stop, 250 Hz, clean stream (no electrodes needed) |
| `capture --minutes 5` | raw recording with a rate/gap verdict |
| `view` | live raw + filtered plots, nothing recorded |
| `session --set calibration` | guided recording (labels each sample) |
| `calibrate <recordings>` | fit a CYCLE model |
| `evaluate <recordings> --model M` | score labelled recordings / count CYCLEs in unlabelled ones |
| `replay <recording> --model M` | list CYCLEs with the protocol label at each |
| `joystick [--dashboard]` | live joystick test: raw values and each push/press |
| `run [--dashboard] [--plot]` | live eyes + joystick, optionally driving the dashboard |

## 9. Code

| File | Role |
| --- | --- |
| `protocol.py`, `device.py`, `chords_compat.py` | Chords packet decoding, exactly-once ordered delivery, device control |
| `processing.py` | health checks, 0.5–20 Hz display filter, `Pipeline` (one pass per sample, in order) |
| `cycle.py` | gaze recovery, `CycleDetector`, scoring and calibration |
| `joystick.py` | `JoystickReader`: centre detection, pushes → NEXT/PREVIOUS, debounced press → SELECT |
| `dashboard.py` | `DashboardLink`: eye CYCLE (source `eog`) and joystick commands (source `joystick`) → `POST /api/v1/input-events` (+ heartbeat), same contract as `Code/backend/simulate_device.py` |
| `recording.py`, `session.py`, `viewer.py`, `cli.py` | recordings, guided protocol, live plot, command line |

Raw values are stored exactly as received (ADC counts, not microvolts).

## 10. Tests

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

Covered:
- Packet decoding against the official Chords decoder.
- Gaps, stalls and device handling.
- Recording protection.
- The detector on Pill-shaped signals: one CYCLE per held look either way, none for the return,
  blinks, glances, saturation, warm-up, gaps or no signal.
- Calibration finding the Pill time constant and precise settings, without changing raw files.
- The joystick reader at 5V and 3V3 centres: one command per push in each direction, a debounced
  SELECT, waiting for a still stick before measuring the centre, and nothing when no joystick is
  connected.
- `DashboardLink` against the real backend app over HTTP: each eye CYCLE and joystick command
  reaches the patient screen as one command with the right source, a bad token is not retried, and
  stale commands are dropped.
