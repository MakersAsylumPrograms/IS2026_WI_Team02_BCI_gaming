"""Live raw and filtered channel-1 waveforms (pyqtgraph, as used by chordspy)."""
import threading
from collections import deque

import numpy as np

WINDOW_SECONDS = 10


class LiveView:
    """Collects samples from the reader thread; the Qt timer redraws from the main thread."""

    def __init__(self, pipeline, sample_rate, status=lambda: ''):
        self.sample_rate = sample_rate
        size = WINDOW_SECONDS * sample_rate
        self._raw, self._filtered = deque(maxlen=size), deque(maxlen=size)
        self._gestures = deque(maxlen=20)
        self._lock = threading.Lock()
        self.status = status
        pipeline.on_sample(self._add)
        pipeline.on_gesture(lambda event: self._gestures.append(event))

    def _add(self, processed):
        with self._lock:
            self._raw.append(processed.sample.raw)
            self._filtered.append(processed.filtered if processed.usable else np.nan)

    def run(self, keep_going):
        """Blocks until the window is closed or keep_going() returns False."""
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = pg.GraphicsLayoutWidget(title='EqualCare EOG - channel 1')
        window.resize(1000, 600)
        raw_plot = window.addPlot(row=0, col=0, title='Raw ADC counts (channel 1, unchanged)')
        filtered_plot = window.addPlot(row=1, col=0, title='Filtered 0.5-20 Hz (used for classification)')
        for plot in (raw_plot, filtered_plot):
            plot.showGrid(x=True, y=True)
            plot.setLabel('bottom', 'seconds')
        raw_curve = raw_plot.plot(pen='y')
        filtered_curve = filtered_plot.plot(pen='c')
        footer = window.addLabel('', row=2, col=0)

        def refresh():
            if not keep_going():
                window.close()
                return
            with self._lock:
                raw, filtered = np.array(self._raw), np.array(self._filtered)
            t = (np.arange(len(raw)) - len(raw)) / self.sample_rate
            raw_curve.setData(t, raw)
            filtered_curve.setData(t, filtered, connect='finite')
            recent = ' '.join(e.gesture for e in list(self._gestures)[-5:])
            footer.setText(f'{self.status()}   last gestures: {recent or "-"}')

        timer = QtCore.QTimer()
        timer.timeout.connect(refresh)
        timer.start(50)
        window.show()
        app.exec_()
        timer.stop()
