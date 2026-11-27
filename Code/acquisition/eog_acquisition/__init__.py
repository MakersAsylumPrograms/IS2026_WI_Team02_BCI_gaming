"""EqualCare EOG acquisition: ESP32-S3 + BioAmp EXG Pill v3 -> USB or Bluetooth -> dashboard commands.

Eye gestures on the Chords-Web EOG signal: look LEFT -> PREVIOUS, look RIGHT -> NEXT, double blink -> SELECT.
    device = EOGDevice('COM7'); device.open()
    pipeline = Pipeline(detector=GestureDetector(gestures.load_model()))
    pipeline.on_sample(callback)    # every ProcessedSample, in order, exactly once
    pipeline.on_event(callback)     # every GestureEvent and JoystickEvent
    link = DashboardLink().start(); pipeline.on_event(link.send)
    device.start(pipeline) ... device.close(); link.stop()
"""
from . import gestures
from .dashboard import DashboardLink
from .device import DeviceError, EOGDevice
from .gestures import GestureDetector, GestureEvent, GestureModel
from .processing import Pipeline, ProcessedSample

__all__ = ['EOGDevice', 'DeviceError', 'Pipeline', 'ProcessedSample', 'GestureDetector', 'GestureEvent',
           'GestureModel', 'gestures', 'DashboardLink']
