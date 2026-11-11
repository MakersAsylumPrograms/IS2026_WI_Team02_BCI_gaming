"""Synthetic horizontal-EOG recordings that follow the guided-session protocol (tests only).

Not a physiological model: gaze steps, deliberate and ordinary blinks, slow electrode
drift and noise, enough to exercise calibration, gating and scoring end to end.
"""
import numpy as np

from eog_acquisition.recording import Recording
from eog_acquisition.session import Protocol, schedule

FS = 250


def amplifier_highpass(signal, tau):
    """First-order high-pass with time constant tau (s): what an AC-coupled amplifier output does."""
    a = tau / (tau + 1 / FS)
    out = np.zeros_like(signal)
    for i in range(1, len(signal)):
        out[i] = a * (out[i - 1] + signal[i] - signal[i - 1])
    return out


# Shape measured from the team's first BioAmp EXG Pill v3 signal check (2026-11-11): movements
# give ~1400-count spikes decaying with tau ~0.11 s, blinks narrower positive spikes, baseline ~1882.
PILL_V3 = dict(gaze_amp=1400.0, noise=25.0, hardware_tau=0.11, baseline=1882, deliberate_blink=(1000, 0.15),
               natural_blink=(500, 0.12))


def synthetic_recording(seed=0, protocol=None, left_sign=1, gaze_amp=300.0, noise=4.0, blink_rate_per_min=15,
                        started='synthetic', hardware_tau=None, baseline=2048, deliberate_blink=(180, 0.30),
                        natural_blink=(90, 0.18)):
    rng = np.random.default_rng(seed)
    protocol = protocol or Protocol()
    steps = schedule(protocol, rng)
    total = int(sum(step.seconds for step in steps) * FS)
    labels, trials = [''] * total, np.zeros(total, dtype=int)
    gaze = np.zeros(total)
    blinks = []  # (start sample, amplitude, duration s)
    position, cursor = 0.0, 0
    for step in steps:
        n = int(step.seconds * FS)
        labels[cursor:cursor + n] = [step.label] * n
        trials[cursor:cursor + n] = step.trial
        react = cursor + int(rng.uniform(0.25, 0.45) * FS)
        target = {'LEFT': left_sign, 'RIGHT': -left_sign, 'CENTER': 0.0}.get(step.label)
        if target is not None:
            new = target * rng.uniform(0.8, 1.2)
            ramp = int(0.04 * FS)
            gaze[cursor:react] = position
            gaze[react:react + ramp] = position + (new - position) * (1 - np.cos(np.linspace(0, np.pi, ramp))) / 2
            gaze[react + ramp:cursor + n] = new
            position = new
        else:
            gaze[cursor:cursor + n] = position
        if step.label == 'BLINK':
            blinks.append((react, rng.normal(deliberate_blink[0], deliberate_blink[0] * 0.14),
                           rng.normal(deliberate_blink[1], deliberate_blink[1] * 0.13)))
        elif step.label in ('NEUTRAL', 'REST', 'NATURAL', 'CENTER'):
            count = rng.poisson(blink_rate_per_min * step.seconds / 60)
            for start in rng.uniform(cursor + 0.2 * FS, cursor + n - 0.5 * FS, count).astype(int):
                blinks.append((start, rng.normal(natural_blink[0], natural_blink[0] * 0.17),
                               rng.normal(natural_blink[1], natural_blink[1] * 0.17)))
        cursor += n

    t = np.arange(total) / FS
    signal = gaze * gaze_amp + 40 * np.sin(2 * np.pi * 0.03 * t)
    for start, amp, duration in blinks:
        n = max(int(duration * FS), 5)
        end = min(start + n, total)
        signal[start:end] += amp * (1 - np.cos(np.linspace(0, 2 * np.pi, n)))[:end - start] / 2
    if hardware_tau:
        signal = amplifier_highpass(signal, hardware_tau)
    signal += baseline + rng.normal(0, noise, total)
    raw = np.clip(np.round(signal), 0, 4095).astype(int)
    index = np.arange(total)
    meta = dict(sample_rate=FS, started_utc=started, in_plus_electrode='synthetic')
    return Recording(meta, index, index % 256, raw, 1000 + index / FS, np.zeros(total, dtype=int),
                     np.zeros(total, dtype=int), labels, trials)
