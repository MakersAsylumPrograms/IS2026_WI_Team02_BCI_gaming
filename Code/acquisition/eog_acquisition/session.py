"""Guided recording protocol for calibration and evaluation sets.

Run it twice: once for calibration, once (separately) for held-out evaluation.
Default length is about 8 minutes: 30 s neutral gaze, 20 LEFT, 20 RIGHT and 20 BLINK
trials in random order, then 2 minutes of normal blinking and incidental movement.
Each cue also plays a tone, because the eyes are away from the screen during LEFT/RIGHT.
"""
import threading
import time
from dataclasses import dataclass

from .classifier import GESTURES, MOVES

PROMPTS = {
    'NEUTRAL': 'Look at the CENTRE mark and keep still. Blink normally.',
    'LEFT': 'Look LEFT at the left mark and hold.',
    'RIGHT': 'Look RIGHT at the right mark and hold.',
    'CENTER': 'Look back at the CENTRE mark.',
    'BLINK': 'One firm, deliberate BLINK.',
    'REST': 'Rest (keep looking at the centre).',
    'NATURAL': 'Use the screen normally and blink naturally. No deliberate far LEFT/RIGHT looks.',
}
TONES = {'LEFT': (440, 300), 'RIGHT': (880, 300), 'CENTER': (660, 120), 'BLINK': (1320, 120),
         'NEUTRAL': (660, 500), 'NATURAL': (660, 500)}  # Hz, ms; REST is silent


@dataclass(frozen=True)
class Protocol:
    neutral_seconds: float = 30.0
    trials_per_gesture: int = 20
    gestures: tuple = GESTURES
    hold_seconds: float = 2.0
    center_seconds: float = 2.0
    blink_seconds: float = 2.0
    rest_seconds: float = 1.5
    natural_seconds: float = 120.0


@dataclass(frozen=True)
class Step:
    label: str
    trial: int
    seconds: float


def schedule(protocol, rng):
    """The full step list; `rng` (numpy Generator) shuffles the trial order."""
    order = [g for g in protocol.gestures for _ in range(protocol.trials_per_gesture)]
    rng.shuffle(order)
    steps = [Step('NEUTRAL', 0, protocol.neutral_seconds)]
    for trial, gesture in enumerate(order, 1):
        if gesture in MOVES:
            steps += [Step(gesture, trial, protocol.hold_seconds), Step('CENTER', trial, protocol.center_seconds)]
        else:
            steps.append(Step(gesture, trial, protocol.blink_seconds))
        steps.append(Step('REST', 0, protocol.rest_seconds))
    steps.append(Step('NATURAL', 0, protocol.natural_seconds))
    return steps


def _tone(label):
    if label not in TONES:
        return
    frequency, ms = TONES[label]
    try:
        import winsound
        threading.Thread(target=winsound.Beep, args=(frequency, ms), daemon=True).start()
    except (ImportError, RuntimeError):
        print('\a', end='', flush=True)


def run_session(pipeline, protocol, rng, keep_going=lambda: True, say=print, tones=True):
    """Label the live stream step by step. Returns False if stopped early (disconnect / Ctrl+C)."""
    steps = schedule(protocol, rng)
    total_trials = sum(1 for s in steps if s.trial and s.label in GESTURES)
    total_seconds = sum(s.seconds for s in steps)
    say(f'Session: {total_trials} trials, about {total_seconds / 60:.1f} minutes. '
        'Tones: low = LEFT, high = RIGHT, short mid = CENTRE, very high = BLINK.')
    begin = time.monotonic()
    try:
        for step in steps:
            pipeline.set_label(step.label, step.trial)
            if tones:
                _tone(step.label)
            progress = f' (trial {step.trial}/{total_trials})' if step.trial and step.label in GESTURES else ''
            say(f'[{time.monotonic() - begin:6.1f}s] {PROMPTS[step.label]}{progress}')
            end = time.monotonic() + step.seconds
            while time.monotonic() < end:
                if not keep_going():
                    return False
                time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        return True
    finally:
        pipeline.set_label('END', 0)
