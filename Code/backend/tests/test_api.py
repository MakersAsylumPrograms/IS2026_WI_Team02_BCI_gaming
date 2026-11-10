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
    result = client.post('/api/v1/session', json={'role': role, 'password': role + '-test'},
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


def test_assets_and_logout_release(system):
    app, _, _ = system
    patient, ph = login(app, 'patient')
    for path in ('/', '/patient/', '/staff/', '/assets/api.js', '/api/v1/health'):
        assert patient.get(path).status_code == 200
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
