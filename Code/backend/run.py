"""Start EqualCare; provision persistent local demo credentials on first run."""
import argparse
import json
import os
import secrets
from pathlib import Path

from waitress import serve

from app import create_app

SECRETS = ('PATIENT_PASSWORD', 'STAFF_PASSWORD', 'DEVICE_TOKEN', 'SECRET')
CREDENTIALS = Path(__file__).parent / 'instance' / 'credentials.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-credentials', action='store_true', help='Print local credentials and exit')
    args = parser.parse_args()
    created = not CREDENTIALS.exists()
    if created:
        CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
        CREDENTIALS.write_text(json.dumps({k: secrets.token_urlsafe(24) for k in SECRETS}, indent=2), encoding='utf-8')
    for key, value in json.loads(CREDENTIALS.read_text(encoding='utf-8')).items():
        os.environ.setdefault('EQUALCARE_' + key, value)
    if created or args.show_credentials:
        print('Local demo credentials (keep private; do not commit):')
        for key in SECRETS[:3]:
            print(f'{key}: {os.environ["EQUALCARE_" + key]}')
    if args.show_credentials:
        return
    print('EqualCare: http://localhost:8000 | Patient: /patient/ | Nurse: /staff/')
    print("Use this laptop's reserved LAN IPv4 address on the nurse laptop and ESP32.")
    serve(create_app(), host='0.0.0.0', port=8000, threads=8)


if __name__ == '__main__':
    main()
