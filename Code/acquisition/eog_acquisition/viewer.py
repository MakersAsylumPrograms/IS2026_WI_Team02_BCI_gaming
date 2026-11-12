"""Live channel-1 plots (pyqtgraph, as used by chordspy).

Top: raw ADC counts, unchanged. Bottom: with a CYCLE detector, the recovered gaze level and the
band a look has to leave (and stay out of for `hold` seconds) to count; without one, the
0.5-20 Hz filtered signal. The large label shows the guided-session command, or flashes CYCLE.
"""
import threading
import time
from collections import deque

import numpy as np

from .session import WORDS

WINDOW_SECONDS = 10
FLASH_SECONDS = 0.8


class LiveView:
    """Collects samples from the reader thread; the Qt timer redraws from the main thread."""

    def __init__(self, pipeline, sample_rate, status=lambda: ''):
        self.sample_rate = sample_rate
        size = WINDOW_SECONDS * sample_rate
        self._raw, self._lower = deque(maxlen=size), deque(maxlen=size)
        self._lock = threading.Lock()
        self.status = status
        self.pipeline = pipeline
        self.detector = pipeline.detector
        self.cycles, self._last_cycle, self._flash = 0, -1e9, ''
        pipeline.on_sample(self._add)
        pipeline.on_event(self._cycle)

    def _add(self, processed):
        with self._lock:
            self._raw.append(processed.sample.raw)
            if self.detector:
                self._lower.append(processed.gaze)
            else:
                self._lower.append(processed.filtered if processed.usable else np.nan)

    def _cycle(self, event):
        if event.source == 'eog':
            self.cycles += 1
            self._flash = 'CYCLE'
        else:
            self._flash = f'{event.direction} → {event.command}'
        self._last_cycle = time.monotonic()

    def run(self, keep_going):
        """Blocks until the window is closed or keep_going() returns False."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = pg.GraphicsLayoutWidget(title='EqualCare EOG - channel 1')
        window.resize(1000, 700)
        command = window.addLabel('', row=0, col=0)
        raw_plot = window.addPlot(row=1, col=0, title='Raw ADC counts (channel 1, unchanged)')
        lower_title = ('Recovered gaze (CYCLE when it leaves the band and stays out)' if self.detector
                       else 'Filtered 0.5-20 Hz')
        lower_plot = window.addPlot(row=2, col=0, title=lower_title)
        for plot in (raw_plot, lower_plot):
            plot.showGrid(x=True, y=True)
            plot.setLabel('bottom', 'seconds')
        raw_curve = raw_plot.plot(pen='y')
        lower_curve = lower_plot.plot(pen='c')
        band = [lower_plot.addLine(y=0, pen=pg.mkPen('m', style=QtCore.Qt.DashLine)) for _ in range(2)]
        for line in band:
            line.setVisible(bool(self.detector))
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
            if self.detector:
                reference, threshold = self.detector.reference, self.detector.model.threshold
                band[0].setValue(reference + threshold)
                band[1].setValue(reference - threshold)
            joystick = self.pipeline.joystick
            footer.setText(f'{self.status()}   CYCLE commands: {self.cycles}'
                           + (f'   detector: {self.detector.state}' if self.detector else '')
                           + (f'   joystick: {"ready" if joystick.ready else "measuring centre (leave it still)"}'
                              if joystick else ''))
            label = self.pipeline.current_label
            if time.monotonic() - self._last_cycle < FLASH_SECONDS:
                command.setText(self._flash, size='48pt', bold=True, color='#3f3')
            else:
                text = WORDS.get(label, '') if label != 'END' else 'DONE'
                command.setText(text, size='48pt', bold=True, color='w')

        timer = QtCore.QTimer()
        timer.timeout.connect(refresh)
        timer.start(50)
        window.show()
        app.exec_()
        timer.stop()
