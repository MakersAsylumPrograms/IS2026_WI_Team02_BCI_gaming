# EOG acquisition and gesture classification

**BioAmp EXG Pill v3 → GPIO 1 → ESP32-S3 → USB-to-UART (COM7) → official Chords Python → LEFT / RIGHT / BLINK**

This milestone stops at local events: gestures are printed and logged. Nothing here
sends dashboard commands, HTTP requests or keypresses. The joystick and dashboard
integration come later through the callbacks described at the end.

## 1. Safety first

- Laptop **on battery**, charger unplugged, and no mains-powered peripherals (monitor,
  USB hub with its own supply) connected while electrodes are on a person.
- Power the Pill from the ESP32's **3V3** pin only. Before the first electrode session,
  check the exact Pill v3 variant's supply and output limits in its guide, and confirm
  there are no undocumented wiring or jumper changes.
- Educational prototype; not a medical device.

## 2. Wiring and electrodes

Joystick assignment for the next integration milestone (PS2 analog breakout):
GND → GND, VCC → **3V3**, VRx → GPIO 4, VRy → GPIO 5,
SW → GPIO 6 (configure INPUT_PULLUP; pressed is LOW).
Use 3.3 V power so the analog outputs remain compatible with ESP32 inputs.
GPIO 1 remains dedicated to the Pill. This is the proposed wiring; joystick
sampling and dashboard forwarding are not implemented in this acquisition build.

Serial acquisition loads the unchanged official `chords_serial.py` from the pinned
installed distribution directly. This avoids the upstream package initializer,
which otherwise starts importing GUI/LSL dependencies and requires native liblsl
even for serial-only use. No native LSL installation is needed for this path.

| Pill v3 | ESP32-S3-DevKitC-1 (N16R8) |
| --- | --- |
| VCC | 3V3 |
| GND | GND |
| OUT | GPIO 1 |

Horizontal EOG with three electrodes, following the Pill v3 EOG guide: IN+ and IN− at
the outer corners of the two eyes, reference (REF) where that guide places it. **Write
down which eye IN+ is on** and pass it with `--in-plus left|right`. Calibration works
out the polarity either way.

## 3. Flash the firmware

Open `Code/firmware/ESP32-S3/ESP32-S3.ino` in the Arduino IDE. Tested with **esp32 by
Espressif 3.3.12** (check Tools › Board › Boards Manager and note your version):

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
Monitor and Plotter** before Python opens COM7. It will look like binary noise anyway.

## 4. Set up Python (once)

```powershell
& ./Code/acquisition/setup.ps1
cd Code/acquisition
```

This creates a separate Python 3.11 `.venv` and installs official `chordspy` 0.2.2 (hash-pinned).
It is separate from the dashboard's environment. All commands below run from `Code/acquisition`:
`.venv\Scripts\python.exe -m eog_acquisition <command>` (add `--port COMx` if not COM7).

## 5. Hardware session: run these in order

| Step | Command | Pass when |
| --- | --- | --- |
| 1. Firmware check | `check` | every line PASS (identification, starts stopped, 250 Hz ±1%, no gaps, no stray text, silent after STOP) |
| 2. Look at the signal | `view` | raw trace moves with eye movements; no clipping/flat warnings |
| 3. Five-minute capture | `capture --minutes 5 --in-plus left` | PASS: 250 Hz within 1%, 0 missing, 0 stalls |
| 4. Calibration set | `session --set calibration --in-plus left` | completes (≈ 8 min) |
| 5. Calibrate | `calibrate data\recordings\calibration-<time>.csv` | no errors; note any warnings |
| 6. Evaluation set (new recording) | `session --set evaluation --in-plus left` | completes |
| 7. Held-out evaluation | `evaluate data\recordings\evaluation-<time>.csv --model data\models\model-<time>.json --apply` | ≥ 90% precision and recall per gesture, ≤ 1 false event/min |
| 8. Live use | `run --model data\models\model-<time>.json --plot` | gestures appear in the terminal and `data\events\` |

**Session layout** (both sets): 30 s looking at a centre mark, then 20 LEFT, 20 RIGHT and
20 BLINK trials in random order, then 2 minutes of normal use with ordinary blinks. Put the
centre mark straight ahead and two side marks about 30° left and right. Tones tell you what
to do, so you don't have to read the screen while looking away: low = LEFT, high = RIGHT,
short mid = back to CENTRE, very high = BLINK. Hold each side look until the CENTRE tone.

**Enabling gestures:** a calibrated model starts with every gesture **disabled**.
`evaluate --apply` enables only the gestures that meet every target on the separate
recording (≥ 20 trials, precision and recall ≥ 0.90, ≤ 1 false event per minute,
including the combined natural-use false-event rate, with at least 119 seconds of natural-use data). BLINK in
particular may stay disabled: one horizontal channel does not always separate deliberate
blinks from ordinary ones. `run --candidates` shows unvalidated output, clearly marked, for
debugging only.

**Share for analysis:** everything is written under `Code/acquisition/data/` (gitignored,
personal data): `recordings/*.csv` plus `*.json` metadata, `models/`, `diagnostics/`
(check reports) and `events/`. Send the relevant files directly; do not commit them.

## 6. What the code does

| Module | Responsibility |
| --- | --- |
| `protocol.py` | 36-byte Chords packet decoder (resync, partial/malformed packets, stray text) and counter → sample index with gap/stall detection |
| `device.py` | `Chords_USB` + the missing ESP32-S3 profile (16 slots, 250 Hz, 12-bit) for WHORU; ordered reader thread; START/STOP; disconnect handling |
| `processing.py` | causal 0.5–20 Hz filter (reset after gaps), health (rate, loss, clipping, flat), `Pipeline` |
| `classifier.py` | excursion detection, template matching, return-to-centre gating, refractory |
| `calibration.py` | per-user thresholds/polarity/templates; held-out scoring and gesture enabling |
| `session.py`, `recording.py`, `viewer.py`, `cli.py` | guided protocol, recordings/event logs, live plots, commands |

- **Raw values are never changed.** Recordings store channel-1 ADC counts exactly as
  received. They are not microvolts; no amplifier/ADC calibration has been done.
- **Every sample is delivered exactly once, in order.** Upstream `chordspy` keeps only a
  rolling window; this adapter hands every decoded sample to recording and processing.
  Missing samples are never invented; they appear as `gap_before` > 0, from the counter.
  A host pause over 1 s is flagged as a `stall`, because whole counter cycles could be hidden.
- **Classification:** the filtered signal must cross a calibrated onset threshold, and the
  shape over the next 0.5 s decides. A gaze step leaves a slow single-sign pulse; a blink
  swings back. Holding gaze emits nothing. The movement back to centre is recognised and
  never sent as the opposite command. If the raw signal holds a level while gaze is held, that
  level shows whether gaze is still away; the BioAmp EXG Pill v3 as tested does not (each movement
  is a ~0.1 s spike), so gaze is then tracked from the alternating spike polarity. Each excursion
  takes the polarity of its largest swing, so a blink just before a return is not mistaken for a
  movement. Nothing is classified during filter warm-up or after a data gap, or when more than
  0.16 s of the decision window is clipped (brief clipping at the tip of a big deliberate movement
  is accepted). Anything not clearly close to one template stays unclassified.
- **Hardware test results** and the handoff for the next session are in
  [hardware-tests/](hardware-tests/) (one dated folder per session).
- **Latency:** a gesture is decided about 0.5 s after it starts.
- **Known limits:** looking straight from one side to the other (without stopping at
  centre) is treated as the return, not a new command. After a data gap the gaze state is
  unknown, so the first movement back may be reported. Accuracy figures so far come from
  synthetic test data only; real performance is unknown until steps 1–7 run on hardware.

## 7. Tests

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

The tests cover:
- byte-for-byte agreement with the official `Chords_USB.read_data` decoder;
- byte order, counter rollover, fragmented and malformed input, dropped samples, stalls;
- exactly-once delivery and recording through a fake firmware port, start/stop, unplugging, wrong board and busy port;
- filter, health and pipeline;
- calibration plus held-out evaluation on synthetic sessions (both electrode polarities);
- hold-and-return and blink-hidden returns;
- disabled gestures staying silent, and suppression during clipping and gaps.

## 8. Later integration

```python
from eog_acquisition import EOGDevice, Pipeline, GestureClassifier, load_model
pipeline = Pipeline(classifier=GestureClassifier(load_model('data/models/model.json')))
pipeline.on_sample(lambda processed: ...)  # every sample, in order, exactly once
pipeline.on_gesture(lambda event: ...)     # event.gesture in LEFT / RIGHT / BLINK
device = EOGDevice('COM7'); device.open(); device.start(pipeline)
```
