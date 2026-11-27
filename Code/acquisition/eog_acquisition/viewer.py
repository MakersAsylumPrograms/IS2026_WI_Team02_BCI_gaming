"""Live channel-1 plots (pyqtgraph, as used by chordspy).

Top: raw ADC counts, unchanged. Bottom: with an eye detector, the Chords-Web EOG signal it works on
and the band a look's pulse has to leave; without one, the same signal filtered by the plot. The large
label shows the current guided-sequence cue, or flashes each command as it happens.
"""
import threading
import time
from collections import deque

import numpy as np

from .chords_filter import ChordsWebFilter

WINDOW_SECONDS = 10
FLASH_SECONDS = 0.8
# label of a guided sequence -> the cue shown on screen
CUES = {'CENTER': 'CENTRE', 'LEFT': '<<< LOOK LEFT', 'RIGHT': 'LOOK RIGHT >>>', 'BLINK': 'BLINK ONCE',
        'DOUBLE_BLINK': 'BLINK TWICE', 'REST': 'REST', 'NATURAL': 'LOOK AROUND', 'END': 'DONE'}


class LiveView:
    """Collects samples from the reader thread; the Qt timer redraws from the main thread."""

    def __init__(self, pipeline, sample_rate, status=lambda: ''):
        self.sample_rate = sample_rate
        size = WINDOW_SECONDS * sample_rate
        self._raw, self._lower = deque(maxlen=size), deque(maxlen=size)
        self._lock = threading.Lock()
        self.status = status
        self.pipeline = pipeline
        self.commands, self._last_flash, self._flash = 0, -1e9, ''
        self._chords = ChordsWebFilter()
        pipeline.on_sample(self._add)
        pipeline.on_event(self._event)

    def _add(self, processed):
        sample = processed.sample
        if self.pipeline.detector:
            lower = processed.eye
        else:                                   # no detector: the same Chords-Web signal, filtered here
            if sample.gap_before or sample.stall:
                self._chords.reset()
            lower = self._chords.process(sample.raw)
        with self._lock:
            self._raw.append(sample.raw)
            self._lower.append(lower)

    def _event(self, event):
        self.commands += event.source == 'eog'
        self._flash = f'{event.direction.replace("_", " ")} → {event.command}'
        self._last_flash = time.monotonic()

    def run(self, keep_going):
        """Blocks until the window is closed or keep_going() returns False."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = pg.GraphicsLayoutWidget(title='EqualCare EOG - channel 1')
        window.resize(1000, 700)
        command = window.addLabel('', row=0, col=0)
        raw_plot = window.addPlot(row=1, col=0, title='Raw ADC counts (channel 1, unchanged)')
        lower_plot = window.addPlot(row=2, col=0, title=(
            'Chords-Web EOG signal (a look: one wide pulse out of the band, up = left, down = right)'
            if self.pipeline.detector else 'Chords-Web EOG signal'))
        lower_plot.enableAutoRange(axis='y', enable=True)
        for plot in (raw_plot, lower_plot):
            plot.showGrid(x=True, y=True)
            plot.setLabel('bottom', 'seconds')
        raw_curve = raw_plot.plot(pen='y')
        lower_curve = lower_plot.plot(pen='c')
        band = [lower_plot.addLine(y=0, pen=pg.mkPen('m', style=QtCore.Qt.DashLine)) for _ in range(2)]
        footer = window.addLabel('', row=3, col=0)

        def refresh():
            if not keep_going():
                window.close()
                return
            with self._lock:
                raw, lower = np.array(self._raw), np.array(self._lower, dtype=float)
            t = (np.arange(len(raw)) - len(raw)) / self.sample_rate
            raw_curve.setData(t, raw)
            lower_curve.setData(t, lower, connect='finite')
            detector, joystick = self.pipeline.detector, self.pipeline.joystick   # the detector may be swapped
            for line, sign in zip(band, (1, -1)):
                line.setVisible(bool(detector))
                if detector:
                    line.setValue(sign * detector.model.look_peak)
            footer.setText(f'{self.status()}   eye commands: {self.commands}'
                           + (f'   detector: {detector.state}' if detector else '')
                           + (f'   joystick: {"ready" if joystick.ready else "measuring centre (leave it still)"}'
                              if joystick else ''))
            if time.monotonic() - self._last_flash < FLASH_SECONDS:
                command.setText(self._flash, size='48pt', bold=True, color='#3f3')
            else:
                command.setText(CUES.get(self.pipeline.current_label, ''), size='48pt', bold=True, color='w')

        timer = QtCore.QTimer()
        timer.timeout.connect(refresh)
        timer.start(50)
        window.show()
        app.exec_()
        timer.stop()
