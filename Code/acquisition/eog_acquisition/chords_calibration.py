"""Fit and check the gesture detector on recordings of prompted looks and blinks.

Two kinds of recording are accepted:

* Chords-Web exports (`Counter,Channel1`) of the team's calibration sequence, with the EOG
  filter and the 50 Hz notch switched on: already-filtered values, no labels. The labels come
  from the cue sheet the person followed by the clock (SEQUENCE, copied from
  `Calibration Sequence.xlsx`), so a look counts for its cue if it starts up to LOOK_EARLY
  seconds before the cue or any time until just after its hold ends.
* This package's own recordings (`sample_index,...,raw,...,label,trial`, from `look-test`,
  `blink-test` or `session`, over USB or Bluetooth): raw ADC counts with the prompt shown at
  each sample. They go through the same Chords-Web filter as live use, and since the prompts
  were timed by the program, the windows are tight (PROMPTED).

Recordings are only read.
"""
import csv
import hashlib
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

import numpy as np

from .chords_filter import ChordsWebFilter
from .gestures import GestureDetector, GestureModel, model_id

# (start s, end s, cue). Everything else is rest at the centre: no command expected.
SEQUENCE = (
    (30, 34, 'LEFT'), (38, 42, 'RIGHT'), (46, 50, 'LEFT'), (54, 58, 'RIGHT'), (62, 66, 'LEFT'), (70, 74, 'RIGHT'),
    (80, 105, 'BLINKS'),            # five single blinks, one every 5 s: no command
    (105, 110, 'DOUBLE_BLINK'), (110, 115, 'DOUBLE_BLINK'), (115, 120, 'DOUBLE_BLINK'), (120, 125, 'DOUBLE_BLINK'),
    (125, 130, 'DOUBLE_BLINK'),
    (130, 160, 'NATURAL'),          # natural viewing: no command
    (160, 164, 'LEFT'), (168, 172, 'RIGHT'), (176, 180, 'LEFT'), (184, 188, 'RIGHT'),
    (192, 207, 'BLINKS'),           # three single blinks
    (207, 212, 'DOUBLE_BLINK'), (212, 217, 'DOUBLE_BLINK'), (217, 222, 'DOUBLE_BLINK'),
    (222, 240, 'NATURAL'),
)
LOOK_EARLY, LOOK_LATE = 3.0, 0.5    # Chords sheet: s a look may start before its cue / be confirmed after its hold
BLINK_EARLY = 1.0                   # Chords sheet: s a double blink may start before its cue
# Prompted recordings: (s before the prompt, s after it ends) in which the answer may start
PROMPTED = dict(LEFT=(0.5, 0.5), RIGHT=(0.5, 0.5), DOUBLE_BLINK=(BLINK_EARLY, 0.6))
# What "works" means here, checked on recordings the thresholds were NOT fitted on:
CRITERIA = dict(look_recall=0.9, look_precision=0.9, false_commands_per_minute=1.0, false_selects=0)
# Looks are fitted first (with the blink settings as they are), then the blinks.
LOOK_GRID = dict(look_peak=(0.45, 0.5, 0.55, 0.6), look_width=(0.16, 0.18, 0.2, 0.22), tail=(0.0, 0.04, 0.08))
BLINK_GRID = dict(blink_rise=(0.35, 0.4, 0.5), blink_fall_within=(0.35, 0.4, 0.5))
FOLDS = 4                           # held-out check: each recording is scored by a fit made without its fold


@dataclass
class ChordsRecording:
    name: str
    values: np.ndarray      # Chords-Web values, as saved
    index: np.ndarray       # sample number since the first row, counting the samples a counter jump skipped
    gap_before: np.ndarray  # samples missing immediately before each row
    sha256: str
    sample_rate: int = 250
    clipped: np.ndarray = None   # not known for Chords exports

    @property
    def seconds(self):
        return (int(self.index[-1]) + 1) / self.sample_rate

    def windows(self):
        """(start s, end s, cue, answer from s, answer until s) for every cue on the sheet."""
        cues = [c for c in SEQUENCE if c[0] < self.seconds]
        out = []
        for position, (start, end, cue) in enumerate(cues):
            if cue in ('LEFT', 'RIGHT'):
                out.append((start, end, cue, start - LOOK_EARLY, end + LOOK_LATE))
            elif cue == 'DOUBLE_BLINK':
                later = position + 1 < len(cues) and cues[position + 1][2] == 'DOUBLE_BLINK'
                out.append((start, end, cue, start - BLINK_EARLY, end - (BLINK_EARLY if later else 0)))
            else:
                out.append((start, end, cue, start, end))
        return out


@dataclass
class PromptedRecording:
    """One of this package's recordings: raw counts plus the prompt label at every sample."""
    name: str
    values: np.ndarray      # raw counts through the Chords-Web filter (restarted at every gap)
    index: np.ndarray
    gap_before: np.ndarray  # 1 where the data has a gap or stall (the detector restarts there)
    clipped: np.ndarray
    labels: list
    trials: np.ndarray
    sha256: str
    sample_rate: int = 250

    @property
    def seconds(self):
        return len(self.values) / self.sample_rate

    def windows(self):
        fs, out, i, n = self.sample_rate, [], 0, len(self.labels)
        while i < n:
            label, trial, j = self.labels[i], self.trials[i], i
            while j < n and self.labels[j] == label and self.trials[j] == trial:
                j += 1
            start, end = i / fs, j / fs
            if label in PROMPTED and trial:
                early, late = PROMPTED[label]
                out.append((start, end, label, start - early, end + late))
            elif label:
                cue = {'BLINK': 'BLINKS', 'NATURAL': 'NATURAL'}.get(label, 'REST')
                out.append((start, end, cue, start, end))
            i = j
        return out


def load_chords_csv(path):
    path = Path(path)
    with open(path, newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    if not rows or 'Counter' not in rows[0] or 'Channel1' not in rows[0]:
        raise ValueError(f'{path.name}: not a Chords-Web recording (expected the columns Counter and Channel1).')
    counter = np.array([int(r['Counter']) for r in rows])
    gap = np.concatenate([[0], (np.diff(counter) - 1) % 256])
    return ChordsRecording(path.stem, np.array([float(r['Channel1']) for r in rows]),
                           np.arange(len(rows)) + np.cumsum(gap), gap, hashlib.sha256(path.read_bytes()).hexdigest())


def load_prompted(path):
    from .recording import load_recording
    path = Path(path).with_suffix('.csv')
    r = load_recording(path)
    if not len(r.raw):
        raise ValueError(f'{path.name}: the recording is empty.')
    breaks = (r.gap_before > 0) | (r.stall > 0)
    chain, values = ChordsWebFilter(), np.empty(len(r.raw))
    for i, raw in enumerate(r.raw):
        if breaks[i]:
            chain.reset()
        values[i] = chain.process(int(raw))
    clipped = (r.raw <= 8) | (r.raw >= 4087)
    return PromptedRecording(path.stem, values, r.index, breaks.astype(int), clipped, list(r.label), r.trial,
                             hashlib.sha256(path.read_bytes()).hexdigest(), r.sample_rate)


def load_any(path):
    """A Chords-Web export or one of this package's recordings, by its columns."""
    path = Path(path)
    with open(path, newline='', encoding='utf-8-sig') as handle:
        header = handle.readline()
    return load_chords_csv(path) if header.startswith('Counter') else load_prompted(path)


def detect(recording, model):
    """Gesture events for a whole recording, through the same streaming code as live use."""
    detector, events = GestureDetector(model), []
    clipped = recording.clipped
    for k, (value, index, gap) in enumerate(zip(recording.values, recording.index, recording.gap_before)):
        if gap:
            detector.reset()
        events += detector.step_filtered(float(value), int(index), index / recording.sample_rate,
                                         bool(clipped[k]) if clipped is not None else False)
    return events


def score(recording, events):
    """Each prompt against the commands that came out; every command no prompt asked for is false."""
    fs, used = recording.sample_rate, set()
    result = dict(looks=0, look_hits=0, wrong_direction=0, missed_looks=[], double_blinks=0, select_hits=0,
                  missed_double_blinks=[], false_moves=0, false_selects=0, false_by_cue={})
    windows = recording.windows()
    # Sample positions, not device indices: a Chords export's index skips the samples a gap lost.
    position = {int(v): k for k, v in enumerate(recording.index)} if isinstance(recording, PromptedRecording) else None
    at = (lambda e: position.get(e.start_index, 0) / fs) if position else (lambda e: e.start_index / fs)

    def first(kinds, start, end):
        for i, event in enumerate(events):
            if i not in used and event.direction in kinds and start <= at(event) < end:
                return i, event
        return None, None

    for start, end, cue, answer_from, answer_to in windows:
        if cue in ('LEFT', 'RIGHT'):
            result['looks'] += 1
            i, event = first(('LEFT', 'RIGHT'), answer_from, answer_to)
            if event is None:
                result['missed_looks'].append(round(start, 2))
            else:
                used.add(i)
                result['look_hits' if event.direction == cue else 'wrong_direction'] += 1
        elif cue == 'DOUBLE_BLINK':
            result['double_blinks'] += 1
            i, event = first(('DOUBLE_BLINK',), answer_from, answer_to)
            if event is None:
                result['missed_double_blinks'].append(round(start, 2))
            else:
                used.add(i)
                result['select_hits'] += 1
    for i, event in enumerate(events):
        if i in used:
            continue
        t = at(event)
        cue = next((c for s, e, c, _, _ in windows if s <= t < e), 'REST')
        result['false_selects' if event.direction == 'DOUBLE_BLINK' else 'false_moves'] += 1
        result['false_by_cue'][cue] = result['false_by_cue'].get(cue, 0) + 1
    result['minutes'] = round(recording.seconds / 60, 2)
    return result


def total(scores):
    """Add up per-recording scores and derive the rates."""
    keys = ('looks', 'look_hits', 'wrong_direction', 'double_blinks', 'select_hits', 'false_moves', 'false_selects')
    t = {k: sum(s[k] for s in scores) for k in keys}
    minutes = sum(s['minutes'] for s in scores)
    false = t['false_moves'] + t['false_selects'] + t['wrong_direction']
    moves = t['look_hits'] + t['wrong_direction'] + t['false_moves']
    t.update(minutes=round(minutes, 2),
             look_recall=round(t['look_hits'] / t['looks'], 3) if t['looks'] else None,
             look_precision=round(t['look_hits'] / moves, 3) if moves else None,
             select_recall=round(t['select_hits'] / t['double_blinks'], 3) if t['double_blinks'] else None,
             false_commands_per_minute=round(false / minutes, 2) if minutes else None)
    return t


def passes(t):
    return bool(t['looks'] and t['look_recall'] >= CRITERIA['look_recall']
                and t['look_precision'] is not None and t['look_precision'] >= CRITERIA['look_precision']
                and t['false_commands_per_minute'] <= CRITERIA['false_commands_per_minute']
                and t['false_selects'] <= CRITERIA['false_selects'])


def _fit_stage(recordings, base, grid, cost):
    """The grid setting with the lowest cost; of the tied ones, the one in the middle of the block
    (the stricter one if two are equally central), so a slightly different session does not fall off it."""
    names, results = list(grid), []
    for values in product(*grid.values()):
        t = total([score(r, detect(r, replace(base, **dict(zip(names, values))))) for r in recordings])
        results.append((cost(t), values))
    best = min(c for c, _ in results)
    tied = np.array(sorted((v for c, v in results if c == best), reverse=True))
    spread = np.ptp(np.array([v for _, v in results]), axis=0)
    spread[spread == 0] = 1                              # a threshold with one grid value: nothing to centre
    chosen = tied[np.argmin((((tied - tied.mean(axis=0)) / spread) ** 2).sum(axis=1))]
    return replace(base, **{name: float(v) for name, v in zip(names, chosen)}), len(tied)


def fit(recordings, base=None):
    """Looks first (fewest false or wrong moves, then fewest missed looks), then double blinks
    (fewest false SELECTs, then fewest missed ones)."""
    base = base or GestureModel()
    model, tied_looks = _fit_stage(recordings, base, LOOK_GRID, lambda t: (
        t['false_moves'] + t['wrong_direction'], t['looks'] - t['look_hits'] - t['wrong_direction']))
    model, tied_blinks = _fit_stage(recordings, model, BLINK_GRID, lambda t: (
        t['false_selects'], t['double_blinks'] - t['select_hits']))
    return model, dict(tied_look_settings=tied_looks, tied_blink_settings=tied_blinks)


def calibrate(recordings, base=None):
    """Fit on all recordings; check each one with a fit made without its fold (up to FOLDS folds).

    Returns (GestureModel with its validation, report).
    """
    if len(recordings) < 2:
        raise ValueError('Calibration needs at least two recordings: each is checked with a fit made without it.')
    folds = min(FOLDS, len(recordings))
    held_out = []
    for fold in range(folds):
        train = [r for i, r in enumerate(recordings) if i % folds != fold]
        model, _ = fit(train, base)
        for i, recording in enumerate(recordings):
            if i % folds == fold:
                s = score(recording, detect(recording, model))
                held_out.append((i, dict(recording=recording.name, fitted_without_it={
                    k: getattr(model, k) for k in (*LOOK_GRID, *BLINK_GRID)}, **s)))
    held_out = [h for _, h in sorted(held_out, key=lambda pair: pair[0])]
    model, info = fit(recordings, base)
    checked = total(held_out)
    in_sample = [dict(recording=r.name, **score(r, detect(r, model))) for r in recordings]
    model = replace(model, validation=None)
    validation = dict(passed=passes(checked), method=f'{folds}-fold, held out by recording', criteria=CRITERIA,
                      model_id=model_id(model), metrics=checked)
    report = dict(
        sources=[dict(file=r.name + '.csv', kind='chords-web' if isinstance(r, ChordsRecording) else 'prompted',
                      sha256=r.sha256, seconds=round(r.seconds, 1), samples_missing=int(np.sum(r.gap_before)))
                 for r in recordings],
        signal='Chords-Web channel 1: its high-pass (0.1 Hz), EOG 10 Hz low-pass, 50 Hz notch; 250 Hz, 12-bit',
        electrodes='IN+ left eye outer corner, IN- right eye outer corner, REF behind the right ear',
        cue_sheet='Calibration Sequence.xlsx', look_window_seconds=[-LOOK_EARLY, LOOK_LATE], prompted_windows=PROMPTED,
        grid={k: list(v) for k, v in {**LOOK_GRID, **BLINK_GRID}.items()}, **info,
        held_out=held_out, held_out_total=checked, in_sample=in_sample, in_sample_total=total(in_sample))
    return replace(model, validation=validation), report
