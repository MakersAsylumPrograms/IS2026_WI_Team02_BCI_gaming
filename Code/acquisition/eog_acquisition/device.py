"""Serial connection to the ESP32-S3 EOG firmware (the Chords USB packet format, protocol.py).

Opens the port, identifies the board with WHORU, then streams on a reader thread that hands
over every decoded sample exactly once, in order.
"""
import threading
import time

import serial

from .protocol import BAUD_RATE, BOARD_ID, Sequencer, StreamDecoder

DEFAULT_PORT = 'auto'   # the first USB port the firmware answers on, else Bluetooth (cli.open_device)
BRIDGES = (0x10C4, 0x1A86, 0x303A, 0x0403)   # USB vendor ids: CP210x, CH34x, Espressif, FTDI
NO_DATA_SECONDS = 1.0   # streaming but silent this long: report it
DRAIN_SECONDS = 0.3     # after STOP, keep reading until quiet this long
WHORU_TRIES = 3


class DeviceError(RuntimeError):
    pass


def usb_serial_ports():
    """USB serial ports, the usual ESP32 USB-to-UART bridges first. Bluetooth and built-in ports are skipped."""
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if p.vid is not None]
    return [p.device for p in sorted(ports, key=lambda p: (p.vid not in BRIDGES, p.device))]


class EOGDevice:
    """Owns the serial port. Lifecycle: open() -> start(sink) -> stop() -> close().

    `sink(samples)` is called from the reader thread with each batch of new samples,
    in order. `on_status(state, message)` reports 'streaming', 'no_data', 'resumed',
    'disconnected' and 'stopped'. After a disconnect the device stays closed: plug it
    back in and start again explicitly.
    """

    def __init__(self, port=DEFAULT_PORT, on_status=None):
        self.port = port
        self.on_status = on_status or (lambda state, message: None)
        self.decoder = StreamDecoder()
        self.sequencer = Sequencer()
        self.ser = None
        self._thread = None
        self._stop = threading.Event()
        self._stop_requested_at = None
        self.error = None

    # ---- connection -------------------------------------------------------------
    def open(self):
        try:
            # Stop a stream left running by an earlier session so WHORU's reply is readable.
            with serial.Serial(self.port, BAUD_RATE, timeout=0.2, write_timeout=1) as probe:
                probe.write(b'STOP\n')
                time.sleep(0.2)
                probe.reset_input_buffer()
        except serial.SerialException as exc:
            raise DeviceError(f'Cannot open {self.port}: {exc}. Check the cable is in the board\'s UART port '
                              'and close the Arduino Serial Monitor/Plotter or any other program using it.') from exc
        try:
            self.ser = serial.Serial(self.port, BAUD_RATE, timeout=0.05, write_timeout=1)
        except serial.SerialException as exc:
            raise DeviceError(f'Cannot open {self.port}: {exc}') from exc
        board = ''
        for _ in range(WHORU_TRIES):
            board = self.command('WHORU', wait=1.0)
            if board:
                break
        if board != BOARD_ID:
            self.ser.close()
            raise DeviceError(f'{self.port} is a {board}, not the {BOARD_ID} EOG firmware.' if board else
                              f'{self.port} opened, but no {BOARD_ID} Chords firmware answered WHORU. '
                              'Flash Code/firmware/ESP32-S3 and check the baud rate (230400).')
        return self.command('STATUS')

    def command(self, text, wait=0.3):
        """Send a command while NOT streaming and return its one-line reply ('' if none)."""
        if self._thread is not None:
            raise DeviceError('Stop streaming before sending commands.')
        self._require_open()
        self.ser.reset_input_buffer()
        self.ser.write(text.encode('ascii') + b'\n')
        deadline = time.monotonic() + wait
        reply = b''
        while time.monotonic() < deadline and not reply.endswith(b'\n'):
            reply += self.ser.read(self.ser.in_waiting or 1)
        return reply.decode('ascii', errors='replace').strip()

    # ---- streaming ----------------------------------------------------------------
    def start(self, sink):
        self._require_open()
        if self._thread is not None:
            raise DeviceError('Already streaming.')
        self.ser.reset_input_buffer()
        self.decoder.text_lines.clear()
        self._stop.clear()
        self._stop_requested_at = None
        self.ser.write(b'START\n')
        self.on_status('streaming', f'Streaming from {self.port}')  # before the reader can report a disconnect
        self._thread = threading.Thread(target=self._read_loop, args=(sink,), name='eog-reader', daemon=True)
        self._thread.start()

    def stop(self):
        """Send STOP, deliver packets already in flight, then return. Safe to call twice."""
        if self._thread is None:
            return
        try:
            if self.ser and self.ser.is_open:
                self.ser.write(b'STOP\n')
        except serial.SerialException:
            pass  # disconnect is reported by the reader
        self._stop_requested_at = time.monotonic()
        self._stop.set()
        self._thread.join(timeout=3)
        self._thread = None
        if self.error is None:
            self.on_status('stopped', 'Streaming stopped')

    def close(self):
        self.stop()
        if self.ser is not None:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None

    @property
    def streaming(self):
        return self._thread is not None and self._thread.is_alive()

    def _read_loop(self, sink):
        last_data = time.monotonic()
        silent = False
        while True:
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
            except (serial.SerialException, OSError, TypeError, AttributeError) as exc:
                # Unplugged: close and stay closed until the user restarts explicitly.
                self.error = f'{self.port} disconnected: {exc}'
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.on_status('disconnected', self.error)
                return
            now = time.monotonic()
            if data:
                last_data = now
                packets = self.decoder.feed(data)
                if packets:
                    samples = [self.sequencer.next(packet, now) for packet in packets]
                    try:
                        sink(samples)
                    except Exception as exc:
                        self.error = f'Input processing failed: {exc}'
                        self.on_status('disconnected', self.error)
                        return
                    if silent:
                        silent = False
                        self.on_status('resumed', 'Data is arriving again')
            if self._stop.is_set():
                if now - last_data >= DRAIN_SECONDS or now - self._stop_requested_at >= 2:
                    return
            elif not silent and now - last_data >= NO_DATA_SECONDS:
                silent = True
                self.on_status('no_data', f'No data for {NO_DATA_SECONDS:.0f} s (board reset or cable?)')

    def _require_open(self):
        if self.ser is None or not self.ser.is_open:
            raise DeviceError(self.error or 'Device is not open.')
