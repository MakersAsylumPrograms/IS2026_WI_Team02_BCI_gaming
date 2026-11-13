"""EqualCare EOG acquisition: ESP32-S3 + BioAmp EXG Pill v3 -> Chords USB -> dashboard commands.

Eye gestures on the Chords-Web EOG signal: look LEFT -> PREVIOUS, look RIGHT -> NEXT, double blink -> SELECT.
    device = EOGDevice('COM7'); device.open()
    pipeline = Pipeline(detector=GestureDetector(gestures.load_model()))
    pipeline.on_sample(callback)    # every ProcessedSample, in order, exactly once
    pipeline.on_event(callback)     # every GestureEvent
    link = DashboardLink().start(); pipeline.on_event(link.send)
    device.start(pipeline) ... device.close(); link.stop()

CycleDetector / load_model are the earlier direction-free CYCLE detector and its model loader.
"""
from . import gestures
from .cycle import CycleDetector, CycleEvent, CycleModel, load_model
from .dashboard import DashboardLink
from .device import DEFAULT_PORT, DeviceError, EOGDevice
from .gestures import GestureDetector, GestureEvent, GestureModel
from .processing import Pipeline, ProcessedSample

__all__ = ['EOGDevice', 'DeviceError', 'DEFAULT_PORT', 'Pipeline', 'ProcessedSample', 'GestureDetector',
           'GestureEvent', 'GestureModel', 'gestures', 'CycleDetector', 'CycleEvent', 'CycleModel', 'DashboardLink',
           'load_model']
