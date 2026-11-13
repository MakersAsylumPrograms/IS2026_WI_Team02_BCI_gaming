"""Send eye and joystick commands to the EqualCare dashboard backend (Code/backend), as the input device.

Same device contract as Code/backend/simulate_device.py and the ESP32 firmware:
  POST /api/v1/input-events  {device_id, boot_id, seq, source: 'eog', command}
  POST /api/v1/heartbeat     {device_id}         every 5 s, so the dashboard shows the device online
  Authorization: Bearer <DEVICE_TOKEN>

Eye gestures (gestures.py) carry their own command, as source 'eog': look LEFT -> PREVIOUS, look
RIGHT -> NEXT, double blink -> SELECT. A CYCLE from an old direction-free model becomes `command`
(PREVIOUS by default). Joystick events are sent with their own command (NEXT / PREVIOUS / SELECT)
as source 'joystick'. A command that cannot be
delivered within FRESH_FOR seconds is dropped, never delivered late. Sending happens on a
background thread, so a slow or absent backend never stalls the serial reader.
"""
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CREDENTIALS = Path(__file__).resolve().parents[2] / 'backend' / 'instance' / 'credentials.json'
DEFAULT_SERVER = 'http://127.0.0.1:8000'
COMMANDS = ('NEXT', 'PREVIOUS')
FRESH_FOR = 1.6          # seconds; like the firmware, never deliver stale input late
HEARTBEAT_EVERY = 5.0
TIMEOUT = 0.4


def device_credentials(path=CREDENTIALS):
    """(device_id, token) from EQUALCARE_DEVICE_ID / EQUALCARE_DEVICE_TOKEN, else the backend's credentials file."""
    data = {}
    if Path(path).exists():
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    device_id = os.environ.get('EQUALCARE_DEVICE_ID') or data.get('DEVICE_ID') or 'esp32-01'
    token = os.environ.get('EQUALCARE_DEVICE_TOKEN') or data.get('DEVICE_TOKEN')
    if not token:
        raise RuntimeError('No device token: start the backend once (it creates Code/backend/instance/'
                           'credentials.json) or set EQUALCARE_DEVICE_TOKEN.')
    return device_id, token


class DashboardLink:
    def __init__(self, server=DEFAULT_SERVER, device_id=None, token=None, command='PREVIOUS', say=print):
        if command not in COMMANDS:
            raise ValueError(f'command must be one of {COMMANDS}')
        if token is None:
            device_id, token = device_credentials()
        self.server, self.device_id, self.token, self.command = server.rstrip('/'), device_id, token, command
        self.say = say
        self.boot_id, self.seq = str(uuid.uuid4()), 0
        self.sent = self.dropped = 0
        self._queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()
        self._threads = []

    def _post(self, path, data):
        request = urllib.request.Request(self.server + '/api/v1' + path, data=json.dumps(data).encode(),
                                         headers={'Content-Type': 'application/json',
                                                  'Authorization': 'Bearer ' + self.token})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)

    def check(self):
        """One heartbeat now; raises if the backend is unreachable or rejects this device."""
        self._post('/heartbeat', {'device_id': self.device_id})

    def start(self):
        for target in (self._heartbeat, self._sender):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def stop(self):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        for thread in self._threads:
            thread.join(timeout=2)

    def send(self, event=None):
        """Queue one dashboard command for an eye event (gesture or CYCLE) or a JoystickEvent."""
        source = getattr(event, 'source', 'eog')
        command = getattr(event, 'command', 'CYCLE')
        if source == 'eog' and command == 'CYCLE':       # the direction-free command of the old CYCLE models
            command = self.command
        if source not in ('eog', 'joystick') or command not in ('NEXT', 'PREVIOUS', 'SELECT'):
            raise ValueError('Unsupported dashboard event')
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait((time.monotonic(), command, source))
        except queue.Full:
            self.dropped += 1
            self.say('[dashboard] input queue full; command discarded')

    def _heartbeat(self):
        warned = False
        while not self._stop.is_set():
            try:
                self._post('/heartbeat', {'device_id': self.device_id})
                warned = False
            except OSError as exc:  # HTTPError and URLError are both OSErrors
                if not warned:
                    self.say(f'[dashboard] heartbeat failed ({exc}); is the backend running at {self.server}?')
                    warned = True
            self._stop.wait(HEARTBEAT_EVERY)

    def _sender(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                return
            queued, command, source = item
            self.seq += 1
            event = dict(device_id=self.device_id, boot_id=self.boot_id, seq=self.seq, source=source,
                         command=command)
            delivered = False
            while time.monotonic() - queued < FRESH_FOR and not self._stop.is_set():
                try:
                    reply = self._post('/input-events', event)
                    delivered = True
                    self.sent += 1
                    if not reply.get('accepted', False):
                        self.say(f'[dashboard] not accepted: {reply.get("reason")}')
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code < 500:          # rejected (bad token / device id): retrying will not help
                        self.say(f'[dashboard] rejected with HTTP {exc.code}; check the device token and id')
                        delivered = True
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            if not delivered:
                self.dropped += 1
                self.say('[dashboard] backend unreachable; command dropped (never delivered late)')
