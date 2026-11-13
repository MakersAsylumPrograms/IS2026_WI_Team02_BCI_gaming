# EOG + joystick input for the dashboard

## Current calibration (2026-11-13): left, right, double blink

**BioAmp EXG Pill v3 (GPIO 1) + joystick (GPIO 4/5/6) → ESP32-S3 → USB-to-UART (COM7) → official Chords Python → dashboard**

`Start-EqualCare.cmd` and `run` load **`models/eog-gestures.json`**, calibrated from the three
Chords-Web recordings in `data/recordings/Fresh Calibration Recording Through Chords/`:

| Eyes | Dashboard | Same as joystick |
| --- | --- | --- |
| look **left** and hold | PREVIOUS | left / up |
| look **right** and hold | NEXT | right / down |
| **double blink** | SELECT | press |
| look back to the centre, single blink | nothing | |

Electrodes: **IN+ at the left eye's outer corner, IN− at the right eye's, REF behind the right
ear.** Swapped IN+/IN− swaps left and right. Look well to the side (the marks were about 30°
out, beyond the screen edges) and stay there for at least half a second; the command comes
about 0.7 s after the eyes move. Blink twice, about half a second apart, for SELECT.

The live signal is channel 1 through the same filters Chords-Web applied when recording (its
high-pass, the EOG 10 Hz low-pass and the 50 Hz notch; `chords_filter.py`), so the thresholds
mean the same thing live as in the recordings. The plot's lower graph is that signal.

Checked by fitting the thresholds without each recording in turn and scoring that recording:

| Recording | Cued looks → right command | Wrong direction | Cued double blinks → SELECT | False commands |
| --- | --- | --- | --- | --- |
| 1st one (4.4 min) | 9 / 10 | 0 | 2 / 8 | 0 |
| 2nd one - better (4.0 min) | 10 / 10 | 0 | 7 / 8 | 0 |
| 3rd one - better (4.1 min) | 10 / 10 | 0 | 5 / 8 | 0 |

Read these with care:

- The missed look is the last one of recording 1; the signal shows no eye movement there.
- The ten missed double blinks show one blink or none in the signal (most of recording 1's
  blinks are barely visible). Blink firmly for SELECT; the joystick press always works.
- "No false commands" rests on about 2.5 minutes of natural viewing and 12.5 minutes in all,
  one person, one afternoon. Any large sideways eye movement that is held is a look to this
  detector, so expect some false PREVIOUS/NEXT in real use. On the older raw sessions of
  2026-11-12 (other electrode orientation, saturating signal) the same thresholds gave up to
  about six a minute during free viewing.
- It has **not** been run with electrodes on a person since calibration. Do the two-minute
  check in section 5 before relying on it.

The model file carries this check; `run --dashboard` forwards eye commands only for a model
whose check passed and whose thresholds are unchanged since (`--allow-provisional-eog` overrides
that for experiments). Both inputs stay active with no joystick priority. Close other serial
tools first. Review and method: `hardware-tests/2026-11-13-chords-calibration/README.md`.

The earlier direction-free CYCLE models (`models/cycle-*.json`, sections 6–7) are kept for
reference; their recordings clipped the ADC and their best fit reached 70% of looks with 64%
precision (`hardware-tests/2026-11-12-final/`).

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
| Pill OUT | GPIO **1** |
| Joystick GND | GND |
| Joystick VCC | **3V3** |
| Joystick VRx | GPIO **4** |
| Joystick VRy | GPIO **5** |
| Joystick SW | GPIO **6** (internal pull-up; pressed = LOW) |

**GPIO 1 sits right next to the TX and RX pins** (header order from the GND corner: GND, TX, RX,
1, 2 ...). Use the printed labels. A joystick wire on TX/RX makes the board stop answering
(`no ESP32-S3 Chords firmware answered WHORU`). Check before a session: `view` should show a raw
EOG baseline of about **1,800–2,000** counts, a firm look should move it by about 1,000, and
`joystick` should print x/y values that change a lot as you push. Both axes stuck at exactly 0
means the joystick has no power.

Do not power the joystick from 5 V. A series resistor alone does not reduce its analog
output to a safe ESP32 input voltage. Reflash the corrected firmware before combined use.

Horizontal EOG with three electrodes: **IN+ at the outer corner of the left eye, IN− at the outer
corner of the right eye, REF behind the right ear** (the placement of the 2026-11-13 calibration).
Use fresh gel electrodes for each session. The gesture model's left/right depend on this
orientation; `--in-plus` only labels recordings.

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

This creates a separate Python 3.11–3.13 `.venv` (using the first installed compatible version)
and installs official `chordspy` 0.2.2 (hash-pinned). If none is installed, run `py install 3.11`,
then rerun setup.
It is separate from the dashboard's environment. All commands below run from `Code/acquisition`:
`.venv\Scripts\python.exe -m eog_acquisition <command>` (add `--port COMx` if not COM7).

## 5. Use it with the dashboard

1. Flash `Code/firmware/ESP32-S3` (it sends the EOG on channel 1 and the joystick on channels 2–4).
2. Start the dashboard backend (`Code/backend/start.ps1`) and sign in on the **patient** screen.
3. Electrodes on, joystick untouched, then:

```powershell
.venv\Scripts\python.exe -m eog_acquisition run --dashboard --plot
```

- **Eyes:** after a 3 s warm-up, look left and hold → the plot flashes **LEFT → PREVIOUS** and the
  highlight moves one option back; look right and hold → **RIGHT → NEXT**; double blink →
  **DOUBLE BLINK → SELECT**. Come back to the centre between looks; that does nothing.
- **Joystick:** a press selects. Each push moves the highlight (left/up → PREVIOUS, right/down →
  NEXT). One push is one command,
  even if held. Calibrate the module once (below) so left really is left. `--no-joystick` ignores it.

**Two-minute check with electrodes on (do this first each session):** run
`.venv\Scripts\python.exe -m eog_acquisition run --plot` (nothing is sent). At rest the raw plot
should sit near 1,800–2,000 and the lower plot near 0. A firm look left must give one wide pulse
**up** out of the dashed band and print `EYES LEFT`; right the same **down**. If left prints
RIGHT, IN+ and IN− are swapped. Then try five looks each way and five double blinks, and watch
for commands while just reading the screen. Recalibrate (section 6) if looks are missed or
ordinary viewing triggers commands.

**Without the sensor** (demo, or to see what the dashboard does with a recording):
`.venv\Scripts\python.exe -m eog_acquisition replay-chords "data\recordings\Fresh Calibration Recording Through Chords\2nd one - better.csv" --dashboard`
replays the recording in real time into the signed-in patient screen (`--speed 4` is faster).

**Joystick calibration (once per module, 30 s):**
`.venv\Scripts\python.exe -m eog_acquisition joystick-calibrate`. Follow the prompts (rest, then push
left, right, up, down twice each). It learns which axis each direction moves and how far the resting
noise reaches, prints which directions this module can produce, and saves `models/joystick.json`,
which `run` and `joystick` then use. Commit that file to share it. Without a profile, a standard
(centred, quiet) joystick still works automatically.

`run` checks that the backend is reachable first and keeps the device shown as online. Commands
that cannot be delivered within 1.6 s are dropped, never delivered late. Without `--dashboard`,
commands are only printed and logged to `data\events\`.

Test the joystick on its own (prints the raw X/Y/button values and every push):
`.venv\Scripts\python.exe -m eog_acquisition joystick` (add `--dashboard` to send them too).

`run` uses the team model `models/eog-gestures.json` unless `--model` is given (a `cycle-*.json`
model still works and gives the old single CYCLE command).

## 6. Calibrate for a person or electrode placement

**Gesture model (current), from Chords-Web recordings:**

1. In Chords-Web, connect the board, switch on the **EOG** filter and the **50 Hz** notch for
   channel 1, and record while following `Calibration Sequence.xlsx` by the clock (4 minutes:
   rest, looks left/right held 4 s, single blinks, double blinks, natural viewing). Save the CSV.
   Make at least two recordings, three is better.
2. `.venv\Scripts\python.exe -m eog_acquisition calibrate-chords <rec1.csv> <rec2.csv> ... --out data\models\eog-gestures-<name>.json`
   (about 1.5 minutes). It prints, for each recording, the result with thresholds fitted
   **without** that recording, and whether the model is approved for the dashboard (at least 90%
   of looks right, at least 90% of look commands right, at most one false command a minute, no
   false SELECT). Leave out `--out` to replace the team model `models/eog-gestures.json`.
3. Point `model` in `Code/launcher-config.json` at the new file, or pass `--model` to `run`.

Recordings are only read. The cue sheet's times are in `chords_calibration.py` (`SEQUENCE`); a
look counts for its cue if it starts up to 3 s before the cue or during its hold.

**CYCLE model (earlier, direction-free), from the guided session:**

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

**Gestures (`gestures.py`).** The Pill's output is the eye signal after a first-order high-pass
(time constant ~0.1–0.15 s), so every eye movement is a pulse: up for left, down for right (IN+
on the left). Chords-Web's own slow high-pass then leaves a small level of the opposite sign
behind any pulse that is not undone by a second one. So after a pulse the detector watches half a
second: if that opposite level is still there, the eyes moved and stayed; if a deep opposite
swing has died away, it was a blink (spike and rebound). A movement is a command when its pulse
is tall (≥ 0.5, i.e. about 1,000 counts) and wide (≥ 0.18 s beyond 0.25): big looks take longer
and clip the ADC, so they are wider rather than taller, while the ordinary eye movements in the
calibration recordings stayed at or under 0.16 s. The detector remembers which side the eyes went to and how far, so the look back does
nothing; after 8 s away it assumes they are back. Two blinks 0.2–0.85 s apart are one SELECT.
Nothing is a command during the 3 s warm-up, across a data gap or while the ADC is pinned.
**Limits:** one horizontal channel cannot tell a deliberate look from an equally large natural
one, and holding a look longer than 8 s makes the look back count as the other direction.

**CYCLE (`cycle.py`, earlier).** A
held look shows up only as a spike that dies away in ~0.1 s, so a held look, a glance and a blink
all look alike. `cycle.py` **inverts that filter** (`GazeRecovery`), which brings back the gaze
level. A held look to either side becomes a plateau and a blink a short pulse. `CycleDetector`
then emits one CYCLE when the recovered gaze moves more than `threshold` from where it was and
stays there for `hold` seconds. After that it waits for the return to centre and settles before
it listens again. Nothing counts during the 3 s warm-up, across a data gap, or while the ADC is
saturated (hard blinks, a loose electrode). After saturation the inversion restarts cleanly.

Historical unverified team model (`models/cycle-default.json`): its source CSV is absent
from GitHub, so the following reported results cannot currently be reproduced. Its overall
training precision is 76.2%, recall 80%, natural false events 3.5/minute; these do not
meet the new independent approval requirements. It cites `calibration-20261112-125337`: Pill
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
| `calibrate-chords <chords csv ...>` | fit and check the gesture model on Chords-Web recordings |
| `replay-chords <chords csv> [--dashboard]` | list the commands a Chords-Web recording gives; optionally send them |
| `calibrate <recordings>` | fit a CYCLE model |
| `evaluate <recordings> --model M` | score labelled recordings / count CYCLEs in unlabelled ones |
| `replay <recording> --model M` | list CYCLEs with the protocol label at each |
| `joystick-calibrate` | 30 s guided joystick calibration → `models/joystick.json` |
| `joystick [--dashboard]` | live joystick test: raw values and each push/press |
| `run [--dashboard] [--plot]` | live eyes + joystick, optionally driving the dashboard |

## 9. Code

| File | Role |
| --- | --- |
| `protocol.py`, `device.py`, `chords_compat.py` | Chords packet decoding, exactly-once ordered delivery, device control |
| `processing.py` | health checks, 0.5–20 Hz display filter, `Pipeline` (one pass per sample, in order) |
| `chords_filter.py` | the Chords-Web filter chain (high-pass, EOG low-pass, 50 Hz notch) on raw samples |
| `gestures.py` | `GestureDetector`: LEFT / RIGHT / DOUBLE BLINK → PREVIOUS / NEXT / SELECT, and its model |
| `chords_calibration.py` | Chords-Web CSV loading, the cue sheet, scoring and calibration of the gesture model |
| `cycle.py`, `blink.py` | earlier: gaze recovery, `CycleDetector`, its scoring and calibration; provisional double blink |
| `joystick.py` | `JoystickReader` and its calibration profile: pushes → NEXT/PREVIOUS, debounced press → SELECT |
| `dashboard.py` | `DashboardLink`: eye commands (source `eog`) and joystick commands (source `joystick`) → `POST /api/v1/input-events` (+ heartbeat), same contract as `Code/backend/simulate_device.py` |
| `recording.py`, `session.py`, `viewer.py`, `cli.py` | recordings, guided protocol, live plot, command line |

Raw values are stored exactly as received (ADC counts, not microvolts).

## 10. Tests

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

Covered:
- The Chords-Web filter chain: its coefficients, resting level, 50 Hz rejection and units.
- The gesture detector on Pill-shaped raw signals: left → PREVIOUS, right → NEXT, nothing for the
  look back, single blinks, small movements, quick glances, warm-up, gaps or a pinned ADC; two
  blinks → SELECT; swapped electrodes swap the directions.
- The team model on the three Chords-Web recordings (which must be byte-identical to the ones it
  was calibrated from), and one of them replayed through the command line, `DashboardLink` and
  the real backend app to the patient API.
- Packet decoding against the official Chords decoder.
- Gaps, stalls and device handling.
- Recording protection.
- The detector on Pill-shaped signals: one CYCLE per held look either way, none for the return,
  blinks, glances, saturation, warm-up, gaps or no signal.
- Calibration finding the Pill time constant and precise settings, without changing raw files.
- The joystick: calibration learning each direction of an off-centre, noisy module (and flagging
  directions it cannot produce), pushes firing once per neutral-return cycle,
  standard 3V3 joysticks without a profile, a debounced SELECT, and nothing when no
  joystick is connected.
- `DashboardLink` against the real backend app over HTTP: each eye CYCLE and joystick command
  reaches the patient screen as one command with the right source, a bad token is not retried, and
  stale commands are dropped.
