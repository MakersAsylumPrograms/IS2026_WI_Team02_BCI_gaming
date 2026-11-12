"""Command line: python -m eog_acquisition <command> --help"""
import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .calibration import TARGETS, CalibrationError, apply_evaluation, calibrate, evaluate, held_out
from .classifier import GESTURES, GestureClassifier
from .device import DEFAULT_PORT, DeviceError, EOGDevice
from .processing import Pipeline
from .protocol import SAMPLE_RATE
from .recording import EventLog, Recorder, load_recording, replay
from .session import Protocol, run_session

DATA = Path(__file__).resolve().parents[1] / 'data'
RATE_TOLERANCE = 0.01  # 250 Hz within 1%
WARMUP_WAIT = 2.0      # let the filter settle before the first session cue


def stamp():
    return datetime.now().strftime('%Y%m%d-%H%M%S')


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


def status_line(health, decoder):
    rate = health['device_rate']
    return (f"rate {rate if rate else '-'} Hz | missing {health['missing']} ({health['loss_percent']}%) | "
            f"stalls {health['stalls']} | clipping {'YES' if health['clipping_now'] else 'no'} | "
            f"flat {'YES' if health['flat_now'] else 'no'} | junk {decoder.junk_bytes} B | "
            f"raw {health['raw_min']}-{health['raw_max']}")


class Live:
    """One streaming run: device + pipeline + Ctrl+C / disconnect handling + status output."""

    def __init__(self, port, pipeline):
        self.pipeline = pipeline
        self.alive = threading.Event()
        self.alive.set()
        self.device = EOGDevice(port, on_status=self._status)
        self._warned = set()
        signal.signal(signal.SIGINT, lambda *_: self.alive.clear())

    def _status(self, state, message):
        print(f'[device] {message}', flush=True)
        if state == 'disconnected':
            self.alive.clear()

    def keep_going(self):
        return self.alive.is_set()

    def start(self):
        reply = self.device.open()
        print(f'Connected: ESP32-S3 on {self.device.port} (status {reply or "?"})')
        self.device.start(self.pipeline)

    def warnings(self):
        health = self.pipeline.health.snapshot()
        for key, text in (('clipping_now', 'Signal is CLIPPING (brief clipping at big movements is tolerated; '
                                           'longer clipping is ignored).'),
                          ('flat_now', 'Signal is FLAT for 2 s: electrode off or Pill not powered?')):
            if health[key] and key not in self._warned:
                print(f'[signal] {text}', flush=True)
            (self._warned.add if health[key] else self._warned.discard)(key)
        return health

    def wait(self, seconds=None, every=5.0, quiet=False):
        """Block until Ctrl+C, a disconnect or `seconds`; print status every `every` seconds."""
        end = time.monotonic() + seconds if seconds else None
        next_print = time.monotonic() + every
        while self.keep_going() and (end is None or time.monotonic() < end):
            time.sleep(0.1)
            health = self.warnings()
            if not quiet and time.monotonic() >= next_print and health['received']:
                print(status_line(health, self.device.decoder), flush=True)
                next_print += every

    def finish(self):
        self.device.stop()
        stats = ''
        if self.device.error is None and self.device.ser is not None:
            try:
                stats = self.device.command('STATS')
            except DeviceError:
                pass
        self.device.close()
        return stats


def summarise(health, stats, decoder):
    print(status_line(health, decoder))
    rate = health['device_rate']
    if rate:
        verdict = 'within' if abs(rate - SAMPLE_RATE) / SAMPLE_RATE <= RATE_TOLERANCE else 'OUTSIDE'
        print(f'Measured device rate {rate} Hz: {verdict} 1% of 250 Hz')
    if stats:
        print(f'Firmware {stats} (missed = sampler late, dropped = queue full; any other gap is transport loss)')
    if decoder.malformed_packets or decoder.inactive_nonzero:
        print(f'Malformed packets {decoder.malformed_packets}, packets with non-zero inactive slots '
              f'{decoder.inactive_nonzero}')


# ---- commands ------------------------------------------------------------------------
def cmd_check(args):
    """Firmware identification, start/stop behaviour, rate and stream cleanliness."""
    pipeline = Pipeline()
    live = Live(args.port, pipeline)
    device = live.device
    before = device.open()
    print(f'WHORU -> ESP32-S3; STATUS before START -> {before!r}')
    device.start(pipeline)
    live.wait(args.seconds, every=2)
    text_while_streaming, junk_while_streaming = list(device.decoder.text_lines), device.decoder.junk_bytes
    device.stop()
    leftover, end = b'', time.monotonic() + 0.5
    while device.error is None and time.monotonic() < end:
        leftover += device.ser.read(device.ser.in_waiting or 1)
    after = device.command('STATUS') if device.error is None else ''
    stats = device.command('STATS') if device.error is None else ''
    device.close()
    health = pipeline.health.snapshot()
    rate = health['device_rate'] or 0
    checks = {
        'Identified as ESP32-S3 (WHORU)': True,
        'Starts stopped (STATUS = STOPPED)': before == 'STOPPED',
        'Streams after START': health['received'] > 0,
        'Sample rate within 1% of 250 Hz': abs(rate - SAMPLE_RATE) / SAMPLE_RATE <= RATE_TOLERANCE,
        'No counter gaps or stalls': health['missing'] == 0 and health['stalls'] == 0,
        'No text or stray bytes while streaming': junk_while_streaming == 0 and not text_while_streaming,
        'Channels 2-16 inactive (zero)': device.decoder.inactive_nonzero == 0,
        'Silent after STOP': not leftover,
        'STATUS after STOP = STOPPED': after == 'STOPPED',
    }
    for name, ok in checks.items():
        print(f'  {"PASS" if ok else "FAIL"}  {name}')
    summarise(health, stats, device.decoder)
    report = dict(port=args.port, checks=checks, health=health, stats=stats, text_while_streaming=text_while_streaming,
                  junk_bytes_while_streaming=junk_while_streaming, leftover_bytes_after_stop=len(leftover))
    print(f'Report: {save_json(args.report or DATA / "diagnostics" / f"check-{stamp()}.json", report)}')
    return 0 if all(checks.values()) else 1


def _stream(args, recorder, classifier=None, minutes=None, plot=False, session=None):
    """Stream until Ctrl+C / disconnect / `minutes` / the guided session ends."""
    pipeline = Pipeline(classifier=classifier, recorder=recorder)
    live = Live(args.port, pipeline)
    worker = None
    try:
        live.start()
        if session is not None:
            def guided():
                session(pipeline, live.keep_going)
                live.alive.clear()
            worker = threading.Thread(target=guided, daemon=True)
            worker.start()
        if plot:
            from .viewer import LiveView
            LiveView(pipeline, SAMPLE_RATE, lambda: status_line(pipeline.health.snapshot(), live.device.decoder)
                     ).run(live.keep_going)
            live.alive.clear()  # closing the window ends the run
        elif worker is not None:
            live.wait(quiet=True)
        else:
            live.wait(minutes * 60 if minutes else None)
        if worker is not None:
            worker.join(timeout=2)
    finally:
        stats = live.finish()
        health = pipeline.health.snapshot()
        if recorder:
            recorder.close(dict(health=health, firmware_stats=stats, junk_bytes=live.device.decoder.junk_bytes,
                                malformed_packets=live.device.decoder.malformed_packets))
    summarise(health, stats, live.device.decoder)
    return pipeline


def cmd_capture(args):
    """Raw capture (default 5 minutes) with a sample-rate and counter-gap verdict."""
    recorder = Recorder(args.out or DATA / 'recordings' / f'capture-{stamp()}', port=args.port, in_plus=args.in_plus,
                        notes='raw capture')
    print(f'Recording {args.minutes} min to {recorder.path} (Ctrl+C stops early)')
    health = _stream(args, recorder, minutes=args.minutes, plot=args.plot).health.snapshot()
    rate = health['device_rate'] or 0
    ok = abs(rate - SAMPLE_RATE) / SAMPLE_RATE <= RATE_TOLERANCE and health['missing'] == 0 and health['stalls'] == 0
    print(f'{"PASS" if ok else "FAIL"}: {health["seconds"]} s, rate {rate} Hz, {health["missing"]} missing, '
          f'{health["stalls"]} stalls. Files: {recorder.path} and {recorder.meta_path}')
    return 0 if ok else 1


def cmd_view(args):
    """Live raw + filtered plots; nothing is recorded."""
    _stream(args, None, plot=True)
    return 0


def cmd_session(args):
    """Guided calibration or evaluation recording."""
    protocol = Protocol(trials_per_gesture=args.trials, neutral_seconds=args.neutral, natural_seconds=args.natural)
    recorder = Recorder(args.out or DATA / 'recordings' / f'{args.set}-{stamp()}', port=args.port,
                        in_plus=args.in_plus, notes=f'{args.set} set',
                        protocol=dict(protocol.__dict__, gestures=list(protocol.gestures), set=args.set))
    print('Before starting: centre mark straight ahead, marks about 30 degrees left and right of it; '
          'Arduino Serial Monitor closed; laptop on battery.')
    finished = {}
    rng = np.random.default_rng(args.seed)

    def session(pipeline, keep_going):
        time.sleep(WARMUP_WAIT)
        finished['ok'] = run_session(pipeline, protocol, rng, keep_going)

    _stream(args, recorder, plot=args.plot, session=session)
    print(f'Saved {recorder.path}' + ('' if finished.get('ok') else '  (INCOMPLETE: stopped early)'))
    if args.set == 'calibration' and finished.get('ok') and args.auto_calibrate:
        return cmd_calibrate(argparse.Namespace(recording=str(recorder.path), out=None))
    if args.set == 'calibration':
        print(f'Next: python -m eog_acquisition calibrate "{recorder.path}"')
    else:
        print(f'Next: python -m eog_acquisition evaluate "{recorder.path}" --model <model.json> --apply')
    return 0 if finished.get('ok') else 1


def print_scores(gestures):
    for g, s in gestures.items():
        print(f"  {g:6} trials {s['trials']:3}  precision {s['precision']}  recall {s['recall']}  "
              f"false {s['false_events']} ({s['false_per_minute']}/min)  {'PASS' if s['passed'] else 'fail'}")


def cmd_calibrate(args):
    recording = load_recording(args.recording)
    try:
        model, report = calibrate(recording, source=str(Path(args.recording).with_suffix('.csv')))
    except CalibrationError as exc:
        print(f'Calibration failed: {exc}')
        return 1
    out = Path(args.out or DATA / 'models' / f'model-{stamp()}.json')
    save_json(out, model)
    save_json(out.with_suffix('.calibration.json'), report)
    print(f"Noise {report['noise']} counts, onset {report['onset']} counts, LEFT polarity {report['left_sign']:+d}")
    print(f"Trials {report['trials']}; examples used {report['detected']}")
    for warning in report['warnings']:
        print(f'Warning: {warning}')
    print('In-sample fit (optimistic, NOT a validation):')
    print_scores(report['in_sample'])
    print(f'Model saved to {out}. Every gesture stays disabled until held-out evaluation:')
    print(f'  python -m eog_acquisition evaluate <evaluation recording> --model "{out}" --apply')
    return 0


def cmd_evaluate(args):
    model = json.loads(Path(args.model).read_text(encoding='utf-8'))
    recording = load_recording(args.recording)
    if not held_out(model, recording) and not args.allow_same:
        print('This is the recording the model was calibrated on; record a separate evaluation set.')
        return 1
    report = evaluate(recording, model)
    print(f"Held-out evaluation: {report['minutes']} min. Targets: precision and recall >= {TARGETS['precision']}, "
          f"<= {TARGETS['false_per_minute']} false/min, >= {TARGETS['min_trials']} trials")
    print_scores(report['gestures'])
    print(f"All false events: {report['false_per_minute']}/min; during natural use: "
          f"{report['natural_false_per_minute']}/min")
    print(f"Gestures meeting every target: {', '.join(report['enable']) or 'none'}")
    save_json(Path(args.model).with_suffix(f'.evaluation-{stamp()}.json'), report)
    if args.apply:
        save_json(args.model, apply_evaluation(model, report))
        print(f"Model updated: enabled = {report['enable'] or 'none'}; every other gesture stays disabled.")
    return 0


def print_event(event, prefix=''):
    flag = '' if event.validated else '  [unvalidated candidate]'
    print(f'{prefix}  {event.gesture:5}  sample {event.start_index}  peak {event.peak}  '
          f'distance {event.distance}{flag}', flush=True)


def cmd_run(args):
    """Live classification to the terminal and an event log. Sends nothing anywhere else."""
    model = json.loads(Path(args.model).read_text(encoding='utf-8'))
    enabled = [g for g in GESTURES if model.get('enabled', {}).get(g)]
    if not enabled and not args.candidates:
        print('No gesture has passed held-out evaluation in this model. Run evaluate --apply, '
              'or add --candidates to watch unvalidated output.')
        return 1
    print(f"Enabled: {', '.join(enabled) or 'none'}" + ('; also showing unvalidated candidates' if args.candidates else ''))
    log = EventLog(args.events or DATA / 'events' / f'events-{stamp()}.csv')
    recorder = Recorder(args.record, port=args.port, in_plus=args.in_plus, notes='live run') if args.record else None
    classifier = GestureClassifier(model, emit_candidates=args.candidates)
    pipeline = Pipeline(classifier=classifier, recorder=recorder)
    pipeline.on_gesture(lambda event: print_event(event, log.write(event)))
    live = Live(args.port, pipeline)
    try:
        live.start()
        if args.plot:
            from .viewer import LiveView
            LiveView(pipeline, SAMPLE_RATE, lambda: status_line(pipeline.health.snapshot(), live.device.decoder)
                     ).run(live.keep_going)
        else:
            live.wait()
    finally:
        stats = live.finish()
        log.close()
        if recorder:
            recorder.close(dict(health=pipeline.health.snapshot(), firmware_stats=stats))
    summarise(pipeline.health.snapshot(), stats, live.device.decoder)
    print(f'Events logged to {log.path}')
    return 0


def cmd_replay(args):
    """Offline classification of a recording, with the protocol label at each event."""
    model = json.loads(Path(args.model).read_text(encoding='utf-8'))
    recording = load_recording(args.recording)
    pipeline = Pipeline(recording.sample_rate, classifier=GestureClassifier(model, emit_candidates=args.candidates))
    labels = dict(zip(recording.index.tolist(), recording.label))
    pipeline.on_gesture(lambda e: print_event(
        e, f'{e.start_index / recording.sample_rate:8.2f}s [{labels.get(e.start_index, "")}]'))
    replay(recording, pipeline)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eog_acquisition', description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    def device_args(p, in_plus=True):
        p.add_argument('--port', default=DEFAULT_PORT, help=f'serial port (default {DEFAULT_PORT})')
        if in_plus:
            p.add_argument('--in-plus', choices=('left', 'right', 'unknown'), default='unknown',
                           help='which outer eye corner the IN+ electrode is on (saved in the metadata)')

    p = sub.add_parser('check', help=cmd_check.__doc__)
    device_args(p, in_plus=False)
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--report')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('capture', help=cmd_capture.__doc__)
    device_args(p)
    p.add_argument('--minutes', type=float, default=5)
    p.add_argument('--out')
    p.add_argument('--plot', action='store_true')
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser('view', help=cmd_view.__doc__)
    device_args(p, in_plus=False)
    p.set_defaults(func=cmd_view)

    p = sub.add_parser('session', help=cmd_session.__doc__)
    device_args(p)
    p.add_argument('--set', choices=('calibration', 'evaluation'), required=True)
    p.add_argument('--auto-calibrate', action='store_true', help='save and calibrate automatically after a completed calibration session')
    p.add_argument('--trials', type=int, default=20, help='trials per gesture (default 20)')
    p.add_argument('--neutral', type=float, default=30, help='neutral-gaze seconds (default 30)')
    p.add_argument('--natural', type=float, default=120, help='natural-use seconds (default 120)')
    p.add_argument('--seed', type=int, help='trial order seed (default random)')
    p.add_argument('--out')
    p.add_argument('--plot', action='store_true')
    p.set_defaults(func=cmd_session)

    p = sub.add_parser('calibrate', help='derive a model from a calibration recording')
    p.add_argument('recording')
    p.add_argument('--out')
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser('evaluate', help='score a held-out recording; --apply enables passing gestures')
    p.add_argument('recording')
    p.add_argument('--model', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--allow-same', action='store_true', help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser('run', help=cmd_run.__doc__)
    device_args(p)
    p.add_argument('--model', required=True)
    p.add_argument('--candidates', action='store_true', help='also print gestures that have not passed evaluation')
    p.add_argument('--events', help='event log CSV (default data/events/events-<time>.csv)')
    p.add_argument('--record', help='also record raw samples to this path')
    p.add_argument('--plot', action='store_true')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('replay', help=cmd_replay.__doc__)
    p.add_argument('recording')
    p.add_argument('--model', required=True)
    p.add_argument('--candidates', action='store_true')
    p.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DeviceError as exc:
        print(f'Device error: {exc}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
