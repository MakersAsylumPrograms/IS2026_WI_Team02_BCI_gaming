"""The Chords-Web serial filter chain, so live samples look exactly like Chords-Web recordings.

Chords-Web (github.com/upsidedownlabs/Chords-Web, src/components/filters.tsx and Connection.tsx)
passes every raw ADC sample of a channel through, in this order:

  HighPassFilter   2nd-order Butterworth high-pass                        (always on)
  EXGFilter        "EOG": 2nd-order Butterworth low-pass, 10 Hz, then x 2 / 2^bits
  Notch            two biquads, 48-52 Hz                                  ("50 Hz")

and that is what its CSV export stores (`Counter,Channel1`: about -1..+1, 0 = resting level).
The coefficients below are copied from that source for 250 Hz, the ESP32-S3 board's rate.
Its high-pass is commented "1.0 Hz" there, but the numbers are a 0.1 Hz Butterworth (the
variable holding it is called pointoneFilter); the numbers are what matters and what is used.
Check: a resting level of 1,900 counts leaks through as 0.00147, the resting mean of the team's
Chords-Web recordings. Raw values are only read; this is a view of them, like processing.EOGFilter.
"""
from .protocol import RESOLUTION_BITS, SAMPLE_RATE

HIGHPASS_HZ, EOG_LOWPASS_HZ, NOTCH_HZ = 0.1, 10.0, 50
# (a1, a2, b0, b1, b2) for  w = x - a1 w1 - a2 w2;  y = b0 w + b1 w1 + b2 w2
HIGHPASS = (-1.99644570, 0.99645200, 0.99822443, -1.99644885, 0.99822443)
EOG_LOWPASS = (-1.64745998, 0.70089678, 0.01335920, 0.02671840, 0.01335920)
NOTCH_50 = ((-0.53127491, 0.93061518, 0.93137886, -0.57635175, 0.93137886),
            (-0.66243374, 0.93214913, 1.00000000, -0.61881558, 1.00000000))


class _Biquad:
    def __init__(self, coefficients):
        self.a1, self.a2, self.b0, self.b1, self.b2 = coefficients
        self.w1 = self.w2 = 0.0

    def settle(self, level):
        """Start as if `level` had been the input for ever; returns the matching output."""
        self.w1 = self.w2 = level / (1 + self.a1 + self.a2)
        return self.w1 * (self.b0 + self.b1 + self.b2)

    def process(self, x):
        w = x - self.a1 * self.w1 - self.a2 * self.w2
        y = self.b0 * w + self.b1 * self.w1 + self.b2 * self.w2
        self.w2, self.w1 = self.w1, w
        return y


class ChordsWebFilter:
    """Raw ADC counts in, the value Chords-Web would have recorded out (EOG filter + 50 Hz notch).

    One difference from Chords-Web, on purpose: its filters start from zero, which makes the
    resting level (~1,900 counts) look like a full-scale step for the first second. Here the
    first sample after a start or reset() is taken as the resting level, so there is no such
    start transient. After that first second the two are the same.
    """

    def __init__(self, bits=RESOLUTION_BITS, sample_rate=SAMPLE_RATE, notch_hz=NOTCH_HZ):
        if sample_rate != SAMPLE_RATE or notch_hz != NOTCH_HZ:
            raise ValueError('Only the calibrated chain is supported: 250 Hz with the 50 Hz notch.')
        self.scale = 2 / 2 ** bits
        self._sections = [_Biquad(HIGHPASS), _Biquad(EOG_LOWPASS)] + [_Biquad(c) for c in NOTCH_50]
        self.reset()

    def reset(self):
        self._started = False

    def process(self, raw):
        x = float(raw)
        if not self._started:
            self._started = True
            for i, section in enumerate(self._sections):
                x = section.settle(x) * (self.scale if i == 1 else 1)
            return x
        for i, section in enumerate(self._sections):
            x = section.process(x) * (self.scale if i == 1 else 1)  # Chords-Web scales after the EOG filter
        return x
