import sys

import pytest

from eog_acquisition.recording import Recorder


def test_existing_recording_is_preserved(tmp_path):
    path = tmp_path / 'session.csv'
    path.write_text('original data')
    with pytest.raises(FileExistsError):
        Recorder(path)
    assert path.read_text() == 'original data'


def test_orphan_metadata_is_preserved(tmp_path):
    path = tmp_path / 'session.json'
    path.write_text('original metadata')
    with pytest.raises(FileExistsError):
        Recorder(path.with_suffix('.csv'))
    assert path.read_text() == 'original metadata'


def test_serial_import_does_not_load_application():
    import eog_acquisition.device  # noqa: F401
    assert 'chordspy.app' not in sys.modules
    assert 'pylsl' not in sys.modules
