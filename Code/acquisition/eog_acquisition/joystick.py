"""Joystick (channels 2-4 of each packet) -> dashboard commands, with no delay.

Wiring: VRx -> GPIO 1, VRy -> GPIO 2, SW -> GPIO 6 (internal pull-up), GND -> GND, VCC -> 5V or 3V3.
The firmware sends X and Y as 0-4095 and the button as 1 (pressed) / 0, 250 times a second.

Button: SELECT on the press, after 20 ms of stable contact (works without any calibration).

Directions, two ways:
* Calibrated profile (models/joystick.json, made by `python -m eog_acquisition joystick-calibrate`):
  the module's own readings at rest and for each push. Each direction gets a trigger just outside
  the resting noise on the axis it really moves, so a push registers within 8 ms of leaving the
  rest range and LEFT is always LEFT, whatever the wiring or mounting. Needed for modules that rest
  off-centre or noisily (the team's module rests at x~540, y~90 and wanders by several hundred).
* Without a profile, a standard joystick is handled automatically: the centre is measured while the
  stick is still, and a push is half the travel on that side (at least 600 counts).

One push = one command; the stick must come back to rest before the next push counts.
LEFT/UP -> PREVIOUS, RIGHT/DOWN -> NEXT (`invert` swaps them).
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .protocol import ADC_MAX, SAMPLE_RATE

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / 'models' / 'joystick.json'
DIRECTIONS = ('LEFT', 'RIGHT', 'UP', 'DOWN')
COMMANDS = {'LEFT': 'PREVIOUS', 'UP': 'PREVIOUS', 'RIGHT': 'NEXT', 'DOWN': 'NEXT'}
DEBOUNCE_SAMPLES = 5        # button: 20 ms at 250 Hz
HOLD_SAMPLES = 2            # direction: 8 ms past the trigger, so a single noise spike never counts
TRIGGER_SHARE = 0.25        # profile: trigger this far from the rest edge towards the push level
MIN_MARGIN = 200            # profile: a push must clear the rest noise by at least this (counts)
# automatic (no profile) standard joystick
PUSH, RELEASE, MIN_PUSH_COUNTS = 0.5, 0.3, 600
STILL_SECONDS, STILL_RANGE, RAIL_MARGIN = 0.5, 200, 20
AXES = {('x', -1): 'LEFT', ('x', 1): 'RIGHT', ('y', -1): 'UP', ('y', 1): 'DOWN'}


@dataclass(frozen=True)
class JoystickEvent:
    command: str        # NEXT / PREVIOUS / SELECT
    direction: str      # LEFT / RIGHT / UP / DOWN / PRESS
    index: int
    host_time: float
    source: str = 'joystick'


@dataclass(frozen=True)
class Rule:
    """One direction: axis 0 (x) or 1 (y) moves with `sign`; fires past `trigger`, re-arms inside `release`."""
    axis: int
    sign: int
    trigger: float
    release: float
    push: float          # typical reading while held (for choosing between directions)


def build_profile(rows):
    """rows: [(label, x, y)] from the guided calibration (labels REST / LEFT / RIGHT / UP / DOWN).
    Returns (profile dict, report lines)."""
    by = {}
    for label, x, y in rows:
        by.setdefault(label, []).append((x, y))
    if len(by.get('REST', [])) < SAMPLE_RATE:
        raise ValueError('Not enough resting data: keep the stick untouched during REST.')
    rest = np.array(by['REST'], dtype=float)
    low, mid, high = np.percentile(rest, 1, 0), np.median(rest, 0), np.percentile(rest, 99, 0)
    rules, report = {}, []
    for direction in DIRECTIONS:
        pushed = np.array(by.get(direction, []), dtype=float)
        if len(pushed) < SAMPLE_RATE // 2:
            report.append(f'{direction:5}: no data')
            continue
        level = np.median(pushed, 0)
        best = None
        for axis in (0, 1):
            sign = 1 if level[axis] > mid[axis] else -1
            edge = high[axis] if sign > 0 else low[axis]
            margin = (level[axis] - edge) * sign             # how far the push clears the rest noise
            if margin >= MIN_MARGIN and (best is None or margin > best[0]):
                best = (margin, axis, sign, edge)
        if best is None:
            report.append(f'{direction:5}: NOT detectable (the readings stay inside the resting noise)')
            continue
        margin, axis, sign, edge = best
        rules[direction] = Rule(axis, sign, round(edge + sign * TRIGGER_SHARE * margin, 1), round(edge, 1),
                                round(float(level[axis]), 1))
        report.append(f'{direction:5}: {"xy"[axis]} {"rises" if sign > 0 else "falls"} to ~{level[axis]:.0f} '
                      f'(rest {low[axis]:.0f}-{high[axis]:.0f}); triggers at {rules[direction].trigger:.0f}')
    # Two directions on the same axis and side cannot be told apart: keep the stronger one.
    for a in list(rules):
        for b in list(rules):
            if a < b and a in rules and b in rules and (rules[a].axis, rules[a].sign) == (rules[b].axis, rules[b].sign):
                weaker = a if abs(rules[a].push - rules[a].release) < abs(rules[b].push - rules[b].release) else b
                report.append(f'{weaker:5}: dropped, it moves the same axis the same way as '
                              f'{b if weaker == a else a}')
                del rules[weaker]
    profile = dict(format='equalcare-joystick/1', rest=dict(low=low.tolist(), median=mid.tolist(), high=high.tolist()),
                   rules={d: asdict(r) for d, r in rules.items()})
    return profile, report


def save_profile(profile, path=DEFAULT_PROFILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding='utf-8')
    return path


def load_profile(path=DEFAULT_PROFILE):
    """The saved profile, or None if there is none."""
    path = Path(path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('format') != 'equalcare-joystick/1':
        raise ValueError(f'{path} is not a joystick profile')
    return data


class JoystickReader:
    def __init__(self, sample_rate=SAMPLE_RATE, invert=False, profile=None):
        self.invert = invert
        self.rules = {d: Rule(**r) for d, r in (profile or {}).get('rules', {}).items()}
        self._still = int(STILL_SECONDS * sample_rate)
        self.reset()

    def reset(self):
        self.mode = 'profile' if self.rules else None   # None until a standard joystick's centre is measured
        self.centre = None
        self.direction = None
        self._window = []
        self._run = 0
        self._pressed = False
        self._button_run = 0

    @property
    def ready(self):
        return self.mode is not None

    def _command(self, direction):
        command = COMMANDS[direction]
        if self.invert:
            command = 'NEXT' if command == 'PREVIOUS' else 'PREVIOUS'
        return command

    def update(self, sample):
        """Feed a protocol.Sample; returns a list of JoystickEvents (usually empty)."""
        if sample.joystick is None:
            return []
        x, y, button = sample.joystick
        events = []
        pressed = bool(button)
        self._button_run = self._button_run + 1 if pressed != self._pressed else 0
        if self._button_run >= DEBOUNCE_SAMPLES:
            self._pressed, self._button_run = pressed, 0
            if pressed:
                events.append(JoystickEvent('SELECT', 'PRESS', sample.index, sample.host_time))

        if self.mode == 'profile':
            direction = self._profile_step((x, y))
        elif self.mode == 'centre':
            direction = self._centre_step(x, y)
        else:
            self._measure_centre(x, y)
            direction = None
        if direction:
            events.append(JoystickEvent(self._command(direction), direction, sample.index, sample.host_time))
        return events

    # ---- calibrated profile ----------------------------------------------------------------
    def _profile_step(self, values):
        if self.direction is not None:
            rule = self.rules[self.direction]
            if (values[rule.axis] - rule.release) * rule.sign < 0:
                self.direction = None                     # back at rest: the next push may count
            return None
        crossed = [((values[r.axis] - r.release) / max(1.0, abs(r.push - r.release)), d)
                   for d, r in self.rules.items() if (values[r.axis] - r.trigger) * r.sign >= 0]
        self._run = self._run + 1 if crossed else 0
        if self._run < HOLD_SAMPLES:
            return None
        self._run = 0
        self.direction = max(crossed)[1]                  # the axis furthest along wins (cross-talk)
        return self.direction

    # ---- standard joystick, no profile -------------------------------------------------------
    def _measure_centre(self, x, y):
        self._window.append((x, y))
        if len(self._window) < self._still:
            return
        values = np.array(self._window)
        self._window = []
        spread = np.percentile(values, 95, axis=0) - np.percentile(values, 5, axis=0)
        centre = np.median(values, 0)
        if (spread <= STILL_RANGE).all() and ((centre > RAIL_MARGIN) & (centre < ADC_MAX - RAIL_MARGIN)).all():
            self.centre, self.mode = (float(centre[0]), float(centre[1])), 'centre'

    def _centre_step(self, x, y):
        moved = []
        for axis, value, centre in (('x', x, self.centre[0]), ('y', y, self.centre[1])):
            delta = value - centre
            share = delta / max(1.0, (ADC_MAX - centre) if delta >= 0 else centre)
            moved.append((axis, delta, share))
        if self.direction is not None:
            if not any(abs(d) >= 0.5 * MIN_PUSH_COUNTS and abs(s) >= RELEASE for _, d, s in moved):
                self.direction = None
            return None
        pushed = [(abs(s), axis, d) for axis, d, s in moved if abs(d) >= MIN_PUSH_COUNTS and abs(s) >= PUSH]
        self._run = self._run + 1 if pushed else 0
        if self._run < HOLD_SAMPLES:
            return None
        self._run = 0
        _, axis, delta = max(pushed)
        self.direction = AXES[(axis, 1 if delta > 0 else -1)]
        return self.direction
