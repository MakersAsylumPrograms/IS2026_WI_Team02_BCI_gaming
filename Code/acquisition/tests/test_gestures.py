"""LEFT / RIGHT / DOUBLE BLINK: the Chords-Web filter, the detector on Pill-shaped raw signals,
and the team model on the Chords-Web recordings it was calibrated from."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter

from eog_acquisition import chords_calibration, gestures
from eog_acquisition.chords_filter import EOG_LOWPASS, HIGHPASS, ChordsWebFilter
from eog_acquisition.gestures import GestureDetector, GestureModel
from eog_acquisition.processing import Pipeline
from eog_acquisition.protocol import Sample
from synthetic import FS, amplifier_highpass

RECORDINGS = Path(__file__).resolve().parents[1] / 'data' / 'recordings' / 'Fresh Calibration Recording Through Chords'
FILES = ('1st one.csv', '2nd one - better.csv', '3rd one - better.csv')
recorded = pytest.mark.skipif(not all((RECORDINGS / f).exists() for f in FILES) or not gestures.DEFAULT_MODEL.exists(),
                              reason='the Chords-Web calibration recordings or the team model are not here')
MODEL = GestureModel()   # the code's defaults; the team model is tested on the real recordings below


def pill(path, blinks=(), ramp=0.1, seed=0):
    """Raw ADC counts the Pill gives for a gaze path [(seconds, level)] plus blinks [(time s, size, duration s)].

    Big, slow-ish eye movements and a 0.15 s amplifier time constant, as in the team's recordings.
    """
    gaze = np.concatenate([np.full(int(s * FS), float(level)) for s, level in path])
    n = int(ramp * FS)
    window = 1 - np.cos(np.linspace(0, 2 * np.pi, n + 2)[1:-1])
    gaze = np.convolve(np.pad(gaze, (n - 1, 0), mode='edge'), window / window.sum(), 'valid')
    for at, size, duration in blinks:
        k, start = int(duration * FS), int(at * FS)
        gaze[start:start + k] += size * (1 - np.cos(np.linspace(0, 2 * np.pi, k))) / 2
    signal = 1900 + amplifier_highpass(gaze, 0.15) + np.random.default_rng(seed).normal(0, 25, len(gaze))
    return np.clip(np.round(signal), 0, 4095).astype(int)


def commands(raw, model=MODEL):
    detector, events = GestureDetector(model), []
    for i, value in enumerate(raw):
        events += detector.step(int(value), bool(value <= 8 or value >= 4087), i, i / FS)
    return [e.command for e in events]


# ---- the Chords-Web filter chain ----------------------------------------------------------
def test_coefficients_are_the_butterworth_filters_chords_web_uses():
    highpass = butter(2, 0.1, 'highpass', fs=FS, output='sos')[0]   # labelled 1 Hz in Chords-Web; it is 0.1 Hz
    lowpass = butter(2, 10, 'lowpass', fs=FS, output='sos')[0]
    for ours, (b0, b1, b2, _, a1, a2) in ((HIGHPASS, highpass), (EOG_LOWPASS, lowpass)):
        assert np.allclose(ours, (a1, a2, b0, b1, b2), atol=1e-7)


def test_resting_level_reads_as_the_recordings_resting_level_from_the_first_sample():
    chain = ChordsWebFilter()
    out = [chain.process(1900) for _ in range(500)]
    assert max(out) - min(out) < 1e-6                       # no start transient
    assert out[-1] == pytest.approx(0.00147, abs=2e-5)      # the resting mean of the team's Chords-Web recordings


def test_mains_hum_is_removed_and_eye_movement_frequencies_pass_in_chords_units():
    t = np.arange(10 * FS) / FS

    def amplitude(hz):
        chain = ChordsWebFilter()
        out = np.array([chain.process(v) for v in 1900 + 500 * np.sin(2 * np.pi * hz * t)])
        return np.ptp(out[5 * FS:]) / 2
    assert amplitude(50) < 0.001
    assert amplitude(3) == pytest.approx(500 * 2 / 4096, rel=0.05)   # 1.0 = 2,048 counts


def test_only_the_calibrated_chain_can_be_built():
    with pytest.raises(ValueError):
        ChordsWebFilter(sample_rate=500)
    with pytest.raises(ValueError):
        ChordsWebFilter(notch_hz=60)


# ---- the detector on raw ADC counts (the live path) ---------------------------------------
def test_look_left_is_previous_look_right_is_next_and_the_way_back_is_nothing():
    assert commands(pill([(5, 0), (3, 2800), (4, 0)])) == ['PREVIOUS']
    assert commands(pill([(5, 0), (3, -2800), (4, 0)])) == ['NEXT']
    assert commands(pill([(5, 0), (2, 2800), (3, 0), (2, -2800), (3, 0), (2, 2800), (3, 0)])) == [
        'PREVIOUS', 'NEXT', 'PREVIOUS']


def test_a_look_is_confirmed_within_a_second():
    detector = GestureDetector(MODEL)
    events = [e for i, v in enumerate(pill([(5, 0), (3, 2800), (4, 0)])) for e in detector.step(int(v), False, i)]
    assert events[0].direction == 'LEFT' and events[0].source == 'eog' and events[0].shift > 0.5
    assert 0.3 < (events[0].index - 5 * FS) / FS < 1.0


def test_two_blinks_select_and_single_blinks_do_nothing():
    assert commands(pill([(12, 0)], blinks=[(5, 2500, 0.25), (5.45, 2500, 0.25)])) == ['SELECT']
    assert commands(pill([(12, 0)], blinks=[(5, 2500, 0.25)])) == []
    assert commands(pill([(14, 0)], blinks=[(5, 2500, 0.25), (7, 2500, 0.25), (9, 2500, 0.25)])) == []


def test_small_movements_quick_glances_and_a_blink_during_a_look_are_not_extra_commands():
    assert commands(pill([(5, 0), (1, 1200), (4, 0)], ramp=0.04)) == []       # an ordinary small eye movement
    assert commands(pill([(5, 0), (0.3, 2800), (5, 0)])) == []                # there and straight back: not held
    assert commands(pill([(5, 0), (3, 2800), (4, 0)], blinks=[(6.5, 2500, 0.25)])) == ['PREVIOUS']


def test_nothing_during_warmup_across_a_gap_or_while_the_adc_is_pinned():
    # Looked away during the 3 s warm-up: no command then, and none for coming back afterwards.
    assert commands(pill([(1, 0), (3, 2800), (4, 0)])) == []
    assert commands(pill([(1, 0), (3, 2800), (4, 0), (3, -2800), (4, 0)])) == ['NEXT']   # then it works as usual
    pinned = np.concatenate([pill([(5, 0)]), np.full(3 * FS, 4095), pill([(5, 0)], seed=1)])
    assert commands(pinned) == []                                             # electrode off / rail: not a look
    detector, events = GestureDetector(MODEL), []
    for i, value in enumerate(pill([(5, 0), (3, 2800), (4, 0)])):
        if i == int(4.5 * FS):
            detector.reset()                                                  # what Pipeline does at a data gap
        events += detector.step(int(value), False, i)
    assert events == []


def test_swapped_electrodes_swap_the_directions():
    swapped = replace(MODEL, left_polarity=-1)
    assert commands(pill([(5, 0), (3, 2800), (4, 0)]), swapped) == ['NEXT']


def test_pipeline_runs_the_detector_on_raw_samples_like_the_joystick_path():
    pipeline, events, shown = Pipeline(detector=GestureDetector(MODEL)), [], []
    pipeline.on_event(events.append)
    pipeline.on_sample(lambda p: shown.append(p.gaze))
    raw = pill([(5, 0), (3, -2800), (4, 0)])
    for i, value in enumerate(raw):
        pipeline.process(Sample(i, i % 256, int(value), i / FS, 0))
    assert [(e.command, e.direction, e.source) for e in events] == [('NEXT', 'RIGHT', 'eog')]
    assert min(shown) < -0.5 and abs(shown[2 * FS]) < 0.1       # the plot gets the Chords-Web signal
    assert pipeline.health.received == len(raw)                  # raw samples are untouched and all counted


def test_model_file_round_trip_and_approval_belongs_to_its_parameters(tmp_path):
    model = replace(MODEL, look_width=0.19)
    approved = replace(model, validation=dict(passed=True, model_id=gestures.model_id(model)))
    loaded = gestures.load_model(gestures.save_model(approved, tmp_path / 'm.json', dict(note='report')))
    assert loaded == approved and gestures.dashboard_approved(loaded)
    assert not gestures.dashboard_approved(replace(loaded, look_width=0.1))    # edited thresholds: not approved
    assert not gestures.dashboard_approved(model)
    with pytest.raises(ValueError):
        GestureModel.from_dict({'format': 'equalcare-eog-cycle/1'})


# ---- the team model on the recordings it was calibrated from ------------------------------
@pytest.fixture(scope='module')
def team():
    return gestures.load_model(), [chords_calibration.load_chords_csv(RECORDINGS / f) for f in FILES]


@recorded
def test_team_model_is_approved_and_matches_the_untouched_recordings(team):
    model, recordings = team
    assert gestures.dashboard_approved(model)
    report = json.loads(gestures.DEFAULT_MODEL.read_text(encoding='utf-8'))['calibration']
    for source, recording, name in zip(report['sources'], recordings, FILES):
        assert source['file'] == name
        assert source['sha256'] == hashlib.sha256((RECORDINGS / name).read_bytes()).hexdigest() == recording.sha256


@recorded
def test_team_model_on_the_calibration_recordings(team):
    model, recordings = team
    scores = [chords_calibration.score(r, chords_calibration.detect(r, model)) for r in recordings]
    assert [s['look_hits'] for s in scores] == [9, 10, 10]      # of 10 each; recording 1 has no last look in it
    assert scores[0]['missed_looks'] == [184]
    assert [s['select_hits'] for s in scores] == [2, 7, 5]      # of 8 each; the rest have one blink or none
    total = chords_calibration.total(scores)
    assert total['wrong_direction'] == total['false_moves'] == total['false_selects'] == 0


@recorded
def test_a_recording_replays_as_the_cue_sheets_commands(team):
    model, recordings = team
    events = chords_calibration.detect(recordings[1], model)
    assert [e.direction for e in events] == (['LEFT', 'RIGHT'] * 3 + ['DOUBLE_BLINK'] * 5 + ['LEFT', 'RIGHT'] * 2
                                             + ['DOUBLE_BLINK'] * 2)


@recorded
def test_calibration_checks_each_recording_with_a_fit_made_without_it(team, monkeypatch):
    _, recordings = team
    monkeypatch.setattr(chords_calibration, 'GRID', dict(look_peak=(0.5,), look_width=(0.12, 0.18), tail=(0.04,),
                                                         blink_peak=(0.45,)))
    model, report = chords_calibration.calibrate(recordings)
    assert model.look_width == 0.18                              # 0.12 lets ordinary eye movements through
    assert [h['recording'] for h in report['held_out']] == [r.name for r in recordings]
    assert model.validation['passed'] and model.validation['model_id'] == gestures.model_id(model)
    assert model.validation['metrics'] == report['held_out_total']
    with pytest.raises(ValueError):
        chords_calibration.calibrate(recordings[:1])


def test_chords_csv_loading_counts_skipped_samples_and_rejects_other_files(tmp_path):
    good = tmp_path / 'rec.csv'
    good.write_text('Counter,Channel1\n254,0.1\n255,0.2\n0,0.3\n5,0.4\n', encoding='utf-8')
    recording = chords_calibration.load_chords_csv(good)
    assert list(recording.gap_before) == [0, 0, 0, 4] and list(recording.index) == [0, 1, 2, 7]
    (tmp_path / 'raw.csv').write_text('sample_index,raw\n0,1900\n', encoding='utf-8')
    with pytest.raises(ValueError):
        chords_calibration.load_chords_csv(tmp_path / 'raw.csv')
