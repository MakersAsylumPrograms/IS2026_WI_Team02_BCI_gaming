"""Start EqualCare; provision persistent local demo credentials on first run."""
import argparse
import getpass
import json

from waitress import serve

from app import create_app
from credentials import CREDENTIALS, load_credentials, save_credentials
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-credentials', action='store_true', help='Print login ID and device settings (passwords are hashed) and exit')
    parser.add_argument('--set-password', choices=('patient', 'staff'), help='Prompt for a new password, save its hash and exit')
    args = parser.parse_args()
    config, issued = load_credentials()
    if args.set_password:
        password = getpass.getpass('New password: ')
        if not password or password != getpass.getpass('Confirm password: '):
            parser.error('Passwords must be non-empty and match.')
        data = json.loads(CREDENTIALS.read_text(encoding='utf-8'))
        data[args.set_password.upper() + '_PASSWORD_HASH'] = generate_password_hash(password)
        save_credentials(data)
        print('Password saved. Restart the backend to use it. Environment overrides take precedence.')
        return
    for key, value in issued.items():
        print(f'{key}: {value} (save now; displayed only on creation)')
    if issued or args.show_credentials:
        for key in ('STAFF_LOGIN_ID', 'DEVICE_ID', 'DEVICE_TOKEN', 'PATIENT_NAME'):
            print(f'{key}: {config[key]}')
        print('Existing passwords are preserved as hashes and cannot be displayed.')
    if args.show_credentials:
        return
    print('EqualCare: http://localhost:8000 | Patient: /patient/ | Nurse: /staff/')
    print("Use this laptop's reserved LAN IPv4 address on the nurse laptop and ESP32.")
    serve(create_app(config), host='0.0.0.0', port=8000, threads=8)


if __name__ == '__main__':
    main()
