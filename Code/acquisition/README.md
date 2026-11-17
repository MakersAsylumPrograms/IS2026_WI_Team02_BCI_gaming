# Eyes and joystick: the board and the laptop side

**BioAmp EXG Pill v3 (GPIO 1) + joystick (GPIO 4/5/6) → ESP32-S3 → USB-to-UART or Bluetooth →
`eog_acquisition` on the patient laptop → EqualCare server.** Only commands (NEXT / PREVIOUS /
SELECT) leave the laptop's acquisition process; raw samples never do.

`Start-EqualCare.cmd` runs all of this for you. This guide is for wiring, flashing, checking the
signal and working from the command line.

## 1. Safety

- Laptop **on battery**, charger unplugged, no mains-powered peripherals (monitor, powered USB hub)
  while electrodes are on a person.
- Power the Pill and the joystick from the ESP32's **3V3** pin only, never 5 V.
- Educational prototype; not a medical device.

## 2. Wiring and electrodes

| Module pin | ESP32-S3-DevKitC-1 (N16R8) |
| --- | --- |
| Pill VCC / GND | 3V3 / GND |
| Pill OUT | GPIO **1** |
| Joystick VCC / GND | **3V3** / GND |
| Joystick VRx / VRy | GPIO **4** / GPIO **5** |
| Joystick SW | GPIO **6** (internal pull-up; pressed = LOW) |

GPIO 1 sits right next to TX and RX on the header. A wire on TX/RX makes the board stop answering
(`no ESP32-S3 Chords firmware answered WHORU`); use the printed labels.

Horizontal EOG with three electrodes: **IN+ at the outer corner of the left eye, IN− at the outer
corner of the right eye, REF behind the right ear.** Fresh gel electrodes each session. Swapping IN+
and IN− swaps left and right; calibration notices this and corrects for it.

## 3. Flash the firmware

Open `Code/firmware/ESP32-S3/ESP32-S3.ino` in the Arduino IDE (tested with **esp32 by Espressif
3.3.12**):

| Tools setting | Value |
| --- | --- |
| Board | ESP32S3 Dev Module |
| USB CDC On Boot | **Disabled** |
| USB Mode | Hardware CDC and JTAG |
| Upload Mode | UART0 / Hardware CDC |
| Flash Size | 16MB (128Mb) |
| PSRAM | OPI PSRAM |
| Port | the board's **UART** USB connector |

Same build from the command line:
`arduino-cli compile --fqbn "esp32:esp32:esp32s3:CDCOnBoot=default,USBMode=hwcdc,UploadMode=default,FlashSize=16M,PSRAM=opi" Code/firmware/ESP32-S3`

Close the Serial Monitor afterwards: only one program can use the port. The firmware starts
stopped and streams 250 samples a second only after `START`.

## 4. USB or Bluetooth

The same firmware does both; whichever link sends `START` gets the stream.

- **USB:** cable in the board's UART connector. The laptop finds the port by itself (`--port auto`,
  the default, tries every USB serial port and keeps the one the firmware answers on).
- **Bluetooth:** no cable. Power the board from a USB power bank, or a 1-cell LiPo through a 5 V
  boost converter into the 5V pin (never straight into 3V3; one power source at a time). It
  advertises as `NPG-EqualCare-6CH`. With no board on USB, `auto` scans for it (up to 10 s).
- To force one: `--port COM7`, `--port BLE` (or `BLE:<name or address>`), or `"port"` in
  `Code/launcher-config.json`.

## 5. Check the signal

From `Code/acquisition`, with the environment `Start-EqualCare.cmd` created:

```powershell
.venv\Scripts\python.exe -m eog_acquisition check
```

checks the firmware, start/stop, the 250 Hz rate and a clean stream (no electrodes needed). Then

```powershell
.venv\Scripts\python.exe -m eog_acquisition test-eyes --plot
```

prints every eye signal as it happens and shows the live plot: the raw signal should sit near
1,800–2,000 counts at rest, and a firm look moves it by about 1,000. In the lower plot a look left is
one wide pulse **up** out of the dashed band, a look right one pulse **down**.

## 6. Calibrate

Normally from the patient screen (**Calibrate eyes**, see the [main README](../../README.md#3-calibrate-the-eyes-for-a-new-person)).
From the command line, with EqualCare closed:

```powershell
.venv\Scripts\python.exe -m eog_acquisition calibrate-eyes --plot
```

Both run the same sequence twice (about 75 s each): 6 cued looks (3 left, 3 right, in random order,
held 2.5 s), 3 single blinks, 5 double blinks and 15 s of free viewing. Then five thresholds (look
pulse height, width and the level left after it; blink rise and how soon it falls) are fitted on a grid to
those two rounds only, and each round is scored with the thresholds fitted on the other. Before that,
the sign of the looks and of the blinks is taken from the rounds themselves: depending on where the
electrodes sit, a blink starts with a spike or with a dip. The model
is saved over `models/eog-gestures.json` only if that check passes (≥ 90% of looks caught, ≥ 90% of
look commands right, ≥ 60% of double blinks caught, ≤ 1 false command a minute, no false SELECT); otherwise the candidate goes to
`data/models/eog-gestures-<time>-not-approved.json` and nothing changes. Each round is kept as
`data/recordings/eye-calibration-<time>-round<n>.csv`.

`run` sends eye commands only from a model whose check passed for exactly its thresholds;
`--allow-provisional-eog` overrides that for experiments. The joystick is always sent.

To refit from saved recordings (calibration rounds and/or Chords-Web exports), with the same check and
the same rule for replacing the model:

```powershell
.venv\Scripts\python.exe -m eog_acquisition refit data\recordings\eye-calibration-*-round1.csv data\recordings\eye-calibration-*-round2.csv
```

## 7. How detection works, and its limits

The live signal is channel 1 through the same filters Chords-Web applies (its 0.1 Hz high-pass, the
10 Hz EOG low-pass and the 50 Hz notch; `chords_filter.py`). The Pill's own high-pass (time
constant ~0.1–0.15 s) turns every eye movement into a pulse, and Chords-Web's slow high-pass leaves a
small opposite level behind it.

- **Look:** after a pulse the detector watches half a second. If the opposite level stays, the eyes
  moved and stayed. A movement is a command when its pulse is tall and wide enough (the calibrated
  thresholds). It remembers which side the eyes went to, so the look back to the centre does
  nothing; after 8 s away it assumes they are back.
- **Blink:** a quick rise with a deep rebound that dies away. Each blink is measured from the low
  point just before it, so the second blink of a pair counts even though it starts in the first
  one's rebound. Two blinks 0.2–0.85 s apart are SELECT; a single blink does nothing.
- Nothing is a command during the 3 s warm-up, across a data gap or while the ADC is pinned.

**Limits.** One horizontal channel gives left, right, blink and double blink; that is all
`Test eyes` can show. Up/down or winks would need a second Pill on a vertical channel. A natural
look that is as large and held as long as a deliberate one is the same signal, so expect some false
moves in real use; the joystick always works. Holding a look longer than 8 s makes the look back
count as the other direction.

**Earlier result (2026-11-13/15, the team's tester):** on three Chords-Web recordings plus look and
blink tests (19.3 minutes), held out by recording: 43 of 46 looks right, 1 wrong direction, 70 of 84
double blinks, 1 false move and no false SELECT. That model is frozen in
`tests/fixtures/team-model-2026-11-15.json` and its recordings stay in `data/recordings/` as the
detector's regression data.

## 8. Joystick

Standard 3V3 joysticks centre themselves at start-up (leave the stick alone for a moment). If a
module's directions come out wrong, calibrate it once (30 s, follow the prompts):

```powershell
.venv\Scripts\python.exe -m eog_acquisition joystick-calibrate
```

This saves `models/joystick.json`, which `run` and `joystick` then use. `joystick` alone prints the
raw X/Y/button values and every push; both axes stuck at 0 means the joystick has no power.

## 9. Commands

`.venv\Scripts\python.exe -m eog_acquisition <command>`; every board command takes `--port`.

| Command | Purpose |
| --- | --- |
| `check` | firmware identity, start/stop, 250 Hz, clean stream |
| `view` | live raw and eye-signal plots |
| `test-eyes` | print every eye signal as it happens; nothing is sent |
| `calibrate-eyes` | the guided calibration above |
| `refit <recordings>` | fit the eye model to saved recordings |
| `replay-chords <recording> [--dashboard]` | list (or send, in real time) the commands a recording gives |
| `capture --minutes 5` | raw recording with a rate/gap verdict |
| `joystick`, `joystick-calibrate` | joystick test and calibration |
| `run [--dashboard] [--plot]` | live eyes + joystick; what the launcher runs |

## 10. Code

| File | Role |
| --- | --- |
| `protocol.py`, `device.py`, `ble.py`, `chords_compat.py` | packet decoding, ordered delivery, USB (official chordspy `Chords_USB`) and Bluetooth |
| `processing.py` | signal health, display filter, `Pipeline` (every sample once, in order) |
| `chords_filter.py` | the Chords-Web filter chain |
| `gestures.py` | `GestureDetector` (LEFT / RIGHT / DOUBLE BLINK) and its model file |
| `chords_calibration.py` | loading recordings, scoring against the prompts, fitting and the held-out check |
| `guided.py` | the calibration sequence, the signal test, and running them for the patient screen |
| `joystick.py` | pushes → NEXT/PREVIOUS, debounced press → SELECT, optional profile |
| `dashboard.py` | `DashboardLink`: commands and heartbeat to the server; calibration requests and progress |
| `recording.py`, `viewer.py`, `cli.py` | recordings and event logs, live plot, command line |

Raw values are stored exactly as received (ADC counts, not microvolts). Tests:
`.venv\Scripts\python.exe -m pytest -q tests`.
