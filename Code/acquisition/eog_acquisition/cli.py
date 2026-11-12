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

from .cycle import (DEFAULT_MODEL, MIN_RECALL, CalibrationError, CycleDetector, calibrate, detect, load_model,
                    save_model, score, evaluate_model, dashboard_approved)
from .dashboard import COMMANDS, DEFAULT_SERVER, DashboardLink
from .joystick import DEFAULT_PROFILE, DIRECTIONS, JoystickReader, build_profile, load_profile, save_profile
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
        'Channels 5-16 inactive (zero)': device.decoder.inactive_nonzero == 0,
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


def _stream(args, recorder, minutes=None, plot=False, session=None):
    """Stream until Ctrl+C / disconnect / `minutes` / the guided session ends."""
    pipeline = Pipeline(recorder=recorder)
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
        return cmd_calibrate(argparse.Namespace(recordings=[str(recorder.path)], out=None, min_recall=MIN_RECALL))
    if args.set == 'calibration':
        print(f'Next: python -m eog_acquisition calibrate "{recorder.path}"')
    else:
        print(f'Next: python -m eog_acquisition evaluate "{recorder.path}" --model <model.json>')
    return 0 if finished.get('ok') else 1


def print_score(name, s):
    if s['trials']:
        print(f"  {name}: {s['hits']}/{s['trials']} deliberate looks gave a CYCLE (recall {s['recall']}); "
              f"precision {s['precision']} overall, {s['protocol_precision']} during the protocol")
        print(f"    false CYCLEs: {s['false_during_protocol']} during the protocol ({s['false_per_minute']}/min, "
              f"{s['false_after_blink_cues']} after blink cues); {s['natural_events']} during free use "
              f"({s['natural_per_minute']}/min)")
    if s['missed']:
        print(f"    missed trials: {s['missed']}")


def cmd_calibrate(args):
    """Fit the CYCLE detector (gaze recovery, threshold, hold) to guided-session recordings."""
    recordings = [load_recording(path) for path in args.recordings]
    try:
        model, report = calibrate(recordings, min_recall=args.min_recall,
                                  sources=[str(Path(p).with_suffix('.csv')) for p in args.recordings])
    except CalibrationError as exc:
        print(f'Calibration failed: {exc}')
        return 1
    out = save_model(model, args.out or DATA / 'models' / f'cycle-{stamp()}.json', report)
    print(f"Pill time constant {report['tau']} s (plateau flatness {report['plateau_flatness']}), typical held "
          f"look {report['typical_shift']} counts from {report['clean_looks']} clean looks")
    print(f"CYCLE = gaze shift over {model.threshold} counts held for {model.hold} s "
          f"(most precise setting with recall >= {args.min_recall})")
    for warning in report['warnings']:
        print(f'Warning: {warning}')
    print('In-sample (optimistic, the same data it was fitted on):')
    for path, s in zip(args.recordings, report['per_recording']):
        print_score(Path(path).stem, s)
    print(f'Model saved to {out}')
    print(f'Check it on another recording:  python -m eog_acquisition evaluate <recording> --model "{out}"')
    return 0


def cmd_evaluate(args):
    """Score a labelled recording (or count CYCLEs in an unlabelled one) with a CYCLE model."""
    model = load_model(args.model)
    for path in args.recordings:
        recording = load_recording(path)
        events = detect(recording, model)
        s = score(recording, events)
        validation = evaluate_model(recording, model)
        report_path = Path(args.model).with_suffix('.evaluation.json')
        save_json(report_path, validation)
        print(f"Evaluation approved: {validation['passed']}; independent: {validation['independent']}; report: {report_path}")
        if args.apply:
            data = json.loads(Path(args.model).read_text(encoding='utf-8'))
            data['validation'] = validation
            save_json(args.model, data)
        if s['trials']:
            print_score(Path(path).stem, s)
        else:
            times = ', '.join(f'{(e.index - recording.index[0]) / recording.sample_rate:.1f}s' for e in events)
            print(f"  {Path(path).stem}: {len(events)} CYCLE(s) in {len(recording.raw) / recording.sample_rate:.0f} s"
                  f"{': ' + times if times else ''}")
    return 0


def connect_dashboard(args):
    """DashboardLink after a successful heartbeat, or None (message printed)."""
    try:
        link = DashboardLink(args.server, command=getattr(args, 'command', 'PREVIOUS'))
        link.check()
    except (OSError, RuntimeError) as exc:
        print(f'Dashboard backend at {args.server} is not reachable ({exc}). Start it with '
              f'Code/backend/start.ps1, sign in on the patient screen, then run this again.')
        return None
    print(f'Dashboard backend at {args.server}: connected as device {link.device_id}.')
    return link.start()


def make_joystick(args):
    """JoystickReader using the saved calibration profile when there is one; prints what it will do."""
    profile = load_profile(args.joystick_profile)
    reader = JoystickReader(invert=args.invert_joystick, profile=profile)
    if profile:
        known = ', '.join(f'{d.lower()} -> {reader._command(d)}' for d in DIRECTIONS if d in reader.rules)
        print(f'Joystick (calibrated): {known or "no directions"}; press -> SELECT.')
    else:
        print('Joystick: press -> SELECT. Directions need a calibration for this module: '
              'python -m eog_acquisition joystick-calibrate  (a standard joystick also works without one).')
    return reader


def cmd_run(args):
    """Live eyes (CYCLE) + joystick; with --dashboard both drive the patient screen."""
    model = load_model(args.model)
    print(f'Eyes: CYCLE = look to EITHER side over {model.threshold} counts and hold {model.hold} s '
          f'(the look back to centre does not count) -> {args.command}.')
    joystick = None if args.no_joystick else make_joystick(args)
    detector = CycleDetector(model)
    if args.dashboard and not dashboard_approved(model):
        if joystick is None:
            print('EOG model is not approved on independent evaluation. Evaluate with --apply first.')
            return 1
        print('EOG model is unverified: dashboard runs JOYSTICK ONLY. EOG preview/calibration remains available.')
        detector = None
    link = None
    if args.dashboard:
        link = connect_dashboard(args)
        if link is None:
            return 1
    log = EventLog(args.events or DATA / 'events' / f'events-{stamp()}.csv')
    recorder = Recorder(args.record, port=args.port, in_plus=args.in_plus, notes='live run') if args.record else None
    pipeline = Pipeline(detector=detector, recorder=recorder, joystick=joystick)
    count = [0]

    def on_event(event):
        log.write(event)
        if link:
            link.send(event)
        if event.source == 'eog':
            count[0] += 1
            print(f'  EYES      CYCLE #{count[0]}  (shift {event.shift:+.0f} counts)'
                  + (f'  -> {args.command}' if link else ''), flush=True)
        else:
            print(f'  JOYSTICK  {event.direction:5}  -> {event.command}' + ('' if link else '  (not sent)'),
                  flush=True)

    pipeline.on_event(on_event)
    live = Live(args.port, pipeline)
    try:
        live.start()
        print('Running. Eyes: 3 s warm-up, then look to either side and hold. Ctrl+C (or close the plot) stops.')
        if args.plot:
            from .viewer import LiveView
            LiveView(pipeline, SAMPLE_RATE, lambda: status_line(pipeline.health.snapshot(), live.device.decoder)
                     ).run(live.keep_going)
        else:
            live.wait()
    finally:
        stats = live.finish()
        log.close()
        if link:
            link.stop()
        if recorder:
            recorder.close(dict(health=pipeline.health.snapshot(), firmware_stats=stats))
    summarise(pipeline.health.snapshot(), stats, live.device.decoder)
    print(f'{count[0]} eye CYCLE(s); events logged to {log.path}'
          + (f'; dashboard: {link.sent} sent, {link.dropped} dropped' if link else ''))
    return 0


def cmd_joystick(args):
    """Live joystick test: raw X/Y/button and each push or press; --dashboard also sends them."""
    reader = make_joystick(args)
    pipeline = Pipeline(joystick=reader)
    link = connect_dashboard(args) if args.dashboard else None
    if args.dashboard and link is None:
        return 1
    state = {'next': 0.0, 'ready': False}

    def on_sample(processed):
        x, y, button = processed.sample.joystick or (0, 0, 0)
        if reader.mode == 'centre' and not state['ready']:
            state['ready'] = True
            print(f'  centre measured: x={reader.centre[0]:.0f} y={reader.centre[1]:.0f}. Push the stick.', flush=True)
        if processed.sample.host_time >= state['next']:
            state['next'] = processed.sample.host_time + 0.5
            print(f'  x={x:4d} y={y:4d} button={"PRESSED" if button else "idle"}', flush=True)

    def on_event(event):
        if link:
            link.send(event)
        print(f'  >>> {event.direction:5} -> {event.command}' + ('  (sent)' if link else ''), flush=True)

    pipeline.on_sample(on_sample)
    pipeline.on_event(on_event)
    live = Live(args.port, pipeline)
    try:
        live.start()
        print('Joystick test. Ctrl+C stops. A reading stuck at 0 or 4095 means a wire or power problem.')
        live.wait(quiet=True)
    finally:
        live.finish()
        if link:
            link.stop()
    if not reader.ready:
        print('Directions were not active: run  python -m eog_acquisition joystick-calibrate  once for this module.')
    return 0


def cmd_joystick_calibrate(args):
    """30-second guided joystick calibration: learns this module's rest and each push direction."""
    steps = [('REST', 3.0, 'Leave the stick untouched')]
    for _ in range(2):
        for direction in DIRECTIONS:
            steps += [('MOVING', 0.4, f'Push {direction} all the way and hold'), (direction, 1.6, ''),
                      ('MOVING', 0.4, 'Let go'), ('REST', 1.1, '')]
    rows = []
    done = {}

    def guide(pipeline, keep_going):
        pipeline.on_sample(lambda p: rows.append((p.label, *p.sample.joystick[:2])) if p.sample.joystick else None)
        time.sleep(1.0)
        for label, seconds, text in steps:
            pipeline.set_label(label)
            if text:
                print(f'  {text}', flush=True)
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if not keep_going():
                    return
                time.sleep(0.02)
        pipeline.set_label('END')
        done['ok'] = True

    print('Joystick calibration (about 30 s). Follow each instruction as soon as it appears.')
    _stream(args, None, session=guide)
    if not done.get('ok'):
        print('Calibration stopped early; nothing saved.')
        return 1
    try:
        profile, report = build_profile([r for r in rows if r[0] in ('REST', *DIRECTIONS)])
    except ValueError as exc:
        print(f'Calibration failed: {exc}')
        return 1
    for line in report:
        print('  ' + line)
    if not profile['rules']:
        print('No direction could be told apart from the resting noise; nothing saved. Check the joystick power.')
        return 1
    print(f'Saved {save_profile(profile, args.joystick_profile)}. `joystick` and `run` use it automatically.')
    return 0


def cmd_replay(args):
    """Offline CYCLE detection on a recording, with the protocol label at each CYCLE."""
    model = load_model(args.model)
    recording = load_recording(args.recording)
    position = {int(v): i for i, v in enumerate(recording.index)}
    for event in detect(recording, model):
        i = position[event.index]
        print(f'{i / recording.sample_rate:8.2f}s  CYCLE  [{recording.label[i] or "-"}]  shift {event.shift:+.0f}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eog_acquisition', description=__doc__)
    sub = parser.add_subparsers(dest='subcommand', required=True)  # 'command' is run's --command option

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

    p = sub.add_parser('calibrate', help=cmd_calibrate.__doc__)
    p.add_argument('recordings', nargs='+', help='guided-session recording(s) with LEFT/RIGHT trials')
    p.add_argument('--out', help='model path (default data/models/cycle-<time>.json)')
    p.add_argument('--min-recall', type=float, default=MIN_RECALL,
                   help=f'share of deliberate looks that must give a CYCLE; precision is maximised above it '
                        f'(default {MIN_RECALL})')
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser('evaluate', help=cmd_evaluate.__doc__)
    p.add_argument('recordings', nargs='+')
    p.add_argument('--model', default=str(DEFAULT_MODEL))
    p.add_argument('--apply', action='store_true', help='persist independent evaluation approval in the model')
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser('run', help=cmd_run.__doc__)
    device_args(p)
    p.add_argument('--model', default=str(DEFAULT_MODEL), help='CYCLE model (default: the team model in models/)')
    p.add_argument('--dashboard', action='store_true', help='send each CYCLE to the EqualCare backend')
    p.add_argument('--server', default=DEFAULT_SERVER)
    p.add_argument('--command', choices=COMMANDS, default='PREVIOUS',
                   help='dashboard command per eye CYCLE (default PREVIOUS: the highlight moves left)')
    p.add_argument('--no-joystick', action='store_true', help='ignore the joystick channels')
    p.add_argument('--invert-joystick', action='store_true', help='swap NEXT and PREVIOUS for the joystick')
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)
    p.add_argument('--events', help='event log CSV (default data/events/events-<time>.csv)')
    p.add_argument('--record', help='also record raw samples to this path')
    p.add_argument('--plot', action='store_true')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('joystick', help=cmd_joystick.__doc__)
    device_args(p, in_plus=False)
    p.add_argument('--dashboard', action='store_true', help='also send joystick commands to the backend')
    p.add_argument('--server', default=DEFAULT_SERVER)
    p.add_argument('--invert-joystick', action='store_true')
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_joystick)

    p = sub.add_parser('joystick-calibrate', help=cmd_joystick_calibrate.__doc__)
    device_args(p, in_plus=False)
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_joystick_calibrate)

    p = sub.add_parser('replay', help=cmd_replay.__doc__)
    p.add_argument('recording')
    p.add_argument('--model', default=str(DEFAULT_MODEL))
    p.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DeviceError as exc:
        print(f'Device error: {exc}')
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'Setup/input error: {exc}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
