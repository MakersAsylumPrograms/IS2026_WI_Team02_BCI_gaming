"""Per-sample processing: causal EOG filter, signal health and the sample pipeline."""
from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from .protocol import ADC_MAX, SAMPLE_RATE

FILTER_LOW_HZ = 0.5
FILTER_HIGH_HZ = 20.0
WARMUP_SECONDS = 1.5  # classification is suppressed this long after a start or discontinuity


class EOGFilter:
    """Causal 0.5-20 Hz band-pass for classification (raw values are never modified).

    A first-order high-pass is used on purpose: higher orders undershoot after a gaze
    step, and that undershoot would look like a return movement. Call reset() after
    any discontinuity; the state restarts from the next sample as a steady level.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, low=FILTER_LOW_HZ, high=FILTER_HIGH_HZ, warmup=WARMUP_SECONDS):
        self.sos = np.vstack([butter(1, low, 'highpass', fs=sample_rate, output='sos'),
                              butter(2, high, 'lowpass', fs=sample_rate, output='sos')])
        self._unit_state = sosfilt_zi(self.sos)
        self.warmup_samples = int(round(warmup * sample_rate))
        self.reset()

    def reset(self):
        self._state = None
        self._warmup_left = self.warmup_samples

    def process(self, value):
        if self._state is None:
            self._state = self._unit_state * value
        out, self._state = sosfilt(self.sos, (value,), zi=self._state)
        if self._warmup_left:
            self._warmup_left -= 1
        return float(out[0])

    @property
    def ready(self):
        return self._warmup_left == 0


class HealthMonitor:
    """Sample rate, loss, clipping and flat-signal checks. All values are raw ADC counts."""

    def __init__(self, sample_rate=SAMPLE_RATE, clip_margin=8, flat_seconds=2.0, flat_range=2):
        self.sample_rate = sample_rate
        self.clip_low, self.clip_high = clip_margin, ADC_MAX - clip_margin
        self.flat_range = flat_range
        self._window = deque(maxlen=int(flat_seconds * sample_rate))
        self.first = self.last = None  # (index, host_time)
        self.received = self.missing = self.stalls = self.clipped = 0
        self._last_clip_index = None

    def update(self, sample):
        """Record one sample; returns True if it is clipped."""
        if self.first is None:
            self.first = (sample.index, sample.host_time)
        else:
            self.missing += sample.gap_before
        self.last = (sample.index, sample.host_time)
        self.received += 1
        self.stalls += sample.stall
        self._window.append(sample.raw)
        clipped = sample.raw <= self.clip_low or sample.raw >= self.clip_high
        if clipped:
            self.clipped += 1
            self._last_clip_index = sample.index
        return clipped

    def snapshot(self):
        span = (self.last[1] - self.first[1]) if self.first else 0.0
        device_rate = (self.last[0] - self.first[0]) / span if span > 0 else None
        received_rate = (self.received - 1) / span if span > 0 else None
        total = self.received + self.missing
        full = len(self._window) == self._window.maxlen
        return dict(
            seconds=round(span, 2), received=self.received, missing=self.missing, stalls=self.stalls,
            loss_percent=round(100 * self.missing / total, 3) if total else 0.0,
            device_rate=round(device_rate, 2) if device_rate else None,
            received_rate=round(received_rate, 2) if received_rate else None,
            clipped_samples=self.clipped,
            clipping_now=self._last_clip_index is not None and self.last[0] - self._last_clip_index < self.sample_rate // 2,
            flat_now=full and max(self._window) - min(self._window) <= self.flat_range,
            raw_min=min(self._window) if self._window else None,
            raw_max=max(self._window) if self._window else None,
        )


@dataclass(frozen=True)
class ProcessedSample:
    sample: object      # protocol.Sample (raw, unchanged)
    filtered: float     # 0.5-20 Hz band-passed ADC counts
    clipped: bool
    usable: bool        # filter warmed up and not clipped
    label: str          # protocol label active when the sample arrived ('' outside sessions)
    trial: int
    ready: bool = True  # filter warmed up (no recent start or discontinuity)
    gaze: float = float('nan')  # recovered gaze level from the CYCLE detector (nan without one)


class Pipeline:
    """Every accepted sample passes through process() exactly once, in order:
    health -> filter -> detector -> joystick -> recorder -> sample callbacks -> event callbacks.

    Device reader threads call it with batches: `device.start(pipeline)`.
    The EOG detector (cycle.CycleDetector) and the joystick (joystick.JoystickReader) are optional;
    raw values are never modified.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, detector=None, recorder=None, joystick=None):
        self.filter = EOGFilter(sample_rate)
        self.health = HealthMonitor(sample_rate)
        self.detector = detector
        self.joystick = joystick
        self.recorder = recorder
        self._tag = ('', 0)
        self._sample_callbacks = []
        self._event_callbacks = []
        self._last_index = None
        self.processed = 0

    def on_sample(self, callback):
        """callback(ProcessedSample) for every sample, e.g. plotting or later integration."""
        self._sample_callbacks.append(callback)

    def on_event(self, callback):
        """callback(event) for each CycleEvent (eyes) and JoystickEvent (joystick), in sample order."""
        self._event_callbacks.append(callback)

    def set_label(self, label, trial=0):
        self._tag = (label, trial)  # one assignment, so the reader thread never sees half an update

    @property
    def current_label(self):
        """The label being applied to incoming samples (the guided session's current step)."""
        return self._tag[0]

    def __call__(self, samples):
        for sample in samples:
            self.process(sample)

    def process(self, sample, tag=None):
        if self._last_index is not None and sample.index <= self._last_index:
            raise ValueError(f'Sample {sample.index} is out of order or a duplicate (last {self._last_index}).')
        self._last_index = sample.index
        label, trial = tag if tag is not None else self._tag
        if sample.gap_before or sample.stall:
            self.filter.reset()  # never filter or detect across a hole in the data
            if self.detector:
                self.detector.reset()
        filtered = self.filter.process(sample.raw)
        clipped = self.health.update(sample)
        ready = self.filter.ready
        events, gaze = [], float('nan')
        if self.detector:
            events = self.detector.step(sample.raw, clipped, sample.index, sample.host_time)
            gaze = self.detector.value
        if self.joystick:
            events = events + self.joystick.update(sample)
        processed = ProcessedSample(sample, filtered, clipped, ready and not clipped, label, trial, ready, gaze)
        if self.recorder:
            self.recorder.write(processed)
        self.processed += 1
        for callback in self._sample_callbacks:
            callback(processed)
        for event in events:
            for callback in self._event_callbacks:
                callback(event)
        return processed
