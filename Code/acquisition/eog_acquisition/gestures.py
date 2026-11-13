"""LEFT, RIGHT and DOUBLE BLINK from horizontal EOG, on the signal Chords-Web shows and records.

Electrodes: IN+ at the left eye's outer corner, IN- at the right eye's, REF behind the right ear.
The signal is channel 1 through chords_filter.ChordsWebFilter (Chords-Web's high-pass, 10 Hz EOG
low-pass and 50 Hz notch; 0 = rest, +-1 = half the ADC range). The Pill's own high-pass (time
constant ~0.1-0.15 s) has already turned every movement into a pulse, and Chords-Web's slow one
leaves a small opposite level behind any pulse that is not undone by a second one:

  look LEFT and hold    one positive pulse, then a small NEGATIVE level that stays for seconds
  look RIGHT and hold   the same, mirrored: one negative pulse, then a small positive level
  back to the centre    the opposite pulse of the look before it; never a command
  blink                 a positive spike and at once a deep negative swing that dies away again

GestureDetector watches the `confirm` seconds after every pulse. If the opposite level is still
there at the end, the eyes moved and stayed (a movement); if a deep swing has died away, it was a
blink. A movement is a LOOK, and so a command, when its pulse is tall and wide: a big eye movement
takes longer and often clips the ADC, so it shows as a wider pulse rather than a taller one. The
detector remembers which side the eyes went to and how far, so the way back does nothing.

  LEFT -> PREVIOUS    RIGHT -> NEXT    two blinks in a row -> SELECT    one blink -> nothing

These are the commands the joystick sends for left, right and press. Raw samples are only read.
"""
import hashlib
import json
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .chords_filter import NOTCH_HZ, ChordsWebFilter
from .protocol import RESOLUTION_BITS, SAMPLE_RATE

FORMAT = 'equalcare-eog-gestures/1'
COMMANDS = dict(LEFT='PREVIOUS', RIGHT='NEXT', DOUBLE_BLINK='SELECT')
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / 'models' / 'eog-gestures.json'


@dataclass(frozen=True)
class GestureModel:
    # The signal the thresholds below belong to. Units: Chords-Web values (1.0 = 2,048 ADC counts).
    sample_rate: int = SAMPLE_RATE
    adc_bits: int = RESOLUTION_BITS
    notch_hz: int = NOTCH_HZ
    in_plus: str = 'left'        # electrode at the left eye's outer corner
    left_polarity: int = 1       # sign of the pulse a look to the LEFT makes
    blink_polarity: int = 1      # sign of a blink's spike
    # Pulses and what follows them
    onset: float = 0.25          # a pulse is the time the signal stays beyond this
    max_pulse: float = 1.0       # s; anything longer is an artefact (loose electrode), not a movement
    confirm: float = 0.5         # s watched after a pulse before deciding what it was
    tail_from: float = 0.4       # the level that is left is measured from here until `confirm` (s)
    # Movements: the opposite level stays, i.e. the eyes went somewhere and stayed
    move_peak: float = 0.4       # smallest pulse that counts as an eye movement at all
    tail: float = 0.04           # smallest opposite level that counts as "stayed"
    return_ratio: float = 2.0    # a movement back is "the way back" up to this many times the way out
    away_timeout: float = 8.0    # s after which the eyes are assumed back at the centre anyway
    # Looks: the movements that are commands (calibrated)
    look_peak: float = 0.5       # a look's pulse is at least this tall ...
    look_width: float = 0.18     # ... and beyond `onset` for at least this long (s)
    # Blinks: a spike whose opposite swing is deep and dies away (calibrated)
    blink_peak: float = 0.45     # a blink's spike is at least this tall
    rebound: float = 0.25        # the swing after it is at least this deep ...
    rebound_ratio: float = 0.3   # ... and at least this share of the spike ...
    recover: float = 0.5         # ... and less than this share of it is left at the end of the watch
    rebound_max: float = 1.5     # a swing bigger than this many spikes is a movement of its own
    double_min: float = 0.2      # s between the starts of the two blinks of a double blink
    double_max: float = 0.85
    select_cooldown: float = 1.0  # s after a SELECT in which blinks are ignored
    # Safety
    max_clipped: float = 0.35    # s of ADC saturation within half a second: signal is not trusted
    settle: float = 1.0          # s of quiet signal needed after saturation or an artefact
    warmup: float = 3.0          # s without commands after start or any gap in the data
    validation: dict = None

    @property
    def threshold(self):
        """The band the live plot draws (same attribute as CycleModel)."""
        return self.look_peak

    def to_dict(self):
        return dict(format=FORMAT, **asdict(self))

    @classmethod
    def from_dict(cls, data):
        if data.get('format') != FORMAT:
            raise ValueError(f'Not an EOG gesture model (format {data.get("format")!r}).')
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def load_model(path=DEFAULT_MODEL):
    return GestureModel.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))


def save_model(model, path, report=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = model.to_dict()
    if report is not None:
        data['calibration'] = report
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


def model_id(model):
    data = model.to_dict()
    data.pop('validation', None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def dashboard_approved(model):
    """True when the stored validation passed and belongs to exactly these parameters."""
    validation = model.validation or {}
    return validation.get('passed') is True and validation.get('model_id') == model_id(model)


@dataclass(frozen=True)
class GestureEvent:
    command: str        # dashboard command: PREVIOUS, NEXT or SELECT
    direction: str      # the gesture: LEFT, RIGHT or DOUBLE_BLINK (same field the joystick uses)
    index: int          # sample where the gesture was confirmed (device time = index / sample rate)
    start_index: int    # sample where its (first) pulse began
    host_time: float
    shift: float        # signed peak of the pulse, Chords-Web units
    source: str = 'eog'


class GestureDetector:
    """Feed samples in order with step() (raw ADC) or step_filtered() (Chords-Web values).

    Each call returns a list with 0 or 1 GestureEvent. `value`, `reference`, `state`, `model` and
    reset() are the attributes Pipeline and the live plot already use on CycleDetector.
    """
    reference = 0.0

    def __init__(self, model=None):
        self.model = m = model or GestureModel()
        fs = m.sample_rate
        self.filter = ChordsWebFilter(m.adc_bits, fs, m.notch_hz)
        self._samples = {name: int(round(getattr(m, name) * fs)) for name in (
            'max_pulse', 'confirm', 'tail_from', 'away_timeout', 'select_cooldown', 'settle', 'warmup')}
        self._recent = int(0.5 * fs)       # saturation check window
        self._clipped = deque()
        self.reset()

    def reset(self):
        """Discontinuity or start: forget everything and warm up again."""
        self.filter.reset()
        self._clipped.clear()
        self._n = 0
        self.state = 'idle'
        self.value = 0.0
        self.gaze = 0                # side the eyes are on: sign of the pulse that took them there, 0 = centre
        self._away_size = 0.0        # size of that pulse, to recognise the way back
        self._last_blink = None
        self._cooldown = 0

    def step(self, raw, clipped=False, index=0, host_time=0.0):
        return self.step_filtered(self.filter.process(raw), index, host_time, clipped)

    def step_filtered(self, value, index=0, host_time=0.0, clipped=False):
        n = self._n
        self._n += 1
        self.value = value
        events = self._follow(value, n, index, host_time, clipped)
        # Warm-up: movements are followed (so a look back to the centre afterwards is known as one),
        # but nothing is a command yet.
        return events if n >= self._samples['warmup'] else []

    def _follow(self, value, n, index, host_time, clipped):
        m, t = self.model, self._samples
        if clipped:
            self._clipped.append(n)
        while self._clipped and self._clipped[0] <= n - self._recent:
            self._clipped.popleft()
        if len(self._clipped) > m.max_clipped * m.sample_rate and self.state != 'unsettled':
            self._distrust()                              # pinned at an ADC rail: nothing here is a gesture
        if self.gaze and n - self._away_since > t['away_timeout']:
            self.gaze = 0

        if self.state == 'unsettled':
            self._quiet = self._quiet + 1 if abs(value) < m.onset and not clipped else 0
            if self._quiet >= t['settle']:
                self.state = 'idle'
        elif self.state == 'idle':
            if abs(value) >= m.onset:
                self._pulse = self._new_pulse(value, n, index)
                self.state = 'pulse'
        elif self.state == 'pulse':
            if value * self._pulse['sign'] >= m.onset:
                self._grow(self._pulse, abs(value))
                if self._pulse['above'] > t['max_pulse']:
                    self._distrust()
            else:                                         # the pulse is over: watch what follows it
                self._end, self._dip, self._swing, self._left, self.state = n, 0.0, None, [0.0, 0], 'watch'
        elif self.state == 'watch':
            pulse, since = self._pulse, n - self._end
            level = value * pulse['sign']
            if level >= m.onset:                          # the same way again
                if self._dip < m.onset:
                    self._grow(pulse, level)              # nothing in between: one movement in two steps
                    self.state = 'pulse'
                    return []
                events = self._blink(n, index, host_time) if self._swung_back() else []
                self._pulse, self.state = self._new_pulse(value, n, index), 'pulse'
                return events
            self._dip = max(self._dip, -level)
            if -level < m.onset:
                self._swing = None
            else:                                         # an opposite swing, followed as a pulse of its own
                if self._swing is None:
                    self._swing = self._new_pulse(value, n, index, size=0.0, above=0)
                self._grow(self._swing, -level)
                if self._swing['peak'] > m.rebound_max * pulse['peak']:
                    self._pulse, self.state = self._swing, 'pulse'   # the first pulse was only a twitch
                    return []
            if since >= t['tail_from']:
                self._left[0] += -level
                self._left[1] += 1
            if since >= t['confirm']:
                left = self._left[0] / self._left[1]
                self.state = 'idle'
                if self._swung_back(left):
                    events = self._blink(n, index, host_time)
                elif left >= m.tail and pulse['peak'] >= m.move_peak:
                    events = self._movement(n, index, host_time)
                else:
                    events = []
                if self._swing is not None:
                    self._pulse, self.state = self._swing, 'pulse'
                return events
        return []

    @staticmethod
    def _new_pulse(value, n, index, size=None, above=1):
        return dict(sign=1 if value > 0 else -1, start=n, start_index=index, peak=abs(value),
                    size=abs(value) if size is None else size, above=above)

    @staticmethod
    def _grow(pulse, level):
        pulse.update(peak=max(pulse['peak'], level), size=pulse['size'] + level, above=pulse['above'] + 1)

    def _swung_back(self, left=0.0):
        """A deep opposite swing after the pulse, of which (almost) nothing is left: spike and rebound."""
        m = self.model
        return self._dip >= max(m.rebound, m.rebound_ratio * self._pulse['peak']) and left < m.recover * self._dip

    def _distrust(self):
        self.state, self._quiet, self.gaze, self._last_blink = 'unsettled', 0, 0, None

    def _blink(self, n, index, host_time):
        m, spike = self.model, self._pulse
        if spike['sign'] != m.blink_polarity or spike['peak'] < m.blink_peak or n < self._cooldown:
            return []
        first, self._last_blink = self._last_blink, spike
        if first is None or not m.double_min <= (spike['start'] - first['start']) / m.sample_rate <= m.double_max:
            return []
        self._last_blink, self._cooldown = None, n + self._samples['select_cooldown']
        return [GestureEvent(COMMANDS['DOUBLE_BLINK'], 'DOUBLE_BLINK', index, first['start_index'], host_time,
                             round(spike['peak'] * spike['sign'], 3))]

    def _movement(self, n, index, host_time):
        """The eyes moved and stayed: a look, the way back from one, or a movement too small to be a command."""
        m, pulse = self.model, self._pulse
        sign, size = pulse['sign'], pulse['size'] / m.sample_rate
        if self.gaze == -sign and size <= m.return_ratio * self._away_size:
            self.gaze = 0                                 # back at the centre: that is not a command
            return []
        self.gaze, self._away_since, self._away_size = sign, n, size   # away from the centre, command or not
        if pulse['peak'] < m.look_peak or pulse['above'] / m.sample_rate < m.look_width:
            return []
        gesture = 'LEFT' if sign == m.left_polarity else 'RIGHT'
        return [GestureEvent(COMMANDS[gesture], gesture, index, pulse['start_index'], host_time,
                             round(pulse['peak'] * sign, 3))]
