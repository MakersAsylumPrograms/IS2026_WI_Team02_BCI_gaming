"""DashboardLink against the REAL EqualCare backend (Code/backend/app.py) served over HTTP."""
import sys
import threading
import time
from pathlib import Path

import pytest

from eog_acquisition import dashboard
from eog_acquisition.cycle import CycleEvent
from eog_acquisition.dashboard import DashboardLink

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
EVENT = CycleEvent('CYCLE', 100, 50, 0.0, -1, 2000.0)


@pytest.fixture
def backend(tmp_path):
    pytest.importorskip('flask')
    from werkzeug.serving import make_server
    sys.path.insert(0, str(BACKEND))
    try:
        from app import create_app
    finally:
        sys.path.remove(str(BACKEND))
    app = create_app(dict(TESTING=True, DATABASE=str(tmp_path / 'db.sqlite3'), PATIENT_PASSWORD='patient-test',
                          STAFF_PASSWORD='staff-test', DEVICE_TOKEN='device-test', SECRET_KEY='test-secret'))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield app, f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def patient_screen(app):
    client = app.test_client()
    assert client.post('/api/v1/session', json={'role': 'patient', 'password': 'patient-test'},
                       headers={'X-EqualCare-Client': 'dashboard'}).status_code == 200

    def poll(previous=None):
        url = '/api/v1/input-events' + (f'?after={previous["cursor"]}&epoch={previous["epoch"]}' if previous else '')
        return client.get(url, headers={'X-Consumer-ID': 'tab-1'}).json
    return client, poll


def wait_for(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_each_cycle_reaches_the_patient_screen_as_one_command(backend):
    app, url = backend
    client, poll = patient_screen(app)
    first = poll()                                   # the screen is listening
    messages = []
    link = DashboardLink(url, device_id='esp32-01', token='device-test', command='PREVIOUS', say=messages.append)
    link.check()
    link.start()
    try:
        link.send(EVENT)
        link.send(EVENT)
        assert wait_for(lambda: link.sent == 2), messages
        events = poll(first)['events']
    finally:
        link.stop()
    assert [(e['command'], e['source'], e['seq']) for e in events] == [('PREVIOUS', 'eog', 1), ('PREVIOUS', 'eog', 2)]
    assert client.get('/api/v1/device').json['connected']      # heartbeat: the dashboard shows the device online
    assert messages == []


def test_joystick_events_keep_their_own_command_and_source(backend):
    from eog_acquisition.joystick import JoystickEvent
    app, url = backend
    _, poll = patient_screen(app)
    first = poll()
    link = DashboardLink(url, device_id='esp32-01', token='device-test', command='PREVIOUS', say=print).start()
    try:
        link.send(JoystickEvent('SELECT', 'PRESS', 10, 0.0))
        link.send(JoystickEvent('NEXT', 'RIGHT', 20, 0.0))
        link.send(EVENT)                                    # an eye CYCLE in between
        assert wait_for(lambda: link.sent == 3)
        events = poll(first)['events']
    finally:
        link.stop()
    assert [(e['command'], e['source']) for e in events] == [('SELECT', 'joystick'), ('NEXT', 'joystick'),
                                                             ('PREVIOUS', 'eog')]


def test_eye_gestures_reach_the_patient_screen_with_their_own_commands(backend):
    from eog_acquisition.gestures import GestureEvent
    app, url = backend
    _, poll = patient_screen(app)
    first = poll()
    link = DashboardLink(url, device_id='esp32-01', token='device-test', command='NEXT', say=print).start()
    try:
        for command, gesture in (('PREVIOUS', 'LEFT'), ('NEXT', 'RIGHT'), ('SELECT', 'DOUBLE_BLINK')):
            link.send(GestureEvent(command, gesture, 100, 50, 0.0, 0.8))
        assert wait_for(lambda: link.sent == 3)
        events = poll(first)['events']
    finally:
        link.stop()
    assert [(e['command'], e['source']) for e in events] == [('PREVIOUS', 'eog'), ('NEXT', 'eog'), ('SELECT', 'eog')]


def test_replaying_a_chords_recording_drives_the_patient_screen(backend, capsys, monkeypatch):
    """The team model on the 2nd calibration recording, through the CLI, DashboardLink and the real backend."""
    from eog_acquisition import cli, gestures
    recording = (Path(__file__).resolve().parents[1] / 'data' / 'recordings'
                 / 'Fresh Calibration Recording Through Chords' / '2nd one - better.csv')
    if not recording.exists() or not gestures.DEFAULT_MODEL.exists():
        pytest.skip('the Chords-Web calibration recordings or the team model are not here')
    app, url = backend
    _, poll = patient_screen(app)
    monkeypatch.setenv('EQUALCARE_DEVICE_ID', 'esp32-01')
    monkeypatch.setenv('EQUALCARE_DEVICE_TOKEN', 'device-test')
    state, seen, done = poll(), [], threading.Event()

    def screen():                                    # what the patient page does: poll, act on each command
        nonlocal state
        while not done.is_set():
            state = poll(state)
            seen.extend(e['command'] for e in state['events'])
            time.sleep(0.02)
    watcher = threading.Thread(target=screen, daemon=True)
    watcher.start()
    try:
        assert cli.main(['replay-chords', str(recording), '--dashboard', '--server', url, '--speed', '40']) == 0
        assert wait_for(lambda: len(seen) == 17)
    finally:
        done.set()
        watcher.join(timeout=2)
    left_right = ['PREVIOUS', 'NEXT']
    assert seen == left_right * 3 + ['SELECT'] * 5 + left_right * 2 + ['SELECT'] * 2
    assert '17 sent, 0 dropped' in capsys.readouterr().out


def test_run_uses_the_approved_team_gesture_model_by_default():
    from eog_acquisition import cli, gestures
    if not gestures.DEFAULT_MODEL.exists():
        pytest.skip('the team model is not here')
    parser_default = cli.load_model(gestures.DEFAULT_MODEL)
    assert isinstance(parser_default, gestures.GestureModel) and cli.dashboard_approved(parser_default)
    with pytest.raises(ValueError):
        cli.cycle_model(gestures.DEFAULT_MODEL)       # evaluate / replay are for CYCLE models


def test_wrong_token_is_reported_and_not_retried(backend):
    _, url = backend
    messages = []
    link = DashboardLink(url, device_id='esp32-01', token='wrong', say=messages.append)
    with pytest.raises(OSError):
        link.check()
    link.start()
    try:
        link.send(EVENT)
        assert wait_for(lambda: any('HTTP 401' in m for m in messages))
    finally:
        link.stop()
    assert link.dropped == 0 and link.sent == 0


def test_unreachable_backend_drops_commands_instead_of_delivering_late(monkeypatch):
    monkeypatch.setattr(dashboard, 'FRESH_FOR', 0.3)
    messages = []
    link = DashboardLink('http://127.0.0.1:9', device_id='esp32-01', token='t', say=messages.append).start()
    try:
        link.send(EVENT)
        assert wait_for(lambda: link.dropped == 1)
    finally:
        link.stop()
    assert link.sent == 0


def test_credentials_come_from_the_environment_first(monkeypatch, tmp_path):
    (tmp_path / 'credentials.json').write_text('{"DEVICE_ID": "file-id", "DEVICE_TOKEN": "file-token"}')
    assert dashboard.device_credentials(tmp_path / 'credentials.json') == ('file-id', 'file-token')
    monkeypatch.setenv('EQUALCARE_DEVICE_TOKEN', 'env-token')
    assert dashboard.device_credentials(tmp_path / 'credentials.json') == ('file-id', 'env-token')
    monkeypatch.delenv('EQUALCARE_DEVICE_TOKEN')
    with pytest.raises(RuntimeError):
        dashboard.device_credentials(tmp_path / 'missing.json')


def test_cli_joystick_and_run_reach_the_dashboard_check(capsys, monkeypatch):
    monkeypatch.setenv('EQUALCARE_DEVICE_TOKEN', 'test-token')
    from eog_acquisition.cli import main
    # No backend at this address: both must stop cleanly at the reachability check (no device needed).
    assert main(['joystick', '--dashboard', '--server', 'http://127.0.0.1:9']) == 1
    assert main(['run', '--dashboard', '--server', 'http://127.0.0.1:9', '--command', 'NEXT']) == 1
    assert capsys.readouterr().out.count('is not reachable') == 2


@pytest.mark.parametrize('approved,override', [(False, False), (True, False), (False, True)])
def test_live_preview_forwards_only_approved_eog(monkeypatch, tmp_path, approved, override):
    from types import SimpleNamespace
    from eog_acquisition import cli
    from eog_acquisition.cycle import CycleModel
    callbacks, delivered = [], []
    link = SimpleNamespace(send=delivered.append, stop=lambda: None, sent=0, dropped=0)
    class Pipeline:
        def __init__(self, **kwargs):
            assert kwargs['detector'] is not None  # preview remains live
            self.health = SimpleNamespace(snapshot=lambda: {})
        def on_event(self, callback):
            callbacks.append(callback)
        def on_sample(self, callback):
            pass
    class Live:
        def __init__(self, port, pipeline):
            self.device = SimpleNamespace(decoder=None)
        def start(self):
            pass
        def wait(self):
            for source, command in [('eog', 'CYCLE'), ('joystick', 'SELECT')]:
                callbacks[0](SimpleNamespace(source=source, command=command, index=1,
                    host_time=1.0, direction='PRESS', shift=100))
        def finish(self):
            return ''
    monkeypatch.setattr(cli, 'load_model', lambda _: CycleModel())
    monkeypatch.setattr(cli, 'dashboard_approved', lambda _: approved)
    monkeypatch.setattr(cli, 'make_joystick', lambda _: object())
    monkeypatch.setattr(cli, 'connect_dashboard', lambda _: link)
    monkeypatch.setattr(cli, 'Pipeline', Pipeline)
    monkeypatch.setattr(cli, 'Live', Live)
    monkeypatch.setattr(cli, 'summarise', lambda *args: None)
    args = ['run', '--dashboard', '--events', str(tmp_path / 'events.csv')]
    if override:
        args.append('--allow-provisional-eog')
    assert cli.main(args) == 0
    assert [e.source for e in delivered] == (['eog', 'joystick'] if approved or override else ['joystick'])


def test_bridge_delivers_mixed_inputs_to_real_patient_api(tmp_path):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from threading import Thread
    from werkzeug.serving import make_server
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
    from app import create_app
    app = create_app(dict(TESTING=True, DATABASE=str(tmp_path / 'requests.sqlite3'),
        PATIENT_PASSWORD='patient-test', STAFF_PASSWORD='staff-test', DEVICE_TOKEN='device-test',
        SECRET_KEY='test-secret'))
    patient = app.test_client()
    assert patient.post('/api/v1/session', json={'role': 'patient', 'password': 'patient-test'},
        headers={'X-EqualCare-Client': 'dashboard'}).status_code == 200
    headers = {'X-Consumer-ID': 'bridge-test'}
    initial = patient.get('/api/v1/input-events', headers=headers).json
    server = make_server('127.0.0.1', 0, app)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    link = DashboardLink(f'http://127.0.0.1:{server.server_port}',
        device_id='esp32-01', token='device-test').start()
    try:
        link.send(SimpleNamespace(source='eog'))
        link.send(SimpleNamespace(source='joystick', command='SELECT'))
        link.send(SimpleNamespace(source='eog', command='SELECT'))
        assert wait_for(lambda: link.sent == 3)
        response = patient.get('/api/v1/input-events', query_string={
            'after': initial['cursor'], 'epoch': initial['epoch']}, headers=headers)
        assert response.status_code == 200
        assert [event['command'] for event in response.json['events']] == ['PREVIOUS', 'SELECT', 'SELECT']
    finally:
        link.stop()
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
