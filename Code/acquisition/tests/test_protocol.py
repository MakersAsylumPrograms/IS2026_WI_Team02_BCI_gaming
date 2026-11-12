import random

import numpy as np
import pytest

from eog_acquisition.device import ChordsUSBESP32S3
from eog_acquisition.protocol import ADC_MAX, NUM_SLOTS, PACKET_LEN, Sequencer, StreamDecoder, encode_packet


def packets(n, seed=0, start=0):
    rng = random.Random(seed)
    values = [rng.randrange(ADC_MAX + 1) for _ in range(n)]
    return values, b''.join(encode_packet(start + i, [v]) for i, v in enumerate(values))


def decode_all(stream, chunks=None):
    decoder = StreamDecoder()
    if chunks is None:
        return decoder.feed(stream), decoder
    out, i = [], 0
    for size in chunks:
        out += decoder.feed(stream[i:i + size])
        i += size
    out += decoder.feed(stream[i:])
    return out, decoder


class FakePort:
    """Just enough of serial.Serial for Chords_USB.read_data()."""
    def __init__(self, data):
        self.data, self.in_waiting = data, len(data)

    def read(self, n):
        chunk, self.data = self.data[:n], self.data[n:]
        self.in_waiting = len(self.data)
        return chunk


def test_profile_extends_official_chords_usb():
    from eog_acquisition.chords_compat import Chords_USB
    assert 'ESP32-S3' not in Chords_USB.supported_boards  # the reason this package adds it
    profile = ChordsUSBESP32S3.supported_boards['ESP32-S3']
    assert profile == {'sampling_rate': 250, 'Num_channels': 16, 'resolution': 12}
    assert PACKET_LEN == 2 * profile['Num_channels'] + Chords_USB.HEADER_LENGTH + 1 == 36


def test_matches_official_decoder_packet_by_packet():
    values, _ = packets(300, seed=3)
    # Fill the inactive slots too, so every byte position is compared.
    rich = [encode_packet(i, [v] + [(v * k) % 4096 for k in range(2, NUM_SLOTS + 1)]) for i, v in enumerate(values)]
    usb = ChordsUSBESP32S3.__new__(ChordsUSBESP32S3)  # skip its SIGINT handler; no port needed
    usb.buffer, usb.num_channels, usb.packet_length = bytearray(), NUM_SLOTS, PACKET_LEN
    usb.data = np.zeros((NUM_SLOTS, 2000))
    ours = StreamDecoder()
    for packet in rich:
        usb.ser = FakePort(packet)
        usb.read_data()
        [decoded] = ours.feed(packet)
        assert list(usb.data[:, -1].astype(int)) == list(decoded.channels)


def test_big_endian_and_counter():
    raw = encode_packet(0x2A, [0x0ABC])
    assert raw[:3] == bytes((0xC7, 0x7C, 0x2A)) and raw[3:5] == bytes((0x0A, 0xBC)) and raw[-1] == 0x01
    [packet], _ = decode_all(raw)
    assert packet.counter == 0x2A and packet.channels[0] == 2748 and not any(packet.channels[1:])


@pytest.mark.parametrize('chunks', ['bytewise', 'random'])
def test_fragmented_reads_give_identical_packets(chunks):
    values, stream = packets(400, seed=1)
    sizes = [1] * len(stream) if chunks == 'bytewise' else [random.Random(2).randint(1, 50) for _ in range(500)]
    out, decoder = decode_all(stream, sizes)
    assert [p.channels[0] for p in out] == values and decoder.junk_bytes == 0 and decoder.malformed_packets == 0


def test_counter_rollover_is_contiguous():
    values, stream = packets(700, seed=4)
    out, _ = decode_all(stream)
    sequencer = Sequencer()
    samples = [sequencer.next(p, 100 + i / 250) for i, p in enumerate(out)]
    assert [s.index for s in samples] == list(range(700))
    assert all(s.gap_before == 0 and not s.stall for s in samples) and sequencer.missing == 0
    assert [s.raw for s in samples] == values


def test_dropped_packets_show_as_gaps_never_as_samples():
    _, stream = packets(20, seed=5)
    kept = [stream[i * PACKET_LEN:(i + 1) * PACKET_LEN] for i in range(20) if i not in (5, 6, 7, 15)]
    out, _ = decode_all(b''.join(kept))
    sequencer = Sequencer()
    samples = [sequencer.next(p, 100 + p.counter / 250) for p in out]
    assert len(samples) == 16 and sequencer.missing == 4
    assert {s.index: s.gap_before for s in samples if s.gap_before} == {8: 3, 16: 1}


def test_long_silence_is_flagged_as_stall():
    sequencer = Sequencer()
    first = sequencer.next(StreamDecoder().feed(encode_packet(10, [1]))[0], 100.0)
    later = sequencer.next(StreamDecoder().feed(encode_packet(11, [1]))[0], 103.0)  # 3 s, counter says 1
    assert first.gap_before == 10 and later.gap_before == 0 and later.stall and sequencer.stalls == 1


def test_malformed_packets_are_skipped_and_resynchronised():
    good = [encode_packet(i, [100 + i]) for i in range(6)]
    bad_end = good[1][:-1] + b'\x02'
    too_big = good[2][:3] + b'\x10\x00' + good[2][5:]  # 4096 does not fit 12 bits
    stream = good[0] + bad_end + b'RUNNING\r\n' + too_big + good[3] + good[4][:20] + b'\xc7' + good[5]
    out, decoder = decode_all(stream, [7] * 100)
    assert [p.counter for p in out] == [0, 3, 5]
    assert decoder.malformed_packets >= 2 and 'RUNNING' in decoder.text_lines


def test_sync_split_across_reads():
    raw = encode_packet(1, [42])
    decoder = StreamDecoder()
    assert decoder.feed(b'junk' + raw[:1]) == []
    [packet] = decoder.feed(raw[1:])
    assert packet.channels[0] == 42 and decoder.junk_bytes == 4


def test_joystick_channels_are_active_and_slots_5_to_16_are_checked():
    _, decoder = decode_all(encode_packet(0, [1, 2048, 2048, 1]))   # EOG + joystick X, Y, button
    assert decoder.inactive_nonzero == 0
    _, decoder = decode_all(encode_packet(0, [1, 2048, 2048, 1, 7]))
    assert decoder.inactive_nonzero == 1


def test_samples_carry_the_joystick_channels():
    [packet], _ = decode_all(encode_packet(0, [1900, 3100, 50, 1]))
    sample = Sequencer().next(packet, 100.0)
    assert sample.raw == 1900 and sample.joystick == (3100, 50, 1)
