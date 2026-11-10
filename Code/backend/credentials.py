"""Local prototype credentials; migrate passwords without changing their values."""
import json
import os
import secrets
from pathlib import Path

from werkzeug.security import generate_password_hash

CREDENTIALS = Path(__file__).parent / 'instance' / 'credentials.json'


def save_credentials(data, path=CREDENTIALS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def load_credentials(path=CREDENTIALS):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    original = dict(data)
    issued = {}
    for role in ('PATIENT', 'STAFF'):
        plain = data.pop(role + '_PASSWORD', None)
        if plain is not None or role + '_PASSWORD_HASH' not in data:
            if plain is not None and (not isinstance(plain, str) or not plain):
                raise ValueError(role + '_PASSWORD must be a non-empty string.')
            if plain is None:
                plain = secrets.token_urlsafe(18)
                issued[role + '_PASSWORD'] = plain
            data[role + '_PASSWORD_HASH'] = generate_password_hash(plain)
    data.pop('SECRET', None)  # Browser sessions now last only for this backend run.
    data.setdefault('STAFF_LOGIN_ID', 'staff')
    data.setdefault('PATIENT_NAME', 'Bed 1')
    data.setdefault('DEVICE_ID', 'esp32-01')
    if 'DEVICE_TOKEN' not in data:
        data['DEVICE_TOKEN'] = secrets.token_urlsafe(24)
    if data != original:
        save_credentials(data, path)
    config = dict(data)
    for key in ('STAFF_LOGIN_ID', 'PATIENT_NAME', 'DEVICE_ID', 'DEVICE_TOKEN'):
        config[key] = os.environ.get('EQUALCARE_' + key, config[key])
    for role in ('PATIENT', 'STAFF'):
        password = os.environ.get('EQUALCARE_' + role + '_PASSWORD')
        if password:
            config[role + '_PASSWORD_HASH'] = generate_password_hash(password)
    return config, issued
