"""Hardware-free ESP32 protocol simulator; Python standard library only."""
import argparse
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8000')
    parser.add_argument('--device-id', default=os.getenv('EQUALCARE_DEVICE_ID', 'esp32-01'))
    parser.add_argument('--source', choices=['joystick', 'eog'], default='joystick')
    parser.add_argument('--commands', nargs='*', choices=['NEXT', 'PREVIOUS', 'SELECT'], help='Send a batch then exit')
    args = parser.parse_args()
    token = os.environ.get('EQUALCARE_DEVICE_TOKEN')
    local_credentials = Path(__file__).parent / 'instance' / 'credentials.json'
    if not token and local_credentials.exists():
        token = json.loads(local_credentials.read_text(encoding='utf-8'))['DEVICE_TOKEN']
    if not token:
        parser.error('Set EQUALCARE_DEVICE_TOKEN to the provisioned token.')
    boot = str(uuid.uuid4())

    def post(path, data):
        req = urllib.request.Request(args.server.rstrip('/') + '/api/v1' + path,
            data=json.dumps(data).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=0.4) as response:
            return json.load(response)

    commands = queue.Queue(maxsize=8)
    stop = threading.Event()
    def reader():
        print('n=NEXT, p=PREVIOUS, s=SELECT; q=quit. Press Enter after each command.')
        while not stop.is_set():
            try:
                line = input().strip().lower()
            except EOFError:
                stop.set(); break
            if line == 'q':
                stop.set(); break
            command = {'n': 'NEXT', 'p': 'PREVIOUS', 's': 'SELECT'}.get(line)
            if command:
                try:
                    commands.put_nowait((command, time.monotonic()))
                except queue.Full:
                    print('Queue full; command discarded')
    if args.commands is None:
        threading.Thread(target=reader, daemon=True).start()
    else:
        for command in args.commands:
            try:
                commands.put_nowait((command, time.monotonic()))
            except queue.Full:
                parser.error('Batch maximum is eight commands.')
    seq, heartbeat = 0, 0
    while not stop.is_set():
        if time.monotonic() - heartbeat >= 5:
            heartbeat = time.monotonic()
            try:
                post('/heartbeat', {'device_id': args.device_id})
            except (OSError, urllib.error.URLError):
                print('Heartbeat unavailable')
        try:
            command, created = commands.get(timeout=0.1)
        except queue.Empty:
            if args.commands is not None:
                break
            continue
        seq += 1
        event = dict(device_id=args.device_id, boot_id=boot, seq=seq, source=args.source, command=command)
        while time.monotonic() - created < 1.6:
            try:
                print(post('/input-events', event)); break
            except urllib.error.HTTPError as exc:
                if 400 <= exc.code < 500:
                    print('Rejected:', exc.code); break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        else:
            print('Expired; discarded:', command)
        if args.commands is not None:
            time.sleep(0.3)


if __name__ == '__main__':
    main()
