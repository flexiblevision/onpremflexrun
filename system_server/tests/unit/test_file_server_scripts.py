"""scripts/ensure_file_server.sh and the two wrappers that call it.

The :8000 server backs Time Machine clip playback. A copy orphaned from its
forever monitor kept listening but dropped every request, so clips played
black, while each install stacked another monitor. These run the real script
against stand-in curl/forever/pkill.
"""
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / 'scripts'
ENSURE = SCRIPTS / 'ensure_file_server.sh'


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def run(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    calls = tmp_path / 'calls'
    state = tmp_path / 'monitors'

    def _run(server, monitors=0):
        state.write_text('\n'.join(['start_fs_server.sh'] * monitors))
        # 'ranges': this server; 'no_ranges': python's http.server; 'down': nothing answers
        _stub(bindir, 'curl', {
            'ranges': 'printf "HTTP/1.0 200 OK\\r\\nAccept-Ranges: bytes\\r\\n"',
            'no_ranges': 'printf "HTTP/1.0 200 OK\\r\\nServer: SimpleHTTP/0.6\\r\\n"',
            'down': 'exit 7'}[server])
        _stub(bindir, 'sleep', 'exit 0')
        _stub(bindir, 'pkill', 'echo "pkill $*" >> %s' % calls)
        # list prints one line per monitor; stop removes one, like forever
        _stub(bindir, 'forever', '''
case "$1" in
  list) cat %(s)s ;;
  stop) echo "stop $2" >> %(c)s; sed -i '1d' %(s)s ;;
  start) echo "$*" >> %(c)s ;;
esac''' % {'s': state, 'c': calls})
        calls.write_text('')
        env = dict(os.environ, PATH='%s:%s' % (bindir, os.environ['PATH']))
        subprocess.run(['sh', str(ENSURE), '8000', 'start_fs_server.sh'],
                       env=env, check=True)
        return calls.read_text().splitlines()

    return _run


class TestEnsureFileServer:
    @pytest.mark.unit
    def test_a_server_that_answers_with_ranges_is_left_alone(self, run):
        assert run('ranges', monitors=1) == []

    @pytest.mark.unit
    def test_a_wedged_server_is_cleared_and_started_once(self, run):
        calls = run('down', monitors=3)
        assert calls.count('stop start_fs_server.sh') == 3
        assert 'pkill -f http.server 8000 ' in calls
        assert 'pkill -f range_http_server.py 8000 ' in calls
        assert calls[-1] == 'start -c sh start_fs_server.sh'
        assert sum(c.startswith('start ') for c in calls) == 1

    @pytest.mark.unit
    def test_an_old_server_without_ranges_is_replaced(self, run):
        # python's http.server answers but cannot seek a clip
        calls = run('no_ranges', monitors=1)
        assert calls[-1] == 'start -c sh start_fs_server.sh'

    @pytest.mark.unit
    def test_a_missing_server_is_started(self, run):
        calls = run('down', monitors=0)
        assert calls[-1] == 'start -c sh start_fs_server.sh'


class TestWrappers:
    @pytest.mark.unit
    @pytest.mark.parametrize('wrapper, port, script', [
        ('filesystem_server.sh', '8000', 'start_fs_server.sh'),
        ('mediasystem_server.sh', '8001', 'start_media_server.sh'),
    ])
    def test_each_wrapper_ensures_its_own_server(self, wrapper, port, script):
        text = (SCRIPTS / wrapper).read_text()
        assert 'ensure_file_server.sh %s ' % port in text
        assert script in text

    @pytest.mark.unit
    @pytest.mark.parametrize('script', ['start_fs_server.sh', 'start_media_server.sh'])
    def test_server_output_does_not_depend_on_the_monitor(self, script):
        line = next(l for l in (SCRIPTS / script).read_text().splitlines()
                    if l.startswith('exec python3 '))
        assert 'range_http_server.py' in line
        assert '> /var/log/' in line and '2>&1' in line


@pytest.fixture
def range_server(tmp_path):
    import socket
    import time
    import urllib.request
    (tmp_path / 'clip.mp4').write_bytes(bytes(range(256)) * 40)   # 10240 bytes
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(['python3', str(SCRIPTS / 'range_http_server.py'), str(port), str(tmp_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = 'http://127.0.0.1:%d/clip.mp4' % port
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    yield url
    proc.kill()
    proc.wait()


def _get(url, byte_range=None):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={'Range': byte_range} if byte_range else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b''


class TestRangeServer:
    """A browser can only seek a clip when its server answers byte ranges."""

    DATA = bytes(range(256)) * 40

    @pytest.mark.unit
    def test_a_range_is_answered_with_206_and_just_those_bytes(self, range_server):
        status, headers, body = _get(range_server, 'bytes=100-199')
        assert status == 206
        assert headers['Content-Range'] == 'bytes 100-199/10240'
        assert body == self.DATA[100:200]

    @pytest.mark.unit
    def test_an_open_ended_and_a_suffix_range(self, range_server):
        assert _get(range_server, 'bytes=10000-')[2] == self.DATA[10000:]
        assert _get(range_server, 'bytes=-40')[2] == self.DATA[-40:]

    @pytest.mark.unit
    def test_an_unsatisfiable_range_is_416(self, range_server):
        status, headers, _ = _get(range_server, 'bytes=20000-20010')
        assert status == 416
        assert headers['Content-Range'] == 'bytes */10240'

    @pytest.mark.unit
    def test_a_plain_request_is_the_whole_file_and_advertises_ranges(self, range_server):
        status, headers, body = _get(range_server)
        assert status == 200 and body == self.DATA
        assert headers['Accept-Ranges'] == 'bytes'
        assert headers['Content-Type'] == 'video/mp4'
