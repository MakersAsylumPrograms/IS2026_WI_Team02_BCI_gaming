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


def test_cli_joystick_and_run_reach_the_dashboard_check(capsys):
    from eog_acquisition.cli import main
    # No backend at this address: both must stop cleanly at the reachability check (no device needed).
    assert main(['joystick', '--dashboard', '--server', 'http://127.0.0.1:9']) == 1
    assert main(['run', '--dashboard', '--server', 'http://127.0.0.1:9', '--command', 'NEXT']) == 1
    assert capsys.readouterr().out.count('is not reachable') == 2
