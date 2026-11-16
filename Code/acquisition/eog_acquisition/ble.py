"""Bluetooth (BLE) connection to the ESP32-S3 EOG firmware, for running the board on a battery.

The firmware (Code/firmware/ESP32-S3) advertises as "NPG-EqualCare-6CH" with the layout of
Upside Down Labs' NPG Lite boards, which the official Chords BLE client also reads:
  data     notify, 10 samples per notification: [counter][6 channels, big-endian 16-bit] x 10
  control  write the same text commands as on USB (START, STOP, WHORU, STATUS, STATS, PINS, JOY);
           replies arrive as notifications.
Channels: EOG, joystick X, Y, button, 0, 0. Each sample becomes the same protocol.Packet the USB
link produces, so everything after this (Sequencer, Pipeline, detectors, dashboard) is unchanged.

BLEEOGDevice has the same interface as device.EOGDevice. Use it with `--port BLE` (any board
named NPG-EqualCare...) or `--port BLE:<name or address>`.
"""
import asyncio
import queue
import threading
import time

from .device import DeviceError
from .protocol import NUM_SLOTS, Packet, Sequencer

DEFAULT_NAME = 'NPG-EqualCare'
SERVICE_UUID = '4fafc201-1fb5-459e-8fcc-c5c9c331914b'
DATA_CHAR_UUID = 'beb5483e-36e1-4688-b7f5-ea07361b26a8'
CONTROL_CHAR_UUID = '0000ff01-0000-1000-8000-00805f9b34fb'
BLE_CHANNELS = 6
SAMPLE_LEN = 1 + 2 * BLE_CHANNELS          # 13 bytes
BLOCK = 10                                  # samples per notification
SCAN_SECONDS = 10.0
NO_DATA_SECONDS = 1.0


def is_ble_port(port):
    return str(port).upper().startswith('BLE')


class _Counters:
    """What device.EOGDevice's StreamDecoder reports, for the status line and summaries."""
    def __init__(self):
        self.junk_bytes = self.malformed_packets = self.inactive_nonzero = self.packets = 0
        self.text_lines = []


class BLEEOGDevice:
    """Same lifecycle as EOGDevice: open() -> start(sink) -> stop() -> close()."""

    def __init__(self, port='BLE', on_status=None):
        target = str(port)[3:].lstrip(':').strip()
        self.target = target or DEFAULT_NAME
        self.port = f'Bluetooth ({self.target})'
        self.on_status = on_status or (lambda state, message: None)
        self.decoder = _Counters()
        self.sequencer = Sequencer()
        self.samples_delivered = 0
        self.error = None
        self._client = None
        self._sink = None
        self._replies = queue.Queue()
        self._streaming = False
        self._last_data = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name='eog-ble', daemon=True)
        self._thread.start()
        self._watchdog = None

    # The serial device's `ser` is checked before sending STATS at the end of a run.
    @property
    def ser(self):
        return self if self._client is not None and self._client.is_connected else None

    def _run(self, coroutine, timeout):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout)

    # ---- connection -------------------------------------------------------------
    def open(self):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as exc:
            raise DeviceError('Bluetooth needs the bleak package (installed with chordspy). '
                              'Start-EqualCare.cmd installs it; run it once with internet.') from exc

        async def connect():
            print(f'[bluetooth] Looking for {self.target} (up to {SCAN_SECONDS:.0f} s)...', flush=True)
            found = await BleakScanner.discover(timeout=SCAN_SECONDS)
            wanted = self.target.upper()
            device = next((d for d in found if d.address.upper() == wanted
                           or (d.name or '').upper().startswith(wanted)), None)
            if device is None:
                names = sorted({d.name for d in found if d.name and d.name.upper().startswith('NPG')})
                raise DeviceError(f'No Bluetooth board called {self.target} found'
                                  + (f' (seen: {", ".join(names)})' if names else '')
                                  + '. Is it powered, flashed with the Bluetooth firmware, and not '
                                    'connected to another program or phone?')
            client = BleakClient(device, disconnected_callback=self._disconnected)
            await client.connect()
            await client.start_notify(CONTROL_CHAR_UUID, self._reply)
            self._client = client
            return device

        try:
            device = self._run(connect(), SCAN_SECONDS + 20)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f'Bluetooth connection failed: {exc}') from exc
        mtu = getattr(self._client, 'mtu_size', None)
        print(f'[bluetooth] Connected to {device.name} ({device.address}), MTU {mtu}', flush=True)
        if mtu and mtu - 3 < SAMPLE_LEN * BLOCK:
            print(f'[bluetooth] Warning: MTU {mtu} is below the {SAMPLE_LEN * BLOCK + 3} bytes a data block '
                  'needs; samples may be cut off.', flush=True)
        name = self.command('WHORU', wait=2.0)
        if name != 'ESP32-S3':
            raise DeviceError(f'The Bluetooth board answered {name!r} instead of ESP32-S3. '
                              'Flash Code/firmware/ESP32-S3.')
        return self.command('STATUS')

    def _disconnected(self, _client):
        if self._client is None:
            return
        self.error = f'{self.port} disconnected'
        self._streaming = False
        self.on_status('disconnected', self.error)

    def _reply(self, _characteristic, data):
        self._replies.put(bytes(data).decode('ascii', errors='replace').strip())

    def command(self, text, wait=0.3):
        """Send a command while NOT streaming and return its one-line reply ('' if none)."""
        if self._streaming:
            raise DeviceError('Stop streaming before sending commands.')
        self._require_open()
        while not self._replies.empty():
            self._replies.get_nowait()
        self._write(text)
        try:
            return self._replies.get(timeout=max(wait, 1.0))   # Bluetooth round trips are slower than USB
        except queue.Empty:
            return ''

    def _write(self, text):
        self._run(self._client.write_gatt_char(CONTROL_CHAR_UUID, text.encode('ascii'), response=True), 5)

    # ---- streaming ----------------------------------------------------------------
    def start(self, sink):
        self._require_open()
        if self._streaming:
            raise DeviceError('Already streaming.')
        self._sink = sink
        self._run(self._client.start_notify(DATA_CHAR_UUID, self._data), 5)
        self._last_data = time.monotonic()
        self._streaming = True
        self._write('START')
        self.on_status('streaming', f'Streaming from {self.port}')
        self._watchdog = threading.Thread(target=self._watch, name='eog-ble-watch', daemon=True)
        self._watchdog.start()

    def _data(self, _characteristic, data):
        now = time.monotonic()
        self._last_data = now
        data = bytes(data)
        if len(data) % SAMPLE_LEN:
            self.decoder.malformed_packets += 1
            self.decoder.junk_bytes += len(data)
            return
        samples = []
        for offset in range(0, len(data), SAMPLE_LEN):
            chunk = data[offset:offset + SAMPLE_LEN]
            values = tuple(int.from_bytes(chunk[i:i + 2], 'big') for i in range(1, SAMPLE_LEN, 2))
            if any(values[4:]):
                self.decoder.inactive_nonzero += 1
            self.decoder.packets += 1
            packet = Packet(counter=chunk[0], channels=values + (0,) * (NUM_SLOTS - BLE_CHANNELS))
            samples.append(self.sequencer.next(packet, now))
        self.samples_delivered += len(samples)
        try:
            self._sink(samples)
        except Exception as exc:
            self.error = f'Input processing failed: {exc}'
            self._streaming = False
            self.on_status('disconnected', self.error)

    def _watch(self):
        silent = False
        while self._streaming:
            time.sleep(0.2)
            quiet = time.monotonic() - self._last_data >= NO_DATA_SECONDS
            if quiet and not silent:
                self.on_status('no_data', f'No data for {NO_DATA_SECONDS:.0f} s (out of range or battery?)')
            elif silent and not quiet:
                self.on_status('resumed', 'Data is arriving again')
            silent = quiet

    def stop(self):
        if not self._streaming:
            return
        self._streaming = False
        try:
            self._write('STOP')
            self._run(self._client.stop_notify(DATA_CHAR_UUID), 5)
        except Exception:
            pass
        time.sleep(0.2)
        if self.error is None:
            self.on_status('stopped', 'Streaming stopped')

    def close(self):
        self.stop()
        client, self._client = self._client, None
        if client is not None:
            try:
                self._run(client.disconnect(), 5)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)

    @property
    def streaming(self):
        return self._streaming

    def _require_open(self):
        if self._client is None or not self._client.is_connected:
            raise DeviceError(self.error or 'Bluetooth device is not connected.')
