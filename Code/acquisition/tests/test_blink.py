from types import SimpleNamespace
from eog_acquisition.blink import DoubleBlinkDetector


def feed(pulses, gaps=()):
    detector = DoubleBlinkDetector()
    events = []
    for n in range(600):
        y = next((value for start, end, value in pulses if start <= n < end), 0)
        sample = SimpleNamespace(index=n, host_time=n / 250, gap_before=int(n in gaps), stall=False)
        events += detector.update(sample, y, True)
    return events


def test_double_with_rebound_emits_one_select():
    events = feed([(30, 50, 600), (55, 65, -300), (115, 135, 700), (140, 150, -300)])
    assert [e.command for e in events] == ['SELECT']
    assert events[0].source == 'eog'


def test_single_blink_and_long_gaze_do_not_select():
    assert not feed([(30, 50, 600)])
    assert not feed([(30, 140, 600), (170, 280, 600)])


def test_gap_and_slow_or_opposite_pair_do_not_select():
    assert not feed([(30, 50, 600), (115, 135, 600)], gaps=(80,))
    assert not feed([(30, 50, 600), (300, 320, 600)])
    assert not feed([(30, 50, 600), (115, 135, -600)])
