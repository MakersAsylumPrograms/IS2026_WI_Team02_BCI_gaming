import concurrent.futures
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app, TYPES


@pytest.fixture
def system(tmp_path):
    now = [100.0]
    config = dict(TESTING=True, DATABASE=str(tmp_path / 'db.sqlite3'),
                  PATIENT_PASSWORD='patient-test', STAFF_PASSWORD='staff-test',
                  DEVICE_TOKEN='device-test', SECRET_KEY='test-secret', CLOCK=lambda: now[0])
    app = create_app(config)
    return app, config, now


def login(app, role):
    client = app.test_client()
    result = client.post('/api/v1/session', json={'role': role, 'password': role + '-test', 'login_id': 'staff'},
                         headers={'X-EqualCare-Client': 'dashboard'})
    assert result.status_code == 200
    return client, {'X-CSRF-Token': result.json['csrf']}


def submit(client, headers, kind='WATER', key='request-1'):
    return client.post('/api/v1/requests', json={'type': kind, 'idempotency_key': key}, headers=headers)


def emit(app, seq=1, boot='boot-1', **changes):
    event = dict(device_id='esp32-01', boot_id=boot, seq=seq, source='joystick', command='NEXT')
    event.update(changes)
    return app.test_client().post('/api/v1/input-events', json=event,
                                  headers={'Authorization': 'Bearer device-test'})


def poll(client, previous=None, consumer='tab-1'):
    url = '/api/v1/input-events'
    if previous:
        url += f'?after={previous["cursor"]}&epoch={previous["epoch"]}'
    return client.get(url, headers={'X-Consumer-ID': consumer})


@pytest.mark.parametrize('kind', TYPES)
def test_closed_loop_and_retry(system, kind):
    app, _, _ = system
    patient, ph = login(app, 'patient')
    staff, sh = login(app, 'staff')
    initial = submit(patient, ph, kind)
    assert initial.status_code == 201
    rid = initial.json['request']['id']
    assert submit(patient, ph, kind).json['request']['id'] == rid
    assert submit(patient, ph, kind, 'another-key').json['request']['id'] == rid
    assert staff.post(f'/api/v1/requests/{rid}/resolve', headers=sh).status_code == 409
    for action, status in [('acknowledge', 'ACKNOWLEDGED'), ('resolve', 'RESOLVED')]:
        for _ in range(2):
            result = staff.post(f'/api/v1/requests/{rid}/{action}', headers=sh)
            assert result.json['request']['status'] == status
    assert submit(patient, ph, kind, 'another-key').json['request']['status'] == 'RESOLVED'
    assert submit(patient, ph, kind, 'new-request').status_code == 201
    rows = patient.get('/api/v1/requests').json['requests']
    assert len(rows) == 2 and rows[0]['acknowledged_at'] and rows[0]['resolved_at']
    assert staff.post(f'/api/v1/requests/{rid}/acknowledge', headers=sh).json['request']['status'] == 'RESOLVED'


def test_roles_csrf_and_validation(system):
    app, _, _ = system
    guest = app.test_client()
    patient, ph = login(app, 'patient')
    staff, sh = login(app, 'staff')
    assert guest.get('/api/v1/requests').status_code == 401
    assert guest.post('/api/v1/session', json={'role': 'patient', 'password': 'patient-test'}).status_code == 403
    assert submit(patient, {}).status_code == 403
    assert submit(staff, sh).status_code == 401
    assert patient.post('/api/v1/requests/x/resolve', headers=ph).status_code == 401
    assert staff.get('/api/v1/input-events').status_code == 401
    assert guest.post('/api/v1/input-events', json={}).status_code == 401
    assert emit(app, command='INVALID').status_code == 400
    assert emit(app, seq=True).status_code == 400
    assert emit(app, boot='').status_code == 400
    assert emit(app, device_id='other').status_code == 400
    assert submit(patient, ph, kind='UNKNOWN').status_code == 400
    assert submit(patient, ph, key='').status_code == 400
    assert submit(patient, ph).status_code == 201
    assert submit(patient, ph, kind='CALL_NURSE').status_code == 409
    assert patient.get('/api/v1/input-events?after=-1').status_code == 400
    assert patient.get('/api/v1/input-events?after=²').status_code == 400
    assert staff.post('/api/v1/requests/missing/resolve', headers=sh).status_code == 404


def test_input_dedup_expiry_boots_and_lease(system):
    app, _, now = system
    patient, _ = login(app, 'patient')
    first = poll(patient).json
    assert first['reset']
    assert emit(app).json['accepted']
    assert not emit(app).json['accepted']
    assert not emit(app, seq=0).json['accepted']
    result = poll(patient, first).json
    assert len(result['events']) == 1
    assert poll(patient, result).json['events'] == []
    assert poll(patient, consumer='tab-2').status_code == 409
    now[0] += 2.1
    assert poll(patient, first).json['events'] == []
    assert emit(app, seq=1, boot='boot-2').json['accepted']
    assert not emit(app, seq=99, boot='boot-1').json['accepted']
    now[0] += 3.1
    takeover = poll(patient, first, 'tab-2').json
    assert takeover['reset'] and takeover['events'] == []
    assert emit(app, seq=2, boot='boot-2').json['accepted']
    assert poll(patient, takeover, 'tab-2').json['events'][0]['seq'] == 2


def test_fresh_page_and_server_restart_do_not_replay(system):
    app, config, _ = system
    patient, _ = login(app, 'patient')
    assert emit(app).status_code == 200
    old = poll(patient).json
    assert old['events'] == []
    restarted = create_app(config)
    patient2, _ = login(restarted, 'patient')
    emit(restarted)
    result = poll(patient2, old).json
    assert result['reset'] and result['events'] == [] and result['epoch'] != old['epoch']


def test_request_persistence(system):
    app, config, _ = system
    patient, ph = login(app, 'patient')
    rid = submit(patient, ph).json['request']['id']
    staff, sh = login(app, 'staff')
    staff.post(f'/api/v1/requests/{rid}/acknowledge', headers=sh)
    restarted = create_app(config)
    patient, ph = login(restarted, 'patient')
    row = submit(patient, ph).json['request']
    assert row['id'] == rid and row['status'] == 'ACKNOWLEDGED'


def test_heartbeat_and_bounded_buffer(system):
    app, _, now = system
    patient, _ = login(app, 'patient')
    assert not patient.get('/api/v1/device').json['connected']
    response = app.test_client().post('/api/v1/heartbeat', json={'device_id': 'esp32-01'},
                                      headers={'Authorization': 'Bearer device-test'})
    assert response.status_code == 200
    assert patient.get('/api/v1/device').json['connected']
    now[0] += 15
    assert not patient.get('/api/v1/device').json['connected']
    for i in range(150):
        emit(app, seq=i)
    assert len(app.extensions['input_state']['events']) == 128


def test_concurrent_submissions_only_create_one(system):
    app, _, _ = system
    def send(i):
        patient, ph = login(app, 'patient')
        return submit(patient, ph, key=f'key-{i}').json['request']['id']
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(send, range(20)))
    assert len(set(ids)) == 1


def test_storage_failure_never_reports_success(system):
    app, config, _ = system
    patient, ph = login(app, 'patient')
    with sqlite3.connect(config['DATABASE']) as db:
        db.execute("CREATE TRIGGER fail_write BEFORE INSERT ON requests BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    result = submit(patient, ph)
    assert result.status_code == 503 and result.json['error']['code'] == 'STORAGE_UNAVAILABLE'
    assert patient.get('/api/v1/requests').json['requests'] == []


def test_built_screens_are_served_and_missing_ones_say_how_to_build(system, tmp_path):
    _, config, _ = system
    site = tmp_path / 'dist'                        # stands in for Code/frontend/dist
    for page in ('index.html', 'patient/index.html', 'staff/index.html', 'assets/app-1a2b.js'):
        (site / page).parent.mkdir(parents=True, exist_ok=True)
        (site / page).write_text(page, encoding='utf-8')
    client = create_app(dict(config, FRONTEND=str(site))).test_client()
    for path, content in (('/', 'index.html'), ('/patient/', 'patient/index.html'), ('/staff/', 'staff/index.html'),
                          ('/assets/app-1a2b.js', 'assets/app-1a2b.js')):
        response = client.get(path)
        assert response.status_code == 200 and response.get_data(as_text=True) == content
        assert "default-src 'self'" in response.headers['Content-Security-Policy']
    assert client.get('/assets/../index.html').status_code == 404
    assert client.get('/assets/missing.js').status_code == 404
    unbuilt = create_app(dict(config, FRONTEND=str(tmp_path / 'nothing'))).test_client()
    for path in ('/', '/patient/', '/staff/'):
        response = unbuilt.get(path)
        assert response.status_code == 503 and response.json['error']['code'] == 'FRONTEND_NOT_BUILT'
    assert unbuilt.get('/api/v1/health').status_code == 200        # the API does not need the screens


def test_assets_and_logout_release(system):
    app, _, _ = system
    patient, ph = login(app, 'patient')
    assert patient.get('/api/v1/health').status_code == 200
    poll(patient)
    assert patient.delete('/api/v1/session', headers=ph).status_code == 200
    second, _ = login(app, 'patient')
    assert poll(second, consumer='tab-2').status_code == 200


def test_missing_navigation_resets_instead_of_delivering_select(system):
    app, _, now = system
    patient, _ = login(app, 'patient')
    first = poll(patient).json
    emit(app, seq=1, command='NEXT')
    now[0] += 2.1
    emit(app, seq=2, command='SELECT')
    result = poll(patient, first).json
    assert result['reset'] and not result['events']
    for seq in range(3, 140):
        emit(app, seq=seq)
    overflow = poll(patient, result).json
    assert overflow['reset'] and not overflow['events']


def test_unicode_password_is_rejected_cleanly(system):
    app, _, _ = system
    response = app.test_client().post('/api/v1/session', json={'role': 'patient', 'password': 'wrong-\u20ac'},
                                      headers={'X-EqualCare-Client': 'dashboard'})
    assert response.status_code == 401


def test_reload_logout_and_backend_restart(system):
    app, config, _ = system
    patient, ph = login(app, 'patient')
    cookie = patient.get_cookie('session')
    assert cookie and cookie.http_only and cookie.same_site == 'Strict'
    # Reload does not require a new login or a new CSRF token.
    for _ in range(3):
        assert patient.get('/patient/').status_code == 200
        restored = patient.get('/api/v1/session')
        assert restored.json['csrf'] == ph['X-CSRF-Token']
        assert restored.json['patient']['name'] == 'Bed 1'
    restarted_config = {k: v for k, v in config.items() if k != 'SECRET_KEY'}
    restarted = create_app(restarted_config).test_client()
    restarted.set_cookie('session', cookie.value)
    assert restarted.get('/api/v1/session').status_code == 401
    assert patient.delete('/api/v1/session', headers=ph).status_code == 200
    assert patient.get('/api/v1/session').status_code == 401


def test_staff_requires_login_id_and_patient_metadata(system):
    app, _, _ = system
    guest = app.test_client()
    for login_id in (None, 'wrong'):
        assert guest.post('/api/v1/session', json=dict(role='staff', password='staff-test', login_id=login_id),
                          headers={'X-EqualCare-Client': 'dashboard'}).status_code == 401
    staff, _ = login(app, 'staff')
    assert staff.get('/api/v1/requests').json['patient'] == dict(id='default', name='Bed 1', device_id='esp32-01')


def test_patient_and_staff_tabs_share_one_browser(system):
    # Tabs in one browser share one cookie: signing in to one screen must not sign out the other.
    app, _, _ = system
    browser = app.test_client()
    tokens = {}
    for role in ('patient', 'staff'):
        result = browser.post('/api/v1/session', json={'role': role, 'password': role + '-test', 'login_id': 'staff'},
                              headers={'X-EqualCare-Client': 'dashboard'})
        tokens[role] = {'X-CSRF-Token': result.json['csrf'], 'X-EqualCare-Role': role}
    for role in ('patient', 'staff'):
        restored = browser.get('/api/v1/session', headers={'X-EqualCare-Role': role}).json
        assert restored['role'] == role and restored['csrf'] == tokens[role]['X-CSRF-Token']
    rid = submit(browser, tokens['patient']).json['request']['id']
    # With two roles signed in, a call must say which screen it comes from.
    assert browser.get('/api/v1/requests').status_code == 401
    assert browser.get('/api/v1/input-events', headers={'X-Consumer-ID': 'tab', 'X-EqualCare-Role': 'patient'}).status_code == 200
    # Each role's CSRF token only works for that role.
    assert browser.post(f'/api/v1/requests/{rid}/acknowledge',
                        headers={**tokens['patient'], 'X-EqualCare-Role': 'staff'}).status_code == 403
    assert browser.post(f'/api/v1/requests/{rid}/acknowledge', headers=tokens['staff']).json['request']['status'] == 'ACKNOWLEDGED'
    assert browser.post(f'/api/v1/requests/{rid}/acknowledge', headers=tokens['patient']).status_code == 401
    # Signing out of the staff screen leaves the patient screen signed in, and vice versa.
    assert browser.delete('/api/v1/session', headers=tokens['staff']).status_code == 200
    assert browser.get('/api/v1/session', headers={'X-EqualCare-Role': 'staff'}).status_code == 401
    assert browser.get('/api/v1/requests', headers={'X-EqualCare-Role': 'patient'}).status_code == 200
    assert browser.delete('/api/v1/session', headers=tokens['patient']).status_code == 200
    assert browser.get('/api/v1/session', headers={'X-EqualCare-Role': 'patient'}).status_code == 401


def test_patient_logout_releases_device_lease_in_shared_browser(system):
    app, _, _ = system
    browser = app.test_client()
    headers = {}
    for role in ('staff', 'patient'):
        result = browser.post('/api/v1/session', json={'role': role, 'password': role + '-test', 'login_id': 'staff'},
                              headers={'X-EqualCare-Client': 'dashboard'})
        headers[role] = {'X-CSRF-Token': result.json['csrf'], 'X-EqualCare-Role': role}
    assert browser.get('/api/v1/input-events', headers={**headers['patient'], 'X-Consumer-ID': 'tab-1'}).status_code == 200
    browser.delete('/api/v1/session', headers=headers['staff'])
    assert app.extensions['input_state']['consumer'] is not None  # Staff logout keeps the patient's lease.
    browser.delete('/api/v1/session', headers=headers['patient'])
    assert app.extensions['input_state']['consumer'] is None


def test_launcher_detects_a_server_already_on_the_port():
    import socket
    from run import port_in_use
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert port_in_use(port)
    assert not port_in_use(port)


@pytest.mark.parametrize('source', ['joystick', 'eog'])
def test_input_sources_share_the_contract(system, source):
    app, _, _ = system
    patient, _ = login(app, 'patient')
    initial = poll(patient).json
    assert emit(app, source=source, command='SELECT').json['accepted']
    result = poll(patient, initial).json['events']
    assert len(result) == 1 and result[0]['source'] == source and result[0]['command'] == 'SELECT'
