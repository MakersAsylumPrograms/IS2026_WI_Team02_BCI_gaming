import json
import sys
from pathlib import Path

from werkzeug.security import check_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credentials import load_credentials


def test_migration_preserves_passwords_token_and_settings(tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps(dict(PATIENT_PASSWORD='old-patient', STAFF_PASSWORD='old-staff',
                                   DEVICE_TOKEN='old-token', SECRET='obsolete', DEVICE_ID='original-device')))
    config, issued = load_credentials(path)
    assert not issued
    assert check_password_hash(config['PATIENT_PASSWORD_HASH'], 'old-patient')
    assert check_password_hash(config['STAFF_PASSWORD_HASH'], 'old-staff')
    assert config['DEVICE_TOKEN'] == 'old-token' and config['DEVICE_ID'] == 'original-device'
    assert config['STAFF_LOGIN_ID'] == 'staff'
    saved = path.read_text()
    assert 'old-patient' not in saved and 'old-staff' not in saved and 'SECRET' not in saved
    assert load_credentials(path)[0] == config
    assert path.read_text() == saved


def test_new_credentials_are_issued_once(tmp_path):
    path = tmp_path / 'credentials.json'
    config, issued = load_credentials(path)
    assert set(issued) == {'PATIENT_PASSWORD', 'STAFF_PASSWORD'}
    for role in ('PATIENT', 'STAFF'):
        assert check_password_hash(config[role + '_PASSWORD_HASH'], issued[role + '_PASSWORD'])
    assert load_credentials(path)[1] == {}


def test_readable_password_replaces_existing_hash(tmp_path):
    path = tmp_path / 'credentials.json'
    config, _ = load_credentials(path)
    saved = json.loads(path.read_text())
    saved['STAFF_PASSWORD'] = 'my-new-readable-password'
    path.write_text(json.dumps(saved))
    updated, issued = load_credentials(path)
    assert not issued
    assert check_password_hash(updated['STAFF_PASSWORD_HASH'], 'my-new-readable-password')
    assert updated['PATIENT_PASSWORD_HASH'] == config['PATIENT_PASSWORD_HASH']
    assert updated['DEVICE_TOKEN'] == config['DEVICE_TOKEN']
    assert 'STAFF_PASSWORD' not in json.loads(path.read_text())
