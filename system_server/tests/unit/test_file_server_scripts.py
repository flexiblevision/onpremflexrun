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

    def _run(server_answers, monitors=0):
        state.write_text('\n'.join(['start_fs_server.sh'] * monitors))
        _stub(bindir, 'curl', 'exit %d' % (0 if server_answers else 7))
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
    def test_a_server_that_answers_is_left_alone(self, run):
        assert run(server_answers=True, monitors=1) == []

    @pytest.mark.unit
    def test_a_wedged_server_is_cleared_and_started_once(self, run):
        calls = run(server_answers=False, monitors=3)
        assert calls.count('stop start_fs_server.sh') == 3
        assert 'pkill -f http.server 8000 ' in calls
        assert calls[-1] == 'start -c sh start_fs_server.sh'
        assert sum(c.startswith('start ') for c in calls) == 1

    @pytest.mark.unit
    def test_a_missing_server_is_started(self, run):
        calls = run(server_answers=False, monitors=0)
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
                    if l.startswith('exec python3 -m http.server'))
        assert '> /var/log/' in line and '2>&1' in line
