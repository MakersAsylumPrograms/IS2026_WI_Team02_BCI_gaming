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
    # Blinks, as the look detector sees them: a spike whose opposite swing is deep and dies away.
    # These only keep blinks from counting as looks; SELECT comes from the blink_* rules below.
    blink_peak: float = 0.45     # unused since 2026-11-14 (BlinkPairs replaced it); kept so saved models keep their id
    rebound: float = 0.25        # the swing after it is at least this deep ...
    rebound_ratio: float = 0.3   # ... and at least this share of the spike ...
    recover: float = 0.5         # ... and less than this share of it is left at the end of the watch
    rebound_max: float = 1.5     # a swing bigger than this many spikes is a movement of its own
    # Double blink -> SELECT (calibrated). Each blink is measured as a rise from the lowest point
    # just before it, because the second blink of a pair starts deep in the first one's rebound.
    blink_rise: float = 0.4      # rise from the low point in the last `blink_window` s to the peak
    blink_window: float = 0.25   # s
    blink_fall: float = 0.7      # then it falls by at least this share of that rise ...
    blink_fall_within: float = 0.4   # ... reaching its trough within this many s of the peak (firm blinks
                                     #     clip the ADC and fall slower: 0.3-0.44 s in the 2026-11-14 tests) ...
    blink_recover: float = 0.05  # ... and comes back up by this share of the fall ...
    blink_recover_within: float = 0.45   # ... within this many s of the trough
    blink_turn: float = 0.12     # change that marks a peak or trough as passed
    # The other blink of a pair may be weaker (often the second: it returns to rest instead of
    # overshooting). One blink of every pair must still pass the full rule above.
    blink_weak_rise: float = 0.3
    blink_weak_fall: float = 0.5
    blink_firm_rise: float = 0.7   # ... and when one is weak, the other must rise at least this much
    double_min: float = 0.2      # s between the peaks of the two blinks of a double blink
    double_max: float = 0.85
    select_cooldown: float = 1.0  # s after a SELECT in which blinks are ignored
    # Safety
    max_clipped: float = 0.35    # s of ADC saturation within half a second: signal is not trusted
    settle: float = 1.0          # s of quiet signal needed after saturation or an artefact
    warmup: float = 3.0          # s without commands after start or any gap in the data
    validation: dict = None

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


class BlinkPairs:
    """Two blinks in a row -> SELECT, from the turning points of the signal.

    A blink is a quick rise of at least `blink_rise` from the lowest point in the `blink_window`
    before its peak, then a fall of at least `blink_fall` of that rise within `blink_fall_within`,
    then a recovery from that trough. Measuring the rise from the low point (not from zero) is what
    lets the second blink count: it starts while the signal is still deep in the first blink's
    rebound, so its peak above zero is small even though the eyelid moved just as much. A look has
    no quick fall and recovery after its pulse, so it is not a blink. step() returns
    (index of the first blink's peak, second blink's peak) for a pair, else None.
    """

    def __init__(self, model):
        m, fs = model, model.sample_rate
        self.sign, self.rise, self.fall, self.turn = m.blink_polarity, m.blink_rise, m.blink_fall, m.blink_turn
        self.recover = m.blink_recover
        self.weak_rise, self.weak_fall, self.firm_rise = m.blink_weak_rise, m.blink_weak_fall, m.blink_firm_rise
        self.window, self.fall_within = int(m.blink_window * fs), int(m.blink_fall_within * fs)
        self.recover_within = int(m.blink_recover_within * fs)
        self.pair_min, self.pair_max = m.double_min * fs, m.double_max * fs
        self.cooldown_samples = int(m.select_cooldown * fs)
        self.count = 0                       # blinks seen so far, single or paired; never reset
        self.reset()

    def blink_between(self, first, last):
        """True if a blink (full or weak) peaked between samples first and last."""
        return any(first <= n <= last for n in self.peaks)

    def reset(self):
        self.peaks = deque(maxlen=8)         # peak sample of every recent blink, paired or not
        self._recent = deque(maxlen=self.window + 2 * self.fall_within + 2)   # (n, value) for the low point
        self._rising, self._extreme, self._peak = True, None, None
        self._candidate = self._last = None
        self._cooldown = -1

    def step(self, value, n, index):
        x = value * self.sign
        self._recent.append((n, x))
        if self._extreme is None:
            self._extreme = (n, x, index)
        en, ev, ei = self._extreme
        if self._rising:
            if x > ev:
                self._extreme = (n, x, index)
            elif ev - x >= self.turn:                         # a peak has passed
                low = min((v for k, v in self._recent if en - self.window <= k <= en), default=ev)  # slow crest: no blink
                self._peak = (en, ev, ei, ev - low)
                self._rising, self._extreme = False, (n, x, index)
        else:
            if x < ev:
                self._extreme = (n, x, index)
            elif x - ev >= self.turn:                         # a trough has passed
                if self._peak:
                    pn, pv, pi, rise = self._peak
                    drop = pv - ev
                    if en - pn <= self.fall_within:
                        full = rise >= self.rise and drop >= self.fall * rise
                        weak = rise >= self.weak_rise and drop >= self.weak_fall * rise
                        if full or weak:
                            # 0 = weak, 1 = full, 2 = full and firm enough to pair with a weak one
                            grade = (2 if rise >= self.firm_rise else 1) if full else 0
                            self._candidate = (pn, pv, pi, en, ev, drop, grade)
                            self.peaks.append(pn)
                            self.count += 1
                self._peak = None
                self._rising, self._extreme = True, (n, x, index)
        if self._candidate:                                   # a blink, once it comes back up
            pn, pv, pi, tn, tv, drop, grade = self._candidate
            if x - tv >= self.recover * drop:
                self._candidate = None
                if n >= self._cooldown:
                    last = self._last
                    in_reach = last and self.pair_min <= pn - last[0] <= self.pair_max
                    # two full blinks, or a weak one with a firm full one
                    if in_reach and min(grade, last[2]) + max(grade, last[2]) >= 2:
                        self._last = None
                        self._cooldown = n + self.cooldown_samples
                        return last[1], pv
                    if grade or not in_reach:
                        self._last = (pn, pi, grade)    # a weak blink never replaces a full one in reach
            elif n - tn > self.recover_within:
                self._candidate = None
        return None


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

    Each call returns a list with 0 or 1 GestureEvent. Pipeline and the live plot read `value`
    (the filtered signal), `state` and `model`; `blinks.count` counts every blink, paired or not.
    """

    def __init__(self, model=None):
        self.model = m = model or GestureModel()
        fs = m.sample_rate
        self.filter = ChordsWebFilter(m.adc_bits, fs, m.notch_hz)
        self._samples = {name: int(round(getattr(m, name) * fs)) for name in (
            'max_pulse', 'confirm', 'tail_from', 'away_timeout', 'select_cooldown', 'settle', 'warmup')}
        self._samples.update(blink_margin=int(0.1 * fs), blink_reach=int(0.5 * fs))   # where a look's own spike is
        self._look_start = 0
        self._recent = int(0.5 * fs)       # saturation check window
        self._clipped = deque()
        self.blinks = BlinkPairs(m)
        self.reset()

    def reset(self):
        """Discontinuity or start: forget everything and warm up again."""
        self.filter.reset()
        self._clipped.clear()
        self.blinks.reset()
        self._looks_from = 0         # no look commands before this sample (just after a SELECT)
        self._n = 0
        self.state = 'idle'
        self.value = 0.0
        self.gaze = 0                # side the eyes are on: sign of the pulse that took them there, 0 = centre
        self._away_size = 0.0        # size of that pulse, to recognise the way back
        self._cooldown = 0

    def step(self, raw, clipped=False, index=0, host_time=0.0):
        return self.step_filtered(self.filter.process(raw), index, host_time, clipped)

    def step_filtered(self, value, index=0, host_time=0.0, clipped=False):
        n = self._n
        self._n += 1
        self.value = value
        events = self._follow(value, n, index, host_time, clipped)
        pair = self.blinks.step(value, n, index)
        # Not gated by the look detector's 'unsettled' state: firm blinks clip the ADC enough to trip it,
        # and a pinned or loose electrode cannot make BlinkPairs' quick fall-and-recover twice in a row.
        if pair:
            first_index, peak = pair
            events = [GestureEvent(COMMANDS['DOUBLE_BLINK'], 'DOUBLE_BLINK', index, first_index, host_time,
                                   round(peak * self.model.blink_polarity, 3))]
            self._looks_from = n + self._samples['select_cooldown']
        elif events and n < self._looks_from:
            events = []                               # the end of a double blink is not a look
        elif events and self.blinks.blink_between(self._look_start - self._samples['blink_margin'],
                                                  self._look_start + self._samples['blink_reach']):
            events = []                               # that pulse was a blink's spike, not a look
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
        self.state, self._quiet, self.gaze = 'unsettled', 0, 0

    def _blink(self, n, index, host_time):
        """A spike with its rebound: never a look. SELECT is decided by BlinkPairs."""
        return []

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
        self._look_start = pulse['start']
        return [GestureEvent(COMMANDS[gesture], gesture, index, pulse['start_index'], host_time,
                             round(pulse['peak'] * sign, 3))]
