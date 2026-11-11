# Hardware test session — 2026-11-11

First bench session of the EOG path: **BioAmp EXG Pill v3 → GPIO 1 → ESP32-S3-DevKitC-1 (N16R8)
→ USB-to-UART (COM7, CP210x) → `Code/acquisition`**. Firmware: `Code/firmware/ESP32-S3`, built with
esp32 core 3.3.12. IN+ electrode on the **left** eye. Steps follow `Code/acquisition/README.md` §5.

## Results

| Step | Result | File |
| --- | --- | --- |
| 1. Firmware check (no electrodes) | **PASS, 9/9**: identifies as ESP32-S3, starts stopped, 249.93 Hz, 0 gaps, no stray bytes, silent after STOP | `01-firmware-check.json` |
| 2. 5-minute timing capture (no electrodes) | **PASS**: 300.02 s, 75,004 samples, 250.0 Hz, 0 missing, 0 stalls, firmware `missed=0 dropped=0` | `02-timing-capture-5min.*` |
| 3. Signal check, electrodes on (run 1, ~17:21) | Clean signal, almost no clipping. **Raw samples lost** (overwritten by run 2); measured numbers and plot kept | `03-signal-check-run1-*` |
| — Run 2 | Lost (same file name reused). Never analysed | — |
| 4. Signal check run 3 (~17:29) | 250 Hz, 0 gaps; **7.5% of samples clipped** (11 of 24 spikes hit the rails; hard blinks at the end saturate for up to 1.3 s) | `04-signal-check-run3.*` |
| 5. Signal check run 4 (~17:35) | 250 Hz, 0 gaps; **3.2% clipped** (11 of 21 spikes hit the rails, each for 0.03–0.15 s) | `05-signal-check-run4.*` |

Each signal check: ~10 s looking at a centre mark, then left/centre ×3, right/centre ×3 (about 3 s
each), then three firm blinks. Recordings are not labelled per sample (they were made with
`capture`, not `session`), so they cannot be used for calibration.

Firmware and data link are confirmed: **no firmware change is needed for timing or transport.**

## What the signal looks like (important for whoever continues)

- **Every eye movement is a sharp spike that falls back to baseline in ~0.1 s** (decay time constant
  0.06–0.19 s, median 0.11 s). The raw level does **not** stay shifted while gaze is held. The Pill
  output, as wired, behaves like it is high-pass filtered at ~1.5 Hz (AC-coupled).
- **Direction is clean:** with IN+ on the left, looking LEFT gives a **positive** spike, RIGHT a
  **negative** spike, and each return to centre gives the opposite spike.
- Spike size ~1,200–2,200 ADC counts; quiet-period noise ~30 counts (≈ 40–60× signal-to-noise).
  Baseline ~1,880–1,930 counts.
- **Blinks are also positive spikes**, ~700–1,300 counts, a bit narrower (decay ~0.07 s). Hard
  blinks saturate for much longer than movements.
- **Clipping:** big deliberate movements reach both ADC limits (0 and 4095) for 0.03–0.15 s at the
  tip. The team chose to keep large deliberate movements (so small natural eye movements stay
  below the trigger threshold) rather than reduce the amplitude.

Plots: `*-plot.png` (top: raw ADC counts; bottom: 0.5–20 Hz filtered; x-axis seconds).

## Code changes made because of this data (in `Code/acquisition/eog_acquisition/classifier.py`)

1. **AC-coupled gating.** The return-to-centre logic no longer assumes the raw level is held. It
   checks whether the signal holds a level; if not (this Pill), it tracks gaze from the alternating
   spike polarity, and any opposite spike at least 60% the size of the movement counts as the return.
2. **Direction from the largest swing** in the 0.5 s decision window, not the first crossing, so a
   blink just before a return is not mistaken for a small movement the other way.
3. **Brief tip clipping is tolerated:** an excursion is still classified if at most 0.16 s of its
   window is clipped (movements here: 0.03–0.15 s). Longer saturation (hard blinks, loose electrode)
   is still ignored.

Synthetic data shaped like this Pill (`tests/synthetic.py`, `PILL_V3`) now gives LEFT/RIGHT
precision and recall of 1.0 on 5 of 5 calibration/evaluation pairs. That is synthetic only:
**real accuracy is still unknown.** Tests: `pytest -q tests` → 34 passed.

## Next steps (continue from here)

Same hardware, same electrode placement (IN+ left), marks as used today (large deliberate movements).
From `Code/acquisition`:

1. `.venv\Scripts\python.exe -m eog_acquisition session --set calibration --in-plus left`
   (≈ 8 min, follow the tones; laptop on battery)
2. `.venv\Scripts\python.exe -m eog_acquisition calibrate data\recordings\calibration-<time>.csv`
3. 5-minute break, electrodes on, then
   `.venv\Scripts\python.exe -m eog_acquisition session --set evaluation --in-plus left`
4. `.venv\Scripts\python.exe -m eog_acquisition evaluate data\recordings\evaluation-<time>.csv --model data\models\model-<time>.json --apply`
   — targets: precision and recall ≥ 0.90 per gesture, ≤ 1 false event per minute.
5. `.venv\Scripts\python.exe -m eog_acquisition run --model data\models\model-<time>.json --plot`

Things to watch:
- `capture` without `--out` names files by time; reusing one `--out` name overwrites (that is how
  run 2 was lost).
- If calibration warns that LEFT/RIGHT polarity is inconsistent, or many movements are reported as
  "clipped longer than 0.16 s", the amplitude is too large for this placement: check whether the
  Pill v3 gain can be lowered, or measure whether the clipping is at the Pill output or the ESP32
  input (~3.1 V at 11 dB attenuation).
- BLINK may stay disabled after evaluation; that is an acceptable outcome.
- Copy the new session recordings, model and evaluation report into a new dated folder here
  (`hardware-tests/<date>/`) when they should be shared; `data/` itself is gitignored.

## File formats

- `*.csv` recordings: `sample_index, counter, raw, host_time, gap_before, stall, label, trial`.
  `raw` = channel-1 ADC counts, unchanged (not microvolts). Replay with `load_recording()`.
- `*.json` next to each CSV: board, port, sample rate, `in_plus_electrode`, start/stop time (UTC),
  chordspy version, final health summary and firmware `STATS`.
- `01-firmware-check.json`: every check with PASS/FAIL, health, bytes seen while streaming.
