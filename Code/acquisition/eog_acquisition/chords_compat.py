"""Load the installed official serial module without Chords' GUI/LSL entrypoint.

chordspy 0.2.2 imports its web application in __init__.py. Serial acquisition
does not need that application's native liblsl, Qt, or Bluetooth dependencies.
The source loaded here is the unchanged module from the installed distribution.
"""
from importlib.metadata import distribution
from importlib.util import module_from_spec, spec_from_file_location


def _load_serial():
    package = distribution('chordspy')
    if package.version != '0.2.2':
        raise RuntimeError('Install the pinned chordspy 0.2.2 acquisition requirements.')
    path = package.locate_file('chordspy/chords_serial.py')
    spec = spec_from_file_location('_equalcare_official_chords_serial', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Chords_USB


Chords_USB = _load_serial()
