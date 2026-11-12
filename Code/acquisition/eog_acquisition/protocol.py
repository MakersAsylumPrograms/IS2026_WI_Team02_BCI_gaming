"""Chords USB packet format sent by Code/firmware/ESP32-S3 and turning packets into ordered samples.

Packet (36 bytes): 0xC7 0x7C, rolling counter (0-255), 16 big-endian uint16 channel
slots, 0x01. Channel 1 carries the EOG signal; channels 2-4 the joystick (X, Y, button
0/1); slots 5-16 are inactive (always 0).
"""
from dataclasses import dataclass

BOARD_ID = 'ESP32-S3'
BAUD_RATE = 230400
SAMPLE_RATE = 250
NUM_SLOTS = 16
RESOLUTION_BITS = 12
ADC_MAX = (1 << RESOLUTION_BITS) - 1
SYNC = bytes((0xC7, 0x7C))
END_BYTE = 0x01
HEADER_LEN = 3
PACKET_LEN = HEADER_LEN + 2 * NUM_SLOTS + 1  # 36
EOG_SLOT = 0  # channel 1 (EOG, GPIO 5)
JOYSTICK_SLOTS = slice(1, 4)  # channels 2-4: X (GPIO 1), Y (GPIO 2), button (GPIO 6, 1 = pressed)
FIRST_INACTIVE_SLOT = 4       # channels 5-16 are always 0
COUNTER_MODULO = 256


@dataclass(frozen=True)
class Packet:
    counter: int
    channels: tuple  # NUM_SLOTS raw ADC values


@dataclass(frozen=True)
class Sample:
    """One accepted EOG sample. Raw values are ADC counts, never microvolts."""
    index: int        # samples since START, including missing ones (index / SAMPLE_RATE = device seconds)
    counter: int      # counter byte from the packet
    raw: int          # channel 1 ADC counts, unchanged
    host_time: float  # time.monotonic() when the packet was decoded
    gap_before: int   # samples missing immediately before this one, from the counter (0 = contiguous)
    stall: bool = False  # nothing arrived for > STALL_SECONDS: whole counter cycles may also be missing
    joystick: tuple = None  # (x, y, button) from channels 2-4; None when replaying a recording


def encode_packet(counter, channels):
    """Build one packet, exactly as the firmware does (used by tests and simulators)."""
    values = list(channels) + [0] * (NUM_SLOTS - len(channels))
    body = b''.join(int(v).to_bytes(2, 'big') for v in values)
    return SYNC + bytes((counter % COUNTER_MODULO,)) + body + bytes((END_BYTE,))


class StreamDecoder:
    """Incremental decoder for arbitrary byte fragments.

    Returns complete packets in arrival order. Bytes outside packets (command replies,
    boot messages, the remains of a damaged packet) are counted as junk and never
    decoded; printable lines among them are kept in `text_lines`.
    """

    def __init__(self):
        self._buffer = bytearray()
        self._text = bytearray()
        self.text_lines = []
        self.packets = 0
        self.junk_bytes = 0
        self.malformed_packets = 0
        self.inactive_nonzero = 0  # packets whose inactive slots were not 0 (wrong firmware?)

    def feed(self, data):
        buffer = self._buffer
        buffer.extend(data)
        packets = []
        while True:
            start = buffer.find(SYNC)
            if start < 0:
                keep = 1 if buffer[-1:] == SYNC[:1] else 0  # SYNC may be split across reads
                self._junk(buffer[:len(buffer) - keep])
                del buffer[:len(buffer) - keep]
                return packets
            if start:
                self._junk(buffer[:start])
                del buffer[:start]
            if len(buffer) < PACKET_LEN:
                return packets
            packet = self._parse(buffer[:PACKET_LEN])
            if packet is None:
                # Not a real packet start: skip one byte and resynchronise on the next SYNC.
                self.malformed_packets += 1
                self._junk(buffer[:1])
                del buffer[:1]
                continue
            del buffer[:PACKET_LEN]
            self.packets += 1
            packets.append(packet)

    def _parse(self, raw):
        if raw[-1] != END_BYTE:
            return None
        channels = tuple(int.from_bytes(raw[i:i + 2], 'big') for i in range(HEADER_LEN, PACKET_LEN - 1, 2))
        if max(channels) > ADC_MAX:
            return None
        if any(channels[FIRST_INACTIVE_SLOT:]):
            self.inactive_nonzero += 1
        return Packet(counter=raw[2], channels=channels)

    def _junk(self, data):
        self.junk_bytes += len(data)
        for byte in data:
            if byte in (10, 13):
                if self._text:
                    self.text_lines.append(self._text.decode('ascii'))
                    self._text.clear()
            elif 32 <= byte < 127 and len(self._text) < 200:
                self._text.append(byte)
            else:
                self._text.clear()  # binary noise: not a text line


STALL_SECONDS = 1.0


class Sequencer:
    """Turns packet counters into a continuous sample index and reports gaps.

    Gaps come from the counter only. The 8-bit counter cannot tell 1 missing sample
    from 257, and host timing cannot settle it either (the OS buffers data while the
    host is busy), so a pause longer than STALL_SECONDS is flagged as a stall: an
    unexplained discontinuity whose true size is unknown.
    """

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._last = None  # (index, counter, host_time)
        self.missing = 0
        self.stalls = 0

    def next(self, packet, host_time):
        stall = False
        if self._last is None:
            # The firmware restarts the counter at 0 on START; a later first counter means lost samples.
            index = gap = packet.counter
        else:
            last_index, last_counter, last_time = self._last
            delta = (packet.counter - last_counter) % COUNTER_MODULO or COUNTER_MODULO
            index, gap = last_index + delta, delta - 1
            stall = host_time - last_time > STALL_SECONDS + delta / self.sample_rate
            self.stalls += stall
        self.missing += gap
        self._last = (index, packet.counter, host_time)
        return Sample(index=index, counter=packet.counter, raw=packet.channels[EOG_SLOT],
                      host_time=host_time, gap_before=gap, stall=stall,
                      joystick=tuple(packet.channels[JOYSTICK_SLOTS]))
