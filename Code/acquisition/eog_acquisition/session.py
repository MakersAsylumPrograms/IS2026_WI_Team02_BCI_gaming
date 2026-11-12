"""Guided recording protocol for calibration and evaluation sets.

Run it twice: once for calibration, once (separately) for held-out evaluation.
Default length is about 9 minutes: 30 s neutral gaze, 20 trials each of LEFT, RIGHT,
BLINK and DOUBLE_BLINK in random order, then 2 minutes of natural use.
The screen shows ONLY the current command, as one big word, and every command beeps
(the eyes are away from the screen during LEFT/RIGHT). The upcoming step is never shown:
showing it made people act one step early.
"""
import os
import sys
import threading
import time
from dataclasses import dataclass

MOVES = ('LEFT', 'RIGHT')                            # must each give one CYCLE
GESTURES = ('LEFT', 'RIGHT', 'BLINK', 'DOUBLE_BLINK')  # blink trials check that blinks give none

# label stored in recordings -> the one command shown on screen
WORDS = {'NEUTRAL': 'CENTRE', 'LEFT': 'LEFT', 'RIGHT': 'RIGHT', 'CENTER': 'CENTRE', 'BLINK': 'BLINK',
         'DOUBLE_BLINK': 'DOUBLE BLINK', 'REST': 'REST', 'NATURAL': 'FREE USE'}
MEANINGS = [
    ('CENTRE', 'look at the centre mark (blinking normally is fine)'),
    ('LEFT', 'move your EYES to the left mark and HOLD there until CENTRE'),
    ('RIGHT', 'move your EYES to the right mark and HOLD there until CENTRE'),
    ('BLINK', 'one firm blink'),
    ('DOUBLE BLINK', 'two quick firm blinks'),
    ('REST', 'relax, eyes on the centre'),
    ('FREE USE', 'use the screen normally until the end'),
]
GET_READY_SECONDS = 5
TONES = {'LEFT': (440, 300), 'RIGHT': (880, 300), 'CENTER': (660, 150), 'BLINK': (1320, 150),
         'DOUBLE_BLINK': (1760, 300), 'NEUTRAL': (660, 500), 'NATURAL': (660, 500), 'REST': (330, 80)}  # Hz, ms

# 5-row block letters for the big command word
FONT = {
    'A': ['.###.', '#...#', '#####', '#...#', '#...#'], 'B': ['####.', '#...#', '####.', '#...#', '####.'],
    'C': ['.####', '#....', '#....', '#....', '.####'], 'D': ['####.', '#...#', '#...#', '#...#', '####.'],
    'E': ['#####', '#....', '####.', '#....', '#####'], 'F': ['#####', '#....', '####.', '#....', '#....'],
    'G': ['.####', '#....', '#..##', '#...#', '.###.'], 'H': ['#...#', '#...#', '#####', '#...#', '#...#'],
    'I': ['#####', '..#..', '..#..', '..#..', '#####'], 'K': ['#...#', '#..#.', '###..', '#..#.', '#...#'],
    'L': ['#....', '#....', '#....', '#....', '#####'], 'N': ['#...#', '##..#', '#.#.#', '#..##', '#...#'],
    'O': ['.###.', '#...#', '#...#', '#...#', '.###.'], 'R': ['####.', '#...#', '####.', '#..#.', '#...#'],
    'S': ['.####', '#....', '.###.', '....#', '####.'], 'T': ['#####', '..#..', '..#..', '..#..', '..#..'],
    'U': ['#...#', '#...#', '#...#', '#...#', '.###.'], 'Y': ['#...#', '.#.#.', '..#..', '..#..', '..#..'],
    ' ': ['...', '...', '...', '...', '...'],
}


def banner(word):
    """The word in 5-row block letters (unknown characters are skipped)."""
    letters = [FONT[c] for c in word.upper() if c in FONT]
    try:
        block = '█' if '█'.encode(sys.stdout.encoding or 'ascii') else '#'
    except (UnicodeEncodeError, LookupError):
        block = '#'
    return ['  '.join(letter[row] for letter in letters).replace('#', block).replace('.', ' ') for row in range(5)]


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


def clock(seconds):
    seconds = max(0, int(round(seconds)))
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


def word(label):
    return WORDS.get(label, label)


def run_session(pipeline, protocol, rng, keep_going=lambda: True, say=print, tones=True, live=True):
    """Label the live stream step by step. Returns False if stopped early (disconnect / Ctrl+C).

    With live=True the screen is cleared for every command, so only the current one is visible,
    and the last line counts down in place.
    """
    steps = schedule(protocol, rng)
    total_trials = sum(1 for s in steps if s.trial and s.label in GESTURES)
    total_seconds = sum(s.seconds for s in steps)
    if live and os.name == 'nt':
        os.system('')  # turn on ANSI escape handling in the Windows console
    rule = '=' * 78
    say(rule)
    say(f' EOG data collection: {total_trials} trials, about {total_seconds / 60:.0f} minutes.')
    say(' Do each command AS SOON AS it appears (or you hear the beep). Keep your head still.')
    say('')
    for name, meaning in MEANINGS:
        say(f'   {name:<13} {meaning}')
    say('')
    say(' Every command beeps: low = LEFT, high = RIGHT, mid = CENTRE, very high = BLINK,')
    say(' highest = DOUBLE BLINK, soft click = REST. Ctrl+C stops early.')
    say(rule)

    def countdown(seconds, text):
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if not keep_going():
                return False
            if live:
                sys.stdout.write(f'\r   {text} {left:4.1f} s   ')
                sys.stdout.flush()
            time.sleep(min(0.1, left))
        return True

    try:
        if not countdown(GET_READY_SECONDS, 'Read the list above. Starting in'):
            return False
        begin = time.monotonic()
        for step in steps:
            pipeline.set_label(step.label, step.trial)
            if tones:
                _tone(step.label)
            elapsed = time.monotonic() - begin
            if live:
                sys.stdout.write('\033[2J\033[H')  # clear: only the current command is on screen
            trial = f'trial {step.trial}/{total_trials}' if step.trial and step.label in GESTURES else ''
            say(f'   {trial:<14} session {clock(elapsed)} / {clock(total_seconds)}')
            say('')
            for line in banner(word(step.label)):
                say('   ' + line)
            say('')
            say(f'   {word(step.label)}')
            if not countdown(step.seconds, ''):
                return False
        if live:
            sys.stdout.write('\033[2J\033[H')
        say('=' * 78)
        say(' Session complete. Keep still while the recording is saved.')
        return True
    finally:
        pipeline.set_label('END', 0)
