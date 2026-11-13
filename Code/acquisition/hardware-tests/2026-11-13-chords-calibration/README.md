# EOG calibration from the Chords-Web recordings (2026-11-13)

## Outcome

- The earlier calibration was not usable and is replaced.
- The three new Chords-Web recordings are now the calibration. Final file:
  `Code/acquisition/models/eog-gestures.json` (JSON, like the other models).
- The dashboard is driven by it through the same path as the joystick: look left = PREVIOUS,
  look right = NEXT, double blink = SELECT. This was checked first on the plain HTML dashboard,
  and again after the React screens replaced it in `Code/frontend` later the same day (same 17
  commands, same 17 screen changes).
- Replaying a recording moves the real patient screen correctly, command for command.
- **Not done:** a run with electrodes on a person. No board was connected (only COM4 and COM5
  were present; the board is COM7). The first live session must start with the two-minute check
  in `Code/acquisition/README.md`, section 5.

## Review of the earlier calibration

`models/cycle-latest-provisional.json` (and `cycle-default.json`) were judged flawed:

- Its own audit (`hardware-tests/2026-11-12-final/`) found 34 of 40 movement windows clipping the
  ADC and too few clean looks to fit; the file is marked `PROVISIONAL_TRAINING_FIT_ONLY`.
- On the recording it was fitted to, it caught 70% of looks with 64% precision and 2.5 false
  commands a minute in free viewing.
- It gives one direction-free command, so it cannot move the highlight both ways.
- It works on raw ADC counts. The new recordings are Chords-Web's filtered, zero-centred values,
  so it can neither be checked against them nor be refitted from them.

## The new recordings

`data/recordings/Fresh Calibration Recording Through Chords/` — not modified (the model stores
each file's SHA-256, and a test compares them).

| File | Length | Counter jump | Note |
| --- | --- | --- | --- |
| 1st one.csv | 264 s | none | weakest: blinks barely visible, last cued look absent |
| 2nd one - better.csv | 240 s | 116 samples at 1.0 s | clean |
| 3rd one - better.csv | 246 s | 81 samples at 0.4 s | clean |

They contain what Chords-Web's source code does to channel 1 with the EOG filter and 50 Hz notch
on: a high-pass, a 10 Hz low-pass, scaling to ±1 for the 12-bit range, then the notch. (That
high-pass is labelled 1 Hz in Chords-Web but its coefficients are 0.1 Hz.) `chords_filter.py`
applies the same numbers to the live raw samples. Check: a resting level of 1,900 counts comes
out as 0.00147, which is the resting mean of recordings 1 and 2.

In that signal, with IN+ at the left eye, IN− at the right eye and REF behind the right ear:

- look left: one wide positive pulse, then a small negative level that stays;
- look right: the mirror image;
- look back to the centre: the opposite pulse;
- blink: a positive spike with an immediate negative rebound that dies away.

## What was built

| File | Role |
| --- | --- |
| `eog_acquisition/chords_filter.py` | Chords-Web's filter chain for live raw samples |
| `eog_acquisition/gestures.py` | the detector, its model file format and the approval check |
| `eog_acquisition/chords_calibration.py` | reads the CSVs, holds the cue sheet, scores and fits |
| `models/eog-gestures.json` | **the final calibration**, with its report inside |
| `tests/test_gestures.py`, additions to `tests/test_dashboard.py` | tests |

Changed: `cli.py` (`run` loads either model type and defaults to the new one; new
`calibrate-chords` and `replay-chords`), `dashboard.py` (eye events carry their own command),
`viewer.py` (labels), `Code/launcher.py` and `launcher-config.example.json` (new model;
`allow_provisional_eog` now off because this model passed its check), and the three READMEs.
The firmware, backend and frontend are unchanged.

A local `Code/launcher-config.json` from before still names the old model; change its `model`
line to `Code/acquisition/models/eog-gestures.json`.

## Calibration and result

Four thresholds were fitted on a grid (fewest false or wrong commands, then fewest misses, then
the middle of the tied settings): pulse height 0.5, pulse width 0.18 s, level left behind 0.04,
blink spike 0.45 (Chords-Web units; 1.0 = 2,048 counts). The other values were set from the
shapes in the recordings and are listed in the model file.

Each recording scored with thresholds fitted on the other two:

| Recording | Looks → right command | Wrong direction | Double blinks → SELECT | False commands |
| --- | --- | --- | --- | --- |
| 1st one | 9 / 10 | 0 | 2 / 8 | 0 |
| 2nd one - better | 10 / 10 | 0 | 7 / 8 | 0 |
| 3rd one - better | 10 / 10 | 0 | 5 / 8 | 0 |
| Together | 29 / 30 | 0 | 14 / 24 | 0 in 12.5 min |

- The missed look (recording 1, 3:04) is not in the signal: no movement there.
- All ten missed double blinks show one blink or none.
- Single blinks (24 cued) and natural viewing (about 2.5 min) gave no command.
- Cue timing was by hand, so a look counts if it starts up to 3 s before its cue.

## Verification

- All tests pass: 82 in `Code/acquisition/tests` (20 new), 26 in `Code/backend/tests`, 6 in
  `Code/tests`.
- Raw-sample path: synthetic Pill-shaped ADC signals through the live `Pipeline` give the
  right commands, and nothing for the look back, single blinks, small movements, quick glances,
  warm-up, data gaps or a pinned ADC.
- Dashboard: recording 2 was replayed (`replay-chords --dashboard --speed 4`) into a temporary
  backend with the real patient page open in Chrome. 17 commands sent, 0 dropped, 17 matching
  screen changes: the highlight went Water ↔ Call nurse six times, the double blinks opened the
  confirm screen and answered it, later looks moved Confirm ↔ Cancel. The same replay is an
  automated test against the backend's patient API.

## Limits

- **No live test with electrodes.** Everything above is recordings and simulation.
- One person, one afternoon, 12.5 minutes. The false-command figure rests on about 2.5 minutes
  of natural viewing.
- A large sideways eye movement that is held is a look to this detector, deliberate or not. On
  the 2026-11-12 raw sessions (other electrode orientation, saturating signal, so not a fair
  test) the same thresholds gave the right command for 34 of 40 looks in the best session, with
  up to about six false commands a minute during free viewing.
- The width threshold has little room: the widest ordinary movement was 0.16 s, the narrowest
  deliberate look 0.20 s, the rest 0.21 s or more.
- Left and right depend on the electrode orientation. Swapped IN+/IN− swaps them.
- Holding a look more than 8 s makes the look back count as the other direction.
- SELECT needs two firm blinks about half a second apart; the joystick press always works.
- Chords-Web and the dashboard cannot both hold COM7. The board needs the repository firmware
  (`Code/firmware/ESP32-S3`); `run` warns at start if it does not find it.

## Next

1. Flash the repository firmware if the upstream Chords one is still on the board.
2. Electrodes on, then `python -m eog_acquisition run --plot` and the two-minute check.
3. If that is good, `Start-EqualCare.cmd`.
4. If looks are missed or viewing triggers commands, record two or three more sequences in
   Chords-Web and rerun `calibrate-chords` with all of them.
