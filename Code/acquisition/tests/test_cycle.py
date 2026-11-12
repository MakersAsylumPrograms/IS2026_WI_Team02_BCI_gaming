import hashlib
import json

import numpy as np
import pytest

from eog_acquisition.cycle import (DEFAULT_MODEL, CalibrationError, CycleDetector, CycleModel, GazeRecovery, calibrate,
                                   detect, load_model, save_model, score)
from eog_acquisition.processing import Pipeline
from eog_acquisition.recording import Recorder, Recording, load_recording, replay
from synthetic import FS, PILL_V3, amplifier_highpass, synthetic_recording

PILL = CycleModel(tau=0.11, threshold=600.0, hold=0.6)


def recording_from(raw, gaps=()):
    n = len(raw)
    gap = np.array([5 if i in gaps else 0 for i in range(n)])
    index = np.arange(n) + np.cumsum(gap)
    return Recording(dict(sample_rate=FS, started_utc='test'), index, index % 256, np.asarray(raw, dtype=int),
                     1000 + index / FS, gap, np.zeros(n, dtype=int), [''] * n, np.zeros(n, dtype=int))


def pill(seconds_and_levels, blinks=(), seed=0):
    """What the Pill outputs for a gaze path [(seconds, level)] plus blinks [(time s, amplitude, duration s)]."""
    gaze = np.concatenate([np.full(int(s * FS), float(level)) for s, level in seconds_and_levels])
    gaze = np.convolve(np.pad(gaze, (9, 0), mode='edge'), np.ones(10) / 10, 'valid')  # 40 ms saccades
    for at, amplitude, duration in blinks:
        n, start = int(duration * FS), int(at * FS)
        gaze[start:start + n] += amplitude * (1 - np.cos(np.linspace(0, 2 * np.pi, n))) / 2
    signal = PILL_V3['baseline'] + amplifier_highpass(gaze, PILL_V3['hardware_tau'])
    signal += np.random.default_rng(seed).normal(0, PILL_V3['noise'], len(signal))
    return np.clip(np.round(signal), 0, 4095).astype(int)


def cycles(raw, model=PILL, gaps=()):
    return detect(recording_from(raw, gaps=set(gaps)), model)


# ---- gaze recovery ------------------------------------------------------------------------
def test_gaze_recovery_undoes_the_pill_highpass():
    raw = pill([(4, 0), (3, 1000), (3, 0)])
    recovery = GazeRecovery(PILL)
    gaze = np.array([recovery.update(v) for v in raw])
    plateau = gaze[int(4.2 * FS):int(5.2 * FS)] - np.median(gaze[int(3 * FS):int(4 * FS)])
    assert 800 < plateau[0] < 1100                    # the held look is a level again (1000 in)
    assert (plateau[0] - plateau[-1]) / plateau[0] < 0.2  # only the slow drift remover's sag over 1 s


# ---- the detector -------------------------------------------------------------------------
@pytest.mark.parametrize('direction', [1, -1])
def test_held_look_either_way_is_one_cycle_and_the_return_is_none(direction):
    events = cycles(pill([(4, 0), (2, 1400 * direction), (4, 0)]))
    assert len(events) == 1 and events[0].command == 'CYCLE' and events[0].direction == direction


def test_left_and_right_both_cycle_the_same_way():
    events = cycles(pill([(4, 0), (2, 1400), (3, 0), (2, -1400), (3, 0), (2, -1400), (3, 0)]))
    assert [e.command for e in events] == ['CYCLE'] * 3


def test_blinks_and_brief_glances_never_cycle():
    raw = pill([(4, 0), (0.3, 1400), (4, 0)], blinks=[(6.0, 1500, 0.25), (7.0, 900, 0.15), (7.4, 900, 0.15)])
    assert cycles(raw) == []


def test_saturation_is_never_a_cycle():
    raw = pill([(10, 0)])
    raw[4 * FS:5 * FS] = 4095        # hard blink / loose electrode pinned at the ADC rail for 1 s
    assert cycles(raw) == []


def test_warm_up_and_gaps_restart_detection():
    assert cycles(pill([(1, 0), (1, 1400), (5, 0)])) == []             # look and return inside the 3 s warm-up
    raw = pill([(4, 0), (2, 1400), (4, 0)])
    assert cycles(raw, gaps=[int(4.3 * FS)]) == []                      # a gap during the look


def test_no_signal_gives_no_cycle():
    flat = np.full(60 * FS, 100) + np.random.default_rng(1).integers(-8, 9, 60 * FS)
    assert cycles(flat) == []


def test_pipeline_streaming_matches_offline_detection():
    recording = recording_from(pill([(4, 0), (2, 1400), (3, 0), (2, -1400), (3, 0)]))
    pipeline = Pipeline(FS, detector=CycleDetector(PILL))
    streamed = []
    pipeline.on_event(streamed.append)
    replay(recording, pipeline)
    assert streamed == detect(recording, PILL) and len(streamed) == 2


# ---- calibration ----------------------------------------------------------------------------
def test_calibration_on_a_pill_like_session_is_precise():
    model, _ = calibrate([synthetic_recording(seed=3, started='cal', **PILL_V3)])
    assert 0.08 <= model.tau <= 0.14                     # finds the Pill's time constant (0.11)
    held_out = synthetic_recording(seed=4, started='eval', **PILL_V3)
    result = score(held_out, detect(held_out, model))
    assert result['recall'] >= 0.8 and result['protocol_precision'] >= 0.95, result
    assert result['false_after_blink_cues'] == 0


def test_calibration_needs_left_right_trials():
    with pytest.raises(CalibrationError):
        calibrate([recording_from(pill([(20, 0)]))])


def test_calibration_never_changes_the_raw_recording(tmp_path):
    original = synthetic_recording(seed=5, started='raw', **PILL_V3)
    pipeline = Pipeline(FS, recorder=Recorder(tmp_path / 'session', in_plus='left'))
    replay(original, pipeline)
    pipeline.recorder.close()
    files = [tmp_path / 'session.csv', tmp_path / 'session.json']
    before = [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
    loaded = load_recording(tmp_path / 'session')
    assert np.array_equal(loaded.raw, original.raw) and loaded.label == original.label
    calibrate([loaded])
    assert [hashlib.sha256(f.read_bytes()).hexdigest() for f in files] == before


# ---- models ---------------------------------------------------------------------------------
def test_model_round_trip_and_old_models_are_refused(tmp_path):
    path = save_model(PILL, tmp_path / 'm.json', report={'note': 'x'})
    assert load_model(path) == PILL
    (tmp_path / 'old.json').write_text(json.dumps({'format': 'equalcare-eog-model/1', 'templates': {}}))
    with pytest.raises(ValueError, match='Not a CYCLE model'):
        load_model(tmp_path / 'old.json')


def test_team_default_model_loads():
    model = load_model(DEFAULT_MODEL)
    assert 0.05 <= model.tau <= 0.25 and model.threshold > 0 and model.hold > 0
