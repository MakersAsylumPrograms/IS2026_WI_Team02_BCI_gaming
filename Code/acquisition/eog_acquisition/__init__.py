"""EqualCare EOG acquisition: ESP32-S3 + BioAmp EXG Pill v3 -> Chords USB -> LEFT / RIGHT / BLINK.

Integration points for later milestones (nothing here sends dashboard commands):
    device = EOGDevice('COM7'); device.open()
    pipeline = Pipeline(classifier=GestureClassifier(load_model(path)))
    pipeline.on_sample(callback)    # every ProcessedSample, in order, exactly once
    pipeline.on_gesture(callback)   # every emitted GestureEvent
    device.start(pipeline) ... device.close()
"""
import json
from pathlib import Path

from .classifier import GESTURES, GestureClassifier, GestureEvent
from .device import DEFAULT_PORT, DeviceError, EOGDevice
from .processing import Pipeline, ProcessedSample


def load_model(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


__all__ = ['EOGDevice', 'DeviceError', 'DEFAULT_PORT', 'Pipeline', 'ProcessedSample', 'GestureClassifier',
           'GestureEvent', 'GESTURES', 'load_model']
