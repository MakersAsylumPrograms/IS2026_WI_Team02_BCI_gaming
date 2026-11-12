"""One direction-free command, CYCLE, from horizontal EOG on the BioAmp EXG Pill v3.

Why this works on this hardware
-------------------------------
The Pill's output is the eye signal after a first-order high-pass (time constant ~0.11 s,
measured on 2026-11-11), so every eye movement shows up as a spike that dies away in ~0.1 s
and a held gaze is invisible. That filter is known, so it can be inverted: GazeRecovery
rebuilds the gaze level from the raw samples. In the recovered signal

  * a look held to EITHER side is a plateau (left and right have opposite signs),
  * a blink is a short pulse that is back at the start within ~0.3 s,
  * looking back to centre is a step back to where the gaze started.

CycleDetector emits one CYCLE when the gaze moves at least `threshold` away from where it
was AND stays there for `hold` seconds. Direction is ignored on purpose (one command, one
direction of cycling), the return to centre is never a second command, blinks are too short
to count, and nothing is trusted while the ADC is saturated (hard blinks, loose electrodes).
Natural looks that are held just as long are indistinguishable on one channel; `hold` and
`threshold` are calibrated to keep those out as far as the data allows.

Raw samples are only ever read.
"""
import json
import hashlib
from collections import deque
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

import numpy as np

from .protocol import SAMPLE_RATE

FORMAT = 'equalcare-eog-cycle/1'
MOVES = ('LEFT', 'RIGHT')            # protocol trials that must give exactly one CYCLE
SCORE_WINDOW_SECONDS = 4.0           # a CYCLE counts for a LEFT/RIGHT trial if it comes this soon after the cue
MIN_RECALL = 0.8                     # calibration keeps at least this share of deliberate looks, then maximises precision
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / 'models' / 'cycle-default.json'


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class CycleModel:
    tau: float = 0.11              # s, Pill output high-pass time constant (calibrated)
    threshold: float = 1600.0      # recovered-gaze shift that counts (ADC counts, calibrated)
    hold: float = 0.6              # s the shift must be held (calibrated)
    leak: float = 8.0              # s, slow drift removal in the recovered gaze
    lowpass_hz: float = 10.0       # noise removal before the inversion
    baseline_seconds: float = 30.0  # raw offset tracking
    reference_seconds: float = 1.0  # how fast the "where the gaze was" reference follows slow drift
    settle: float = 0.6            # s to wait after a return / rejection before taking a new reference
    away_timeout: float = 8.0      # s after which a missed return is forgotten
    max_clipped: float = 0.35      # s of ADC saturation tolerated around one look
    warmup: float = 3.0            # s ignored after start or any gap in the data
    sample_rate: int = SAMPLE_RATE
    calibration_ids: tuple = ()
    validation: dict = None

    def to_dict(self):
        return dict(format=FORMAT, **asdict(self))

    @classmethod
    def from_dict(cls, data):
        if data.get('format') != FORMAT:
            raise ValueError(f'Not a CYCLE model (format {data.get("format")!r}); calibrate a new one.')
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        values['calibration_ids'] = tuple(values.get('calibration_ids', ()))
        return cls(**values)


def load_model(path=DEFAULT_MODEL):
    return CycleModel.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))


def save_model(model, path, report=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = model.to_dict()
    if report is not None:
        data['calibration'] = report
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return path


class GazeRecovery:
    """Streaming inverse of the Pill's first-order high-pass: raw ADC counts in, gaze level out."""

    def __init__(self, model):
        fs = model.sample_rate
        self._baseline_k = 1 / (model.baseline_seconds * fs)
        self._lowpass = 1 - np.exp(-2 * np.pi * model.lowpass_hz / fs)
        self._a = model.tau / (model.tau + 1 / fs)          # the hardware filter's coefficient
        self._leak = float(np.exp(-1 / (model.leak * fs)))
        self.reset()

    def reset(self):
        self._baseline = None

    def restart(self, raw):
        """After ADC saturation: the integrated level is meaningless, so start again from 0 at this
        sample, keeping the pre-saturation baseline (it was frozen while the ADC was clipped)."""
        if self._baseline is None:
            return self.update(raw)
        self._z1 = self._z2 = self._previous = float(raw) - self._baseline
        self.gaze = 0.0
        return self.gaze

    def update(self, raw, clipped=False):
        if self._baseline is None:
            self._baseline, self._z1, self._z2, self._previous, self.gaze = float(raw), 0.0, 0.0, 0.0, 0.0
        if not clipped:
            self._baseline += self._baseline_k * (raw - self._baseline)
        self._z1 += self._lowpass * (raw - self._baseline - self._z1)
        self._z2 += self._lowpass * (self._z1 - self._z2)
        # Hardware: y[n] = a (y[n-1] + x[n] - x[n-1])  =>  x[n] = x[n-1] + y[n]/a - y[n-1]
        self.gaze = self._leak * self.gaze + self._z2 / self._a - self._previous
        self._previous = self._z2
        return self.gaze


@dataclass(frozen=True)
class CycleEvent:
    command: str        # always 'CYCLE'
    index: int          # sample where the hold was confirmed (device time = index / sample rate)
    start_index: int    # sample where the gaze shift began
    host_time: float
    direction: int      # +1 / -1 polarity of the shift; diagnostic only, commands ignore it
    shift: float        # recovered gaze shift, ADC counts
    source: str = 'eog'


class CycleDetector:
    """Feed ProcessedSamples (or raw values) in order; returns a list with 0 or 1 CycleEvent."""

    def __init__(self, model=None):
        self.model = model or CycleModel()
        fs = self.model.sample_rate
        self.gaze = GazeRecovery(self.model)
        self._need = int(round(self.model.hold * fs))
        self._settle = int(round(self.model.settle * fs))
        self._timeout = int(round(self.model.away_timeout * fs))
        self._warmup = int(round(self.model.warmup * fs))
        self._max_clipped = self.model.max_clipped * fs
        self._recent = int(0.5 * fs)       # saturation check window
        self._lead = int(0.3 * fs)         # saturation just before a look also counts against it
        self._ref_k = 1 / (self.model.reference_seconds * fs)
        self._clipped = deque()            # internal sample numbers of recent clipped samples
        self.reset()

    def reset(self):
        """Discontinuity or start: forget everything and warm up again."""
        self.gaze.reset()
        self._clipped.clear()
        self._n = 0
        self.state = 'warmup'
        self.reference = 0.0
        self.value = 0.0

    def _clipped_since(self, n):
        return sum(1 for i in self._clipped if i >= n)

    def update(self, processed):
        sample = processed.sample
        return self.step(sample.raw, processed.clipped, sample.index, sample.host_time)

    def step(self, raw, clipped, index=0, host_time=0.0):
        m, n = self.model, self._n
        self._n += 1
        x = self.value = self.gaze.update(raw, clipped)
        if clipped:
            self._clipped.append(n)
        while self._clipped and self._clipped[0] < n - self._need - self._lead - self._recent:
            self._clipped.popleft()

        if n < self._warmup:
            self.reference = x
            return []
        if self.state == 'warmup':
            self.state = 'idle'
        if clipped and self._clipped_since(n - self._recent) > self._max_clipped:
            self.state = 'saturated'                    # pinned at the ADC rail: nothing here can be trusted
            return []
        if self.state == 'saturated':
            if clipped:
                return []
            x = self.value = self.gaze.restart(raw)     # the saturated stretch would otherwise drift for seconds
            self.state, self._since = 'settle', n
            return []

        if self.state == 'idle':
            shift = x - self.reference
            if abs(shift) > m.threshold:
                self.state, self._frozen, self._sign, self._start = 'looking', self.reference, (1 if shift > 0 else -1), n
                self._start_index = index
            else:
                self.reference += self._ref_k * (x - self.reference)
        elif self.state == 'looking':
            shift = (x - self._frozen) * self._sign
            if shift < 0.7 * m.threshold:
                self.state, self._since = 'settle', n  # not held: a blink or a passing glance
            elif n - self._start >= self._need:
                self.state, self._since = 'settle', n
                if self._clipped_since(self._start - self._lead) <= self._max_clipped:
                    self.state = 'away'                 # held long enough, and not a saturated blink
                    return [CycleEvent('CYCLE', index, self._start_index, host_time, self._sign, round(shift, 1))]
        elif self.state == 'away':
            shift = (x - self._frozen) * self._sign
            if shift < 0.5 * m.threshold or n - self._since > self._timeout:
                self.state, self._since = 'settle', n  # back at centre: that is not a command
        elif self.state == 'settle':
            if n - self._since >= self._settle:
                self.state, self.reference = 'idle', x
        return []


# ---- offline use: replay, scoring, calibration --------------------------------------------
def _clipped_flags(raw, margin=8):
    raw = np.asarray(raw)
    return (raw <= margin) | (raw >= 4095 - margin)


def detect(recording, model):
    """CYCLE events for a whole recording, through the same streaming code as live use."""
    detector = CycleDetector(model)
    flags = _clipped_flags(recording.raw)
    events = []
    for i in range(len(recording.raw)):
        if recording.gap_before[i] or recording.stall[i]:
            detector.reset()
        events += detector.step(int(recording.raw[i]), bool(flags[i]), int(recording.index[i]),
                                float(recording.host_time[i]))
    return events


def _cues(recording):
    cues = {}
    for i, (label, trial) in enumerate(zip(recording.label, recording.trial)):
        if trial and label not in ('', 'REST', 'CENTER', 'NEUTRAL', 'NATURAL', 'END') and trial not in cues:
            cues[int(trial)] = (label, i)
    return cues


def score(recording, events):
    """Hits / misses on LEFT/RIGHT trials, and every other CYCLE as a false command."""
    fs = recording.sample_rate
    position = {int(v): i for i, v in enumerate(recording.index)}
    cues = _cues(recording)
    moves = sorted((i, t) for t, (label, i) in cues.items() if label in MOVES)
    blink_cues = sorted(i for label, i in cues.values() if label not in MOVES)
    hits, false, blink_false, natural = set(), 0, 0, 0
    for event in events:
        i = position.get(event.index)
        onset = position.get(event.start_index)
        if i is None or onset is None:
            false += 1
            continue
        trial = int(recording.trial[onset]) if recording.label[onset] in MOVES else None
        cue = cues.get(trial)
        if not cue or i < onset or i - cue[1] > SCORE_WINDOW_SECONDS * fs:
            trial = None
        if trial is not None and trial not in hits:
            hits.add(trial)
            continue
        if recording.label[i] == 'NATURAL':
            natural += 1
            continue
        false += 1
        last_move = max((c for c, _ in moves if c <= i), default=-1)
        last_blink = max((c for c in blink_cues if c <= i), default=-1)
        blink_false += last_blink > last_move
    protocol_minutes = sum(1 for label in recording.label if label not in ('', 'NATURAL', 'END')) / fs / 60
    natural_minutes = sum(1 for label in recording.label if label == 'NATURAL') / fs / 60
    total_false = false + natural
    return dict(
        trials=len(moves), hits=len(hits), missed=sorted(set(t for _, t in moves) - hits),
        recall=round(len(hits) / len(moves), 3) if moves else None,
        precision=round(len(hits) / (len(hits) + total_false), 3) if hits or total_false else None,
        protocol_precision=round(len(hits) / (len(hits) + false), 3) if hits or false else None,
        false_during_protocol=false, false_after_blink_cues=blink_false,
        false_per_minute=round(false / protocol_minutes, 2) if protocol_minutes else None,
        natural_events=natural, natural_per_minute=round(natural / natural_minutes, 2) if natural_minutes else None,
        natural_seconds=natural_minutes * 60,
        minutes=round(protocol_minutes + natural_minutes, 2))


def _plateau_slopes(recording, model):
    """Signed flatness of every held LEFT/RIGHT plateau for this tau (0 = perfectly flat)."""
    fs = recording.sample_rate
    gaze, flags = GazeRecovery(model), _clipped_flags(recording.raw)
    detector = CycleDetector(model)
    recovered = []
    for j, value in enumerate(recording.raw):
        if recording.gap_before[j] or recording.stall[j]:
            detector.reset()
        detector.step(int(value), bool(flags[j]), int(recording.index[j]), float(recording.host_time[j]))
        recovered.append(detector.value)
    x = np.array(recovered)
    slopes, shifts = [], []
    for label, i in _cues(recording).values():
        if label not in MOVES or i < fs:
            continue
        before = float(np.median(x[i - int(0.5 * fs):i]))
        end = i
        while (end < len(x) and recording.label[end] == label
               and recording.trial[end] == recording.trial[i]):
            end += 1
        window = x[i:end] - before
        if (len(window) < fs or flags[i:end].any()
                or np.any(recording.gap_before[max(0, i - fs):end])
                or np.any(recording.stall[max(0, i - fs):end])):
            continue
        peak = np.max(np.abs(window))
        start = int(np.argmax(np.abs(window) > 0.5 * peak))
        a, b = start + int(0.25 * fs), start + int(0.85 * fs)
        if b > len(window):
            continue
        step = float(np.mean(window[a:a + int(0.15 * fs)]))
        if abs(step) < 1e-6:
            continue
        # A correctly recovered plateau still sags slightly because of the drift leak; expect that.
        sag = np.exp(-(b - a - int(0.15 * fs)) / fs / model.leak) - 1
        slopes.append((np.mean(window[b - int(0.15 * fs):b]) - step) / step - sag)
        shifts.append(abs(step))
    return slopes, shifts


def calibrate(recordings, base=None, min_recall=MIN_RECALL, sources=()):
    """Fit tau, threshold and hold to labelled guided-session recordings.

    tau: the value that makes held looks flattest. threshold and hold: the most PRECISE pair
    (every CYCLE outside a LEFT/RIGHT trial counts as false, natural use included) whose
    recall is still >= min_recall. Returns (CycleModel, report).
    """
    base = base or CycleModel()
    recordings = [r for r in recordings if any(label in MOVES for label in r.label)]
    if not recordings:
        raise CalibrationError('No LEFT/RIGHT trials found: calibrate from a guided session recording.')
    trials = sum(sum(1 for label, _ in _cues(r).values() if label in MOVES) for r in recordings)
    if trials < 10:
        raise CalibrationError(f'Only {trials} LEFT/RIGHT trials; need at least 10.')

    best_tau, best_flat, shifts = base.tau, None, []
    for tau in np.round(np.arange(0.05, 0.255, 0.01), 3):
        candidate = replace(base, tau=float(tau))
        slopes, found = [], []
        for r in recordings:
            s, f = _plateau_slopes(r, candidate)
            slopes += s
            found += f
        if len(slopes) < 5:
            continue
        flat = abs(float(np.median(slopes)))
        if best_flat is None or flat < best_flat:
            best_tau, best_flat, shifts = float(tau), flat, found
    if not shifts:
        raise CalibrationError('No clean held LEFT/RIGHT looks found (too much clipping or no signal).')
    typical = float(np.median(shifts))

    grid = []
    for fraction in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
        for hold in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3):
            model = replace(base, tau=best_tau, threshold=round(fraction * typical, 1), hold=hold)
            hits = false = total = 0
            for r in recordings:
                s = score(r, detect(r, model))
                hits, total = hits + s['hits'], total + s['trials']
                false += s['false_during_protocol'] + s['natural_events']
            recall = hits / total
            precision = hits / (hits + false) if hits + false else 0.0
            grid.append((precision, recall, -hold, model))
    passing = [g for g in grid if g[1] >= min_recall]
    warnings = []
    if passing:
        precision, recall, _, model = max(passing, key=lambda g: (g[0], g[1], g[2]))
    else:
        precision, recall, _, model = max(grid, key=lambda g: (g[1], g[0]))
        warnings.append(f'No setting reached recall {min_recall}; using the highest-recall one ({recall:.2f}).')
    report = dict(sources=[str(s) for s in sources], tau=best_tau, plateau_flatness=round(best_flat, 3),
                  typical_shift=round(typical, 1), clean_looks=len(shifts), threshold=model.threshold,
                  hold=model.hold, min_recall=min_recall, in_sample_precision=round(precision, 3),
                  in_sample_recall=round(recall, 3), warnings=warnings,
                  per_recording=[score(r, detect(r, model)) for r in recordings])
    model = replace(model, calibration_ids=tuple(recording_id(r) for r in recordings), validation=None)
    return model, report


def recording_id(recording):
    """Identity survives file copies and metadata/filename changes."""
    digest = hashlib.sha256()
    for values in (recording.raw, recording.index):
        digest.update(np.asarray(values, dtype='<i8').tobytes())
    return digest.hexdigest()


def model_id(model):
    data = model.to_dict()
    data.pop('validation', None)
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def evaluate_model(recording, model):
    metrics = score(recording, detect(recording, model))
    independent = bool(model.calibration_ids) and recording_id(recording) not in model.calibration_ids
    total_false = metrics['false_during_protocol'] + metrics['natural_events']
    precision = metrics['hits'] / (metrics['hits'] + total_false) if metrics['hits'] + total_false else 0
    recall = metrics['hits'] / metrics['trials'] if metrics['trials'] else 0
    natural_rate = metrics['natural_events'] * 60 / metrics['natural_seconds'] if metrics['natural_seconds'] else float('inf')
    passed = bool(independent and metrics['trials'] >= 40
                  and metrics['natural_seconds'] >= 119
                  and precision >= .9 and recall >= .9 and natural_rate <= 1)
    return dict(passed=passed, independent=independent, recording_id=recording_id(recording),
                model_id=model_id(model), metrics=metrics)


def dashboard_approved(model):
    validation = model.validation or {}
    return validation.get('passed') is True and validation.get('model_id') == model_id(model)
