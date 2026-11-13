"""Fit and check the gesture detector on Chords-Web recordings of the team's calibration sequence.

A recording is the CSV Chords-Web exports (`Counter,Channel1`) with the EOG filter and the 50 Hz
notch switched on for channel 1: already-filtered values, 250 per second, no time column and no
labels. The labels come from the cue sheet the person followed by the clock (SEQUENCE below,
copied from `Calibration Sequence.xlsx` next to the recordings), so they are only as exact as
that person's timing: a look counts for its cue if it starts up to LOOK_EARLY seconds before the
cue or any time until just after its hold ends.

Recordings are only read.
"""
import csv
import hashlib
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path

import numpy as np

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
LOOK_EARLY, LOOK_LATE = 3.0, 0.5    # s a look may start before its cue / be confirmed after its hold
BLINK_EARLY = 1.0                   # s a double blink may start before its cue (its window ends at the next cue)
# What "works" means here, checked on recordings the thresholds were NOT fitted on:
CRITERIA = dict(look_recall=0.9, look_precision=0.9, false_commands_per_minute=1.0, false_selects=0)
GRID = dict(look_peak=(0.45, 0.5, 0.55, 0.6), look_width=(0.16, 0.18, 0.2, 0.22), tail=(0.0, 0.04, 0.08),
            blink_peak=(0.35, 0.45, 0.55))


@dataclass
class ChordsRecording:
    name: str
    values: np.ndarray      # Chords-Web values, as saved
    index: np.ndarray       # sample number since the first row, counting the samples a counter jump skipped
    gap_before: np.ndarray  # samples missing immediately before each row
    sha256: str
    sample_rate: int = 250

    @property
    def seconds(self):
        return (int(self.index[-1]) + 1) / self.sample_rate


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


def detect(recording, model):
    """Gesture events for a whole recording, through the same streaming code as live use."""
    detector, events = GestureDetector(model), []
    for value, index, gap in zip(recording.values, recording.index, recording.gap_before):
        if gap:
            detector.reset()
        events += detector.step_filtered(float(value), int(index), index / recording.sample_rate)
    return events


def score(recording, events):
    """Each cue against the commands that came out; every command no cue asked for is false."""
    fs, used = recording.sample_rate, set()
    result = dict(looks=0, look_hits=0, wrong_direction=0, missed_looks=[], double_blinks=0, select_hits=0,
                  missed_double_blinks=[], false_moves=0, false_selects=0, false_by_cue={})
    cues = [c for c in SEQUENCE if c[0] < recording.seconds]

    def first(kinds, start, end):
        for i, event in enumerate(events):
            if i not in used and event.direction in kinds and start <= event.start_index / fs < end:
                return i, event
        return None, None

    for position, (start, end, cue) in enumerate(cues):
        if cue in ('LEFT', 'RIGHT'):
            result['looks'] += 1
            i, event = first(('LEFT', 'RIGHT'), start - LOOK_EARLY, end + LOOK_LATE)
            if event is None:
                result['missed_looks'].append(start)
            else:
                used.add(i)
                result['look_hits' if event.direction == cue else 'wrong_direction'] += 1
        elif cue == 'DOUBLE_BLINK':
            result['double_blinks'] += 1
            later = cues[position + 1][2] == 'DOUBLE_BLINK' if position + 1 < len(cues) else False
            i, event = first(('DOUBLE_BLINK',), start - BLINK_EARLY, end - (BLINK_EARLY if later else 0))
            if event is None:
                result['missed_double_blinks'].append(start)
            else:
                used.add(i)
                result['select_hits'] += 1
    for i, event in enumerate(events):
        if i in used:
            continue
        at = event.start_index / fs
        cue = next((c for s, e, c in cues if s <= at < e), 'REST')
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


def fit(recordings, base=None):
    """The grid setting with the fewest wrong or false commands, then the fewest misses.

    Usually many settings tie; the one in the middle of the tied block is taken, not one at its
    edge, so that a slightly different session does not fall off it (the stricter one if two are
    equally central).
    """
    base = base or GestureModel()
    names = list(GRID)
    results = []
    for values in product(*GRID.values()):
        model = replace(base, **dict(zip(names, values)))
        t = total([score(r, detect(r, model)) for r in recordings])
        false = t['false_moves'] + t['false_selects'] + t['wrong_direction']
        misses = (t['looks'] - t['look_hits'] - t['wrong_direction']) + (t['double_blinks'] - t['select_hits'])
        results.append(((false, misses), values))
    best = min(cost for cost, _ in results)
    tied = np.array(sorted((values for cost, values in results if cost == best), reverse=True))
    centre = tied.mean(axis=0)
    spread = np.ptp(np.array([v for _, v in results]), axis=0)
    spread[spread == 0] = 1                              # a threshold with one grid value: nothing to centre
    chosen = tied[np.argmin((((tied - centre) / spread) ** 2).sum(axis=1))]
    return replace(base, **{name: float(v) for name, v in zip(names, chosen)}), dict(tied_settings=len(tied))


def calibrate(recordings, base=None):
    """Fit on all recordings; check by fitting without each recording in turn and scoring it.

    Returns (GestureModel with its validation, report).
    """
    if len(recordings) < 2:
        raise ValueError('Calibration needs at least two recordings: each is checked with a fit made without it.')
    held_out = []
    for i, recording in enumerate(recordings):
        model, _ = fit(recordings[:i] + recordings[i + 1:], base)
        s = score(recording, detect(recording, model))
        held_out.append(dict(recording=recording.name, fitted_without_it={k: getattr(model, k) for k in GRID}, **s))
    model, info = fit(recordings, base)
    checked = total(held_out)
    in_sample = [dict(recording=r.name, **score(r, detect(r, model))) for r in recordings]
    model = replace(model, validation=None)
    validation = dict(passed=passes(checked), method='leave-one-recording-out', criteria=CRITERIA,
                      model_id=model_id(model), metrics=checked)
    report = dict(
        sources=[dict(file=r.name + '.csv', sha256=r.sha256, seconds=round(r.seconds, 1),
                      samples_missing=int(r.gap_before.sum())) for r in recordings],
        signal='Chords-Web channel 1: its high-pass (0.1 Hz), EOG 10 Hz low-pass, 50 Hz notch; 250 Hz, 12-bit',
        electrodes='IN+ left eye outer corner, IN- right eye outer corner, REF behind the right ear',
        cue_sheet='Calibration Sequence.xlsx', look_window_seconds=[-LOOK_EARLY, LOOK_LATE],
        grid={k: list(v) for k, v in GRID.items()}, **info,
        held_out=held_out, held_out_total=checked, in_sample=in_sample, in_sample_total=total(in_sample))
    return replace(model, validation=validation), report
