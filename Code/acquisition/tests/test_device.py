"""EOGDevice against a fake serial port that behaves like Code/firmware/ESP32-S3."""
import threading
import time

import pytest
import serial

from eog_acquisition.device import DeviceError, EOGDevice
from eog_acquisition.processing import Pipeline
from eog_acquisition.protocol import encode_packet
from eog_acquisition.recording import Recorder, load_recording


class Firmware:
    def __init__(self, board='ESP32-S3', limit=None, drop=(), unplug_after=None, per_read=4, chunk=11):
        self.board, self.limit, self.drop, self.unplug_after = board, limit, set(drop), unplug_after
        self.per_read, self.chunk = per_read, chunk
        self.running, self.unplugged, self.tick = False, False, 0
        self.out, self.inbox = bytearray(), bytearray()
        self.lock = threading.Lock()

    @staticmethod
    def value(tick):
        return (tick * 37) % 4096

    def receive(self, data):
        self.inbox += data
        while b'\n' in self.inbox:
            line, _, rest = bytes(self.inbox).partition(b'\n')
            self.inbox = bytearray(rest)
            command = line.strip().upper()
            if command == b'WHORU':
                self.out += self.board.encode() + b'\r\n'
            elif command == b'START':
                self.running, self.tick = True, 0
            elif command == b'STOP':
                self.running = False
            elif command == b'STATUS':
                self.out += (b'RUNNING' if self.running else b'STOPPED') + b'\r\n'
            elif command == b'STATS':
                self.out += b'STATS missed=0 dropped=0\r\n'
            else:
                self.out += b'UNKNOWN COMMAND\r\n'

    def produce(self):
        if self.unplugged or (self.unplug_after is not None and self.tick >= self.unplug_after):
            self.unplugged = True
            raise serial.SerialException('ClearCommError failed: device unplugged')
        for _ in range(self.per_read if self.running else 0):
            if self.limit is not None and self.tick >= self.limit:
                return
            if self.tick not in self.drop:
                self.out += encode_packet(self.tick, [self.value(self.tick)])
            self.tick += 1


class FakeSerial:
    firmware = None

    def __init__(self, port=None, baudrate=None, timeout=None, write_timeout=None, **_):
        if FakeSerial.firmware is None:
            raise serial.SerialException(f"could not open port '{port}': PermissionError(13, 'Access is denied.')")
        self.fw, self.port, self.timeout, self.is_open = FakeSerial.firmware, port, timeout, True

    def write(self, data):
        with self.fw.lock:
            self.fw.receive(data)
        return len(data)

    @property
    def in_waiting(self):
        with self.fw.lock:
            self.fw.produce()
            return len(self.fw.out)

    def read(self, n=1):
        with self.fw.lock:
            self.fw.produce()
            data = bytes(self.fw.out[:min(n, self.fw.chunk)])
            del self.fw.out[:len(data)]
        if not data:
            time.sleep(0.005)
        return data

    def readline(self):
        line = b''
        for _ in range(40):
            line += self.read(1)
            if line.endswith(b'\n'):
                break
        return line

    def reset_input_buffer(self):
        with self.fw.lock:
            self.fw.out.clear()

    flushInput = reset_input_buffer

    def flushOutput(self):
        pass

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@pytest.fixture
def firmware(monkeypatch):
    monkeypatch.setattr(serial, 'Serial', FakeSerial)

    def make(**kwargs):
        FakeSerial.firmware = Firmware(**kwargs)
        return FakeSerial.firmware
    yield make
    FakeSerial.firmware = None


def stream(expected, recorder=None):
    statuses = []
    device = EOGDevice('COM7', on_status=lambda state, message: statuses.append(state))
    assert device.open() == 'STOPPED'  # firmware starts stopped
    received = []
    pipeline = Pipeline(recorder=recorder)
    pipeline.on_sample(received.append)
    device.start(pipeline)
    deadline = time.monotonic() + 5
    while len(received) < expected and device.streaming and time.monotonic() < deadline:
        time.sleep(0.01)
    return device, pipeline, received, statuses


def test_every_sample_once_in_order_and_recorded(firmware, tmp_path):
    fw = firmware(limit=1000)
    recorder = Recorder(tmp_path / 'capture', port='COM7')
    device, pipeline, received, statuses = stream(1000, recorder)
    device.stop()
    count = len(received)
    time.sleep(0.1)
    assert len(received) == count == 1000  # nothing after STOP
    assert [p.sample.index for p in received] == list(range(1000))
    assert [p.sample.raw for p in received] == [Firmware.value(t) for t in range(1000)]
    assert device.command('STATUS') == 'STOPPED' and device.command('STATS').startswith('STATS')
    device.close()
    recorder.close()
    recording = load_recording(tmp_path / 'capture')
    assert list(recording.raw) == [Firmware.value(t) for t in range(1000)] and recorder.rows == 1000
    assert statuses[0] == 'streaming' and statuses[-1] == 'stopped' and not fw.running
    assert device.decoder.junk_bytes == 0 and pipeline.health.snapshot()['missing'] == 0


def test_dropped_samples_are_gaps(firmware):
    firmware(limit=600, drop={100, 101, 400})
    device, pipeline, received, _ = stream(597)
    device.close()
    assert len(received) == 597 and pipeline.health.snapshot()['missing'] == 3
    assert {p.sample.index: p.sample.gap_before for p in received if p.sample.gap_before} == {102: 2, 401: 1}


def test_disconnect_closes_and_requires_explicit_restart(firmware):
    firmware(unplug_after=300)
    device, _, received, statuses = stream(300)
    deadline = time.monotonic() + 3
    while device.streaming and time.monotonic() < deadline:
        time.sleep(0.01)
    assert statuses[-1] == 'disconnected' and not device.streaming
    # Bytes still buffered at the unplug are lost (as on real hardware); everything received is once and in order.
    assert received and [p.sample.index for p in received] == list(range(len(received)))
    assert not device.ser.is_open
    with pytest.raises(DeviceError):
        device.start(lambda samples: None)
    device.close()


def test_wrong_board_is_refused(firmware):
    firmware(board='UNO-R3')
    with pytest.raises(DeviceError, match='not the ESP32-S3'):
        EOGDevice('COM7').open()


def test_busy_port_explains_what_to_close(firmware):
    with pytest.raises(DeviceError, match='Serial Monitor'):
        EOGDevice('COM7').open()
