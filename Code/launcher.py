"""Windows single-window supervisor. Only child processes started here are stopped."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / 'Code'
# The gesture model carries its own passed evaluation, so eye commands reach the dashboard without
# the provisional override. Set allow_provisional_eog only to try a model that has not passed.
DEFAULTS = dict(port='COM7', server='http://127.0.0.1:8000',
                model='Code/acquisition/models/eog-gestures.json', plot=True,
                open_browser=True, allow_provisional_eog=False)


def config():
    path = CODE / 'launcher-config.json'
    settings = DEFAULTS | (json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {})
    # Backend run.py currently has a fixed local port. Do not pretend custom ports work.
    if settings['server'].rstrip('/') != DEFAULTS['server']:
        raise RuntimeError('This launcher starts the local backend at http://127.0.0.1:8000.')
    model = (ROOT / settings['model']).resolve()
    if not model.is_file():
        raise RuntimeError(f'Model not found: {model}. Select an existing model in Code/launcher-config.json.')
    settings['model'] = str(model)
    return settings


def environment(folder):
    """Install only inside the subsystem venv, and only on first/changed setup."""
    python = folder / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = folder / 'requirements.txt'
    marker = folder / '.venv' / 'equalcare-requirements.txt'
    if not python.exists():
        print(f'[setup] Creating {folder.name} environment', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(folder / '.venv')], check=True)
    expected = requirements.read_bytes()
    if not marker.exists() or marker.read_bytes() != expected:
        print(f'[setup] Installing {folder.name} dependencies; first setup needs internet.', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
        marker.write_bytes(expected)
    return python


def screens(folder):
    """Build the React screens (Code/frontend -> dist) when they are missing or older than their source."""
    built = folder / 'dist' / 'index.html'
    sources = [folder / name for name in ('package.json', 'package-lock.json', 'vite.config.js', 'index.html')]
    sources += [p for part in ('src', 'public', 'patient', 'staff') for p in (folder / part).rglob('*') if p.is_file()]
    if built.exists() and built.stat().st_mtime >= max(p.stat().st_mtime for p in sources):
        return
    npm = shutil.which('npm')
    if npm is None:
        if not built.exists():
            raise RuntimeError('The screens are not built and Node.js is not installed. Install Node.js 20 or '
                               'newer from nodejs.org, then start again.')
        print('[setup] The screens are older than their source and Node.js is not installed; '
              'using the existing build.', flush=True)
        return
    lock = (folder / 'package-lock.json').read_bytes()
    marker = folder / 'node_modules' / 'equalcare-package-lock.json'
    if not marker.exists() or marker.read_bytes() != lock:
        print('[setup] Installing screen dependencies; first setup needs internet.', flush=True)
        subprocess.run([npm, 'ci', '--no-audit', '--no-fund'], cwd=folder, check=True)
        marker.write_bytes(lock)
    print('[setup] Building the patient and staff screens', flush=True)
    subprocess.run([npm, 'run', 'build'], cwd=folder, check=True)


def occupied():
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(('127.0.0.1', 8000)) == 0


def safe_line(line):
    # Backend first-run provisioning prints credentials. Never copy those into logs.
    if any(word in line.upper() for word in ('PASSWORD:', 'DEVICE_TOKEN:', 'SECRET:')):
        return '[credentials] Credential generated; stored in the local backend instance folder.\n'
    return line


def pump(process, name, log):
    for line in process.stdout:
        message = f'[{name}] {safe_line(line).rstrip()}'
        # Preserve the backend's one-time first-run login display for the user,
        # but redact credentials before writing persistent diagnostic logs.
        print(f'[{name}] {line.rstrip()}', flush=True)
        log.write(message + '\n')
        log.flush()


def start(command, folder, name, log):
    child = subprocess.Popen([str(x) for x in command], cwd=folder, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                             env=os.environ | {'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'},
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    reader = threading.Thread(target=pump, args=(child, name, log), daemon=True)
    reader.start()
    return child, reader


def stop(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # Acquisition Ctrl+C handlers may intercept terminate; kill only our own tree.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    children = []
    log = None
    try:
        settings = config()
        if occupied():
            raise RuntimeError('Port 8000 is already occupied. Close the previous backend/launcher first; no existing process was stopped.')
        backend_python = environment(CODE / 'backend')
        acquisition_python = environment(CODE / 'acquisition')
        screens(CODE / 'frontend')
        runtime = CODE / 'runtime'
        runtime.mkdir(exist_ok=True)
        log_path = runtime / time.strftime('session-%Y%m%d-%H%M%S.log')
        log = log_path.open('x', encoding='utf-8')
        backend, reader = start([backend_python, 'run.py'], CODE / 'backend', 'backend', log)
        children.append((backend, reader))
        deadline = time.monotonic() + 30
        while True:
            if backend.poll() is not None:
                raise RuntimeError('Backend stopped during startup. See output above.')
            try:
                with urllib.request.urlopen(settings['server'] + '/api/v1/health', timeout=0.5) as response:
                    if json.load(response).get('status') == 'ok':
                        break
            except (OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Backend did not become healthy within 30 seconds.')
            time.sleep(0.2)
        print('[ready] Backend healthy. Patient: http://127.0.0.1:8000/patient/', flush=True)
        print('[ready] Staff: http://THIS-LAPTOP-LAN-IP:8000/staff/', flush=True)
        print(f'[ready] Starting EOG + joystick on {settings["port"]}. Logs: {log_path}', flush=True)
        command = [acquisition_python, '-m', 'eog_acquisition', 'run', '--dashboard',
                   '--port', settings['port'], '--model', settings['model'], '--server', settings['server']]
        if settings['plot']:
            command.append('--plot')
        if settings['allow_provisional_eog']:
            command.append('--allow-provisional-eog')
            print('[PROVISIONAL EOG] Prototype navigation enabled; independent accuracy is unverified.', flush=True)
        acquisition, reader = start(command, CODE / 'acquisition', 'input', log)
        children.append((acquisition, reader))
        if settings['open_browser']:
            webbrowser.open(settings['server'] + '/patient/')
        print('[running] Keep this window open. Ctrl+C stops both processes.', flush=True)
        while acquisition.poll() is None:
            if backend.poll() is not None:
                raise RuntimeError('Backend stopped. Inputs stopped to prevent stale delivery.')
            time.sleep(0.2)
        if acquisition.returncode:
            raise RuntimeError('Input process stopped with an error. Check COM7, firmware, model and output above.')
        print('[stopped] Input session finished. Shutting down backend.', flush=True)
        return 0
    except KeyboardInterrupt:
        print('\n[stopping] Closing this session.', flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[error] {exc}', flush=True)
        return 1
    finally:
        for child, reader in reversed(children):
            stop(child)
            reader.join(timeout=2)
        if log is not None:
            log.close()


if __name__ == '__main__':
    sys.exit(main())
