import numpy as np
import pytest

from eog_acquisition.calibration import CalibrationError, apply_evaluation, calibrate, evaluate, held_out
from eog_acquisition.classifier import GestureClassifier
from eog_acquisition.processing import EOGFilter, HealthMonitor, Pipeline
from eog_acquisition.protocol import Sample
from eog_acquisition.recording import Recorder, Recording, load_recording, replay
from synthetic import FS, PILL_V3, amplifier_highpass, synthetic_recording


def recording_from(raw, labels=None, gaps=()):
    n = len(raw)
    gap = np.array([1 if i in gaps else 0 for i in range(n)])
    index = np.arange(n) + np.cumsum(gap)
    return Recording(dict(sample_rate=FS, started_utc='test'), index, index % 256, np.asarray(raw, dtype=int),
                     1000 + index / FS, gap, np.zeros(n, dtype=int), labels or [''] * n, np.zeros(n, dtype=int))


def gaze(seconds_and_levels, noise=3.0, seed=0, blinks=()):
    """Piecewise gaze levels [(seconds, level)] with 40 ms saccades, plus blinks [(time s, amplitude, duration s)]."""
    rng = np.random.default_rng(seed)
    levels = np.concatenate([np.full(int(s * FS), level, dtype=float) for s, level in seconds_and_levels])
    smooth = np.convolve(np.pad(levels, (9, 0), mode='edge'), np.ones(10) / 10, 'valid')
    signal = 2048 + smooth + rng.normal(0, noise, len(levels))
    for at, amp, duration in blinks:
        n, start = int(duration * FS), int(at * FS)
        signal[start:start + n] += amp * (1 - np.cos(np.linspace(0, 2 * np.pi, n))) / 2
    return np.round(signal).astype(int)


def events_for(model, raw, gaps=(), candidates=True):
    pipeline = Pipeline(FS, classifier=GestureClassifier(model, emit_candidates=candidates))
    events = []
    pipeline.on_gesture(events.append)
    replay(recording_from(raw, gaps=set(gaps)), pipeline)
    return [e.gesture for e in events]


def left_level(model):
    return 300 * model['left_sign']


# ---- filter, health, pipeline ----------------------------------------------------------
def test_filter_rejects_dc_and_warms_up_after_reset():
    f = EOGFilter()
    out = [f.process(3000) for _ in range(FS * 2)]
    assert max(abs(v) for v in out) < 1e-6 and f.ready
    f.reset()
    assert not f.ready
    for _ in range(f.warmup_samples):
        f.process(1000)
    assert f.ready


def test_gaze_step_gives_one_signed_pulse_without_undershoot():
    f = EOGFilter()
    out = np.array([f.process(v) for v in [2000] * 500 + [2300] * 1000])
    assert out.max() > 250 and out.min() > -5  # first-order high-pass: no opposite swing


def test_health_reports_rate_loss_clipping_and_flat():
    health = HealthMonitor()
    for i in range(FS * 3):
        index = i if i < 400 else i + 2
        health.update(Sample(index, 0, 4095 if i == 700 else 2048 + i % 7, 100 + index / FS, 2 if i == 400 else 0))
    snap = health.snapshot()
    assert snap['missing'] == 2 and snap['clipped_samples'] == 1 and not snap['flat_now']
    assert abs(snap['device_rate'] - FS) < 1
    for i in range(FS * 3, FS * 6):
        health.update(Sample(i + 2, 0, 2048, 100 + (i + 2) / FS, 0))
    assert health.snapshot()['flat_now'] and not health.snapshot()['clipping_now']


def test_pipeline_rejects_duplicates_and_resets_filter_on_gaps():
    pipeline = Pipeline()
    out = [pipeline.process(Sample(i, i % 256, 2048, i / FS, 0)) for i in range(FS * 2)]
    assert out[-1].usable
    after_gap = pipeline.process(Sample(FS * 2 + 5, 0, 2048, 3.0, 5))
    assert not after_gap.usable  # warm-up again after a discontinuity
    with pytest.raises(ValueError):
        pipeline.process(Sample(FS * 2 + 5, 0, 2048, 3.0, 0))


# ---- calibration and evaluation -------------------------------------------------------
def test_calibration_derives_polarity_and_evaluation_enables_passing_gestures(model):
    assert model['left_sign'] == 1 and model['onset'] >= 6 * model['noise']
    assert set(model['templates']) >= {'LEFT', 'RIGHT', 'BLINK', 'NATURAL_BLINK'}
    assert not any(model['enabled'].values())  # nothing is enabled before held-out evaluation
    held = synthetic_recording(seed=2, started='evaluation')
    assert held_out(model, held)
    report = evaluate(held, model)
    for gesture in ('LEFT', 'RIGHT', 'BLINK', 'DOUBLE_BLINK'):
        scores = report['gestures'][gesture]
        assert scores['trials'] == 20 and scores['precision'] >= 0.9 and scores['recall'] >= 0.9, (gesture, scores)
    assert report['false_per_minute'] <= 1.0 and report['natural_false_per_minute'] <= 1.0
    enabled = apply_evaluation(model, report)['enabled']
    assert enabled == {g: g in report['enable'] for g in ('LEFT', 'RIGHT', 'BLINK', 'DOUBLE_BLINK')}


def test_reversed_electrodes_are_resolved_by_calibration():
    reversed_model, _ = calibrate(synthetic_recording(seed=3, left_sign=-1))
    assert reversed_model['left_sign'] == -1
    report = evaluate(synthetic_recording(seed=4, left_sign=-1, started='other'), reversed_model)
    assert report['gestures']['LEFT']['recall'] >= 0.9 and report['gestures']['RIGHT']['recall'] >= 0.9


def test_gesture_failing_targets_stays_disabled(model):
    report = evaluate(synthetic_recording(seed=2, started='evaluation'), model,
                      targets=dict(precision=0.9, recall=0.9, false_per_minute=1.0, min_trials=21))
    assert report['enable'] == []  # 20 trials < 21 required
    assert not any(apply_evaluation(model, report)['enabled'].values())


def test_calibration_needs_neutral_data():
    with pytest.raises(CalibrationError, match='NEUTRAL'):
        calibrate(recording_from(gaze([(20, 0)])))


# ---- gating ---------------------------------------------------------------------------
def test_holding_gaze_and_returning_emit_one_command_each(model):
    left = left_level(model)
    assert events_for(model, gaze([(3, 0), (6, left), (4, 0), (3, -left), (4, 0)])) == ['LEFT', 'RIGHT']


def test_blink_hiding_a_return_does_not_shift_later_commands(model):
    left = left_level(model)
    raw = gaze([(3, 0), (2, left), (4, 0), (2, -left), (3, 0), (2, left), (3, 0)], blinks=[(5.0, 120, 0.25)])
    moves = [name for name in events_for(model, raw) if name != 'BLINK']
    assert moves == ['LEFT', 'RIGHT', 'LEFT']


def test_disabled_gestures_are_not_emitted(model):
    left = left_level(model)
    assert events_for(model, gaze([(3, 0), (2, left), (3, 0)]), candidates=False) == []


def test_clipping_suppresses_classification(model):
    raw = np.clip(gaze([(3, 0), (2, 2047), (3, 0)]), 0, 4095)  # driven into the ADC rail
    names = events_for(model, raw)
    assert 'LEFT' not in names and 'RIGHT' not in names


def test_no_event_across_a_gap(model):
    left = left_level(model)
    raw = gaze([(3, 0), (2, left), (3, 0)])
    assert 'LEFT' not in events_for(model, raw, gaps=[int(3.1 * FS)])  # gap right after the movement starts


# ---- AC-coupled amplifier (measured Pill v3 behaviour: spikes that decay in ~0.11 s) ---------
def pill_gaze(seconds_and_levels, blinks=()):
    """Gaze levels / blinks as above, passed through the amplifier's high-pass like the real Pill output."""
    analog = gaze(seconds_and_levels, noise=0.0, blinks=blinks).astype(float) - 2048
    rng = np.random.default_rng(1)
    signal = PILL_V3['baseline'] + amplifier_highpass(analog, PILL_V3['hardware_tau']) + rng.normal(0, 25, len(analog))
    return np.clip(np.round(signal), 0, 4095).astype(int)


def test_pill_like_signal_calibrates_and_meets_targets(pill_model):
    report = evaluate(synthetic_recording(seed=4, started='pill-evaluation', **PILL_V3), pill_model)
    for gesture in ('LEFT', 'RIGHT'):
        scores = report['gestures'][gesture]
        assert scores['precision'] >= 0.9 and scores['recall'] >= 0.9, (gesture, scores)
    assert report['false_per_minute'] <= 1.0


def test_pill_like_hold_and_return_emit_one_command_each(pill_model):
    left = 1400 * pill_model['left_sign']
    raw = pill_gaze([(3, 0), (5, left), (3, 0), (5, -left), (3, 0), (2, left), (3, 0)])
    assert [n for n in events_for(pill_model, raw) if n != 'BLINK'] == ['LEFT', 'RIGHT', 'LEFT']


def test_pill_like_blink_just_before_a_return_is_not_mistaken(pill_model):
    left = 1400 * pill_model['left_sign']
    # A blink 0.15 s before the return from LEFT; the return must still be recognised.
    raw = pill_gaze([(3, 0), (3, left), (3, 0), (3, -left), (3, 0), (3, left), (3, 0)],
                    blinks=[(5.85, 500, 0.12)])
    assert [n for n in events_for(pill_model, raw) if n != 'BLINK'] == ['LEFT', 'RIGHT', 'LEFT']


def test_brief_clipping_at_big_movement_tips_is_tolerated(pill_model):
    left = 3200 * pill_model['left_sign']  # tips go past the ADC rails for well under 0.16 s
    raw = pill_gaze([(3, 0), (3, left), (3, 0), (3, -left), (3, 0)])
    assert ((raw == 0) | (raw == 4095)).sum() > 5  # really clipped
    pipeline = Pipeline(FS, classifier=GestureClassifier(pill_model, emit_candidates=True))
    replay(recording_from(raw), pipeline)
    movements = [ex for ex, _, _ in pipeline.classifier.decisions if ex.peak > 1000]
    assert len(movements) == 4 and all(ex.valid for ex in movements)


# ---- recording round trip -------------------------------------------------------------
def test_recording_round_trip_replays_identically(tmp_path, model):
    original = synthetic_recording(seed=7, started='roundtrip')
    pipeline = Pipeline(FS, recorder=Recorder(tmp_path / 'session', in_plus='left'))
    replay(original, pipeline)
    pipeline.recorder.close()
    loaded = load_recording(tmp_path / 'session')
    assert np.array_equal(loaded.raw, original.raw) and loaded.label == original.label
    assert loaded.meta['in_plus_electrode'] == 'left' and 'not microvolts' in loaded.meta['units']
    assert evaluate(loaded, model)['gestures'] == evaluate(original, model)['gestures']
