"""The release_cut.sh wizard.

The wizard composes a command line out of answers and then execs it. That
composition has been silently wrong before - `set --` inside a shell function
rebinds only that function's positional parameters, so every answer was dropped
and the caller ran with no arguments at all. Nothing caught it, because nothing
ran the wizard: the questions were verified and the execution was not.

So these tests run the real script on a real pty and assert on the argv it
finally execs. python3 and git are stubbed on PATH, which is what makes that
argv observable without cutting a release.
"""
import os
import pty
import select
import subprocess

import pytest

REPO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SCRIPT = os.path.join(REPO, 'release_cut.sh')

PYTHON_STUB = '''#!/bin/sh
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
    *--update-components*) exit "${SURVEY_EXIT:-0}" ;;
    *-c*) printf '%s\\n' "${CUR_MAJOR:-1}" ;;
esac
exit 0
'''

GIT_STUB = '''#!/bin/sh
printf 'git %s\\n' "$*" >> "$STUB_LOG"
case "$1 $2" in
    "diff --quiet") exit "${GIT_DIFF_EXIT:-0}" ;;
esac
exit 0
'''


@pytest.fixture
def wizard(tmp_path):
    """Runs the real script on a pty, with python3 and git stubbed."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, body in (('python3', PYTHON_STUB), ('git', GIT_STUB)):
        target = bin_dir / name
        target.write_text(body)
        target.chmod(0o755)
    log = tmp_path / 'calls.log'
    log.write_text('')

    def run(answers, timeout=8, **overrides):
        env = dict(os.environ,
                   PATH='{}:{}'.format(bin_dir, os.environ['PATH']),
                   STUB_LOG=str(log))
        env.update({k: str(v) for k, v in overrides.items()})

        master, slave = pty.openpty()
        proc = subprocess.Popen([SCRIPT], stdin=slave, stdout=slave,
                                stderr=slave, env=env, close_fds=True)
        os.close(slave)
        os.write(master, ''.join(a + '\n' for a in answers).encode())

        output = b''
        while True:
            ready, _, _ = select.select([master], [], [], timeout)
            if not ready:
                proc.kill()
                os.close(master)
                raise AssertionError(
                    'the wizard asked more than it was answered:\n'
                    + output.decode(errors='replace'))
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        os.close(master)
        code = proc.wait(timeout=timeout)
        calls = [line for line in log.read_text().splitlines() if line]
        return code, output.decode(errors='replace'), calls

    return run


CUT = ['1']          # What do you want to do? -> cut
X86 = ['1']          # Which architecture?     -> x86
FROM_FILE = ['1']    # Which component versions? -> components.json
FROM_SURVEY = ['2']  #                          -> survey the registry
NO_MAJOR = ['']      # Start a new major series? -> stay on this one
NO_PROMOTE = ['1']   # Promote after signing?    -> no
CONFIRM = ['yes']


def execed(calls):
    """The argv the wizard finally ran, as one string."""
    cuts = [c for c in calls if '-m release.cut' in c
            and '--update-components' not in c]
    assert cuts, 'the wizard never ran release.cut: {}'.format(calls)
    return cuts[-1]


class TestTheSurveyIsOfferedInTheQuestions:
    """It used to be a separate verb, so you only ran it if you already knew it
    existed - and components.json went stale while releases pinned whatever was
    last edited by hand."""

    def test_choosing_it_runs_the_survey(self, wizard):
        code, out, calls = wizard(
            CUT + X86 + FROM_SURVEY + NO_MAJOR + NO_PROMOTE + CONFIRM)

        assert any('--update-components' in c for c in calls)
        assert code == 0

    def test_an_unchanged_file_carries_straight_on_to_the_cut(self, wizard):
        code, out, calls = wizard(
            CUT + X86 + FROM_SURVEY + NO_MAJOR + NO_PROMOTE + CONFIRM,
            GIT_DIFF_EXIT=0)

        assert 'nothing to change' in out
        assert '--from-stable' not in execed(calls)
        assert code == 0

    def test_the_other_two_sources_still_work(self, wizard):
        code, _, calls = wizard(
            CUT + X86 + FROM_FILE + NO_MAJOR + NO_PROMOTE + CONFIRM)
        assert '--from-stable' not in execed(calls)

        code, _, calls = wizard(
            CUT + X86 + ['3'] + NO_MAJOR + NO_PROMOTE + CONFIRM)
        assert '--from-stable' in execed(calls)

    def test_the_survey_is_not_run_for_the_other_sources(self, wizard):
        _, _, calls = wizard(
            CUT + X86 + FROM_FILE + NO_MAJOR + NO_PROMOTE + CONFIRM)

        assert not any('--update-components' in c for c in calls)


class TestAChangedFileStopsForACommit:
    """The commit IS the decision to ship these versions, and the only record
    of who made it. Carrying on would also just fail - the cut's own preflight
    refuses a dirty tree, because the manifest pins HEAD."""

    def test_it_stops_before_cutting(self, wizard):
        code, out, calls = wizard(CUT + X86 + FROM_SURVEY, GIT_DIFF_EXIT=1)

        assert code == 0
        assert not [c for c in calls if '-m release.cut' in c
                    and '--update-components' not in c]

    def test_it_says_what_to_do_next(self, wizard):
        _, out, _ = wizard(CUT + X86 + FROM_SURVEY, GIT_DIFF_EXIT=1)

        assert 'git commit' in out
        assert './release_cut.sh' in out

    def test_it_does_not_commit_anything_itself(self, wizard):
        _, _, calls = wizard(CUT + X86 + FROM_SURVEY, GIT_DIFF_EXIT=1)

        assert not any(c.startswith('git commit') or c.startswith('git add')
                       for c in calls)


class TestAFailedSurvey:
    """docker login expires, and the registry is not always reachable. That
    must not silently cut from a file the operator was told would be refreshed."""

    def test_it_asks_rather_than_carrying_on(self, wizard):
        code, out, calls = wizard(CUT + X86 + FROM_SURVEY + ['no'],
                                  SURVEY_EXIT=1)

        assert code == 1
        assert 'nothing was done' in out

    def test_declining_is_the_default(self, wizard):
        code, out, _ = wizard(CUT + X86 + FROM_SURVEY + [''], SURVEY_EXIT=1)

        assert code == 1

    def test_accepting_cuts_from_the_file_as_it_stands(self, wizard):
        _, _, calls = wizard(
            CUT + X86 + FROM_SURVEY + ['yes'] + NO_MAJOR + NO_PROMOTE + CONFIRM,
            SURVEY_EXIT=1)

        assert '--from-stable' not in execed(calls)

    def test_it_names_the_likely_cause(self, wizard):
        _, out, _ = wizard(CUT + X86 + FROM_SURVEY + ['no'], SURVEY_EXIT=1)

        assert 'docker login' in out


class TestTheAnswersReachTheCommand:
    """The regression that started this file: every answer was dropped between
    the last question and the exec, and the wizard ran with no arguments."""

    def test_the_arch_is_passed(self, wizard):
        _, _, calls = wizard(
            CUT + ['2'] + FROM_FILE + NO_MAJOR + NO_PROMOTE + CONFIRM)

        assert '--arch arm' in execed(calls)

    def test_a_major_bump_is_passed(self, wizard):
        _, _, calls = wizard(
            CUT + X86 + FROM_FILE + ['2'] + NO_PROMOTE + CONFIRM,
            CUR_MAJOR=1)

        assert '--major 2' in execed(calls)

    def test_a_promotion_channel_is_passed(self, wizard):
        _, _, calls = wizard(
            CUT + X86 + FROM_FILE + NO_MAJOR + ['2'] + CONFIRM)

        assert '--channel beta' in execed(calls)

    def test_declining_the_confirmation_runs_nothing(self, wizard):
        code, out, calls = wizard(
            CUT + X86 + FROM_FILE + NO_MAJOR + NO_PROMOTE + ['no'])

        assert code == 1
        assert 'nothing was done' in out
        assert not [c for c in calls if '-m release.cut' in c]
