"""EqualCare EOG acquisition: ESP32-S3 + BioAmp EXG Pill v3 -> Chords USB -> CYCLE commands.

One command, CYCLE: a look to either side, held. Each CYCLE can move the dashboard highlight:
    device = EOGDevice('COM7'); device.open()
    pipeline = Pipeline(detector=CycleDetector(load_model()))
    pipeline.on_sample(callback)    # every ProcessedSample, in order, exactly once
    pipeline.on_event(callback)     # every CycleEvent
    link = DashboardLink().start(); pipeline.on_event(link.send)
    device.start(pipeline) ... device.close(); link.stop()
"""
from .cycle import CycleDetector, CycleEvent, CycleModel, load_model
from .dashboard import DashboardLink
from .device import DEFAULT_PORT, DeviceError, EOGDevice
from .processing import Pipeline, ProcessedSample

__all__ = ['EOGDevice', 'DeviceError', 'DEFAULT_PORT', 'Pipeline', 'ProcessedSample', 'CycleDetector', 'CycleEvent',
           'CycleModel', 'DashboardLink', 'load_model']
