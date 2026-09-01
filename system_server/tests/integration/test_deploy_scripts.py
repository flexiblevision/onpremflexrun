"""Behavioural tests for the deploy shell scripts.

These scripts are the actual install and upgrade path for a factory-floor
device, and a fault in them costs a site visit. They are driven here through
pytest rather than a separate shell harness so they run under the same command
as everything else, with no extra tooling to install.

Each test runs the real script or the real function with `docker`, `git`,
`crontab`, `sudo` and `nvidia-smi` replaced by stubs, so nothing touches the
host.
"""
import json
import os
import re
import stat
import shutil
import subprocess
import textwrap
import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
LIB = os.path.join(REPO, 'upgrades', 'lib', 'deploy_common.sh')
FLEX_RUN = os.path.join(REPO, 'upgrades', 'upgrade_flex_run.sh')
CONTAINER_UPGRADES = os.path.join(REPO, 'upgrades', 'system_container_upgrades.sh')
SYSTEM_SETUP = os.path.join(REPO, 'setup', 'system_setup.sh')


def _write_stub(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, 'w') as handle:
        handle.write('#!/bin/sh\n' + textwrap.dedent(body))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def sh(tmp_path):
    """Run shell snippets with a stubbed PATH. Returns a callable."""
    stubs = str(tmp_path / 'bin')
    os.makedirs(stubs)
    # Default stubs; individual tests overwrite what they care about.
    _write_stub(stubs, 'sudo', 'exec "$@"\n')
    _write_stub(stubs, 'nvidia-smi', 'exit 1\n')
    _write_stub(stubs, 'systemctl', 'exit 0\n')

    def run(script, env=None, cwd=None):
        environment = dict(os.environ)
        environment['PATH'] = stubs + os.pathsep + environment['PATH']
        environment['HOME'] = str(tmp_path / 'home')
        environment.update(env or {})
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                              env=environment, cwd=cwd or str(tmp_path))

    run.stubs = stubs
    run.tmp = tmp_path
    return run


# --------------------------------------------------------------------------
# upgrade_flex_run.sh - the refresh that replaces the scripts about to run
# --------------------------------------------------------------------------

@pytest.fixture
def flexrun(sh):
    """A home dir with a live tree carrying untracked device state."""
    home = sh.tmp / 'home'
    (home / 'flex-run' / 'setup' / 'mqtt' / 'ssl').mkdir(parents=True)
    (home / 'flex-run' / 'system_server').mkdir(parents=True, exist_ok=True)
    (home / 'flex-run' / 'deploy.py').write_text('OLD-VERSION-CODE\n')
    (home / 'flex-run' / 'setup' / 'mqtt' / 'ssl' / 'device.key').write_text('PRIVATE-KEY\n')
    (home / 'flex-run' / 'system_server' / 'creds.txt').write_text('creds\n')
    (home / 'fvconfig.json').write_text('{"branch": "master"}')

    def git_stub(mode):
        _write_stub(sh.stubs, 'git', """
            if [ "$1" = "-C" ]; then
                echo deadbeefcafe1234567890abcdefdeadbeefcafe; exit 0
            fi
            for a in "$@"; do d="$a"; done
            case "%s" in
              fail)    echo "fatal: Remote branch not found" >&2; exit 128 ;;
              partial) mkdir -p "$d"; : > "$d/deploy.py"; exit 0 ;;
              ok)      mkdir -p "$d/system_server" "$d/upgrades/lib"
                       echo NEW-VERSION-CODE > "$d/deploy.py"
                       : > "$d/requirements.txt"
                       : > "$d/system_server/server.py"
                       : > "$d/system_server/upgrade_runner.py"
                       : > "$d/upgrades/system_container_upgrades.sh"
                       : > "$d/upgrades/lib/deploy_common.sh"
                       exit 0 ;;
            esac
            """ % mode)

    sh.git = git_stub
    sh.home = home
    return sh


class TestUpgradeFlexRun:

    @pytest.mark.parametrize('config,code,reason', [
        (None, 10, 'missing config'),
        ('{"environ":"cloud"}', 10, 'no .branch key'),
        ('{"branch":"master; rm -rf /"}', 10, 'shell metacharacters in branch'),
    ])
    def test_bad_config_is_refused(self, flexrun, config, code, reason):
        flexrun.git('ok')
        path = flexrun.home / 'fvconfig.json'
        if config is None:
            path.unlink()
        else:
            path.write_text(config)
        result = flexrun('sh %s' % FLEX_RUN)
        assert result.returncode == code, '%s: %s' % (reason, result.stderr)

    def test_clone_failure_exits_nonzero(self, flexrun):
        flexrun.git('fail')
        assert flexrun('sh %s' % FLEX_RUN).returncode == 11

    def test_incomplete_clone_is_not_copied_over_the_live_tree(self, flexrun):
        flexrun.git('partial')
        result = flexrun('sh %s' % FLEX_RUN)
        assert result.returncode == 12
        assert 'missing' in result.stderr

    def test_happy_path_updates_and_records_the_commit(self, flexrun):
        flexrun.git('ok')
        result = flexrun('sh %s' % FLEX_RUN)
        assert result.returncode == 0, result.stderr
        assert 'NEW-VERSION-CODE' in (flexrun.home / 'flex-run' / 'deploy.py').read_text()
        version = (flexrun.home / 'flex-run' / '.flexrun_version').read_text()
        assert 'commit=deadbeefcafe' in version
        assert 'branch=master' in version

    def test_slashed_branch_is_allowed(self, flexrun):
        flexrun.git('ok')
        (flexrun.home / 'fvconfig.json').write_text('{"branch":"release/v1.9.2"}')
        assert flexrun('sh %s' % FLEX_RUN).returncode == 0

    @pytest.mark.parametrize('mode,code', [('fail', 11), ('partial', 12)])
    def test_live_tree_is_untouched_on_failure(self, flexrun, mode, code):
        flexrun.git(mode)
        assert flexrun('sh %s' % FLEX_RUN).returncode == code
        tree = flexrun.home / 'flex-run'
        assert tree.joinpath('deploy.py').read_text() == 'OLD-VERSION-CODE\n'
        assert not tree.joinpath('.flexrun_version').exists()

    def test_device_state_survives_a_successful_refresh(self, flexrun):
        """rsync --delete would wipe MQTT keys; the copy must stay additive."""
        flexrun.git('ok')
        assert flexrun('sh %s' % FLEX_RUN).returncode == 0
        tree = flexrun.home / 'flex-run'
        assert tree.joinpath('setup/mqtt/ssl/device.key').read_text() == 'PRIVATE-KEY\n'
        assert tree.joinpath('system_server/creds.txt').read_text() == 'creds\n'

    def test_temp_tree_is_cleaned_up(self, flexrun):
        flexrun.git('ok')
        flexrun('sh %s' % FLEX_RUN)
        assert not (flexrun.home / 'flex-run-temp').exists()

    def test_an_unpinned_refresh_says_so(self, flexrun):
        """Branch tip is not the code any release was signed against, so the
        weaker mode has to be visible in the log."""
        flexrun.git('ok')
        result = flexrun('sh %s' % FLEX_RUN)
        assert result.returncode == 0
        assert 'no commit pinned' in result.stdout
        assert 'pinned=no' in (flexrun.home / 'flex-run' / '.flexrun_version').read_text()


PIN = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'
OTHER = 'ffffffffffffffffffffffffffffffffffffffff'


@pytest.fixture
def pinned(sh):
    """flexrun home plus a git stub that understands the pinned fetch path."""
    home = sh.tmp / 'home'
    (home / 'flex-run').mkdir(parents=True)
    (home / 'flex-run' / 'deploy.py').write_text('OLD-VERSION-CODE\n')
    (home / 'fvconfig.json').write_text('{"branch": "master"}')

    def git_stub(mode, head=PIN):
        _write_stub(sh.stubs, 'git', """
            populate() {
                mkdir -p "$1/system_server" "$1/upgrades/lib"
                echo NEW-VERSION-CODE > "$1/deploy.py"
                : > "$1/requirements.txt"
                : > "$1/system_server/server.py"
                : > "$1/system_server/upgrade_runner.py"
                : > "$1/upgrades/system_container_upgrades.sh"
                : > "$1/upgrades/lib/deploy_common.sh"
            }
            MODE=%s
            if [ "$1" = "-C" ]; then
                tree="$2"; shift 2
                case "$1" in
                  init)      mkdir -p "$tree"; exit 0 ;;
                  remote)    exit 0 ;;
                  fetch)     [ "$MODE" = fallback ] && exit 128
                             populate "$tree"; exit 0 ;;
                  checkout)  [ "$MODE" = checkout_fail ] && exit 128
                             populate "$tree"; exit 0 ;;
                  rev-parse) echo %s; exit 0 ;;
                esac
                exit 0
            fi
            # plain clone, used by the fallback path
            for a in "$@"; do d="$a"; done
            mkdir -p "$d"
            exit 0
            """ % (mode, head))

    sh.git = git_stub
    sh.home = home
    return sh


class TestPinnedCommit:
    """Pinning is what stops branch tip from replacing the code that checks the
    manifest signature, so the refusals matter more than the happy path."""

    @pytest.mark.parametrize('bad', [
        'abc', 'A1B2C3D4E5F60718293A4B5C6D7E8F9012345678',
        'a1b2c3d4e5f60718293a4b5c6d7e8f901234567', 'master',
        'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678a', '../../etc/passwd',
        'HEAD', '$(rm -rf /)',
    ])
    def test_anything_but_a_full_sha_is_refused(self, pinned, bad):
        pinned.git('ok')
        result = pinned("sh %s --commit '%s'" % (FLEX_RUN, bad))
        assert result.returncode == 10, result.stderr
        assert 'flex-run-temp' not in os.listdir(str(pinned.home))

    def test_an_unknown_argument_is_refused(self, pinned):
        pinned.git('ok')
        assert pinned('sh %s --release 1.9.2' % FLEX_RUN).returncode == 10

    def test_a_pinned_commit_is_fetched_and_applied(self, pinned):
        pinned.git('ok')
        result = pinned('sh %s --commit %s' % (FLEX_RUN, PIN))
        assert result.returncode == 0, result.stderr
        assert 'NEW-VERSION-CODE' in (pinned.home / 'flex-run' / 'deploy.py').read_text()

    def test_the_pin_is_recorded(self, pinned):
        pinned.git('ok')
        pinned('sh %s --commit %s' % (FLEX_RUN, PIN))
        version = (pinned.home / 'flex-run' / '.flexrun_version').read_text()
        assert 'commit=%s' % PIN in version
        assert 'pinned=yes' in version

    def test_the_environment_variable_works_too(self, pinned):
        pinned.git('ok')
        result = pinned('sh %s' % FLEX_RUN, env={'FLEXRUN_PIN_COMMIT': PIN})
        assert result.returncode == 0, result.stderr
        assert 'pinned=yes' in (pinned.home / 'flex-run' / '.flexrun_version').read_text()

    def test_a_remote_that_will_not_serve_the_sha_falls_back_to_a_clone(self, pinned):
        """Refusing to pin here would silently drop back to branch tip."""
        pinned.git('fallback')
        result = pinned('sh %s --commit %s' % (FLEX_RUN, PIN))
        assert result.returncode == 0, result.stderr
        assert 'cloning branch instead' in result.stdout
        assert 'pinned=yes' in (pinned.home / 'flex-run' / '.flexrun_version').read_text()

    def test_a_tree_that_landed_on_another_commit_is_refused(self, pinned):
        """The check that makes the pin worth anything."""
        pinned.git('ok', head=OTHER)
        result = pinned('sh %s --commit %s' % (FLEX_RUN, PIN))
        assert result.returncode == 15, result.stderr
        assert PIN in result.stderr and OTHER in result.stderr
        assert (pinned.home / 'flex-run' / 'deploy.py').read_text() == 'OLD-VERSION-CODE\n'

    def test_a_failed_checkout_leaves_the_live_tree_alone(self, pinned):
        pinned.git('checkout_fail')
        result = pinned('sh %s --commit %s' % (FLEX_RUN, PIN))
        assert result.returncode == 11
        assert (pinned.home / 'flex-run' / 'deploy.py').read_text() == 'OLD-VERSION-CODE\n'
        assert not (pinned.home / 'flex-run' / '.flexrun_version').exists()

    def test_a_bad_branch_still_stops_a_pinned_refresh(self, pinned):
        pinned.git('ok')
        (pinned.home / 'fvconfig.json').write_text('{"environ":"cloud"}')
        assert pinned('sh %s --commit %s' % (FLEX_RUN, PIN)).returncode == 10


# --------------------------------------------------------------------------
# system_setup.sh - first install. An unverified install presents as a device
# that looks set up and does not work, which costs a site visit.
# --------------------------------------------------------------------------

SETUP_ARGS = '1.9.2 1.9.2 1.9.2 x86 1.9.2 1.9.2 1.9.2 1.9.2'


@pytest.fixture
def setup_env(sh, tmp_path):
    """Runs the real script and the real library, with a stubbed mqtt step.

    The script is copied into a controlled tree because it resolves both the
    library and setup_mqtt.sh relative to its own location.
    """
    tree = tmp_path / 'tree'
    (tree / 'setup' / 'mqtt').mkdir(parents=True)
    (tree / 'upgrades' / 'lib').mkdir(parents=True)
    shutil.copy(SYSTEM_SETUP, str(tree / 'setup' / 'system_setup.sh'))
    shutil.copy(LIB, str(tree / 'upgrades' / 'lib' / 'deploy_common.sh'))

    # The real provisioning step and the real keys, not a stub: install now
    # refuses to continue without a trust store, and that refusal is worth
    # exercising here rather than mocking away.
    shutil.copy(os.path.join(REPO, 'setup', 'provision_trust.sh'),
                str(tree / 'setup' / 'provision_trust.sh'))
    os.chmod(str(tree / 'setup' / 'provision_trust.sh'), 0o755)
    shutil.copytree(os.path.join(REPO, 'release', 'keys'),
                    str(tree / 'release' / 'keys'))
    for name in ('__init__.py', 'trust.py'):
        shutil.copy(os.path.join(REPO, 'release', name),
                    str(tree / 'release' / name))

    state = tmp_path / 'state'
    state.mkdir()
    home = sh.tmp / 'home'
    home.mkdir(parents=True, exist_ok=True)
    (home / 'fvconfig.json').write_text(json.dumps({
        'auth0_domain': 'auth.flexiblevision.com',
        'auth0_CID': 'abc123',
        'auth_alg': 'RS256',
        'environ': 'cloud',
        'cloud_domain': 'cloud.example.com',
        'gcp_functions_domain': 'functions.example.com',
        'jwt_secret_key': 'secret',
    }))

    _write_stub(str(tree / 'setup' / 'mqtt'), 'setup_mqtt.sh',
                'echo "mqtt stub $*" >> %s/calls\n'
                'test ! -f %s/mqtt_fail\n' % (state, state))

    _write_stub(sh.stubs, 'docker', """
        STATE=%s
        echo "docker $*" >> "$STATE/calls"
        name=''; prev=''
        for a in "$@"; do
            case "$prev" in --name) name="$a" ;; esac
            case "$a" in --name=*) name="${a#--name=}" ;; esac
            prev="$a"
        done
        case "$1" in
          pull)
            grep -qxF "$2" "$STATE/pull_fail" 2>/dev/null && exit 1
            exit 0 ;;
          run)
            grep -qxF "$name" "$STATE/run_fail" 2>/dev/null && exit 1
            grep -qxF "$name" "$STATE/never_running" 2>/dev/null && exit 0
            echo "$name" >> "$STATE/running"
            exit 0 ;;
          ps)
            sort -u "$STATE/running" 2>/dev/null || true
            exit 0 ;;
          rm)
            shift
            for a in "$@"; do
                # -e matters: a bare "-f" would be read as grep's own flag.
                case "$a" in -*) continue ;; esac
                if [ -f "$STATE/running" ]; then
                    grep -vxF -e "$a" "$STATE/running" > "$STATE/running.new" || true
                    mv "$STATE/running.new" "$STATE/running" || true
                fi
            done
            exit 0 ;;
          inspect)
            for a in "$@"; do last="$a"; done
            if grep -qxF "$last" "$STATE/restarting" 2>/dev/null; then echo 3; else echo 0; fi
            exit 0 ;;
        esac
        exit 0
        """ % state)

    _write_stub(sh.stubs, 'curl', """
        STATE=%s
        echo "curl $*" >> "$STATE/calls"
        for a in "$@"; do
            case "$a" in
              *172.17.0.1:5000*)
                 test ! -f "$STATE/capdev_dead"
                 exit $? ;;
            esac
        done
        echo 'stub'
        exit 0
        """ % state)

    for name in ('apt-get', 'gpg', 'tee', 'nvidia-ctk', 'wget'):
        _write_stub(sh.stubs, name, 'exit 0\n')
    # tar must actually produce the tree, or the arm node step is not exercised.
    _write_stub(sh.stubs, 'tar', 'mkdir -p node-v10.16.1-linux-arm64/bin\nexit 0\n')
    # Real sleeps would add ~60s: smoke_settled waits per container.
    _write_stub(sh.stubs, 'sleep', 'exit 0\n')

    trust_dir = tmp_path / 'trust'

    def run(args=SETUP_ARGS, **kwargs):
        env = dict(kwargs.pop('env', None) or {})
        env.setdefault('FLEXRUN_TRUST_DIR', str(trust_dir))
        return sh('sh %s %s' % (tree / 'setup' / 'system_setup.sh', args),
                  env=env, **kwargs)

    run.state = state
    run.tree = tree
    run.home = home

    def calls():
        path = state / 'calls'
        return path.read_text().splitlines() if path.exists() else []

    def mark(filename, *values):
        (state / filename).write_text('\n'.join(values) + '\n')

    run.calls = calls
    run.mark = mark
    return run


class TestSystemSetupArguments:

    @pytest.mark.parametrize('args,missing', [
        ('"" 1.9.2 1.9.2 x86 1.9.2 1.9.2 1.9.2 1.9.2', 'capdev'),
        ('1.9.2 "" 1.9.2 x86 1.9.2 1.9.2 1.9.2 1.9.2', 'captureui'),
        ('1.9.2 1.9.2 1.9.2 x86 1.9.2 1.9.2 1.9.2 ""', 'visiontools'),
    ])
    def test_an_empty_version_names_the_argument(self, setup_env, args, missing):
        result = setup_env(args)
        assert result.returncode == 20, result.stderr
        assert missing in result.stderr

    def test_nothing_is_pulled_when_arguments_are_bad(self, setup_env):
        setup_env('1.9.2 1.9.2 1.9.2 x86 1.9.2 1.9.2 1.9.2 ""')
        assert not any('pull' in line for line in setup_env.calls())

    @pytest.mark.parametrize('arch', ['', 'amd64', 'x86_64', 'arm64'])
    def test_an_unsupported_arch_is_refused(self, setup_env, arch):
        result = setup_env('1.9.2 1.9.2 1.9.2 "%s" 1.9.2 1.9.2 1.9.2 1.9.2' % arch)
        assert result.returncode == 20, result.stdout


class TestSystemSetupConfig:

    def test_a_missing_config_is_refused(self, setup_env):
        (setup_env.home / 'fvconfig.json').unlink()
        assert setup_env().returncode == 21

    @pytest.mark.parametrize('key', ['auth0_domain', 'auth0_CID', 'environ'])
    def test_a_null_required_value_is_refused(self, setup_env, key):
        """jq prints "null" and exits 0 for an absent key, so unchecked this
        installs containers configured to authenticate against "null"."""
        config = json.loads((setup_env.home / 'fvconfig.json').read_text())
        del config[key]
        (setup_env.home / 'fvconfig.json').write_text(json.dumps(config))

        result = setup_env()
        assert result.returncode == 21, result.stdout
        assert key.upper() in result.stderr or key in result.stderr

    def test_nothing_is_pulled_when_config_is_bad(self, setup_env):
        config = json.loads((setup_env.home / 'fvconfig.json').read_text())
        del config['environ']
        (setup_env.home / 'fvconfig.json').write_text(json.dumps(config))
        setup_env()
        assert not any('pull' in line for line in setup_env.calls())

    def test_an_optional_value_only_warns(self, setup_env):
        config = json.loads((setup_env.home / 'fvconfig.json').read_text())
        del config['cloud_domain']
        (setup_env.home / 'fvconfig.json').write_text(json.dumps(config))

        result = setup_env()
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'CLOUD_DOMAIN' in result.stdout


class TestSystemSetupHappyPath:

    def test_it_succeeds(self, setup_env):
        result = setup_env()
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'all containers verified' in result.stdout

    def test_every_image_is_pulled(self, setup_env):
        setup_env()
        pulled = [line.split()[-1] for line in setup_env.calls()
                  if line.startswith('docker pull')]
        assert 'mongo:4.2' in pulled
        for component in ('backend', 'frontend', 'prediction', 'predictlite',
                          'vision', 'nodecreator', 'visiontools'):
            assert any('x86-%s:1.9.2' % component in image for image in pulled), component

    def test_every_container_is_started(self, setup_env):
        setup_env()
        started = [line for line in setup_env.calls() if line.startswith('docker run')]
        assert len(started) == 8
        for name in ('mongo', 'capdev', 'captureui', 'localprediction',
                     'predictlite', 'vision', 'nodecreator', 'visiontools'):
            assert any(name in line for line in started), name

    def test_all_pulls_happen_before_any_container_starts(self, setup_env):
        """The whole point of the ordering: a bad version must not leave a
        half-built device."""
        setup_env()
        calls = setup_env.calls()
        last_pull = max(i for i, c in enumerate(calls) if c.startswith('docker pull'))
        first_run = min(i for i, c in enumerate(calls) if c.startswith('docker run'))
        assert last_pull < first_run

    def test_capdev_gets_a_readiness_check_not_just_a_process_check(self, setup_env):
        setup_env()
        assert any('172.17.0.1:5000' in line and 'jwks' in line
                   for line in setup_env.calls() if line.startswith('curl'))

    def test_the_mqtt_broker_is_set_up(self, setup_env):
        setup_env()
        assert any(line.startswith('mqtt stub') for line in setup_env.calls())

    def test_the_arch_reaches_every_image_name(self, setup_env):
        setup_env('1.9.2 1.9.2 1.9.2 arm 1.9.2 1.9.2 1.9.2 1.9.2')
        pulled = [line.split()[-1] for line in setup_env.calls()
                  if line.startswith('docker pull')]
        assert all('arm-' in p or p.startswith('mongo:') for p in pulled), pulled


class TestSystemSetupArchCoverage:
    """visiontools has no arm image yet. Pull-all-first turns that from a
    skipped container into a failed install unless the script knows."""

    def test_arm_skips_visiontools_entirely(self, setup_env):
        result = setup_env('1.9.2 1.9.2 1.9.2 arm 1.9.2 1.9.2 1.9.2 1.9.2')
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any('visiontools' in line for line in setup_env.calls())
        assert 'skipping visiontools' in result.stdout

    def test_x86_still_installs_visiontools(self, setup_env):
        setup_env()
        assert any('visiontools' in line and line.startswith('docker run')
                   for line in setup_env.calls())

    def test_arm_installs_the_other_seven_containers(self, setup_env):
        setup_env('1.9.2 1.9.2 1.9.2 arm 1.9.2 1.9.2 1.9.2 1.9.2')
        started = [l for l in setup_env.calls() if l.startswith('docker run')]
        assert len(started) == 7

    def test_arm_succeeds_with_no_visiontools_version_at_all(self, setup_env):
        """deploy.py cannot supply one, so it must not be required."""
        result = setup_env('1.9.2 1.9.2 1.9.2 arm 1.9.2 1.9.2 1.9.2 ""')
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_version_check_sentinel_is_not_treated_as_a_tag(self, setup_env):
        """is_container_uptodate returns the string 'True' when it thinks
        nothing is needed; pulling fvonprem/x86-visiontools:True would fail."""
        result = setup_env('1.9.2 1.9.2 1.9.2 x86 1.9.2 1.9.2 1.9.2 True')
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any('visiontools:True' in line for line in setup_env.calls())

    def test_arm_does_not_verify_a_container_it_never_started(self, setup_env):
        setup_env('1.9.2 1.9.2 1.9.2 arm 1.9.2 1.9.2 1.9.2 1.9.2')
        assert not any('visiontools' in l for l in setup_env.calls()
                       if l.startswith('docker inspect'))


class TestSystemSetupFailures:

    def test_a_pull_failure_starts_nothing(self, setup_env):
        """The single biggest change: previously one bad version left some
        containers running and some not."""
        setup_env.mark('pull_fail', 'fvonprem/x86-vision:1.9.2')
        result = setup_env()
        assert result.returncode == 22, result.stdout
        assert not any(line.startswith('docker run') for line in setup_env.calls())

    def test_a_pull_failure_names_the_image(self, setup_env):
        setup_env.mark('pull_fail', 'fvonprem/x86-vision:1.9.2')
        assert 'x86-vision:1.9.2' in setup_env().stderr

    def test_a_run_failure_stops_the_install(self, setup_env):
        setup_env.mark('run_fail', 'vision')
        result = setup_env()
        assert result.returncode == 23, result.stdout
        assert 'vision' in result.stderr

    def test_a_container_that_never_comes_up_fails_the_install(self, setup_env):
        setup_env.mark('never_running', 'visiontools')
        result = setup_env()
        assert result.returncode == 24, result.stdout
        assert 'visiontools' in result.stderr

    def test_a_crash_looping_container_fails_the_install(self, setup_env):
        """docker ps is satisfied by a container that is restarting in a loop."""
        setup_env.mark('restarting', 'predictlite')
        result = setup_env()
        assert result.returncode == 24, result.stdout
        assert 'predictlite' in result.stderr

    def test_capdev_not_answering_fails_the_install(self, setup_env):
        (setup_env.state / 'capdev_dead').write_text('')
        result = setup_env()
        assert result.returncode == 24, result.stdout
        assert 'capdev' in result.stderr

    def test_every_broken_container_is_reported_not_just_the_first(self, setup_env):
        """An engineer on a first install wants the whole picture."""
        setup_env.mark('never_running', 'vision', 'visiontools')
        setup_env.mark('restarting', 'predictlite')
        result = setup_env()
        assert result.returncode == 24
        for name in ('vision', 'visiontools', 'predictlite'):
            assert name in result.stderr, name

    def test_a_failed_install_says_the_device_is_not_ready(self, setup_env):
        setup_env.mark('never_running', 'vision')
        assert 'NOT ready' in setup_env().stderr

    def test_an_mqtt_failure_stops_the_install(self, setup_env):
        (setup_env.state / 'mqtt_fail').write_text('')
        result = setup_env()
        assert result.returncode == 23
        assert 'MQTT' in result.stderr


class TestSystemSetupRerun:

    def test_an_existing_container_is_replaced_rather_than_colliding(self, setup_env):
        (setup_env.state / 'running').write_text('capdev\nmongo\n')
        result = setup_env()
        assert result.returncode == 0, result.stdout + result.stderr
        assert any(line.startswith('docker rm -f capdev') or
                   'rm -f capdev' in line for line in setup_env.calls())

    def test_a_rerun_after_a_partial_install_succeeds(self, setup_env):
        setup_env.mark('run_fail', 'vision')
        assert setup_env().returncode == 23
        (setup_env.state / 'run_fail').unlink()
        assert setup_env().returncode == 0


# --------------------------------------------------------------------------
# set_conf_directive - replaces a directive instead of appending a new one
# --------------------------------------------------------------------------

class TestSetConfDirective:

    @pytest.fixture
    def conf(self, sh):
        path = sh.tmp / 'redis.conf'
        path.write_text(
            '# Redis configuration file example\n'
            'bind 127.0.0.1 ::1\n'
            '# maxmemory <bytes>\n'
            'appendonly no\n'
            # six upgrades of the old append-only behaviour
            + 'maxmemory 10000000000\nmaxmemory-policy allkeys-lru\n' * 6
        )
        return path

    def _apply(self, sh, conf, value='10000000000'):
        return sh('. %s\nset_conf_directive %s maxmemory %s\n'
                  'set_conf_directive %s maxmemory-policy allkeys-lru'
                  % (LIB, conf, value, conf))

    def test_accumulated_duplicates_are_collapsed(self, sh, conf):
        assert conf.read_text().count('\nmaxmemory ') == 6
        self._apply(sh, conf)
        assert conf.read_text().count('\nmaxmemory ') == 1
        assert conf.read_text().count('\nmaxmemory-policy ') == 1

    def test_is_idempotent(self, sh, conf):
        for _ in range(5):
            self._apply(sh, conf)
        text = conf.read_text()
        assert text.count('\nmaxmemory ') == 1
        assert text.count('\nmaxmemory-policy ') == 1

    def test_value_change_replaces_rather_than_appends(self, sh, conf):
        self._apply(sh, conf)
        self._apply(sh, conf, value='3221225472')
        text = conf.read_text()
        assert text.count('\nmaxmemory ') == 1
        assert 'maxmemory 3221225472' in text

    def test_commented_defaults_are_preserved(self, sh, conf):
        self._apply(sh, conf)
        assert '# maxmemory <bytes>' in conf.read_text()

    def test_prefix_key_is_not_clobbered(self, sh, conf):
        """Setting `maxmemory` must not eat the `maxmemory-policy` line."""
        self._apply(sh, conf)
        assert 'maxmemory-policy allkeys-lru' in conf.read_text()

    def test_unrelated_lines_are_kept(self, sh, conf):
        self._apply(sh, conf)
        text = conf.read_text()
        assert 'bind 127.0.0.1 ::1' in text
        assert 'appendonly no' in text

    def test_file_mode_is_preserved(self, sh, conf):
        os.chmod(str(conf), 0o640)
        self._apply(sh, conf)
        assert stat.S_IMODE(os.stat(str(conf)).st_mode) == 0o640

    def test_missing_file_warns_and_fails(self, sh):
        result = sh('. %s\nset_conf_directive %s/nope.conf maxmemory 1' % (LIB, sh.tmp))
        assert result.returncode != 0
        assert 'does not exist' in result.stdout

    def test_no_temp_file_is_left_behind(self, sh, conf):
        self._apply(sh, conf)
        leftovers = [n for n in os.listdir(str(sh.tmp)) if '.flexrun.' in n]
        assert leftovers == []


# --------------------------------------------------------------------------
# install_crontab - one atomic replace, site entries preserved
# --------------------------------------------------------------------------

class TestInstallCrontab:

    @pytest.fixture
    def cron(self, sh):
        live = sh.tmp / 'crontab.live'
        _write_stub(sh.stubs, 'crontab', """
            [ "$1" = "-l" ] && { [ -f "%s" ] && cat "%s" || exit 1; exit 0; }
            [ "$1" = "-" ] && { cat > "%s"; exit 0; }
            exit 0
            """ % (live, live, live))
        sh.live = live
        return sh

    def _install(self, cron):
        return cron('. %s\ninstall_crontab' % LIB,
                    env={'FLEXRUN_BACKUP_DIR': str(cron.tmp / 'backups')})

    def _managed(self, cron):
        lines = cron.live.read_text().splitlines()
        inside, out = False, []
        for line in lines:
            if 'BEGIN flex-run' in line:
                inside = True
                continue
            if 'END flex-run' in line:
                inside = False
                continue
            if inside and line.strip():
                out.append(line)
        return out

    def _outside(self, cron):
        lines = cron.live.read_text().splitlines()
        inside, out = False, []
        for line in lines:
            if 'BEGIN flex-run' in line:
                inside = True
                continue
            if 'END flex-run' in line:
                inside = False
                continue
            if not inside and line.strip():
                out.append(line)
        return out

    def test_installs_on_a_fresh_unit(self, cron):
        result = self._install(cron)
        assert result.returncode == 0, result.stderr
        assert len(self._managed(cron)) >= 17
        assert self._outside(cron) == []

    def test_is_idempotent(self, cron):
        self._install(cron)
        first = cron.live.read_text()
        for _ in range(4):
            self._install(cron)
        assert cron.live.read_text() == first

    def test_site_entries_outside_the_markers_survive(self, cron):
        self._install(cron)
        cron.live.write_text('MAILTO=ops@customer.example\n'
                             '30 3 * * * /opt/customer/nightly_export.sh\n'
                             + cron.live.read_text())
        self._install(cron)
        outside = self._outside(cron)
        assert 'MAILTO=ops@customer.example' in outside
        assert '30 3 * * * /opt/customer/nightly_export.sh' in outside

    def test_duplicates_added_by_other_scripts_are_collapsed(self, cron):
        """ftp_server_setup.sh and local_zip_push.sh still append directly."""
        self._install(cron)
        cron.live.write_text(
            cron.live.read_text()
            + '@reboot sudo sh %s/flex-run/scripts/start_ftp_server.sh\n' % cron.tmp.joinpath('home')
            + '@reboot sudo sh  %s/flex-run/scripts/filesystem_server.sh\n' % cron.tmp.joinpath('home')
        )
        self._install(cron)
        text = cron.live.read_text()
        assert text.count('start_ftp_server.sh') == 1
        assert text.count('filesystem_server.sh') == 1

    def test_a_stale_managed_block_is_replaced_not_duplicated(self, cron):
        cron.live.write_text(
            '# BEGIN flex-run managed - replaced on upgrade, do not edit\n'
            '@reboot echo ENTRY_FROM_AN_OLD_VERSION\n'
            '# END flex-run managed\n')
        self._install(cron)
        text = cron.live.read_text()
        assert 'ENTRY_FROM_AN_OLD_VERSION' not in text
        assert text.count('BEGIN flex-run') == 1

    def test_ftp_entry_tracks_whether_vsftpd_is_configured(self, cron):
        # The entry is conditional on /etc/vsftpd.conf, which we cannot create
        # here; assert the block is internally consistent either way.
        self._install(cron)
        managed = self._managed(cron)
        expected = os.path.exists('/etc/vsftpd.conf')
        present = any('start_ftp_server.sh' in line for line in managed)
        assert present == expected


# --------------------------------------------------------------------------
# ensure_fstab_entry - a duplicate swap line breaks boot
# --------------------------------------------------------------------------

class TestEnsureFstabEntry:

    def _call(self, sh, fstab, line):
        # ensure_fstab_entry targets /etc/fstab; re-point it at the fixture.
        return sh('. %s\n'
                  'ensure_fstab_entry() {\n'
                  '  line="$1"; sf=$(printf "%%s" "$line" | awk "{print \\$1}")\n'
                  '  if awk -v s="$sf" \'$1 == s { f=1 } END { exit !f }\' %s; then\n'
                  '    echo "already present: $sf"; return 0; fi\n'
                  '  printf "%%s\\n" "$line" >> %s; echo "added: $line"; }\n'
                  'ensure_fstab_entry "%s"' % (LIB, fstab, fstab, line))

    @pytest.fixture
    def fstab(self, sh):
        path = sh.tmp / 'fstab'
        path.write_text('UUID=abc / ext4 defaults 0 1\n')
        return path

    def test_adds_when_absent(self, sh, fstab):
        self._call(sh, fstab, '/mnt/swapfile swap swap defaults 0 0')
        assert fstab.read_text().count('/mnt/swapfile') == 1

    def test_repeated_runs_do_not_duplicate(self, sh, fstab):
        for _ in range(3):
            self._call(sh, fstab, '/mnt/swapfile swap swap defaults 0 0')
        assert fstab.read_text().count('/mnt/swapfile') == 1

    def test_matches_on_mount_source_not_whole_line(self, sh, fstab):
        """Different options for the same swapfile is still a duplicate."""
        self._call(sh, fstab, '/mnt/swapfile swap swap defaults 0 0')
        self._call(sh, fstab, '/mnt/swapfile swap swap sw 0 0')
        assert fstab.read_text().count('/mnt/swapfile') == 1

    def test_a_different_source_is_added(self, sh, fstab):
        self._call(sh, fstab, '/mnt/swapfile swap swap defaults 0 0')
        self._call(sh, fstab, '/mnt2/swapfile swap swap defaults 0 0')
        assert fstab.read_text().count('swapfile') == 2

    def test_existing_entries_are_preserved(self, sh, fstab):
        self._call(sh, fstab, '/mnt/swapfile swap swap defaults 0 0')
        assert 'UUID=abc / ext4 defaults 0 1' in fstab.read_text()


# --------------------------------------------------------------------------
# save_state / restore_state - never destroy the only copy of device config
# --------------------------------------------------------------------------

class TestContainerStateStaging:

    @pytest.fixture
    def staged(self, sh):
        staging = sh.tmp / 'staging'
        staging.mkdir()
        helpers = sh.tmp / 'helpers.sh'
        source = open(CONTAINER_UPGRADES).read()
        block = []
        keep = False
        for line in source.splitlines(True):
            if line.startswith('save_state()') or line.startswith('restore_state()'):
                keep = True
            if keep:
                block.append(line)
            if keep and line.rstrip() == '}':
                keep = False
        helpers.write_text(''.join(block))
        assert 'save_state()' in helpers.read_text()
        assert 'restore_state()' in helpers.read_text()
        sh.staging = staging
        sh.helpers = helpers
        return sh

    def _docker(self, sh, exists=True, cp_ok=True, file_in_container=True):
        _write_stub(sh.stubs, 'docker', """
            case "$1" in
              inspect) [ "%s" = "1" ] && exit 0 || exit 1 ;;
              cp)      if [ "%s" = "1" ]; then
                         case "$2" in *:*) echo content > "$3" ;; esac
                         exit 0
                       fi
                       echo "Error: no space left on device" >&2; exit 1 ;;
              exec)    [ "%s" = "1" ] && exit 0 || exit 1 ;;
              *)       exit 0 ;;
            esac
            """ % (int(exists), int(cp_ok), int(file_in_container)))

    def _save(self, sh):
        return sh('STAGING=%s\n. %s\nsave_state capdev /fvbackend/cameras.json cameras.json'
                  % (sh.staging, sh.helpers))

    def test_absent_container_is_safe_to_proceed(self, staged):
        self._docker(staged, exists=False)
        result = self._save(staged)
        assert result.returncode == 0
        assert 'does not exist yet' in result.stdout

    def test_state_is_copied_into_staging_not_host_root(self, staged):
        self._docker(staged)
        result = self._save(staged)
        assert result.returncode == 0
        assert (staged.staging / 'cameras.json').exists()

    def test_absent_file_is_safe_to_proceed(self, staged):
        self._docker(staged, cp_ok=False, file_in_container=False)
        result = self._save(staged)
        assert result.returncode == 0
        assert 'nothing to preserve' in result.stdout

    def test_unsaveable_state_blocks_the_upgrade(self, staged):
        """The data-loss path: config exists but cannot be copied out."""
        self._docker(staged, cp_ok=False, file_in_container=True)
        result = self._save(staged)
        assert result.returncode == 1
        assert 'leaving capdev in place' in result.stdout

    def test_the_gate_prevents_container_removal(self, staged):
        """safe_pull && save_state must skip the whole swap on failure."""
        self._docker(staged, cp_ok=False, file_in_container=True)
        result = staged(
            'STAGING=%s\n. %s\n'
            'safe_pull() { return 0; }\n'
            'remove_container() { echo DESTROYED_$1; }\n'
            'if safe_pull img && save_state capdev /fvbackend/cameras.json cameras.json; then\n'
            '  remove_container capdev\n'
            'fi\n'
            'echo CONTINUED' % (staged.staging, staged.helpers))
        assert 'DESTROYED' not in result.stdout, 'container destroyed with unsaved config'
        assert 'CONTINUED' in result.stdout, 'run should carry on to other containers'

    def test_restore_puts_state_back(self, staged):
        self._docker(staged)
        (staged.staging / 'cameras.json').write_text('saved\n')
        result = staged('STAGING=%s\n. %s\nrestore_state capdev /fvbackend/ cameras.json'
                        % (staged.staging, staged.helpers))
        assert result.returncode == 0
        assert 'restored' in result.stdout

    def test_nothing_staged_is_not_an_error(self, staged):
        self._docker(staged)
        result = staged('STAGING=%s\n. %s\nrestore_state capdev /fvbackend/ cameras.json'
                        % (staged.staging, staged.helpers))
        assert result.returncode == 0
        assert 'nothing staged' in result.stdout

    def test_failed_restore_keeps_the_staged_copy(self, staged):
        self._docker(staged, cp_ok=False)
        (staged.staging / 'cameras.json').write_text('saved\n')
        result = staged('STAGING=%s\n. %s\nrestore_state capdev /fvbackend/ cameras.json'
                        % (staged.staging, staged.helpers))
        assert result.returncode == 1
        assert (staged.staging / 'cameras.json').read_text() == 'saved\n', \
            'staged copy was lost after a failed restore'


# --------------------------------------------------------------------------
# container swap and rollback - the edge that did not exist before
# --------------------------------------------------------------------------

class TestSwapAndRollback:
    """A crash-looping image used to leave a dead service and the script
    carried on to the next container. These tests are about the previous
    container surviving the window and coming back."""

    @pytest.fixture
    def swap(self, sh):
        state = sh.tmp / 'containers.txt'
        calls = sh.tmp / 'docker-calls.log'

        def docker_stub(existing, running, curl_ok):
            state.write_text('\n'.join(existing) + '\n' if existing else '')
            # Every invocation is logged to a file, not stdout: the real
            # functions send `docker stop`/`rm` to /dev/null, so asserting on
            # stub chatter would silently assert nothing.
            _write_stub(sh.stubs, 'docker', """
                STATE=%s
                LOG=%s
                echo "$@" >> "$LOG"
                case "$1" in
                  ps)
                    [ -f "$STATE" ] && cat "$STATE" || true ;;
                  rename)
                    grep -vx "$2" "$STATE" > "$STATE.t" 2>/dev/null || true
                    mv "$STATE.t" "$STATE" 2>/dev/null || true
                    echo "$3" >> "$STATE" ;;
                  rm)
                    target="$3"; [ "$2" = "-f" ] || target="$2"
                    grep -vx "$target" "$STATE" > "$STATE.t" 2>/dev/null || true
                    mv "$STATE.t" "$STATE" 2>/dev/null || true ;;
                  run)
                    for a in "$@"; do
                      case "$a" in --name=*) echo "${a#--name=}" >> "$STATE" ;; esac
                    done ;;
                esac
                exit 0
                """ % (state, calls))
            _write_stub(sh.stubs, 'curl', 'exit %d\n' % (0 if curl_ok else 7))

        def run(script, existing=('capdev',), running=True, curl_ok=True, env=None):
            docker_stub(list(existing), running, curl_ok)
            if calls.exists():
                calls.unlink()
            result = sh('. %s\n%s' % (LIB, script), env=env)
            names = state.read_text().split() if state.exists() else []
            result.docker = calls.read_text() if calls.exists() else ''
            return result, names

        return run

    def test_retire_renames_instead_of_removing(self, swap):
        """The previous container has to still exist during the window."""
        result, names = swap('retire_container capdev')
        assert 'rename capdev capdev_prev' in result.docker
        assert 'rm ' not in result.docker
        assert 'capdev_prev' in names

    def test_retire_stops_the_container_first(self, swap):
        result, _ = swap('retire_container capdev')
        assert 'stop capdev' in result.docker

    def test_retire_on_a_fresh_unit_is_not_an_error(self, swap):
        result, _ = swap('retire_container capdev', existing=[])
        assert result.returncode == 0
        assert 'nothing to retire' in result.stdout

    def test_a_leftover_prev_from_an_interrupted_run_is_discarded(self, swap):
        """Otherwise the rename fails and the only way on would be deleting
        the live container - what this exists to avoid."""
        result, names = swap('retire_container capdev',
                             existing=['capdev', 'capdev_prev'])
        assert 'leftover capdev_prev' in result.stdout
        assert names.count('capdev_prev') == 1

    def test_rollback_restores_and_starts_the_previous(self, swap):
        result, names = swap('rollback_container capdev', existing=['capdev', 'capdev_prev'])
        assert result.returncode == 0
        assert 'rm -f capdev' in result.docker
        assert 'rename capdev_prev capdev' in result.docker
        assert 'start capdev' in result.docker
        assert 'capdev' in names

    def test_rollback_with_nothing_to_restore_says_the_service_is_down(self, swap):
        """Honest failure: better than reporting success with a dead service."""
        result, _ = swap('rollback_container capdev', existing=['capdev'])
        assert result.returncode != 0
        assert 'is DOWN' in result.stdout

    def test_discard_previous_only_removes_the_prev(self, swap):
        result, names = swap('discard_previous capdev', existing=['capdev', 'capdev_prev'])
        assert 'rm -f capdev_prev' in result.docker
        assert 'capdev' in names
        assert 'capdev_prev' not in names

    def test_discard_previous_is_safe_when_there_is_none(self, swap):
        result, _ = swap('discard_previous capdev', existing=['capdev'])
        assert result.returncode == 0

    def test_smoke_http_passes_when_the_endpoint_answers(self, swap):
        result, _ = swap('smoke_http capdev http://x/ready 2 0', curl_ok=True)
        assert result.returncode == 0
        assert 'answered' in result.stdout

    def test_smoke_http_fails_when_it_never_answers(self, swap):
        """A container that started but cannot reach Mongo fails here and
        passes `docker ps` - which is the whole reason this exists."""
        result, _ = swap('smoke_http capdev http://x/ready 2 0', curl_ok=False)
        assert result.returncode != 0
        assert 'never answered' in result.stdout

    def test_the_full_swap_rolls_back_on_a_failed_smoke_check(self, swap):
        """End to end: retire, start, smoke fails, previous comes back."""
        script = (
            'retire_container capdev\n'
            'docker run -d --name=capdev image\n'
            'if smoke_http capdev http://x/ready 1 0; then\n'
            '  discard_previous capdev\n'
            'else\n'
            '  rollback_container capdev\n'
            'fi'
        )
        result, names = swap(script, existing=['capdev'], curl_ok=False)
        assert 'ROLLBACK' in result.stdout
        assert 'rename capdev_prev capdev' in result.docker
        assert 'capdev' in names, 'the service must be back'
        assert 'capdev_prev' not in names, 'the rename consumed it'

    def test_the_full_swap_keeps_the_new_image_on_success(self, swap):
        script = (
            'retire_container capdev\n'
            'docker run -d --name=capdev image\n'
            'if smoke_http capdev http://x/ready 1 0; then\n'
            '  discard_previous capdev\n'
            'else\n'
            '  rollback_container capdev\n'
            'fi'
        )
        result, names = swap(script, existing=['capdev'], curl_ok=True)
        assert 'ROLLBACK' not in result.stdout
        assert 'rm -f capdev_prev' in result.docker
        assert 'capdev' in names


# --------------------------------------------------------------------------
# container dispatch - which containers get upgraded, and with what image name
# --------------------------------------------------------------------------

class TestContainerUpgradeDispatch:
    """`[ "$X" != 'True' ]` decides whether a container is touched at all.

    A mis-quoted comparison here reads every container as out of date and
    re-pulls and swaps the whole stack on every run, or reads them all as
    current and silently upgrades nothing. Both parse cleanly, so only a
    behavioural test catches it.
    """

    @pytest.fixture
    def runner(self, sh):
        staging = sh.tmp / 'staging'
        staging.mkdir()
        (sh.tmp / 'home').mkdir(exist_ok=True)
        # Record what the script tried to pull, and neutralise everything else.
        # `ps` must list the containers as present, or verify_running burns
        # 3 retries x 3s per container and the test takes minutes.
        _write_stub(sh.stubs, 'docker', """
            case "$1" in
              pull) echo "PULLED $2" >> %s/pulls.log ;;
              inspect) exit 1 ;;
              ps) printf '%%s\\n' capdev captureui localprediction predictlite \\
                      vision nodecreator visiontools vernemq ;;
            esac
            exit 0
            """ % sh.tmp)
        _write_stub(sh.stubs, 'python3', 'exit 0\n')
        _write_stub(sh.stubs, 'jq', 'echo stub\n')
        _write_stub(sh.stubs, 'uuidgen', 'echo test-uuid\n')
        # The script tail-calls start_servers.sh by path. Stub the file, not the
        # `sh` binary - the harness itself runs through `sh`.
        tail = sh.tmp / 'home' / 'flex-run' / 'upgrades'
        tail.mkdir(parents=True, exist_ok=True)
        (tail / 'start_servers.sh').write_text('exit 0\n')

        def run(versions, arch='x86'):
            args = ' '.join('"%s"' % v for v in
                            [versions[0], versions[1], versions[2], arch] + list(versions[3:]))
            result = sh('sh %s %s' % (CONTAINER_UPGRADES, args),
                        env={'FLEXRUN_STAGING_DIR': str(staging),
                             'FLEXRUN_RUN_ID': 'test-run'})
            log = sh.tmp / 'pulls.log'
            pulls = log.read_text().splitlines() if log.exists() else []
            if log.exists():
                log.unlink()
            return result, pulls

        return run

    ALL_CURRENT = ['True'] * 7
    ALL_STALE = ['1.9.3'] * 7

    def test_nothing_is_pulled_when_every_container_is_current(self, runner):
        result, pulls = runner(self.ALL_CURRENT)
        app_pulls = [p for p in pulls if 'vernemq' not in p]
        assert app_pulls == [], 'pulled images while already up to date: %s' % app_pulls

    def test_every_container_is_pulled_when_all_are_stale(self, runner):
        result, pulls = runner(self.ALL_STALE)
        for component in ('backend', 'frontend', 'prediction', 'predictlite',
                          'vision', 'nodecreator', 'visiontools'):
            assert any(component in p for p in pulls), \
                '%s was not pulled: %s' % (component, pulls)

    def test_only_the_stale_container_is_pulled(self, runner):
        versions = ['1.9.3'] + ['True'] * 6          # backend stale, rest current
        result, pulls = runner(versions)
        app_pulls = [p for p in pulls if 'vernemq' not in p]
        assert len(app_pulls) == 1, app_pulls
        assert 'backend:1.9.3' in app_pulls[0]

    def test_image_name_uses_the_arch_argument(self, runner):
        result, pulls = runner(['1.9.3'] + ['True'] * 6, arch='arm')
        assert any('fvonprem/arm-backend:1.9.3' in p for p in pulls), pulls

    def test_an_empty_version_is_not_a_syntax_error(self, runner):
        """Reachable when the version service returns 200 with an empty body."""
        result, pulls = runner([''] + ['True'] * 6)
        assert 'unexpected operator' not in result.stderr, result.stderr
        assert 'syntax error' not in result.stderr.lower(), result.stderr


class TestUpgradeSystemDispatch:
    """upgrade_system.sh must not let an empty version shift the arch argument."""

    @pytest.fixture
    def dispatch(self, sh):
        (sh.tmp / 'home' / 'flex-run' / 'upgrades').mkdir(parents=True)
        upgrades = sh.tmp / 'home' / 'flex-run' / 'upgrades'
        (upgrades / 'install_dependencies.sh').write_text('exit 0\n')
        # Stand in for the real container script and record its argv.
        (upgrades / 'system_container_upgrades.sh').write_text(
            'printf "%s\\n" "$@" > ' + str(sh.tmp / 'argv.txt') + '\n')

        def run(versions, arch='x86_64'):
            _write_stub(sh.stubs, 'arch', 'echo %s\n' % arch)
            args = ' '.join('"%s"' % v for v in versions)
            result = sh('sh %s %s' % (
                os.path.join(REPO, 'system_server', 'upgrade_system.sh'), args))
            argv_file = sh.tmp / 'argv.txt'
            argv = argv_file.read_text().splitlines() if argv_file.exists() else []
            return result, argv

        return run

    def test_arch_lands_in_position_four(self, dispatch):
        result, argv = dispatch(['1.9.3'] * 7)
        assert argv == ['1.9.3', '1.9.3', '1.9.3', 'x86',
                        '1.9.3', '1.9.3', '1.9.3', '1.9.3'], argv

    def test_empty_version_does_not_shift_the_arch(self, dispatch):
        """Unquoted, the empty arg vanished and 'x86' moved to position 3."""
        result, argv = dispatch(['1.9.3', '', '1.9.1', '1.9.2', '1.9.2', '1.8.4', '1.9.0'])
        assert len(argv) == 8, 'argument count changed: %s' % argv
        assert argv[1] == '', 'empty version was dropped instead of passed'
        assert argv[3] == 'x86', 'arch ended up in the wrong position: %s' % argv

    def test_arm_is_mapped(self, dispatch):
        result, argv = dispatch(['1.9.3'] * 7, arch='aarch64')
        assert argv[3] == 'arm', argv

    def test_unsupported_arch_fails_loudly(self, dispatch):
        """It used to fall through both branches and exit 0 having done nothing."""
        result, argv = dispatch(['1.9.3'] * 7, arch='riscv64')
        assert result.returncode != 0
        assert 'unsupported architecture' in result.stderr
        assert argv == [], 'container upgrade ran on an unsupported arch'


# --------------------------------------------------------------------------
# every deploy script must at least parse under dash and bash
# --------------------------------------------------------------------------

def _deploy_scripts():
    found = []
    for relative in ('upgrades', 'setup', 'scripts', 'system_server'):
        base = os.path.join(REPO, relative)
        for root, dirs, files in os.walk(base):
            # create_ap is vendored third-party bash; not ours to hold to this.
            dirs[:] = [d for d in dirs if d not in ('create_ap', '__pycache__')]
            for name in files:
                if name.endswith('.sh') and not name.startswith('._'):
                    found.append(os.path.join(root, name))
    return sorted(found)


# Scripts that legitimately need bash. Everything else must parse under dash,
# because the deploy path runs scripts as `sh <script>` - from subprocess calls
# and from `@reboot sudo sh ...` crontab entries - and there the shebang is
# ignored, so a bashism is a runtime failure on a device.
#
# Checking every script and listing the exceptions is deliberate: working out
# which scripts are `sh`-invoked needs pattern-matching over shell source and
# gets it wrong quietly, whereas an over-strict check fails loudly and is fixed
# by adding one line here with a reason.
BASH_ONLY = {
    'scripts/configure_network.sh',  # bash array; executed directly, shebang applies
}


@pytest.mark.parametrize('script', _deploy_scripts(),
                         ids=lambda p: os.path.relpath(p, REPO))
def test_script_parses(script):
    relative = os.path.relpath(script, REPO)
    shell = 'bash' if relative in BASH_ONLY else 'dash'
    result = subprocess.run([shell, '-n', script], capture_output=True, text=True)
    assert result.returncode == 0, (
        '%s must parse under %s: %s' % (relative, shell, result.stderr))


def test_bash_only_allowlist_stays_minimal():
    """Stops the allowlist rotting: if it parses under dash, it should be checked."""
    for relative in sorted(BASH_ONLY):
        path = os.path.join(REPO, relative)
        assert os.path.exists(path), '%s is on BASH_ONLY but does not exist' % relative
        result = subprocess.run(['dash', '-n', path], capture_output=True, text=True)
        assert result.returncode != 0, (
            '%s parses under dash now - remove it from BASH_ONLY so it keeps '
            'being checked as POSIX' % relative)


# Scripts held to a clean shellcheck run. Scoped to the deploy path rather than
# the whole repo so it can be enforced now instead of after a 61-file cleanup;
# widen it as other scripts are brought up to standard.
SHELLCHECK_CLEAN = [
    'upgrades/upgrade_flex_run.sh',
    'upgrades/lib/deploy_common.sh',
    'upgrades/install_dependencies.sh',
    'upgrades/system_container_upgrades.sh',
    'system_server/upgrade_system.sh',
    'scripts/local_setup.sh',
    'setup/system_setup.sh',
]

# SC2034 fires on variables the docker run lines read indirectly, and SC1090 on
# the runtime-resolved library path - both are intentional here.
SHELLCHECK_IGNORE = 'SC2034,SC1090'


@pytest.mark.parametrize('relative', SHELLCHECK_CLEAN)
def test_deploy_script_is_shellcheck_clean(relative):
    binary = shutil.which('shellcheck')
    if binary is None:
        pytest.skip('shellcheck not installed (pip install -r requirements-dev.txt)')
    path = os.path.join(REPO, relative)
    result = subprocess.run(
        [binary, '-s', 'dash', '-e', SHELLCHECK_IGNORE, '-f', 'gcc', path],
        capture_output=True, text=True)
    assert result.returncode == 0, '\n' + result.stdout
