"""Excursion-based LEFT / RIGHT / BLINK classification on the filtered horizontal EOG channel.

The 0.5 Hz high-pass turns a gaze step into a pulse that keeps one sign and decays
slowly (about 0.3 s), while a blink in this channel is brief and swings back past zero.
So an excursion starts when |filtered| crosses the calibrated onset threshold and is
classified WINDOW_SECONDS later from its shape, never from the sustained gaze level.

Gating:
  * after LEFT, the next movement of the opposite sign is the return to centre and is
    consumed silently (and vice versa), so holding gaze and coming back emit nothing;
  * if the raw signal holds a level while gaze is held (DC-coupled EOG), the level
    just before each excursion says whether gaze is still away, so a return hidden
    by a blink cannot leave the gate out of step. If the amplifier's own high-pass
    removes the held level (the BioAmp EXG Pill v3 as tested returns to baseline in
    ~0.1 s), only the alternating polarity and the away timeout are used;
  * further movement in the same direction while still away emits nothing;
  * no gesture is emitted within the refractory interval of the previous one;
  * excursions touching a data gap or filter warm-up are never classified, nor are
    excursions clipped for longer than MAX_CLIPPED_SECONDS. Brief clipping at the tip of
    a large deliberate movement is accepted (team data, 2026-11-11: movement tips clip for
    0.03-0.15 s, hard blinks for 0.26-1.26 s);
  * anything not close enough to one calibrated template stays unclassified.
"""
from collections import deque
from dataclasses import dataclass

import numpy as np

GESTURES = ('LEFT', 'RIGHT', 'BLINK')
MOVES = ('LEFT', 'RIGHT')
REJECT = 'NATURAL_BLINK'  # calibrated from ordinary blinks; matched but never emitted
FEATURES = ('log_peak', 'peak_time', 'persistence', 'opposite')
FEATURE_FLOOR = (0.10, 0.02, 0.05, 0.05)  # minimum template spread per feature

WINDOW_SECONDS = 0.5      # decision delay after onset
PERSIST_AFTER = 0.2       # persistence = signal 0.2 s after the peak, relative to the peak
SETTLE_SECONDS = 0.06     # |filtered| below release this long ends an excursion
MAX_BUSY_SECONDS = 3.0
ACCEPT_DISTANCE = 3.0     # RMS z-score to the nearest template
AMBIGUITY_MARGIN = 0.5    # nearest must beat the runner-up by this much
REFRACTORY_SECONDS = 0.5
MAX_CLIPPED_SECONDS = 0.16  # clipped time allowed inside one decision window
LEVEL_BEFORE = (0.30, 0.05)  # raw level window before onset (seconds before, start..end)
LEVEL_AFTER = 0.15           # raw level window ending at the decision
AWAY_TIMEOUT_SECONDS = 10.0  # forget "gaze is away" after this long (drift makes levels meaningless)
LEVEL_HELD_FRACTION = 0.4    # raw step >= this x filtered peak: the signal holds the gaze level
RETURN_PEAK_FRACTION = 0.6   # an opposite excursion this big relative to the movement is the return


@dataclass(frozen=True)
class Away:
    gesture: str
    sign: int
    neutral_level: float  # raw level before the movement
    away_level: float     # raw level after it
    start: int
    held: bool            # the raw signal kept the new level (DC-coupled)
    peak: float


@dataclass(frozen=True)
class Excursion:
    start_index: int
    decided_index: int
    host_time: float
    sign: int             # +1 / -1: polarity of the largest swing in the decision window
    peak: float           # largest excursion in that polarity, filtered ADC counts
    peak_time: float      # seconds from onset to peak
    persistence: float    # signal PERSIST_AFTER after the peak / peak (gaze step ~0.5, blink <= 0)
    opposite: float       # largest swing to the other polarity / peak
    valid: bool
    reason: str = ''

    @property
    def features(self):
        return np.array([np.log(max(self.peak, 1e-6)), self.peak_time, self.persistence, self.opposite])

    @property
    def movement_like(self):
        return self.persistence > 0.25 and self.opposite < 0.3


@dataclass(frozen=True)
class GestureEvent:
    gesture: str
    start_index: int      # onset sample (device time = index / sample rate)
    decided_index: int
    host_time: float
    peak: float
    distance: float
    validated: bool       # True only for gestures enabled by held-out evaluation


class ExcursionDetector:
    def __init__(self, onset, release, sample_rate):
        self.onset, self.release, self.sample_rate = onset, release, sample_rate
        self.window = int(WINDOW_SECONDS * sample_rate)
        self.settle = int(SETTLE_SECONDS * sample_rate)
        self.max_busy = int(MAX_BUSY_SECONDS * sample_rate)
        self.persist_after = int(PERSIST_AFTER * sample_rate)
        self.max_clipped = int(MAX_CLIPPED_SECONDS * sample_rate)
        self.rearm_on_opposite = False  # set by the classifier after a movement-like excursion
        self.reset()

    def reset(self):
        self._state, self._buffer, self._quiet = 'idle', [], 0

    def update(self, processed):
        index, y = processed.sample.index, processed.filtered
        if self._state == 'collecting':
            self._buffer.append(y)
            self._clipped += processed.clipped
            if not processed.ready:
                self._bad = 'filter warm-up'
            elif self._clipped > self.max_clipped:
                self._bad = f'clipped longer than {MAX_CLIPPED_SECONDS} s'
            if len(self._buffer) >= self.window:
                self._state, self._busy_since, self._quiet = 'busy', index, 0
                return self._finish(index, processed.sample.host_time)
            return None
        if self._state == 'busy':
            self._quiet = self._quiet + 1 if abs(y) < self.release else 0
            if self._quiet >= self.settle or index - self._busy_since > self.max_busy:
                self._state = 'idle'
            elif not (self.rearm_on_opposite and processed.ready and -y * self._sign > self.onset):
                return None
            else:
                return self._begin(processed)  # a movement's return starts before its pulse fades
        if processed.ready and abs(y) > self.onset:
            return self._begin(processed)
        return None

    def _begin(self, processed):
        y = processed.filtered
        self._state, self._start, self._sign = 'collecting', processed.sample.index, 1 if y > 0 else -1
        self._buffer, self._bad, self._clipped = [y], False, int(processed.clipped)
        return None

    def _finish(self, index, host_time):
        # Polarity of the LARGEST swing, not the first crossing: a blink just before a return to
        # centre must not turn the return into a small excursion of the wrong sign.
        values = np.asarray(self._buffer)
        k = int(np.argmax(np.abs(values)))
        self._sign = 1 if values[k] > 0 else -1
        signal = values * self._sign
        peak = float(signal[k])
        after = float(signal[min(k + self.persist_after, len(signal) - 1)])
        return Excursion(self._start, index, host_time, self._sign, peak, k / self.sample_rate,
                         after / peak, max(0.0, -float(signal.min())) / peak,
                         valid=not self._bad, reason=self._bad or '')


class GestureClassifier:
    """Feed ProcessedSamples with update(); returns emitted GestureEvents.

    `model` comes from calibration.calibrate(). Only gestures marked enabled by
    held-out evaluation are emitted, unless emit_candidates=True (testing/evaluation).
    """

    def __init__(self, model, emit_candidates=False):
        self.model = model
        self.sample_rate = model['sample_rate']
        self.detector = ExcursionDetector(model['onset'], model['release'], self.sample_rate)
        self.templates = {name: (np.array(t['mean']), np.array(t['std']), t['sign'])
                          for name, t in model['templates'].items()}
        self.enabled = {g for g in GESTURES if model.get('enabled', {}).get(g)}
        self.emit_candidates = emit_candidates
        self.refractory = int(model.get('refractory_seconds', REFRACTORY_SECONDS) * self.sample_rate)
        self.away_timeout = int(AWAY_TIMEOUT_SECONDS * self.sample_rate)
        self.decisions = []  # (Excursion, label, decision) for diagnostics
        self._raw = deque(maxlen=int(2 * self.sample_rate))  # (index, raw) for gaze-level checks
        self.reset()

    def reset(self):
        """Discontinuity: forget the excursion in progress and the gaze state."""
        self.detector.reset()
        self._raw.clear()
        self.away = None  # Away while gaze is off centre after an emitted movement
        self._last_emit = None

    def _level(self, first, last):
        values = [raw for index, raw in self._raw if first <= index <= last]
        return float(np.mean(values)) if values else None

    def _near_neutral(self, level):
        if not self.away.held or level is None:
            return False  # an AC-coupled signal says nothing about where gaze rests
        span = max(abs(self.away.away_level - self.away.neutral_level), self.model['onset'])
        return abs(level - self.away.neutral_level) < 0.5 * span

    def _is_return(self, excursion, label):
        """An opposite-polarity excursion big enough to be the eyes coming back (and not a blink)."""
        return (excursion.sign == -self.away.sign and label not in ('BLINK', REJECT)
                and (label in MOVES or excursion.movement_like
                     or excursion.peak >= RETURN_PEAK_FRACTION * self.away.peak))

    def direction_sign(self, gesture):
        left = self.model['left_sign']
        return left if gesture == 'LEFT' else -left

    def classify(self, excursion):
        """Nearest template whose polarity matches, or (None, reason)."""
        scores = []
        for name, (mean, std, sign) in self.templates.items():
            if sign and sign != excursion.sign:
                continue
            scores.append((float(np.sqrt(np.mean(((excursion.features - mean) / std) ** 2))), name))
        if not scores:
            return None, 'no template with this polarity'
        scores.sort()
        distance, name = scores[0]
        if distance > ACCEPT_DISTANCE:
            return None, f'far from every template ({distance:.1f})'
        if len(scores) > 1 and scores[1][0] - distance < AMBIGUITY_MARGIN:
            return None, f'ambiguous between {name} and {scores[1][1]}'
        return name, distance

    def update(self, processed):
        self._raw.append((processed.sample.index, processed.sample.raw))
        excursion = self.detector.update(processed)
        if excursion is None:
            return []
        label, detail = self.classify(excursion) if excursion.valid else (None, excursion.reason)
        movement = label in MOVES or (label is None and excursion.movement_like)
        self.detector.rearm_on_opposite = movement

        fs, start = self.sample_rate, excursion.start_index
        before = self._level(start - int(LEVEL_BEFORE[0] * fs), start - int(LEVEL_BEFORE[1] * fs))
        after = self._level(excursion.decided_index - int(LEVEL_AFTER * fs), excursion.decided_index)
        if self.away and (start - self.away.start > self.away_timeout or self._near_neutral(before)):
            self.away = None  # gaze was already back at centre: the return was missed or long ago

        decision = label
        if self.away and self._is_return(excursion, label):
            self.away, decision = None, 'RETURN'  # coming back to centre is not a command
        elif self.away and label in MOVES and self.away.held:
            decision = f'{label} (still away)'   # the level shows gaze never came back: not a new command
        if label in MOVES and decision == label:
            # New movement. On an AC-coupled signal a repeat in the same direction means the return was missed.
            held = before is not None and after is not None and abs(after - before) >= LEVEL_HELD_FRACTION * excursion.peak
            self.away = Away(label, self.direction_sign(label), before, after, start, held, excursion.peak)

        events = []
        if decision in GESTURES:
            refractory = self._last_emit is not None and excursion.start_index - self._last_emit < self.refractory
            validated = decision in self.enabled
            if refractory:
                decision = f'{decision} (refractory)'
            elif validated or self.emit_candidates:
                self._last_emit = excursion.start_index
                events.append(GestureEvent(decision, excursion.start_index, excursion.decided_index,
                                           excursion.host_time, round(excursion.peak, 1), round(detail, 2), validated))
            else:
                decision = f'{decision} (disabled)'
        self.decisions.append((excursion, label, decision))
        return events
