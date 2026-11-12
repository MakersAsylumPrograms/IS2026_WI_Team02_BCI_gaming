import pytest

from eog_acquisition.cycle import CycleDetector, CycleModel
from eog_acquisition.joystick import JoystickReader
from eog_acquisition.processing import Pipeline
from eog_acquisition.protocol import Sample

FS = 250


def samples(path, start=0):
    """path: [(seconds, x, y, button)] -> Samples with joystick channels."""
    out, i = [], start
    for seconds, x, y, button in path:
        for _ in range(int(seconds * FS)):
            out.append(Sample(i, i % 256, 1900, i / FS, 0, False, (x, y, button)))
            i += 1
    return out


def commands(path, **kw):
    reader = JoystickReader(**kw)
    return [(e.direction, e.command) for s in samples(path) for e in reader.update(s)], reader


@pytest.mark.parametrize('centre', [3100, 2000])   # 5 V supply (ADC saturates) and 3V3 supply
def test_each_direction_gives_one_command_per_push(centre):
    path = [(1, centre, centre, 0),
            (0.5, centre, 0, 0), (0.5, centre, centre, 0),       # up
            (0.5, centre, 4095, 0), (0.5, centre, centre, 0),    # down
            (0.5, 0, centre, 0), (0.5, centre, centre, 0),       # left
            (0.5, 4095, centre, 0), (0.5, centre, centre, 0)]    # right
    events, reader = commands(path)
    assert reader.ready and abs(reader.centre[0] - centre) < 1
    assert events == [('UP', 'PREVIOUS'), ('DOWN', 'NEXT'), ('LEFT', 'PREVIOUS'), ('RIGHT', 'NEXT')]


def test_holding_a_push_is_one_command_and_invert_swaps():
    path = [(1, 3100, 3100, 0), (3, 0, 3100, 0), (0.5, 3100, 3100, 0)]
    assert commands(path)[0] == [('LEFT', 'PREVIOUS')]
    assert commands(path, invert=True)[0] == [('LEFT', 'NEXT')]


def test_small_wobble_is_ignored():
    path = [(1, 3100, 3100, 0), (2, 3100 + 300, 3100 - 800, 0)]   # well under half the travel
    assert commands(path)[0] == []


def test_button_press_is_one_debounced_select():
    bounce = [(0.008, 3100, 3100, 1), (0.008, 3100, 3100, 0)] * 3          # contact bounce: ignored
    path = [(1, 3100, 3100, 0)] + bounce + [(0.3, 3100, 3100, 1), (0.3, 3100, 3100, 0)]
    assert commands(path)[0] == [('PRESS', 'SELECT')]


def test_no_joystick_connected_sends_nothing():
    for stuck in (0, 4095):                                               # disconnected / unpowered axes
        events, reader = commands([(3, stuck, stuck, 0), (1, 2000, stuck, 0)])
        assert events == [] and not reader.ready


def test_moving_while_measuring_the_centre_waits_for_stillness():
    wobble = [(0.1, 3100 + (300 if k % 2 else -300), 3100, 0) for k in range(10)]
    events, reader = commands(wobble + [(1, 3100, 3100, 0), (0.5, 0, 3100, 0)])
    assert reader.ready and events == [('LEFT', 'PREVIOUS')]


def test_pipeline_delivers_joystick_and_eye_events_together():
    pipeline = Pipeline(FS, detector=CycleDetector(CycleModel()), joystick=JoystickReader())
    seen = []
    pipeline.on_event(seen.append)
    for s in samples([(1, 3100, 3100, 0), (0.5, 4095, 3100, 0), (0.5, 3100, 3100, 0)]):
        pipeline.process(s)
    assert [(e.source, e.command) for e in seen] == [('joystick', 'NEXT')]


def test_replayed_recordings_have_no_joystick_and_are_ignored():
    reader = JoystickReader()
    assert reader.update(Sample(0, 0, 1900, 0.0, 0)) == []


def test_button_selects_even_before_the_stick_centre_is_known():
    # X/Y unpowered (0) so no centre, but the button only needs GND: SELECT must still be sent.
    events, reader = commands([(1, 0, 0, 0), (0.3, 0, 0, 1), (0.3, 0, 0, 0)])
    assert not reader.ready and events == [('PRESS', 'SELECT')]


def test_low_resting_joystick_like_the_teams_module():
    # Measured 2026-11-12: rests at x~540, y~90 (noise +-60), pushes reach ~4000.
    rest = [(0.04, 540 + d, 90 + d // 3, 0) for d in (-30, 25, -10, 40, -45, 15) * 5]
    events, reader = commands(rest + [(0.5, 4010, 120, 0), (0.5, 540, 90, 0),      # right
                                      (0.5, 560, 3941, 0), (0.5, 540, 90, 0),      # down
                                      (0.5, 377, 37, 0), (0.5, 540, 90, 0),        # small dip: ignored
                                      (0.5, 2131, 136, 0), (0.5, 540, 90, 0)])     # under half travel: ignored
    assert reader.ready and abs(reader.centre[0] - 540) < 40 and abs(reader.centre[1] - 90) < 20
    assert events == [('RIGHT', 'NEXT'), ('DOWN', 'NEXT')]


# ---- calibrated profile: shaped like the team's module (2026-11-12) ---------------------------
# At rest x wanders 300-1100 and y 0-350. In this synthetic module RIGHT drives x up (Y follows a little),
# DOWN drives y up, LEFT drives x towards 0 (just below the rest noise) and UP does nothing measurable.
import numpy as np

from eog_acquisition.joystick import build_profile

PUSHES = {'RIGHT': (4000, 1300), 'DOWN': (700, 3300), 'LEFT': (40, 150), 'UP': (700, 170)}


def noisy(seconds, x=(300, 1100), y=(0, 350), seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    return list(zip(rng.integers(*x, n).tolist(), rng.integers(*y, n).tolist()))


def calibration_rows():
    rows = [('REST', x, y) for x, y in noisy(3)]
    for k, (direction, (px, py)) in enumerate(PUSHES.items()):
        rows += [(direction, x, y) for x, y in noisy(1.6, (px - 40, px + 40), (py - 40, py + 40), seed=k + 1)]
        rows += [('REST', x, y) for x, y in noisy(1.1, seed=k + 10)]
    return rows


def test_calibration_learns_each_direction_from_the_module_itself():
    profile, report = build_profile(calibration_rows())
    rules = profile['rules']
    assert set(rules) == {'RIGHT', 'DOWN', 'LEFT'}                     # UP never leaves the resting noise
    assert (rules['RIGHT']['axis'], rules['RIGHT']['sign']) == (0, 1)
    assert (rules['DOWN']['axis'], rules['DOWN']['sign']) == (1, 1)
    assert (rules['LEFT']['axis'], rules['LEFT']['sign']) == (0, -1)
    assert any('UP' in line and 'NOT detectable' in line for line in report)


def test_calibrated_pushes_fire_immediately_in_the_right_direction():
    profile, _ = build_profile(calibration_rows())
    reader = JoystickReader(profile=profile)
    rule = profile['rules']['RIGHT']
    stream = [(x, y, 0) for x, y in noisy(2, seed=50)]                     # rest noise: nothing
    ramp = np.linspace(700, 4000, 25).astype(int)                           # a 100 ms push to the right
    stream += [(int(v), 400, 0) for v in ramp] + [(4000, 1300, 0)] * 50     # held (Y cross-talk)
    stream += [(x, y, 0) for x, y in noisy(0.5, seed=51)]
    stream += [(700, 3300, 0)] * 50 + [(x, y, 0) for x, y in noisy(0.5, seed=52)]   # down
    stream += [(40, 150, 0)] * 50 + [(x, y, 0) for x, y in noisy(0.5, seed=53)]     # left
    events = [(e.direction, e.command, e.index) for i, (x, y, b) in enumerate(stream)
              for e in reader.update(Sample(i, i % 256, 1900, i / FS, 0, False, (x, y, b)))]
    assert [(d, c) for d, c, _ in events] == [('RIGHT', 'NEXT'), ('DOWN', 'NEXT'), ('LEFT', 'PREVIOUS')]
    crossing = 2 * FS + int(np.argmax(ramp >= rule['trigger']))
    assert events[0][2] - crossing <= 2                                     # within 8 ms of the trigger


def test_directions_on_the_same_axis_and_side_cannot_both_be_kept():
    rows = calibration_rows() + [('UP', x, y) for x, y in noisy(1.6, (3000, 3100), (100, 200), seed=99)]
    rows = [r for r in rows if r[0] != 'UP' or r[1] >= 3000]                # UP now also drives x up, weaker
    profile, report = build_profile(rows)
    assert 'UP' not in profile['rules'] and any('UP' in line and 'dropped' in line for line in report)
