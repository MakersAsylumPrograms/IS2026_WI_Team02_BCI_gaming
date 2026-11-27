"""The guided eye calibration and test: the sequence, the fit and its approval gate, and the patient-screen
round trip through the real backend."""
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from eog_acquisition import chords_calibration, gestures, guided
from eog_acquisition.dashboard import DashboardLink
from eog_acquisition.processing import Pipeline, ProcessedSample
from eog_acquisition.protocol import Sample
from eog_acquisition.recording import Recorder
from test_dashboard import backend, patient_screen, wait_for   # noqa: F401  (backend is a fixture)

DATA = Path(__file__).resolve().parents[1] / 'data' / 'recordings'
# Two earlier prompted recordings of the same kind the calibration makes: a look test and a blink test.
ROUNDS = [DATA / 'look-test-20261115-125657.csv', DATA / 'blink-test-20261115-124139.csv']
# A calibration from the patient screen, 2026-11-17: this placement's blinks start with a dip, not a spike.
DIPPING = [DATA / f'eye-calibration-20261117-183645-round{n}.csv' for n in (1, 2)]
REFERENCE = Path(__file__).resolve().parent / 'fixtures' / 'team-model-2026-11-15.json'
recorded = pytest.mark.skipif(not all(p.exists() for p in ROUNDS), reason='the prompted recordings are not here')


def test_a_round_asks_for_each_signal_and_is_scored_as_prompts():
    steps = guided.round_steps(np.random.default_rng(1))
    labels = [s.label for s in steps if s.trial]
    assert sorted(labels.count(x) for x in ('LEFT', 'RIGHT', 'BLINK', 'DOUBLE_BLINK')) == [3, 3, 3, 8]
    assert steps[0].label == 'CENTER' and steps[0].seconds >= gestures.GestureModel().warmup
    assert 80 < sum(s.seconds for s in steps) < 100
    # Laid out as a recording, the scorer finds every look and double blink as a prompt.
    fs, label, trial = 250, [], []
    for s in steps:
        label += [s.label] * int(s.seconds * fs)
        trial += [s.trial] * int(s.seconds * fs)
    recording = chords_calibration.PromptedRecording('r', np.zeros(len(label)), np.arange(len(label)),
                                                     np.zeros(len(label), int), np.zeros(len(label), bool),
                                                     label, np.array(trial), 'x')
    s = chords_calibration.score(recording, [])
    assert (s['looks'], s['double_blinks'], s['false_moves']) == (6, 8, 0)


@recorded
def test_fit_replaces_the_model_only_when_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(guided, 'DATA', tmp_path)
    model_path = tmp_path / 'eog-gestures.json'
    model_path.write_bytes(REFERENCE.read_bytes())
    current = gestures.load_model(REFERENCE)

    result, model = guided.fit(ROUNDS, current, model_path, 'T1')
    assert result['passed'] and gestures.dashboard_approved(model) and result['flipped'] == []
    assert gestures.load_model(model_path) == model
    assert (tmp_path / 'models' / 'eog-gestures-before-T1.json').read_bytes() == REFERENCE.read_bytes()
    assert result['metrics']['looks'] == 16 and result['shortfalls'] == []

    monkeypatch.setitem(chords_calibration.CRITERIA, 'look_recall', 1.01)     # impossible: must not replace
    before = model_path.read_bytes()
    result, _ = guided.fit(ROUNDS, current, model_path, 'T2')
    assert not result['passed'] and model_path.read_bytes() == before
    assert Path(result['saved']).name == 'eog-gestures-T2-not-approved.json'
    assert result['shortfalls'] and 'looks caught' in result['shortfalls'][0]


@recorded
def test_swapped_electrodes_are_noticed_and_corrected():
    recordings = [chords_calibration.load_prompted(p) for p in ROUNDS[:1]]
    model = gestures.load_model(REFERENCE)
    backwards = replace(model, left_polarity=-model.left_polarity, validation=None)
    oriented, flipped = guided.orient(recordings, backwards)
    assert flipped == ['left/right'] and oriented.left_polarity == model.left_polarity
    assert guided.orient(recordings, model) == (model, [])


@pytest.mark.skipif(not all(p.exists() for p in DIPPING), reason='the 2026-11-17 calibration is not here')
def test_blinks_that_start_with_a_dip_are_noticed_and_caught():
    recordings = [chords_calibration.load_prompted(p) for p in DIPPING]
    model = replace(gestures.load_model(REFERENCE), validation=None)
    assert model.blink_polarity == 1

    def selects(m):
        return chords_calibration.total([chords_calibration.score(r, chords_calibration.detect(r, m))
                                         for r in recordings])['select_hits']
    oriented, flipped = guided.orient(recordings, model)
    # This placement is also mirrored left/right compared with the team's.
    assert flipped == ['left/right', 'blink'] and oriented.blink_polarity == -1
    assert selects(model) <= 1 and selects(oriented) >= 8           # of 10 cued double blinks
    assert guided.orient(recordings, oriented) == (oriented, [])     # already right: nothing changes


def test_a_model_whose_double_blinks_fail_is_not_approved():
    metrics = dict(looks=12, look_hits=12, wrong_direction=0, double_blinks=16, select_hits=0, false_moves=0,
                   false_selects=0, minutes=3.0, look_recall=1.0, look_precision=1.0, select_recall=0.0,
                   false_commands_per_minute=0.0)
    assert not chords_calibration.passes(metrics)
    assert guided._shortfalls(metrics) == ['double blinks caught 0 of 16 (needs 80%)']
    assert not chords_calibration.passes(dict(metrics, select_hits=12, select_recall=0.75))
    assert chords_calibration.passes(dict(metrics, select_hits=13, select_recall=0.812))


def test_a_round_is_recorded_with_its_cues_while_samples_stream(tmp_path):
    pipeline, stop = Pipeline(), threading.Event()

    def board():                                       # the reader thread: samples keep coming throughout
        n = 0
        while not stop.is_set():
            pipeline([Sample(n, n % 256, 2000, time.monotonic(), 0)])
            n += 1
            time.sleep(0.002)
    reader = threading.Thread(target=board, daemon=True)
    reader.start()
    steps = [guided.Step('CENTER', 0, 0.3), guided.Step('LEFT', 1, 0.3), guided.Step('REST', 0, 0.3),
             guided.Step('DOUBLE_BLINK', 2, 0.3)]
    recorder = Recorder(tmp_path / 'round', port='test')
    pipeline.recorder = recorder
    try:
        assert guided.run_steps(pipeline, steps, lambda _: None, lambda: True, beep=False)
        pipeline.recorder = None
        recorder.close()                               # as calibrate() does, while the reader still runs
        time.sleep(0.05)
        recorder.write(ProcessedSample(Sample(10 ** 6, 0, 2000, 0.0, 0), False, 'END', 0))   # late write: ignored
    finally:
        stop.set()
        reader.join(timeout=2)
    assert not reader.is_alive()
    recording = chords_calibration.load_prompted(recorder.path)
    assert [w[2] for w in recording.windows() if w[2] != 'REST'] == ['LEFT', 'DOUBLE_BLINK']
    s = chords_calibration.score(recording, [])
    assert (s['looks'], s['double_blinks']) == (1, 1)


def test_watch_reports_single_blinks_and_gestures():
    seen = []
    pipeline = Pipeline(detector=gestures.GestureDetector())
    with guided.SignalWatch(pipeline, seen.append) as watch:
        pipeline.detector.blinks.count += 1                  # the detector saw a blink ...
        watch._sample(None)                                   # first sample only sets the baseline
        pipeline.detector.blinks.count += 1
        watch._sample(None)
        watch._event(gestures.GestureEvent('NEXT', 'RIGHT', 1, 1, 0.0, -0.8))
    assert seen == ['BLINK', 'RIGHT']
    assert not pipeline._sample_callbacks and not pipeline._event_callbacks   # removed afterwards


def test_patient_screen_starts_a_test_and_sees_it_finish(backend):  # noqa: F811
    app, url = backend
    client, _ = patient_screen(app)
    csrf = client.get('/api/v1/session').json['csrf']
    headers = {'X-CSRF-Token': csrf}
    assert client.post('/api/v1/calibration', json={'mode': 'test'}, headers=headers).json['error']['code'] \
        == 'DEVICE_OFFLINE'
    link = DashboardLink(url, device_id='esp32-01', token='device-test', say=print)
    pipeline = Pipeline(detector=gestures.GestureDetector())
    control = guided.CalibrationControl(pipeline, link, REFERENCE, test_seconds=0.6, say=lambda *_: None)
    link.on_calibration(control.handle)
    link.check()
    link.start()
    try:
        assert client.post('/api/v1/calibration', json={'mode': 'test'}, headers=headers).status_code == 201
        assert client.post('/api/v1/calibration', json={'mode': 'test'}, headers=headers).status_code == 409
        assert wait_for(lambda: client.get('/api/v1/calibration').json['job']['state'] == 'running', 3)
        assert control.busy
        assert wait_for(lambda: client.get('/api/v1/calibration').json['job']['state'] == 'done', 4)
        job = client.get('/api/v1/calibration').json['job']
        assert job['mode'] == 'test' and job['progress'] == 1.0 and job['signals'] == []
        # a new request can start once the last one is over, and cancelling stops it
        assert client.post('/api/v1/calibration', json={'mode': 'test'}, headers=headers).status_code == 201
        assert wait_for(lambda: control.busy, 3)
        assert client.delete('/api/v1/calibration', headers=headers).json['job']['state'] == 'cancelled'
        assert wait_for(lambda: not control.busy, 3)
    finally:
        link.stop()


def test_a_request_nobody_takes_up_fails_with_a_reason(backend):  # noqa: F811
    app, url = backend
    client, _ = patient_screen(app)
    headers = {'X-CSRF-Token': client.get('/api/v1/session').json['csrf']}
    DashboardLink(url, device_id='esp32-01', token='device-test').check()       # heartbeat only, no poller
    assert client.post('/api/v1/calibration', json={'mode': 'calibrate'}, headers=headers).status_code == 201
    state = app.extensions['input_state']
    state['calibration']['since'] -= 60
    job = client.get('/api/v1/calibration').json['job']
    assert job['state'] == 'failed' and 'did not answer' in job['message']
