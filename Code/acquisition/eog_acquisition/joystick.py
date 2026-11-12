"""Joystick (channels 2-4 of each packet) -> dashboard commands.

Wiring: VRx -> GPIO 1, VRy -> GPIO 2, SW -> GPIO 6 (internal pull-up), GND -> GND, VCC -> 5V or 3V3.
The firmware sends X and Y as 0-4095 and the button as 1 (pressed) / 0.

* The centre is measured automatically: the first 0.5 s in which the stick is still and neither
  axis is pinned at an ADC rail (0 or 4095 = unpowered or disconnected). The centre does NOT have to
  be mid-range: the team's module rests at about x=540, y=90. Until then nothing but the button is sent.
* A push counts when it moves at least MIN_PUSH_COUNTS from the centre AND reaches PUSH of the travel
  available on that side (the two sides can differ a lot: on 5V the ADC saturates at ~3.1 V, and a
  low-resting module has almost no travel below its centre, so its LEFT/UP cannot be detected; NEXT
  alone still reaches every option because the menu wraps). The stick must come back near the
  centre before the next push counts. One push = one command, no repeats.
* Fallback ("high-push" mode): if no steady centre is found within FALLBACK_SECONDS but both axes rest
  low (the team's module, measured 2026-11-12, wanders 0-1100 at rest and pushes reach 2000-4095), a
  push is an axis rising above HIGH for HOLD_SAMPLES samples: X -> NEXT, Y -> PREVIOUS (the higher
  axis wins, because Y follows X partly on this module). It must fall below LOW before the next push.
* LEFT and UP -> PREVIOUS, RIGHT and DOWN -> NEXT, button press -> SELECT (debounced).
  Which raw direction is "left" depends on how the module is mounted: see AXES / `invert`.
"""
from dataclasses import dataclass

import numpy as np

from .protocol import ADC_MAX, SAMPLE_RATE

PUSH = 0.5                     # share of the travel on that side
RELEASE = 0.3
MIN_PUSH_COUNTS = 600          # and at least this far from the centre (noise near a low centre is ~100)
STILL_SECONDS = 0.5
STILL_RANGE = 200              # ADC counts (5th-95th percentile) an axis may wander while measuring the centre
RAIL_MARGIN = 20               # a centre this close to 0 or 4095 means unpowered / disconnected
DEBOUNCE_SAMPLES = 5            # 20 ms at 250 Hz
FALLBACK_SECONDS = 3.0         # no steady centre by then -> high-push mode (if the axes rest low)
LOW_REST = 1300                # both axes' median below this = a low-resting module
HIGH = 1800                    # high-push mode: an axis above this is a push ...
LOW = 1300                     # ... and it must drop below this before the next push
HOLD_SAMPLES = 3               # 12 ms above HIGH, so single noise spikes never count
# raw deflection -> direction for the module as mounted in the team's build
AXES = {('x', -1): 'LEFT', ('x', 1): 'RIGHT', ('y', -1): 'UP', ('y', 1): 'DOWN'}
COMMANDS = {'LEFT': 'PREVIOUS', 'UP': 'PREVIOUS', 'RIGHT': 'NEXT', 'DOWN': 'NEXT'}


@dataclass(frozen=True)
class JoystickEvent:
    command: str        # NEXT / PREVIOUS / SELECT
    direction: str      # LEFT / RIGHT / UP / DOWN / PRESS
    index: int
    host_time: float
    source: str = 'joystick'


class JoystickReader:
    def __init__(self, sample_rate=SAMPLE_RATE, invert=False):
        self.invert = invert
        self._still = int(STILL_SECONDS * sample_rate)
        self.reset()

    def reset(self):
        self.centre = None
        self.mode = None          # 'centre' (normal joystick) or 'high' (low-resting, noisy module)
        self.direction = None
        self._window = []
        self._recent = []
        self._seen = 0
        self._run = 0
        self._pressed = False
        self._button_run = 0

    @property
    def ready(self):
        return self.mode is not None

    def _calibrate(self, x, y):
        self._seen += 1
        self._recent = (self._recent + [(x, y)])[-2 * self._still:]
        if self._seen >= FALLBACK_SECONDS * self._still / STILL_SECONDS:
            rest = np.median(np.array(self._recent), 0)
            if ((rest > RAIL_MARGIN) & (rest < LOW_REST)).all():
                self.mode = 'high'
                return
        self._window.append((x, y))
        if len(self._window) < self._still:
            return
        values = np.array(self._window)
        self._window = []
        spread = np.percentile(values, 95, axis=0) - np.percentile(values, 5, axis=0)
        centre = np.median(values, 0)
        off_rails = ((centre > RAIL_MARGIN) & (centre < ADC_MAX - RAIL_MARGIN)).all()
        if (spread <= STILL_RANGE).all() and off_rails:
            self.centre = (float(centre[0]), float(centre[1]))
            self.mode = 'centre'

    def deflection(self, x, y):
        """(signed counts from the centre, share of the travel on that side) for each axis."""
        out = []
        for value, centre in zip((x, y), self.centre):
            delta = value - centre
            travel = (ADC_MAX - centre) if delta >= 0 else centre
            out.append((delta, delta / max(1.0, travel)))
        return out

    @staticmethod
    def _beyond(delta, share, fraction, counts):
        return abs(delta) >= counts and abs(share) >= fraction

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

        # Button: must hold the same state DEBOUNCE_SAMPLES in a row; SELECT on the press edge.
        # It does not depend on the X/Y centre (an unconnected SW pin idles released via the pull-up).
        pressed = bool(button)
        self._button_run = self._button_run + 1 if pressed != self._pressed else 0
        if self._button_run >= DEBOUNCE_SAMPLES:
            self._pressed, self._button_run = pressed, 0
            if pressed:
                events.append(JoystickEvent('SELECT', 'PRESS', sample.index, sample.host_time))

        if not self.ready:
            self._calibrate(x, y)
            return events
        if self.mode == 'high':
            return events + self._high_push(x, y, sample)
        axes = dict(zip('xy', self.deflection(x, y)))
        if self.direction is None:
            pushed = [(abs(share), axis, delta) for axis, (delta, share) in axes.items()
                      if self._beyond(delta, share, PUSH, MIN_PUSH_COUNTS)]
            if pushed:
                _, axis, delta = max(pushed)
                self.direction = AXES[(axis, 1 if delta > 0 else -1)]
                events.append(JoystickEvent(self._command(self.direction), self.direction, sample.index,
                                            sample.host_time))
        elif not any(self._beyond(d, s, RELEASE, 0.5 * MIN_PUSH_COUNTS) for d, s in axes.values()):
            self.direction = None
        return events

    def _high_push(self, x, y, sample):
        if self.direction is not None:
            if max(x, y) < LOW:
                self.direction = None
            return []
        over = [(value, axis) for axis, value in (('X', x), ('Y', y)) if value >= HIGH]
        self._run = self._run + 1 if over else 0
        if self._run < HOLD_SAMPLES:
            return []
        self._run = 0
        _, axis = max(over)
        self.direction = f'{axis}-PUSH'
        command = 'NEXT' if axis == 'X' else 'PREVIOUS'
        if self.invert:
            command = 'NEXT' if command == 'PREVIOUS' else 'PREVIOUS'
        return [JoystickEvent(command, self.direction, sample.index, sample.host_time)]
