"""3V3 PS2 fallback: GPIO4=X, GPIO5=Y, GPIO6=SW (active LOW in firmware).

One tilt emits once until stable neutral. One debounced press emits SELECT.
This module never suppresses EOG. Invalid calibration is rejected rather than
trying to compensate for unpowered, shorted or excessively noisy hardware.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .protocol import ADC_MAX, SAMPLE_RATE

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / 'models' / 'joystick.json'
DIRECTIONS = ('LEFT', 'RIGHT', 'UP', 'DOWN')
COMMANDS = dict(LEFT='PREVIOUS', UP='PREVIOUS', RIGHT='NEXT', DOWN='NEXT')
FORMAT = 'equalcare-joystick/2'


@dataclass(frozen=True)
class JoystickEvent:
    command: str
    direction: str
    index: int
    host_time: float
    source: str = 'joystick'


def _validate(profile):
    if profile.get('format') != FORMAT:
        raise ValueError('Old joystick profile: repeat joystick-calibrate with 3V3 wiring.')
    centre = np.asarray(profile.get('centre'), dtype=float)
    if centre.shape != (2,) or not np.isfinite(centre).all() or not ((centre > 100) & (centre < ADC_MAX - 100)).all():
        raise ValueError('Invalid joystick centre.')
    rules = profile.get('rules', {})
    if set(rules) != set(DIRECTIONS):
        raise ValueError('All four joystick directions must be distinguishable.')
    seen = set()
    for rule in rules.values():
        axis, sign, travel = rule['axis'], rule['sign'], rule['travel']
        if axis not in (0, 1) or sign not in (-1, 1) or not 600 <= travel <= ADC_MAX:
            raise ValueError('Invalid joystick travel or direction.')
        if (axis, sign) in seen:
            raise ValueError('Joystick directions overlap; check wiring.')
        seen.add((axis, sign))
    return profile


def build_profile(rows):
    groups = {label: np.asarray([(x, y) for tag, x, y in rows if tag == label], dtype=float)
              for label in ('REST', *DIRECTIONS)}
    if any(len(v) < SAMPLE_RATE // 2 for v in groups.values()):
        raise ValueError('Record REST and all four directions before calibration.')
    if any(not np.isfinite(v).all() or v.min() < 0 or v.max() > ADC_MAX for v in groups.values()):
        raise ValueError('Joystick readings outside the ADC range.')
    centre = np.median(groups['REST'], axis=0)
    noise = np.percentile(groups['REST'], 95, axis=0) - np.percentile(groups['REST'], 5, axis=0)
    if (noise > 200).any():
        raise ValueError('Joystick rest is too noisy. Check 3V3 power, ground and wiring.')
    rules, report = {}, []
    for direction in DIRECTIONS:
        delta = np.median(groups[direction], axis=0) - centre
        axis = int(np.argmax(np.abs(delta)))
        travel = float(abs(delta[axis]))
        if travel < max(600, 4 * noise[axis]):
            raise ValueError(f'{direction} has insufficient travel; check wiring.')
        rules[direction] = dict(axis=axis, sign=1 if delta[axis] > 0 else -1, travel=travel)
        report.append(f'{direction}: axis {axis}, travel {travel:.0f} counts')
    return _validate(dict(format=FORMAT, centre=centre.tolist(), rules=rules)), report


def save_profile(profile, path=DEFAULT_PROFILE):
    _validate(profile)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding='utf-8')
    return path


def load_profile(path=DEFAULT_PROFILE):
    path = Path(path)
    return _validate(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else None


class JoystickReader:
    def __init__(self, sample_rate=SAMPLE_RATE, invert=False, profile=None):
        self.invert = invert
        self.profile = _validate(profile) if profile else None
        self.centre = tuple(profile['centre']) if profile else None
        self.rules = profile['rules'] if profile else {}
        self._still = max(1, round(0.5 * sample_rate))
        self._debounce = max(1, round(0.02 * sample_rate))
        self.reset()

    @property
    def ready(self):
        return self.centre is not None

    def reset(self):
        # Retain a known centre, but require neutral/release after a gap.
        self.mode = 'profile' if self.profile else ('centre' if self.centre else None)
        self.direction = None
        self._window = []
        self._armed = self._button_ready = self._pressed = False
        self._neutral_run = self._direction_run = self._button_run = 0
        self._candidate = self._last_index = None

    def _command(self, direction):
        command = COMMANDS[direction]
        return ('NEXT' if command == 'PREVIOUS' else 'PREVIOUS') if self.invert else command

    def update(self, sample):
        if sample.joystick is None:
            self.reset()
            return []
        if sample.gap_before or sample.stall or (self._last_index is not None and sample.index != self._last_index + 1):
            self.reset()
        self._last_index = sample.index
        x, y, button = sample.joystick
        if not (0 <= x <= ADC_MAX and 0 <= y <= ADC_MAX and button in (0, 1)):
            self.reset()
            return []
        events = []
        pressed = bool(button)
        if not self._button_ready:
            self._button_run = self._button_run + 1 if not pressed else 0
            if self._button_run >= self._debounce:
                self._button_ready, self._button_run = True, 0
        else:
            self._button_run = self._button_run + 1 if pressed != self._pressed else 0
            if self._button_run >= self._debounce:
                self._pressed, self._button_run = pressed, 0
                if pressed:
                    events.append(JoystickEvent('SELECT', 'PRESS', sample.index, sample.host_time))
        if self.centre is None:
            self._window.append((x, y))
            if len(self._window) >= self._still:
                values, self._window = np.asarray(self._window), []
                centre = np.median(values, axis=0)
                if (np.ptp(values, axis=0) <= 200).all() and ((centre > ADC_MAX * 0.2) & (centre < ADC_MAX * 0.8)).all():
                    self.centre, self.mode = tuple(centre), 'centre'
            return events
        delta = np.asarray((x, y)) - self.centre
        if self.rules:
            shares = {d: delta[r['axis']] * r['sign'] / r['travel'] for d, r in self.rules.items()}
        else:
            shares = {d: delta[a] * sign / max(600, (ADC_MAX - self.centre[a]) if sign > 0 else self.centre[a])
                      for d, a, sign in [('LEFT', 0, -1), ('RIGHT', 0, 1), ('UP', 1, -1), ('DOWN', 1, 1)]}
        self._neutral_run = self._neutral_run + 1 if max(shares.values()) < 0.25 else 0
        if self._neutral_run >= self._debounce:
            self._armed, self.direction = True, None
        candidate = max(shares, key=shares.get)
        candidate = candidate if shares[candidate] >= 0.5 else None
        self._direction_run = self._direction_run + 1 if candidate == self._candidate else 1
        self._candidate = candidate
        if self._armed and candidate and self._direction_run >= self._debounce:
            self._armed, self.direction = False, candidate
            events.append(JoystickEvent(self._command(candidate), candidate, sample.index, sample.host_time))
        return events
