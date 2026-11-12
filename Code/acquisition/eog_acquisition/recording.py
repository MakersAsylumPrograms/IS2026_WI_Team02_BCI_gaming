"""Recordings (raw samples + labels), their metadata, CYCLE event logs and replay.

A recording is two files:
  <name>.csv   sample_index,counter,raw,host_time,gap_before,stall,label,trial
               raw = channel 1 ADC counts exactly as received (no scaling, not microvolts)
  <name>.json  metadata: board, port, rates, units, electrode side, protocol, final health
"""
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .protocol import BOARD_ID, RESOLUTION_BITS, SAMPLE_RATE, Sample

FORMAT = 'equalcare-eog-recording/1'
COLUMNS = ('sample_index', 'counter', 'raw', 'host_time', 'gap_before', 'stall', 'label', 'trial')
EVENT_COLUMNS = ('utc_time', 'host_time', 'source', 'command', 'index', 'direction', 'shift_counts')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def chordspy_version():
    try:
        from importlib.metadata import version
        return version('chordspy')
    except Exception:
        return 'unknown'


class Recorder:
    def __init__(self, path, port='', in_plus='unknown', notes='', protocol=None):
        self.path = Path(path).with_suffix('.csv')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.path.with_suffix('.json')
        self.meta = dict(format=FORMAT, board=BOARD_ID, port=port, sample_rate=SAMPLE_RATE,
                         adc_bits=RESOLUTION_BITS, units='raw ADC counts (uncalibrated; not microvolts)',
                         channel='1 (GPIO 5, BioAmp EXG Pill v3, horizontal EOG)',
                         in_plus_electrode=in_plus, started_utc=utc_now(), chordspy=chordspy_version(),
                         notes=notes, protocol=protocol or {})
        if self.meta_path.exists():
            raise FileExistsError(f'Recording metadata already exists: {self.meta_path}')
        self._file = open(self.path, 'x', newline='', encoding='utf-8')
        self._csv = csv.writer(self._file)
        self._csv.writerow(COLUMNS)
        self.rows = 0
        self._write_meta()

    def write(self, processed):
        s = processed.sample
        self._csv.writerow((s.index, s.counter, s.raw, f'{s.host_time:.6f}', s.gap_before, int(s.stall),
                            processed.label, processed.trial))
        self.rows += 1
        if self.rows % 250 == 0:
            self._file.flush()

    def close(self, summary=None):
        if self._file.closed:
            return
        self._file.close()
        self.meta.update(stopped_utc=utc_now(), rows=self.rows, summary=summary or {})
        self._write_meta()

    def _write_meta(self):
        self.meta_path.write_text(json.dumps(self.meta, indent=2), encoding='utf-8')


@dataclass
class Recording:
    meta: dict
    index: np.ndarray
    counter: np.ndarray
    raw: np.ndarray
    host_time: np.ndarray
    gap_before: np.ndarray
    stall: np.ndarray
    label: list
    trial: np.ndarray

    def samples(self):
        """Yield (Sample, (label, trial)) in recorded order, for replay through a Pipeline."""
        for i in range(len(self.index)):
            yield (Sample(int(self.index[i]), int(self.counter[i]), int(self.raw[i]), float(self.host_time[i]),
                          int(self.gap_before[i]), bool(self.stall[i])),
                   (self.label[i], int(self.trial[i])))

    @property
    def sample_rate(self):
        return self.meta.get('sample_rate', SAMPLE_RATE)


def load_recording(path):
    path = Path(path).with_suffix('.csv')
    meta_path = path.with_suffix('.json')
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    with open(path, newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    column = lambda name, kind: np.array([kind(r[name]) for r in rows], dtype=kind)  # noqa: E731
    return Recording(meta, column('sample_index', int), column('counter', int), column('raw', int),
                     column('host_time', float), column('gap_before', int), column('stall', int),
                     [r['label'] for r in rows], column('trial', int))


def replay(recording, pipeline):
    """Run a recording through a pipeline exactly as live samples would go."""
    for sample, tag in recording.samples():
        pipeline.process(sample, tag)
    return pipeline


class EventLog:
    """Appends emitted eye (CYCLE) and joystick events to a CSV file."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._file = open(self.path, 'a', newline='', encoding='utf-8')
        self._csv = csv.writer(self._file)
        if new:
            self._csv.writerow(EVENT_COLUMNS)

    def write(self, event):
        stamp = utc_now()
        self._csv.writerow((stamp, f'{event.host_time:.6f}', event.source, event.command, event.index,
                            event.direction, getattr(event, 'shift', '')))
        self._file.flush()
        return stamp

    def close(self):
        self._file.close()
