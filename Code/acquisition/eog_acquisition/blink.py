"""Provisional short same-polarity pulse pair -> one SELECT. No single-blink action."""
from .cycle import CycleEvent


class DoubleBlinkDetector:
    def __init__(self, sample_rate=250, threshold=180):
        self.fs, self.threshold = sample_rate, threshold
        self.reset()

    def reset(self):
        self.pulse = None
        self.first = None
        self.cooldown = -1
        self.previous_index = None

    def update(self, sample, filtered, ready):
        n = sample.index
        if (sample.gap_before or sample.stall or not ready or
            (self.previous_index is not None and n != self.previous_index + 1)):
            self.reset()
        self.previous_index = n
        if not ready or n < self.cooldown:
            return []
        if self.first and n - self.first[0] > .8 * self.fs:
            self.first = None
        if self.pulse is None:
            if abs(filtered) >= self.threshold:
                self.pulse = (n, 1 if filtered > 0 else -1, abs(filtered))
            return []
        start, sign, peak = self.pulse
        self.pulse = (start, sign, max(peak, abs(filtered)))
        if filtered * sign >= self.threshold * .4:
            return []
        self.pulse = None
        duration = (n - start) / self.fs
        if not .025 <= duration <= .30:
            self.first = None
            return []
        if self.first:
            first, polarity, size = self.first
            if polarity != sign:
                return []  # ignore the opposite-polarity filter rebound
            separation = (start - first) / self.fs
            if .18 <= separation <= .75 and polarity == sign and .35 <= peak / size <= 2.85:
                self.first = None
                self.cooldown = n + self.fs
                return [CycleEvent('SELECT', n, first, sample.host_time, 0, peak)]
        self.first = (start, sign, peak)
        return []
