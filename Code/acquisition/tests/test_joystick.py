import pytest

from eog_acquisition.joystick import JoystickReader, build_profile, load_profile, save_profile
from eog_acquisition.protocol import Sample


def feed(reader, path, start=0, gap=False):
    events = []
    index = start
    for count, x, y, button in path:
        for _ in range(count):
            events.extend(reader.update(Sample(index, index % 256, 1900, index / 250,
                                               int(gap and index == start), False, (x, y, button))))
            index += 1
    return [(e.direction, e.command) for e in events], index


REST = (150, 2048, 2048, 0)


def test_each_direction_hold_neutral_and_invert():
    path = [REST, (250, 0, 2048, 0), (20, 2048, 2048, 0),
            (250, 4095, 2048, 0), (20, 2048, 2048, 0),
            (250, 2048, 0, 0), (20, 2048, 2048, 0), (250, 2048, 4095, 0)]
    assert feed(JoystickReader(), path)[0] == [('LEFT', 'PREVIOUS'), ('RIGHT', 'NEXT'),
                                              ('UP', 'PREVIOUS'), ('DOWN', 'NEXT')]
    assert feed(JoystickReader(invert=True), [REST, (20, 0, 2048, 0)])[0] == [('LEFT', 'NEXT')]


def test_diagonal_is_one_command_and_requires_both_axes_neutral():
    events, _ = feed(JoystickReader(), [REST, (40, 4095, 3800, 0), (30, 2048, 4095, 0),
                                      (20, 2048, 2048, 0), (30, 0, 2048, 0)])
    assert events == [('RIGHT', 'NEXT'), ('LEFT', 'PREVIOUS')]


def test_button_bounce_and_holding():
    bounce = [(2, 2048, 2048, 1), (2, 2048, 2048, 0)] * 3
    assert feed(JoystickReader(), [REST] + bounce + [(100, 2048, 2048, 1),
                (10, 2048, 2048, 0), (50, 2048, 2048, 1)])[0] == [('PRESS', 'SELECT'), ('PRESS', 'SELECT')]


def test_start_pressed_does_not_select_until_released():
    assert feed(JoystickReader(), [(100, 2048, 2048, 1), (10, 2048, 2048, 0),
                                  (10, 2048, 2048, 1)])[0] == [('PRESS', 'SELECT')]


@pytest.mark.parametrize('value', [0, 50, 4095])
def test_unpowered_or_rail_rest_never_becomes_centre(value):
    reader = JoystickReader()
    assert feed(reader, [(500, value, value, 0)])[0] == []
    assert not reader.ready


def test_button_can_select_when_axes_unpowered():
    reader = JoystickReader()
    assert feed(reader, [(20, 0, 0, 0), (20, 0, 0, 1)])[0] == [('PRESS', 'SELECT')]
    assert not reader.ready


def test_gap_during_hold_requires_release_and_neutral():
    reader = JoystickReader()
    events, end = feed(reader, [REST, (20, 4095, 2048, 1)])
    assert events == [('PRESS', 'SELECT'), ('RIGHT', 'NEXT')]
    events, end = feed(reader, [(50, 4095, 2048, 1)], start=end + 5, gap=True)
    assert events == []
    events, _ = feed(reader, [(20, 2048, 2048, 0), (20, 4095, 2048, 1)], start=end)
    assert events == [('PRESS', 'SELECT'), ('RIGHT', 'NEXT')]


def calibration_rows():
    rows = [('REST', 2048 + i % 5, 2048 - i % 5) for i in range(300)]
    for label, x, y in [('LEFT', 0, 2048), ('RIGHT', 4095, 2048),
                        ('UP', 2048, 0), ('DOWN', 2048, 4095)]:
        rows.extend([(label, x, y)] * 150)
    return rows


def test_profile_round_trip_and_runtime(tmp_path):
    profile, report = build_profile(calibration_rows())
    path = save_profile(profile, tmp_path / 'joystick.json')
    loaded = load_profile(path)
    assert len(report) == 4 and loaded == profile
    assert feed(JoystickReader(profile=loaded), [REST, (20, 0, 2048, 0)])[0] == [('LEFT', 'PREVIOUS')]


def test_profile_rejects_missing_stuck_and_noisy_calibration():
    rows = calibration_rows()
    with pytest.raises(ValueError):
        build_profile([r for r in rows if r[0] != 'UP'])
    with pytest.raises(ValueError):
        build_profile([('UP', 2048, 2048) if r[0] == 'UP' else r for r in rows])
    with pytest.raises(ValueError):
        build_profile([('REST', 500 if i % 2 else 3000, 2048) if r[0] == 'REST' else r
                       for i, r in enumerate(rows)])


def test_old_profile_is_rejected_and_missing_profile_is_optional(tmp_path):
    assert load_profile(tmp_path / 'missing.json') is None
    with pytest.raises(ValueError):
        JoystickReader(profile={'format': 'equalcare-joystick/1'})


def test_invalid_samples_reset_without_events():
    reader = JoystickReader()
    feed(reader, [REST])
    assert feed(reader, [(20, 5000, 2048, 1)], start=150)[0] == []
    assert reader.update(Sample(171, 171, 1900, 0.7, 0)) == []
