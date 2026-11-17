"""Short guided eye sequences on the live signal.

calibrate()  two rounds of cued looks, blinks and free viewing (about 2.5 minutes), recorded, then the
             gesture thresholds are fitted to those two rounds only: this person, this electrode placement.
             The new model replaces the current one only if it passes chords_calibration.CRITERIA on
             each round with thresholds fitted on the other.
watch()      shows every eye signal as it happens (LEFT, RIGHT, BLINK, DOUBLE BLINK) without sending
             anything, so the person can see what the detector sees.

Both run on a worker thread against a streaming Pipeline and report through report(dict), which the
command line prints and `run --dashboard` forwards to the patient screen.
"""
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np

from . import chords_calibration, gestures
from .gestures import GestureDetector
from .recording import Recorder

DATA = Path(__file__).resolve().parents[1] / 'data'
ROUNDS = 2
TONES = {'LEFT': (440, 250), 'RIGHT': (880, 250), 'CENTER': (660, 120), 'BLINK': (1320, 120),
         'DOUBLE_BLINK': (1760, 250), 'NATURAL': (660, 400)}          # Hz, ms
CUES = {'CENTER': 'Look at the centre', 'REST': 'Relax, eyes on the centre', 'LEFT': 'Look LEFT and hold',
        'RIGHT': 'Look RIGHT and hold', 'BLINK': 'Blink ONCE', 'DOUBLE_BLINK': 'Blink TWICE',
        'NATURAL': 'Look around the screen normally'}


@dataclass(frozen=True)
class Step:
    label: str        # stored with every sample: CENTER, REST, LEFT, RIGHT, BLINK, DOUBLE_BLINK, NATURAL
    trial: int        # > 0 for a prompt that is scored
    seconds: float

    @property
    def cue(self):
        return CUES[self.label]


def round_steps(rng, looks=6, single_blinks=3, double_blinks=5, natural=15.0, warmup=3.5):
    """One round, about 75 s. Looks come in random order; the detector's warm-up is spent at the centre."""
    sides = ['LEFT', 'RIGHT'] * (looks // 2) + ['LEFT'] * (looks % 2)
    rng.shuffle(sides)
    steps, trial = [Step('CENTER', 0, warmup)], 0
    for side in sides:
        trial += 1
        steps += [Step('CENTER', 0, 2.0), Step(side, trial, 2.5)]
    steps.append(Step('CENTER', 0, 2.0))
    for label, count, seconds in (('BLINK', single_blinks, 2.0), ('DOUBLE_BLINK', double_blinks, 2.5)):
        for _ in range(count):
            trial += 1
            steps += [Step('REST', 0, 1.5), Step(label, trial, seconds)]
    return steps + [Step('REST', 0, 1.0), Step('NATURAL', 0, natural)]


def tone(label):
    if label not in TONES:
        return
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=TONES[label], daemon=True).start()
    except (ImportError, RuntimeError):
        pass                                  # no beep off Windows; the cue is on screen anyway


def _sleep(seconds, keep_going):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not keep_going():
            return False
        time.sleep(min(0.05, max(0.0, end - time.monotonic())))
    return True


def run_steps(pipeline, steps, report, keep_going, round_no=1, rounds=1, beep=True):
    """Label the live stream step by step. False if stopped early."""
    total = sum(s.seconds for s in steps)
    done = 0.0
    try:
        for number, step in enumerate(steps, 1):
            pipeline.set_label(step.label, step.trial)
            if beep:
                tone(step.label)
            report(dict(state='running', label=step.label, cue=step.cue, seconds=step.seconds, round=round_no,
                        rounds=rounds, step=number, steps=len(steps),
                        progress=round(((round_no - 1) + done / total) / rounds, 3)))
            if not _sleep(step.seconds, keep_going):
                return False
            done += step.seconds
        return True
    finally:
        pipeline.set_label('END', 0)


def _shortfalls(metrics):
    """Which approval criteria the held-out check missed, in words."""
    c, out = chords_calibration.CRITERIA, []
    if not metrics['looks'] or (metrics['look_recall'] or 0) < c['look_recall']:
        out.append(f"looks caught {metrics['look_hits']} of {metrics['looks']} (needs {c['look_recall']:.0%})")
    if metrics['look_precision'] is not None and metrics['look_precision'] < c['look_precision']:
        out.append(f"{metrics['look_precision']:.0%} of look commands were right (needs {c['look_precision']:.0%})")
    if (metrics['false_commands_per_minute'] or 0) > c['false_commands_per_minute']:
        out.append(f"{metrics['false_commands_per_minute']} false commands a minute "
                   f"(needs at most {c['false_commands_per_minute']:g})")
    if not metrics['double_blinks'] or (metrics['select_recall'] or 0) < c['select_recall']:
        out.append(f"double blinks caught {metrics['select_hits']} of {metrics['double_blinks']} "
                   f"(needs {c['select_recall']:.0%})")
    if metrics['false_selects'] > c['false_selects']:
        out.append(f"{metrics['false_selects']} SELECT(s) without a double blink (needs none)")
    return out


def orient(recordings, base):
    """`base`, with the look directions and the blink's sign flipped where the recordings show them the other
    way round: electrode placement decides both (a blink can start with a dip instead of a spike).
    Returns (model, what was flipped: a list of 'left/right' and 'blink')."""
    def totals(model):
        return chords_calibration.total([chords_calibration.score(r, chords_calibration.detect(r, model))
                                         for r in recordings])
    flipped, now = [], totals(base)
    if now['wrong_direction'] > now['look_hits']:
        candidate = replace(base, left_polarity=-base.left_polarity)
        other = totals(candidate)
        if other['look_hits'] > now['look_hits']:
            base, now = candidate, other
            flipped.append('left/right')
    candidate = replace(base, blink_polarity=-base.blink_polarity)
    other = totals(candidate)
    if other['select_hits'] - other['false_selects'] > now['select_hits'] - now['false_selects']:
        base = candidate
        flipped.append('blink')
    return base, flipped


def fit(paths, current, model_path, stamp):
    """Fit on the recordings (eye-calibration rounds or Chords-Web exports); save. Returns the result the
    patient screen and the terminal show, and the model."""
    recordings = [chords_calibration.load_any(p) for p in paths]
    base, flipped = orient(recordings, replace(current, validation=None))
    model, report = chords_calibration.calibrate(recordings, base)
    metrics = model.validation['metrics']
    passed = model.validation['passed']
    models = DATA / 'models'
    if passed:
        if Path(model_path).exists():
            previous = models / f'eog-gestures-before-{stamp}.json'
            previous.parent.mkdir(parents=True, exist_ok=True)
            previous.write_bytes(Path(model_path).read_bytes())
        saved = gestures.save_model(model, model_path, report)
    else:
        saved = gestures.save_model(model, models / f'eog-gestures-{stamp}-not-approved.json', report)
    keys = ('looks', 'look_hits', 'wrong_direction', 'double_blinks', 'select_hits', 'false_moves',
            'false_selects', 'minutes')
    return dict(passed=passed, flipped=flipped, saved=str(saved), shortfalls=_shortfalls(metrics),
                metrics={k: metrics[k] for k in keys}, recordings=[str(p) for p in paths]), model


def calibrate(pipeline, model_path, report, keep_going, port='', in_plus='left', rounds=ROUNDS, seed=None,
              beep=True):
    """Record `rounds` rounds and fit. Returns (result dict, new model or None); result['state'] is
    'done' or 'cancelled'. On 'done' the caller decides whether to use the model (result['passed'])."""
    rng = np.random.default_rng(seed)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    current = pipeline.detector.model if pipeline.detector else gestures.load_model(model_path)
    paths = []
    for round_no in range(1, rounds + 1):
        steps = round_steps(rng)
        recorder = Recorder(DATA / 'recordings' / f'eye-calibration-{stamp}-round{round_no}', port=port,
                            in_plus=in_plus, notes=f'eye calibration, round {round_no} of {rounds}',
                            protocol=dict(steps=[asdict(s) for s in steps]))
        pipeline.recorder = recorder
        finished = run_steps(pipeline, steps, report, keep_going, round_no, rounds, beep)
        pipeline.recorder = None
        recorder.close(dict(health=pipeline.health.snapshot(), finished=finished))
        if not finished:
            return dict(state='cancelled'), None
        paths.append(recorder.path)
    report(dict(state='fitting', cue='Working out your settings...', progress=1.0))
    result, model = fit(paths, current, model_path, stamp)
    return dict(state='done', **result), model


class SignalWatch:
    """Every eye signal on a pipeline, as it happens: LEFT, RIGHT, DOUBLE_BLINK events and single BLINKs.

    Single blinks never become events (only pairs do); they are read from the detector's blink count.
    """

    def __init__(self, pipeline, on_signal):
        self.pipeline, self.on_signal = pipeline, on_signal
        self._detector = self._blinks = None

    def _sample(self, processed):
        detector = self.pipeline.detector                 # swapped after a calibration: start counting afresh
        if detector is None:
            return
        count = detector.blinks.count
        if detector is self._detector and self._blinks is not None and count > self._blinks:
            self.on_signal('BLINK')
        self._detector, self._blinks = detector, count

    def _event(self, event):
        if event.source == 'eog':
            self.on_signal(event.direction)

    def __enter__(self):
        self.pipeline.on_sample(self._sample)
        self.pipeline.on_event(self._event)
        return self

    def __exit__(self, *_):
        self.pipeline.remove_callback(self._sample)
        self.pipeline.remove_callback(self._event)


def watch(pipeline, seconds, report, keep_going):
    """Show each eye signal for `seconds`; nothing is sent. Returns the signals seen, in order."""
    seen, start = [], time.monotonic()

    def on_signal(kind):
        seen.append(dict(kind=kind, at=round(time.monotonic() - start, 2)))
        report(dict(state='running', cue='Try each signal', signals=seen[-12:],
                    progress=round(min(1.0, (time.monotonic() - start) / seconds), 3)))

    report(dict(state='running', cue='Try each signal', signals=[], progress=0.0))
    with SignalWatch(pipeline, on_signal):
        end = start + seconds
        while time.monotonic() < end and keep_going():
            time.sleep(0.25)
            report(dict(state='running', cue='Try each signal', signals=seen[-12:],
                        progress=round(min(1.0, (time.monotonic() - start) / seconds), 3)))
    return seen


class CalibrationControl:
    """Runs what the patient screen asks for (calibrate or test) while `run --dashboard` keeps streaming.

    While `busy`, the caller must not forward eye or joystick commands. A passed calibration swaps
    the pipeline's detector at once; nothing restarts.
    """

    def __init__(self, pipeline, link, model_path, port='', in_plus='left', test_seconds=30.0, say=print):
        self.pipeline, self.link, self.model_path = pipeline, link, model_path
        self.port, self.in_plus, self.test_seconds, self.say = port, in_plus, test_seconds, say
        self._cancel = threading.Event()
        self._thread = None

    @property
    def busy(self):
        return self._thread is not None and self._thread.is_alive()

    def handle(self, job):
        if job is None or job['state'] == 'cancelled':
            self._cancel.set()
        elif job['state'] == 'requested' and not self.busy:
            self._cancel.clear()
            self._thread = threading.Thread(target=self._run, args=(job,), name='eye-calibration', daemon=True)
            self._thread.start()

    def _report(self, job_id, fields):
        self.link.report(job_id, **fields)

    def _run(self, job):
        job_id = job['id']
        report = lambda fields: self._report(job_id, fields)          # noqa: E731
        keep_going = lambda: not self._cancel.is_set()                 # noqa: E731
        try:
            if job['mode'] == 'test':
                self.say('[calibration] eye test started from the patient screen')
                seen = watch(self.pipeline, self.test_seconds, report, keep_going)
                report(dict(state='done', cue='', progress=1.0, signals=seen[-12:]))
                return
            self.say('[calibration] eye calibration started from the patient screen')
            result, model = calibrate(self.pipeline, self.model_path, report, keep_going, self.port, self.in_plus)
            if result['state'] == 'cancelled':
                self.say('[calibration] cancelled')
                return
            if result['passed']:
                self.pipeline.detector = GestureDetector(model)
                self.say(f"[calibration] approved; now using {result['saved']}")
            else:
                self.say(f"[calibration] not approved ({'; '.join(result['shortfalls'])}); "
                         f"kept the previous model, candidate saved to {result['saved']}")
            report(dict(state='done', cue='', progress=1.0, result=result))
        except Exception as exc:                                       # report, never kill the input process
            self.say(f'[calibration] failed: {exc}')
            report(dict(state='failed', message=str(exc)))
