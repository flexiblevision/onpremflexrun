"""Deploying an addon from its descriptor."""
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from addons import registry, runtime


def completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture
def docker():
    with patch.object(runtime, '_run', return_value=completed()) as run:
        yield run


def argv_for(run, index=-1):
    return run.call_args_list[index][0][0]


class TestReferenceResolution:
    @pytest.mark.unit
    def test_the_repository_is_arch_aware(self):
        addon = registry.get('anomaly_audio')
        assert runtime.resolve_reference(addon, 'arm') == \
            'fvonprem/arm-audio-anomaly:1'

    @pytest.mark.unit
    def test_ocr_keeps_the_tag_its_install_script_used(self):
        # Changing it would move every device to a different image on enable.
        assert runtime.resolve_reference(registry.get('ocr'), 'x86') == \
            'fvonprem/x86-ocr:prod'

    @pytest.mark.unit
    def test_a_pinned_digest_overrides_the_tag(self):
        pinned = 'fvonprem/x86-ocr@sha256:' + 'a' * 64
        assert runtime.resolve_reference(registry.get('ocr'), 'x86', pinned) == pinned

    @pytest.mark.unit
    def test_image_tag_in_the_environment_still_wins(self, monkeypatch):
        monkeypatch.setenv('IMAGE_TAG', '7')
        assert runtime.resolve_reference(registry.get('assembly'), 'x86') == \
            'fvonprem/x86-assembly-client:7'

    @pytest.mark.unit
    @pytest.mark.parametrize('machine,arch', [
        ('x86_64', 'x86'), ('aarch64', 'arm'), ('riscv64', 'riscv64')])
    def test_arch_naming_matches_the_shell_convention(self, machine, arch):
        assert runtime.system_arch(machine) == arch


class TestRunArgv:
    @pytest.mark.unit
    def test_the_audio_argv_matches_its_old_install_script(self):
        addon = registry.get('anomaly_audio')
        argv = runtime.build_run_argv(addon, 'img')

        assert argv[:5] == ['docker', 'run', '-d', '--name', 'audio-anomaly']
        assert '--restart' in argv and 'unless-stopped' in argv
        assert argv[argv.index('--network') + 1] == 'host'
        assert argv[argv.index('--gpus') + 1] == 'device=0'
        assert 'MONGO_URI=mongodb://172.17.0.1:27017/' in argv
        assert ('/home/visioncell/Documents/audio_anomaly_data:/app/data') in argv
        assert 'max-size=50m' in argv and 'max-file=5' in argv
        assert argv[-1] == 'img'

    @pytest.mark.unit
    def test_published_ports_are_rendered(self):
        argv = runtime.build_run_argv(registry.get('assembly'), 'img')
        assert argv[argv.index('-p') + 1] == '3021:3021'

    @pytest.mark.unit
    def test_the_image_is_the_last_argument(self):
        argv = runtime.build_run_argv(registry.get('ocr'), 'img')
        assert argv[-1] == 'img'

    @pytest.mark.unit
    def test_no_gpu_flag_when_the_addon_does_not_want_one(self):
        argv = runtime.build_run_argv(registry.get('assembly'), 'img')
        assert '--gpus' not in argv

    @pytest.mark.unit
    def test_values_are_passed_as_argv_not_shell(self):
        addon = dict(registry.get('ocr'))
        addon['container'] = dict(addon['container'], env={'K': 'a b"; rm -rf /'})
        argv = runtime.build_run_argv(addon, 'img')
        assert 'K=a b"; rm -rf /' in argv


class TestDeploy:
    @pytest.mark.unit
    def test_the_previous_container_is_removed_first(self, docker):
        # install_ocr.sh did not: a second run collided on the name.
        runtime.deploy('ocr', arch='x86')

        commands = [c[0][0][:2] for c in docker.call_args_list]
        assert ['docker', 'pull'] in commands
        assert ['docker', 'stop'] in commands
        assert ['docker', 'rm'] in commands
        assert commands.index(['docker', 'rm']) < \
            [i for i, c in enumerate(commands) if c == ['docker', 'run']][0]

    @pytest.mark.unit
    def test_an_arch_the_addon_is_not_built_for_is_refused(self, docker):
        # Only an x86 ocr image exists; an arm enable could never succeed.
        with pytest.raises(runtime.DeployError) as exc:
            runtime.deploy('ocr', arch='arm')
        assert 'arm' in str(exc.value)
        docker.assert_not_called()

    @pytest.mark.unit
    def test_a_failed_pull_still_tries_a_local_image(self):
        def responses(argv, timeout=None):
            if argv[:2] == ['docker', 'pull']:
                return completed(1, stderr='no such host')
            return completed(0)

        with patch.object(runtime, '_run', side_effect=responses) as run:
            assert runtime.deploy('ocr', arch='x86')

        assert any(c[0][0][:2] == ['docker', 'run'] for c in run.call_args_list)

    @pytest.mark.unit
    def test_a_failed_run_raises_rather_than_reporting_success(self):
        def responses(argv, timeout=None):
            if argv[:2] == ['docker', 'run']:
                return completed(1, stderr='no such image')
            return completed(0)

        with patch.object(runtime, '_run', side_effect=responses):
            with pytest.raises(runtime.DeployError) as exc:
                runtime.deploy('assembly', arch='x86')
        assert 'no such image' in str(exc.value)

    @pytest.mark.unit
    def test_an_optional_gpu_falls_back_to_cpu(self):
        # A device with no nvidia runtime must still be able to enable it.
        attempts = []

        def responses(argv, timeout=None):
            if argv[:2] == ['docker', 'run']:
                attempts.append(argv)
                if '--gpus' in argv:
                    return completed(125, stderr='could not select device driver')
            return completed(0)

        with patch.object(runtime, '_run', side_effect=responses), \
             patch.object(runtime, '_ensure_volumes'):
            assert runtime.deploy('anomaly_audio', arch='x86')

        assert len(attempts) == 2
        assert '--gpus' in attempts[0]
        assert '--gpus' not in attempts[1]

    @pytest.mark.unit
    def test_declared_volumes_are_created(self, docker, tmp_path):
        addon = dict(registry.get('anomaly_audio'))
        target = tmp_path / 'audio_data'
        addon['container'] = dict(
            addon['container'],
            volumes=[{'host': str(target), 'container': '/app/data', 'create': True}])

        with patch.object(registry, 'get', return_value=addon):
            runtime.deploy('anomaly_audio', arch='x86')

        assert target.is_dir()


class TestTeardown:
    @pytest.mark.unit
    def test_the_container_is_stopped_and_removed(self, docker):
        runtime.teardown('anomaly_audio')

        assert [c[0][0] for c in docker.call_args_list] == [
            ['docker', 'stop', 'audio-anomaly'],
            ['docker', 'rm', 'audio-anomaly'],
        ]

    @pytest.mark.unit
    def test_a_kind_with_no_hook_is_an_error(self, docker):
        addon = dict(registry.get('client_mode'))
        with patch.object(registry, 'get', return_value=addon):
            with pytest.raises(runtime.DeployError):
                runtime.teardown('client_mode')


class TestHealth:
    @pytest.mark.unit
    def test_a_200_is_healthy(self):
        with patch('addons.runtime.requests.get',
                   return_value=MagicMock(status_code=200)) as get:
            assert runtime.health('anomaly_audio') is True

        assert get.call_args[0][0] == 'http://172.17.0.1:5702/api/audio/devices'

    @pytest.mark.unit
    def test_the_probe_is_bounded(self):
        # An unbounded probe hangs the settings page on a wedged container.
        with patch('addons.runtime.requests.get',
                   return_value=MagicMock(status_code=200)) as get:
            runtime.health('anomaly_audio')
        assert get.call_args[1]['timeout'] == 2

    @pytest.mark.unit
    def test_a_non_200_is_not_healthy(self):
        with patch('addons.runtime.requests.get',
                   return_value=MagicMock(status_code=503)):
            assert runtime.health('ocr') is False

    @pytest.mark.unit
    def test_an_unreachable_service_is_not_healthy(self):
        with patch('addons.runtime.requests.get', side_effect=OSError('refused')):
            assert runtime.health('ocr') is False

    @pytest.mark.unit
    def test_a_systemd_addon_is_checked_with_systemctl(self, docker):
        assert runtime.health('ftp') is True
        assert docker.call_args[0][0] == \
            ['systemctl', 'is-active', '--quiet', 'vsftpd']
