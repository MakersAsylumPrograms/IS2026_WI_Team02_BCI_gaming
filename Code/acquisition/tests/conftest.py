import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]


@pytest.fixture(scope='session')
def model():
    """One calibrated model shared by the classifier tests (synthetic calibration set)."""
    from eog_acquisition.calibration import calibrate
    from synthetic import synthetic_recording
    calibrated, _ = calibrate(synthetic_recording(seed=1, started='calibration'))
    return calibrated


@pytest.fixture(scope='session')
def pill_model():
    """Model calibrated on synthetic data shaped like the measured BioAmp EXG Pill v3 output (AC-coupled)."""
    from eog_acquisition.calibration import calibrate
    from synthetic import PILL_V3, synthetic_recording
    calibrated, _ = calibrate(synthetic_recording(seed=3, started='pill-calibration', **PILL_V3))
    return calibrated
