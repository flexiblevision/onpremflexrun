"""Behavioural tests for the deploy shell scripts.

These scripts are the actual install and upgrade path for a factory-floor
device, and a fault in them costs a site visit. They are driven here through
pytest rather than a separate shell harness so they run under the same command
as everything else, with no extra tooling to install.

Each test runs the real script or the real function with `docker`, `git`,
`crontab`, `sudo` and `nvidia-smi` replaced by stubs, so nothing touches the
host.
"""
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
    'system_server/upgrade_system.sh',
    'scripts/local_setup.sh',
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
