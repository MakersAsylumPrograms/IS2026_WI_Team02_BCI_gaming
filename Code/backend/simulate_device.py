"""Hardware-free ESP32 simulator: sends the same JSON as the firmware. Python standard library only."""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

KEYS = {'n': 'NEXT', 'p': 'PREVIOUS', 's': 'SELECT'}
FRESH_FOR = 1.6  # seconds; like the firmware, never deliver stale input late


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    parser.add_argument('--device-id', default=os.getenv('EQUALCARE_DEVICE_ID', 'esp32-01'))
    parser.add_argument('--source', choices=['joystick', 'eog'], default='joystick')
    parser.add_argument('--commands', nargs='*', choices=list(KEYS.values()), help='Send these commands, then exit')
    args = parser.parse_args()
    token = os.environ.get('EQUALCARE_DEVICE_TOKEN')
    credentials = Path(__file__).parent / 'instance' / 'credentials.json'
    if not token and credentials.exists():
        token = json.loads(credentials.read_text(encoding='utf-8'))['DEVICE_TOKEN']
    if not token:
        parser.error('Set EQUALCARE_DEVICE_TOKEN to the provisioned token.')
    boot, seq = str(uuid.uuid4()), 0

    def post(path, data):
        req = urllib.request.Request(args.server.rstrip('/') + '/api/v1' + path, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=0.4) as response:
            return json.load(response)

    def heartbeat():
        while True:
            try:
                post('/heartbeat', {'device_id': args.device_id})
            except OSError:
                print('Heartbeat unavailable')
            time.sleep(5)

    def send(command):
        nonlocal seq
        seq += 1
        event = dict(device_id=args.device_id, boot_id=boot, seq=seq, source=args.source, command=command)
        deadline = time.monotonic() + FRESH_FOR
        while time.monotonic() < deadline:
            try:
                print(post('/input-events', event))
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:  # Rejected by the backend; retrying will not help.
                    print('Rejected:', exc.code)
                    return
            except OSError:
                pass
            time.sleep(0.05)
        print('Expired; discarded:', command)

    threading.Thread(target=heartbeat, daemon=True).start()
    if args.commands is not None:
        for command in args.commands:
            send(command)
            time.sleep(0.3)
        return
    print('n=NEXT, p=PREVIOUS, s=SELECT; q=quit. Press Enter after each command.')
    for line in sys.stdin:
        line = line.strip().lower()
        if line == 'q':
            break
        if line in KEYS:
            send(KEYS[line])


if __name__ == '__main__':
    main()
