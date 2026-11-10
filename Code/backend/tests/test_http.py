"""Real Waitress socket + stdlib simulator interoperability test."""
import json
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from waitress import create_server
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app


@pytest.mark.parametrize('source', ['joystick', 'eog'])
def test_real_http_simulator(tmp_path, source):
    app = create_app(dict(DATABASE=str(tmp_path / 'http.sqlite3'), PATIENT_PASSWORD='patient-test',
                          STAFF_PASSWORD='staff-test', DEVICE_TOKEN='device-test'))
    server = create_server(app, host='127.0.0.1', port=0, threads=2)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.effective_port}'
    try:
        with urllib.request.urlopen(base + '/api/v1/health') as result:
            assert json.load(result)['status'] == 'ok'
        env = dict(os.environ, EQUALCARE_DEVICE_TOKEN='device-test')
        result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'simulate_device.py'),
                                 '--server', base, '--source', source, '--commands', 'NEXT', 'SELECT'],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("'accepted': True") == 2
        events = app.extensions['input_state']['events']
        assert [e['command'] for e in events] == ['NEXT', 'SELECT']
        assert all(e['source'] == source for e in events)
    finally:
        server.close()
        server.task_dispatcher.shutdown()
