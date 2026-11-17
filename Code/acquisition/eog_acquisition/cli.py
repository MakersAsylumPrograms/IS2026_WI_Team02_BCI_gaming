"""Command line: python -m eog_acquisition <command> --help"""
import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import chords_calibration, gestures, guided
from .ble import BLEEOGDevice, is_ble_port
from .dashboard import DEFAULT_SERVER, DashboardLink
from .device import DEFAULT_PORT, DeviceError, EOGDevice, usb_serial_ports
from .gestures import GestureDetector
from .joystick import DEFAULT_PROFILE, DIRECTIONS, JoystickReader, build_profile, load_profile, save_profile
from .processing import Pipeline
from .protocol import SAMPLE_RATE
from .recording import EventLog, Recorder

DATA = Path(__file__).resolve().parents[1] / 'data'
RATE_TOLERANCE = 0.01  # 250 Hz within 1%


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


def open_device(port, on_status):
    """(device, STATUS reply) for an opened board. 'auto': the first USB serial port the firmware
    answers on, else the board on Bluetooth. 'BLE' or 'BLE:<name>': Bluetooth only. Else that port."""
    if str(port).lower() != 'auto':
        device = BLEEOGDevice(port, on_status=on_status) if is_ble_port(port) else EOGDevice(port, on_status=on_status)
        return device, device.open()
    problems = []
    for candidate in usb_serial_ports():
        device = EOGDevice(candidate, on_status=on_status)
        try:
            return device, device.open()
        except DeviceError as exc:
            problems.append(f'{candidate}: {exc}')
    print('[device] No board answered on USB' + (f' ({"; ".join(problems)})' if problems else '')
          + '. Looking for it on Bluetooth...', flush=True)
    device = BLEEOGDevice('BLE', on_status=on_status)
    try:
        return device, device.open()
    except DeviceError as exc:
        raise DeviceError('No board found on USB or Bluetooth. Plug it into the UART USB port (or power it '
                          'for Bluetooth) and close Chords-Web, the Arduino Serial Monitor and any phone app. '
                          f'Bluetooth: {exc}') from exc


class Live:
    """One streaming run: device + pipeline + Ctrl+C / disconnect handling + status output."""

    def __init__(self, port, pipeline):
        self.port, self.pipeline, self.device = port, pipeline, None
        self.alive = threading.Event()
        self.alive.set()
        self._warned = set()
        signal.signal(signal.SIGINT, lambda *_: self.alive.clear())

    def _status(self, state, message):
        print(f'[device] {message}', flush=True)
        if state == 'disconnected':
            self.alive.clear()

    def keep_going(self):
        return self.alive.is_set()

    def open(self):
        self.device, reply = open_device(self.port, self._status)
        print(f'Connected: ESP32-S3 on {self.device.port} (status {reply or "?"})', flush=True)
        return reply

    def start(self):
        self.open()
        pins = self.device.command('PINS', wait=0.3)
        if pins == 'EOG=1 X=4 Y=5 SW=6 VCC=3V3':
            print(f'[firmware] {pins}')
            print(f'[firmware] Direct ADC: {self.device.command("JOY", wait=0.3)}')
        else:
            print('[firmware] Pin mapping not confirmed. Upload the current ESP32-S3.ino '
                  'for EOG=GPIO1 and joystick=GPIO4/5/6. Older firmware may read the wrong pins.')
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

    def show(self, plot):
        """The live plot until it is closed (which ends the run), else wait quietly."""
        if plot:
            from .viewer import LiveView
            LiveView(self.pipeline, SAMPLE_RATE, lambda: status_line(self.pipeline.health.snapshot(),
                                                                     self.device.decoder)).run(self.keep_going)
            self.alive.clear()
        else:
            self.wait(quiet=True)

    def finish(self):
        if self.device is None:
            return ''
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


def stream(args, pipeline, worker=None, plot=False, minutes=None):
    """Stream until Ctrl+C / disconnect / `minutes` / `worker(keep_going)` returns. Returns the Live run."""
    live = Live(args.port, pipeline)
    thread = None
    try:
        live.start()
        if worker is not None:
            def guide():
                try:
                    worker(live.keep_going)
                finally:
                    live.alive.clear()
            thread = threading.Thread(target=guide, daemon=True)
            thread.start()
        if plot or worker is not None:
            live.show(plot)
        else:
            live.wait(minutes * 60 if minutes else None)
        if thread is not None:
            thread.join(timeout=2)
    finally:
        live.stats = live.finish()
        if pipeline.recorder:
            pipeline.recorder.close(dict(health=pipeline.health.snapshot(), firmware_stats=live.stats))
    if live.device is not None:
        summarise(pipeline.health.snapshot(), live.stats, live.device.decoder)
    return live


# ---- board checks --------------------------------------------------------------------
def cmd_check(args):
    """Firmware identification, start/stop behaviour, rate and stream cleanliness (no electrodes needed)."""
    pipeline = Pipeline()
    live = Live(args.port, pipeline)
    before = live.open()
    device = live.device
    device.start(pipeline)
    live.wait(args.seconds, every=2)
    text_while_streaming, junk_while_streaming = list(device.decoder.text_lines), device.decoder.junk_bytes
    device.stop()
    leftover, end = b'', time.monotonic() + 0.5
    while isinstance(device, EOGDevice) and device.error is None and time.monotonic() < end:
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
    report = dict(port=device.port, checks=checks, health=health, stats=stats,
                  text_while_streaming=text_while_streaming, junk_bytes_while_streaming=junk_while_streaming,
                  leftover_bytes_after_stop=len(leftover))
    print(f'Report: {save_json(args.report or DATA / "diagnostics" / f"check-{stamp()}.json", report)}')
    return 0 if all(checks.values()) else 1


def cmd_capture(args):
    """Raw capture (default 5 minutes) with a sample-rate and counter-gap verdict."""
    recorder = Recorder(args.out or DATA / 'recordings' / f'capture-{stamp()}', port=args.port, in_plus=args.in_plus,
                        notes='raw capture')
    print(f'Recording {args.minutes} min to {recorder.path} (Ctrl+C stops early)')
    pipeline = Pipeline(recorder=recorder)
    stream(args, pipeline, plot=args.plot, minutes=args.minutes)
    health = pipeline.health.snapshot()
    rate = health['device_rate'] or 0
    ok = abs(rate - SAMPLE_RATE) / SAMPLE_RATE <= RATE_TOLERANCE and health['missing'] == 0 and health['stalls'] == 0
    print(f'{"PASS" if ok else "FAIL"}: {health["seconds"]} s, rate {rate} Hz, {health["missing"]} missing, '
          f'{health["stalls"]} stalls. Files: {recorder.path} and {recorder.meta_path}')
    return 0 if ok else 1


def cmd_view(args):
    """Live raw + eye-signal plots; nothing is recorded or sent."""
    stream(args, Pipeline(detector=GestureDetector(gestures.load_model(args.model))), plot=True)
    return 0


# ---- eyes ------------------------------------------------------------------------------
class TerminalReport:
    """Prints a guided sequence's progress: each new cue, or each new eye signal, on its own line."""

    def __init__(self):
        self.key = self.signal = None

    def __call__(self, fields):
        if fields.get('signals') is not None:              # test-eyes
            seen = fields['signals']
            if seen and seen[-1] is not self.signal:
                self.signal = seen[-1]
                print(f"   >>> {self.signal['kind'].replace('_', ' ')}", flush=True)
            return
        key = (fields.get('state'), fields.get('round'), fields.get('step'))
        if key != self.key and fields.get('cue'):
            self.key = key
            where = f"round {fields['round']}/{fields['rounds']}  " if fields.get('round') else ''
            print(f"   {where}{fields['cue'].upper()}", flush=True)


def print_result(result):
    m = result['metrics']
    print('\n' + '=' * 66)
    print(f" Looks: {m['look_hits']} of {m['looks']} gave the right command, {m['wrong_direction']} the wrong one")
    print(f" Double blinks: {m['select_hits']} of {m['double_blinks']} gave SELECT")
    print(f" False commands: {m['false_moves']} move, {m['false_selects']} SELECT in {m['minutes']} min")
    if 'left/right' in result['flipped']:
        print(' Left and right came out reversed: the model now expects IN+ and IN- the other way round.')
    if 'blink' in result['flipped']:
        print(' Blinks came out upside down for this electrode placement: the model now expects them that way.')
    if result['passed']:
        print(f" APPROVED. Saved to {result['saved']}; restart EqualCare to use it.")
    else:
        print(f" NOT approved: {'; '.join(result['shortfalls'])}.")
        print(f" The previous model stays in use. Candidate saved to {result['saved']}.")
    print('=' * 66)


def cmd_calibrate_eyes(args):
    """Calibrate the eyes now: about 2.5 minutes of cued looks and blinks, then a fit to them."""
    pipeline = Pipeline(detector=GestureDetector(gestures.load_model(args.model)))
    print('Eye calibration, two rounds of about 75 s. Follow each cue as soon as it appears:\n'
          '  LOOK LEFT / LOOK RIGHT  move only your eyes far to that side and hold\n'
          '  BLINK ONCE / BLINK TWICE  firm blinks, about half a second apart for twice\n'
          '  LOOK AROUND  use the screen normally. Ctrl+C stops (nothing is changed).')
    outcome = {}

    def worker(keep_going):
        outcome['result'], _ = guided.calibrate(pipeline, args.model, TerminalReport(), keep_going,
                                                port=args.port, in_plus=args.in_plus, seed=args.seed)

    stream(args, pipeline, worker, plot=args.plot)
    result = outcome.get('result')
    if not result or result['state'] != 'done':
        print('Stopped before the end; the eye model was not changed.')
        return 1
    print_result(result)
    return 0 if result['passed'] else 1


def cmd_test_eyes(args):
    """Show every eye signal (LEFT, RIGHT, BLINK, DOUBLE BLINK) as it happens; nothing is sent."""
    pipeline = Pipeline(detector=GestureDetector(gestures.load_model(args.model)))
    print(f'Eye test for {args.seconds:g} s: look left, look right, blink once, blink twice. '
          'Each signal is printed as the detector sees it. Ctrl+C stops.')
    stream(args, pipeline, lambda keep_going: guided.watch(pipeline, args.seconds, TerminalReport(), keep_going),
           plot=args.plot)
    return 0


def print_gesture_score(name, s):
    print(f"  {name}: {s['look_hits']}/{s['looks']} cued looks gave the right command, "
          f"{s['wrong_direction']} the wrong one; {s['select_hits']}/{s['double_blinks']} cued double blinks "
          f"gave SELECT; false commands: {s['false_moves']} move, {s['false_selects']} SELECT "
          f"in {s['minutes']} min")


def cmd_refit(args):
    """Fit the eye model to saved recordings (eye-calibration rounds and/or Chords-Web exports); like a
    calibration, it replaces --model only if the fit passes its check."""
    print(f'Calibrating on {len(args.recordings)} recordings (each is scored with a fit made without it):')
    for path in args.recordings:
        print(f'  {Path(path).name}')
    result, _ = guided.fit(args.recordings, gestures.load_model(args.model), args.model, stamp())
    print_result(result)
    return 0 if result['passed'] else 1


def cmd_replay_chords(args):
    """Replay a recording through the eye detector; --dashboard sends the commands, in real time."""
    model = gestures.load_model(args.model)
    recording = chords_calibration.load_any(args.recording)
    link = None
    if args.dashboard:
        link = connect_dashboard(args)
        if link is None:
            return 1
        print(f'Replaying {recording.seconds:.0f} s at {args.speed:g}x. Ctrl+C stops.')
    events, start = chords_calibration.detect(recording, model), time.monotonic()
    try:
        for event in events:
            at = event.index / recording.sample_rate
            if link:
                time.sleep(max(0.0, start + at / args.speed - time.monotonic()))
                link.send(event)
            print(f'{at:8.2f}s  {event.direction:12}  -> {event.command}  (pulse {event.shift:+.2f})', flush=True)
        if link:
            time.sleep(0.5)                               # let the last command leave
    except KeyboardInterrupt:
        pass
    finally:
        if link:
            link.stop()
    print_gesture_score(recording.name, chords_calibration.score(recording, events))
    if link:
        print(f'dashboard: {link.sent} sent, {link.dropped} dropped')
    return 0


# ---- dashboard -------------------------------------------------------------------------
def connect_dashboard(args):
    """DashboardLink after a successful heartbeat, or None (message printed)."""
    try:
        link = DashboardLink(args.server)
        link.check()
    except (OSError, RuntimeError) as exc:
        print(f'Dashboard backend at {args.server} is not reachable ({exc}). Start EqualCare '
              '(Start-EqualCare.cmd), sign in on the patient screen, then run this again.')
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
        print('Joystick: leave centred and release the button during startup. '
              '3V3 power; X=GPIO4, Y=GPIO5, SW=GPIO6. Standard sticks auto-centre; '
              'use joystick-calibrate only if necessary.')
    return reader


def cmd_run(args):
    """Live eyes + joystick; with --dashboard both drive the patient screen, which can also start calibration."""
    detector = GestureDetector(gestures.load_model(args.model))
    print(f'Eyes ({Path(args.model).name}, IN+ at the {detector.model.in_plus} eye): look LEFT and hold -> '
          'PREVIOUS, look RIGHT and hold -> NEXT, double blink -> SELECT.')
    joystick = None if args.no_joystick else make_joystick(args)
    if not gestures.dashboard_approved(detector.model):
        print('[EOG] This eye model has not passed its check'
              + (': sending anyway (--allow-provisional-eog).' if args.allow_provisional_eog
                 else '. Eye commands are shown but not sent until a calibration passes.'))
    link = connect_dashboard(args) if args.dashboard else None
    if args.dashboard and link is None:
        return 1
    log = EventLog(args.events or DATA / 'events' / f'events-{stamp()}.csv')
    recorder = Recorder(args.record, port=args.port, in_plus=args.in_plus, notes='live run') if args.record else None
    pipeline = Pipeline(detector=detector, recorder=recorder, joystick=joystick)
    control = None
    if link:
        control = guided.CalibrationControl(pipeline, link, args.model, port=args.port, in_plus=args.in_plus)
        link.on_calibration(control.handle)
    count = [0]

    def on_event(event):
        if control and control.busy:                 # calibrating or testing: the screen is showing cues
            return
        log.write(event)
        eog_forward = gestures.dashboard_approved(pipeline.detector.model) or args.allow_provisional_eog
        sent = link is not None and (event.source == 'joystick' or eog_forward)
        if sent:
            link.send(event)
        if event.source == 'eog':
            count[0] += 1
            print(f'  EYES      {event.direction} #{count[0]}  (pulse {event.shift:+.2f})  '
                  + (f'-> {event.command}' if sent else '[not sent]'), flush=True)
        else:
            print(f'  JOYSTICK  {event.direction:5}  -> {event.command}' + ('' if sent else '  (not sent)'),
                  flush=True)

    pipeline.on_event(on_event)
    last_status = [0.0]

    def joystick_status(processed):
        if not joystick or processed.sample.host_time - last_status[0] < 5:
            return
        last_status[0] = processed.sample.host_time
        print(f'[joystick] raw X/Y/SW={processed.sample.joystick}; '
              f'centre={joystick.centre}; ready={joystick.ready}', flush=True)
        if not joystick.ready:
            print('[joystick] Waiting for stable centre: release stick. Zero/rail readings: '
                  'check 3V3, common GND, GPIO4/5, and reflash the updated firmware.', flush=True)
    pipeline.on_sample(joystick_status)
    live = Live(args.port, pipeline)
    try:
        live.start()
        print('Running. Eyes: 3 s warm-up, then look to a side and hold. Ctrl+C (or close the plot) stops.')
        live.show(args.plot)
    finally:
        stats = live.finish()
        log.close()
        if link:
            link.stop()
        if recorder:
            recorder.close(dict(health=pipeline.health.snapshot(), firmware_stats=stats))
    if live.device is not None:
        summarise(pipeline.health.snapshot(), stats, live.device.decoder)
    print(f'{count[0]} eye command(s); events logged to {log.path}'
          + (f'; dashboard: {link.sent} sent, {link.dropped} dropped' if link else ''))
    return 0


# ---- joystick --------------------------------------------------------------------------
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
    pipeline = Pipeline()
    pipeline.on_sample(lambda p: rows.append((p.label, *p.sample.joystick[:2])) if p.sample.joystick else None)

    def guide(keep_going):
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
    stream(args, pipeline, guide)
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m eog_acquisition', description=__doc__)
    sub = parser.add_subparsers(dest='subcommand', required=True)

    def add(name, func, port=True, in_plus=False, model=False, server=False, plot=False):
        p = sub.add_parser(name, help=func.__doc__)
        p.set_defaults(func=func)
        if port:
            p.add_argument('--port', default=DEFAULT_PORT,
                           help='auto (default: USB, then Bluetooth), a serial port such as COM7, or BLE')
        if in_plus:
            p.add_argument('--in-plus', choices=('left', 'right', 'unknown'), default='left',
                           help='which outer eye corner the IN+ electrode is on (saved with recordings)')
        if model:
            p.add_argument('--model', default=str(gestures.DEFAULT_MODEL),
                           help='eye model (default: models/eog-gestures.json)')
        if server:
            p.add_argument('--server', default=DEFAULT_SERVER)
        if plot:
            p.add_argument('--plot', action='store_true', help='also show the live signal plot')
        return p

    p = add('check', cmd_check)
    p.add_argument('--seconds', type=float, default=10)
    p.add_argument('--report')

    p = add('capture', cmd_capture, in_plus=True, plot=True)
    p.add_argument('--minutes', type=float, default=5)
    p.add_argument('--out')

    add('view', cmd_view, model=True)

    p = add('calibrate-eyes', cmd_calibrate_eyes, in_plus=True, model=True, plot=True)
    p.add_argument('--seed', type=int, help='left/right order seed (default random)')

    p = add('test-eyes', cmd_test_eyes, model=True, plot=True)
    p.add_argument('--seconds', type=float, default=60.0)

    p = add('refit', cmd_refit, port=False, model=True)
    p.add_argument('recordings', nargs='+', help='eye-calibration-*.csv recordings and/or Chords-Web CSVs')

    p = add('replay-chords', cmd_replay_chords, port=False, model=True, server=True)
    p.add_argument('recording', help='Chords-Web CSV or one of this program\'s recordings')
    p.add_argument('--dashboard', action='store_true', help='send the commands to the EqualCare backend')
    p.add_argument('--speed', type=float, default=1.0, help='with --dashboard: playback speed (default 1)')

    p = add('run', cmd_run, in_plus=True, model=True, server=True, plot=True)
    p.add_argument('--dashboard', action='store_true', help='send eye and joystick commands to the EqualCare backend')
    p.add_argument('--allow-provisional-eog', action='store_true',
                   help='send eye commands even when the model has not passed its check')
    p.add_argument('--no-joystick', action='store_true', help='ignore the joystick channels')
    p.add_argument('--invert-joystick', action='store_true', help='swap NEXT and PREVIOUS for the joystick')
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)
    p.add_argument('--events', help='event log CSV (default data/events/events-<time>.csv)')
    p.add_argument('--record', help='also record raw samples to this path')

    p = add('joystick', cmd_joystick, server=True)
    p.add_argument('--dashboard', action='store_true', help='also send joystick commands to the backend')
    p.add_argument('--invert-joystick', action='store_true')
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)

    p = add('joystick-calibrate', cmd_joystick_calibrate)
    p.add_argument('--joystick-profile', default=str(DEFAULT_PROFILE), help=argparse.SUPPRESS)

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
