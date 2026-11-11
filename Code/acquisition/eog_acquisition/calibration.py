"""Per-user calibration from a labeled recording, and held-out evaluation.

Recordings come from the guided session (session.py), whose labels are:
  NEUTRAL  look at the centre mark         LEFT / RIGHT  look at the side mark and hold
  CENTER   look back at the centre mark    BLINK         one deliberate blink
  REST     relax between trials            NATURAL       normal use, ordinary blinks
Calibration derives the noise level, onset threshold, LEFT/RIGHT polarity and one
feature template per gesture (plus ordinary blinks, which are matched but never
emitted). Evaluation replays a different recording and decides which gestures may
be enabled; a gesture that misses the targets stays disabled.
"""
import numpy as np

from .classifier import (FEATURE_FLOOR, FEATURES, GESTURES, REFRACTORY_SECONDS, REJECT,
                         ExcursionDetector, GestureClassifier)
from .processing import Pipeline
from .recording import replay, utc_now

CUE_WINDOW_SECONDS = 1.5    # an intended gesture must start within this long after its cue
SCORE_WINDOW_SECONDS = 2.0  # evaluation: an emitted gesture counts for the trial whose cue preceded it by <= this
NOISE_MULTIPLIER = 6.0      # onset is at least this many times the neutral noise
ONSET_FRACTION = 0.35       # ...and about a third of the smallest intended gestures
MIN_TEMPLATE_EVENTS = 5
TARGETS = dict(precision=0.90, recall=0.90, false_per_minute=1.0, min_trials=20)


class CalibrationError(ValueError):
    pass


def _processed(recording):
    pipeline = Pipeline(recording.sample_rate)
    out = []
    pipeline.on_sample(out.append)
    replay(recording, pipeline)
    return out, pipeline.health.snapshot()


def _segment(processed, onset, release, sample_rate):
    detector = ExcursionDetector(onset, release, sample_rate)
    excursions = []
    for p in processed:
        if p.sample.gap_before or p.sample.stall:
            detector.reset()
        excursion = detector.update(p)
        if excursion:
            excursions.append(excursion)
            detector.rearm_on_opposite = excursion.movement_like
    return excursions


def _cues(processed):
    """trial -> (gesture, cue sample index) for every LEFT / RIGHT / BLINK trial."""
    cues = {}
    for p in processed:
        if p.trial and p.label in GESTURES and p.trial not in cues:
            cues[p.trial] = (p.label, p.sample.index)
    return cues


def _label(excursions, processed, sample_rate):
    tags = {p.sample.index: (p.label, p.trial) for p in processed}
    cues = _cues(processed)
    window = CUE_WINDOW_SECONDS * sample_rate
    best = {}  # trial -> largest valid excursion starting soon after its cue
    labeled = {name: [] for name in (*GESTURES, REJECT)}
    for ex in excursions:
        if not ex.valid:
            continue
        label, trial = tags.get(ex.start_index, ('', 0))
        if label in GESTURES and trial in cues and ex.start_index - cues[trial][1] <= window:
            if trial not in best or ex.peak > best[trial].peak:
                best[trial] = ex
        elif label in ('NEUTRAL', 'NATURAL') and not ex.movement_like:
            labeled[REJECT].append(ex)  # ordinary blinks
    for trial, ex in best.items():
        labeled[cues[trial][0]].append(ex)
    return labeled, cues


def _share(signs, sign):
    return sum(1 for s in signs if s == sign) / len(signs) if signs else 0.0


def calibrate(recording, source=''):
    """Returns (model, report). Raises CalibrationError when the data cannot support a model."""
    fs = recording.sample_rate
    processed, health = _processed(recording)
    neutral = np.array([p.filtered for p in processed if p.label == 'NEUTRAL' and p.usable])
    if len(neutral) < 10 * fs:
        raise CalibrationError('Need at least 10 s of usable NEUTRAL data (no clipping or gaps).')
    noise = max(1.4826 * float(np.median(np.abs(neutral - np.median(neutral)))), 0.5)
    onset = NOISE_MULTIPLIER * noise
    warnings = []

    labeled, _ = _label(_segment(processed, onset, onset / 2, fs), processed, fs)
    intended = [ex.peak for g in GESTURES for ex in labeled[g]]
    if len(intended) < MIN_TEMPLATE_EVENTS:
        raise CalibrationError(f'Only {len(intended)} gesture responses rose above the noise. Check electrode '
                               'contact and that the movements were made after each cue.')
    smallest = float(np.percentile(intended, 10))
    if ONSET_FRACTION * smallest > onset:
        onset = ONSET_FRACTION * smallest
    else:
        warnings.append(f'Smaller gestures are only {smallest / noise:.1f}x the noise level; expect poor accuracy.')
    release = onset / 2
    labeled, cues = _label(_segment(processed, onset, release, fs), processed, fs)

    left_signs = [ex.sign for ex in labeled['LEFT']]
    right_signs = [ex.sign for ex in labeled['RIGHT']]
    if len(left_signs) < MIN_TEMPLATE_EVENTS or len(right_signs) < MIN_TEMPLATE_EVENTS:
        raise CalibrationError('At least 5 detected LEFT and 5 detected RIGHT movements are required.')
    left_sign = 1 if sum(left_signs) > 0 else -1
    if _share(left_signs, left_sign) < 0.8 or _share(right_signs, -left_sign) < 0.8:
        raise CalibrationError('LEFT and RIGHT movements do not have consistent opposite polarities. '
                               'Check the electrode placement (outer eye corners) and the reference.')

    templates = {}
    for name in (*GESTURES, REJECT):
        events = labeled[name]
        if len(events) < MIN_TEMPLATE_EVENTS:
            warnings.append(f'{name}: only {len(events)} usable examples, so it is not modelled.')
            continue
        features = np.array([ex.features for ex in events])
        signs = [ex.sign for ex in events]
        majority = 1 if sum(signs) >= 0 else -1
        sign = {'LEFT': left_sign, 'RIGHT': -left_sign}.get(name, majority if _share(signs, majority) >= 0.8 else 0)
        templates[name] = dict(sign=sign, count=len(events), mean=np.round(features.mean(0), 4).tolist(),
                               std=np.round(np.maximum(features.std(0, ddof=1), FEATURE_FLOOR), 4).tolist())

    model = dict(format='equalcare-eog-model/1', created_utc=utc_now(), calibration_recording=source,
                 calibration_started_utc=recording.meta.get('started_utc'),
                 in_plus_electrode=recording.meta.get('in_plus_electrode', 'unknown'),
                 sample_rate=fs, units='filtered ADC counts', noise=round(noise, 3), onset=round(onset, 3),
                 release=round(release, 3), left_sign=left_sign, features=list(FEATURES), templates=templates,
                 refractory_seconds=REFRACTORY_SECONDS, enabled={g: False for g in GESTURES}, evaluation=None)
    trials = {g: sum(1 for cue in cues.values() if cue[0] == g) for g in GESTURES}
    report = dict(noise=model['noise'], onset=model['onset'], left_sign=left_sign, trials=trials,
                  detected={name: len(labeled[name]) for name in labeled}, warnings=warnings, health=health,
                  in_sample=evaluate(recording, model)['gestures'])
    return model, report


def evaluate(recording, model, targets=TARGETS):
    """Score a held-out labeled recording. Every candidate gesture is emitted during scoring."""
    fs = model['sample_rate']
    pipeline = Pipeline(fs, classifier=GestureClassifier(model, emit_candidates=True))
    events, tags = [], {}
    pipeline.on_gesture(events.append)
    pipeline.on_sample(lambda p: tags.__setitem__(p.sample.index, (p.label, p.trial)))
    replay(recording, pipeline)

    cues = {}
    for index, (label, trial) in tags.items():
        if trial and label in GESTURES and trial not in cues:
            cues[trial] = (label, index)
    window = SCORE_WINDOW_SECONDS * fs
    decided = set()
    hits = {g: 0 for g in GESTURES}
    natural_false = 0
    for event in events:
        label, trial = tags.get(event.start_index, ('', 0))
        cue = cues.get(trial)
        if label in GESTURES and cue and trial not in decided and event.start_index - cue[1] <= window:
            decided.add(trial)  # the first gesture after a cue decides that trial
            hits[event.gesture] += event.gesture == cue[0]
        elif label == 'NATURAL':
            natural_false += 1

    indexes = sorted(tags)
    minutes = (indexes[-1] - indexes[0] + 1) / fs / 60 if indexes else 0.0
    natural_minutes = sum(1 for label, _ in tags.values() if label == 'NATURAL') / fs / 60
    gestures = {}
    for g in GESTURES:
        trials = sum(1 for cue in cues.values() if cue[0] == g)
        emitted = sum(1 for e in events if e.gesture == g)
        false = emitted - hits[g]
        precision = hits[g] / emitted if emitted else None
        recall = hits[g] / trials if trials else None
        false_rate = false / minutes if minutes else None
        passed = (g in model['templates'] and trials >= targets['min_trials'] and precision is not None
                  and precision >= targets['precision'] and recall >= targets['recall']
                  and false_rate <= targets['false_per_minute'])
        gestures[g] = dict(trials=trials, emitted=emitted, correct=hits[g], false_events=false,
                           precision=None if precision is None else round(precision, 3),
                           recall=None if recall is None else round(recall, 3),
                           false_per_minute=None if false_rate is None else round(false_rate, 3),
                           modelled=g in model['templates'], passed=bool(passed))
    total_false = sum(v['false_events'] for v in gestures.values())
    return dict(recording_started_utc=recording.meta.get('started_utc'), minutes=round(minutes, 2),
                targets=targets, gestures=gestures,
                false_per_minute=round(total_false / minutes, 3) if minutes else None,
                natural_minutes=round(natural_minutes, 2),
                natural_false_per_minute=round(natural_false / natural_minutes, 3) if natural_minutes else None,
                enable=[g for g in GESTURES if gestures[g]['passed']])


def apply_evaluation(model, report):
    """Enable exactly the gestures that met every target on held-out data."""
    updated = dict(model)
    updated['enabled'] = {g: g in report['enable'] for g in GESTURES}
    updated['evaluation'] = dict(evaluated_utc=utc_now(), recording_started_utc=report['recording_started_utc'],
                                 minutes=report['minutes'], false_per_minute=report['false_per_minute'],
                                 gestures=report['gestures'])
    return updated


def held_out(model, recording):
    """False when the evaluation recording is the one the model was calibrated on."""
    started = recording.meta.get('started_utc')
    return not started or started != model.get('calibration_started_utc')
