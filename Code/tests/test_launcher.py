import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location('equalcare_launcher', Path(__file__).parents[1] / 'launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_credentials_are_redacted(self):
        for text in ('DEVICE_TOKEN: secret', 'PATIENT_PASSWORD: secret', 'STAFF_PASSWORD: secret'):
            self.assertNotIn('secret', launcher.safe_line(text))
        self.assertEqual('connected', launcher.safe_line('connected'))

    def test_no_install_on_unchanged_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            python = folder / '.venv' / ('Scripts/python.exe' if launcher.os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            (folder / 'requirements.txt').write_text('example==1')
            (folder / '.venv/equalcare-requirements.txt').write_text('example==1')
            with patch.object(launcher.subprocess, 'run') as run:
                self.assertEqual(python, launcher.environment(folder))
                run.assert_not_called()

    def test_existing_port_does_not_start_or_stop_processes(self):
        with patch.object(launcher, 'config', return_value=launcher.DEFAULTS), \
             patch.object(launcher, 'occupied', return_value=True), \
             patch.object(launcher, 'start') as start, patch.object(launcher, 'stop') as stop:
            self.assertEqual(1, launcher.main())
            start.assert_not_called()
            stop.assert_not_called()

    def test_failed_install_never_marks_environment_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            python = folder / '.venv' / ('Scripts/python.exe' if launcher.os.name == 'nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            (folder / 'requirements.txt').write_text('example==1')
            failure = launcher.subprocess.CalledProcessError(1, 'pip')
            with patch.object(launcher.subprocess, 'run', side_effect=failure):
                with self.assertRaises(launcher.subprocess.CalledProcessError):
                    launcher.environment(folder)
            self.assertFalse((folder / '.venv/equalcare-requirements.txt').exists())

    def test_missing_interpreter_does_not_mark_environment_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'requirements.txt').write_text('example==1')
            with patch.object(launcher.subprocess, 'run', side_effect=FileNotFoundError('python missing')):
                with self.assertRaises(FileNotFoundError):
                    launcher.environment(folder)
            self.assertFalse((folder / '.venv/equalcare-requirements.txt').exists())

    def test_only_owned_child_is_stopped_after_startup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            backend, reader = MagicMock(), MagicMock()
            backend.poll.return_value = 1
            with patch.object(launcher, 'CODE', folder), \
                 patch.object(launcher, 'config', return_value=launcher.DEFAULTS), \
                 patch.object(launcher, 'occupied', return_value=False), \
                 patch.object(launcher, 'environment', return_value='python'), \
                 patch.object(launcher, 'screens'), \
                 patch.object(launcher, 'start', return_value=(backend, reader)), \
                 patch.object(launcher, 'stop') as stop:
                self.assertEqual(1, launcher.main())
                stop.assert_called_once_with(backend)

    def frontend(self, directory, built, stale=False):
        folder = Path(directory)
        for name in ('package.json', 'package-lock.json', 'vite.config.js', 'index.html', 'src/app.jsx'):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(name)
        if built:
            (folder / 'dist').mkdir()
            (folder / 'dist/index.html').write_text('built')
        if stale:                                   # the source was edited after the build
            newer = (folder / 'dist/index.html').stat().st_mtime + 10
            launcher.os.utime(folder / 'src/app.jsx', (newer, newer))
        return folder

    def test_screens_already_built_from_current_source_are_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = self.frontend(directory, built=True)
            with patch.object(launcher.subprocess, 'run') as run:
                launcher.screens(folder)
                run.assert_not_called()

    def test_screens_are_installed_once_and_rebuilt_when_source_is_newer(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = self.frontend(directory, built=True, stale=True)
            (folder / 'node_modules').mkdir()
            with patch.object(launcher.shutil, 'which', return_value='npm'), \
                 patch.object(launcher.subprocess, 'run') as run:
                launcher.screens(folder)
                launcher.screens(folder)          # dependencies unchanged: build only
            commands = [call.args[0][1:] for call in run.call_args_list]
            self.assertEqual([['ci', '--no-audit', '--no-fund'], ['run', 'build'], ['run', 'build']], commands)

    def test_without_node_missing_screens_stop_with_instructions_and_old_ones_still_start(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(launcher.shutil, 'which', return_value=None), \
                 patch.object(launcher.subprocess, 'run') as run:
                with self.assertRaisesRegex(RuntimeError, 'Node.js'):
                    launcher.screens(self.frontend(directory + '/missing', built=False))
                launcher.screens(self.frontend(directory + '/old', built=True, stale=True))
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
